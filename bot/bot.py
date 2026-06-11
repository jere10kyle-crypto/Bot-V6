import discord
from discord.ext import commands, tasks
import json
import os
import time
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

# ── Config ───────────────────────────────────────────────────────────────────
TOKEN    = os.getenv("DISCORD_TOKEN", "")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ── Data helpers ──────────────────────────────────────────────────────────────
def load_json(filename, default):
    path = os.path.join(DATA_DIR, filename)
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ── Shared state ──────────────────────────────────────────────────────────────
strikes    = load_json("strikes.json", {})
logs       = load_json("logs.json", [])
user_names = load_json("user_names.json", {})

_raw_words = load_json("banned_words.json", {})
if isinstance(_raw_words, list):
    banned_words = {w: 1 for w in _raw_words}
    save_json("banned_words.json", banned_words)
else:
    banned_words = _raw_words

# Anti-spam / anti-raid tracking (in-memory only)
message_times      = defaultdict(list)
warned_users       = set()
word_warning_count = defaultdict(int)
word_repeat_times  = defaultdict(lambda: defaultdict(list))

# Anti-raid state
guild_join_times: dict = defaultdict(list)  # {guild_id: [timestamps]}
raid_active: set = set()                    # guild_ids currently in lockdown
raid_unlock_tasks: dict = {}               # {guild_id: asyncio.Task}

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Logging helper ────────────────────────────────────────────────────────────
def add_log(action, user, reason, moderator="AutoMod"):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action":    action,
        "user":      str(user),
        "reason":    reason,
        "moderator": str(moderator),
    }
    logs.append(entry)
    if len(logs) > 500:
        logs.pop(0)
    save_json("logs.json", logs)
    if hasattr(user, "id") and hasattr(user, "display_name"):
        user_names[str(user.id)] = user.display_name
        save_json("user_names.json", user_names)

DEFAULT_SETTINGS = {
    "tier1_strikes": 1,  "tier1_minutes": 5,
    "tier2_strikes": 2,  "tier2_minutes": 15,
    "tier3_strikes": 3,  "tier3_minutes": 60,
    "tier4_strikes": 4,  "tier4_minutes": 1440,
    "tier5_strikes": 5,  "tier5_minutes": 40320,
    "spam_word_limit":   5,
    "spam_word_window":  10,
    "spam_word_tier":    2,
    "raid_join_limit":      5,
    "raid_join_window":     10,
    "raid_lockdown_minutes": 10,
    "raid_timeout_minutes":  10,
}

TIERS = [5, 4, 3, 2, 1]

def get_settings():
    return {**DEFAULT_SETTINGS, **load_json("settings.json", {})}

# ── Strike / timeout helpers ──────────────────────────────────────────────────
async def apply_timeout(member, mins, reason):
    """Apply a Discord timeout (mute). Discord caps timeouts at 40320 minutes (28 days)."""
    mins = min(mins, 40320)
    until = discord.utils.utcnow() + timedelta(minutes=mins)
    try:
        await member.timeout(until, reason=reason)
        return True
    except discord.Forbidden:
        print(f"[AutoMod] ⚠️  TIMEOUT FAILED for {member} — bot needs 'Timeout Members' permission and a higher role")
        return False
    except discord.HTTPException as e:
        print(f"[AutoMod] ⚠️  TIMEOUT HTTP ERROR for {member}: {e}")
        return False

async def add_strike(guild, member, reason):
    uid = str(member.id)
    current = load_json("strikes.json", {})
    current[uid] = current.get(uid, 0) + 1
    strikes.update(current)
    save_json("strikes.json", current)
    add_log("STRIKE", member, reason)

    count = strikes[uid]
    s = get_settings()
    for t in TIERS:
        if count >= s[f"tier{t}_strikes"]:
            mins = min(s[f"tier{t}_minutes"], 40320)
            ok = await apply_timeout(member, mins,
                reason=f"Tier {t} – {count} strike(s) – {mins}m mute")
            if ok:
                add_log(f"MUTE_T{t}", member,
                    f"Tier {t} triggered at {count} strike(s) ({mins}m)")
            break
    return count

# ── Spam / anti-raid detection ────────────────────────────────────────────────
SPAM_LIMIT  = 5
SPAM_WINDOW = 5

async def send_dm(user, content):
    try:
        await user.send(content)
    except (discord.Forbidden, discord.HTTPException):
        pass

async def purge_user_messages(channel, user, limit=50):
    try:
        await channel.purge(limit=limit, check=lambda m: m.author.id == user.id)
    except (discord.Forbidden, discord.HTTPException):
        pass

async def check_spam(message):
    uid = str(message.author.id)
    now = time.time()
    message_times[uid] = [t for t in message_times[uid] if now - t < SPAM_WINDOW]
    message_times[uid].append(now)
    if len(message_times[uid]) < SPAM_LIMIT:
        warned_users.discard(uid)
    if len(message_times[uid]) >= SPAM_LIMIT:
        if uid not in warned_users:
            warned_users.add(uid)
            message_times[uid] = []
            await purge_user_messages(message.channel, message.author)
            await send_dm(message.author,
                f"⚠️ **AutoMod — {message.guild.name}**\n"
                f"You were flagged for spamming and your messages were removed. "
                f"Please slow down or you will be muted.")
            await add_strike(message.guild, message.author, "Spam detection")
            return True
    return False

async def check_word_spam(message):
    s      = get_settings()
    limit  = s["spam_word_limit"]
    window = s["spam_word_window"]
    tier   = max(1, min(5, s["spam_word_tier"]))
    uid    = str(message.author.id)
    now    = time.time()
    for word in message.content.lower().split():
        if not word:
            continue
        times = word_repeat_times[uid][word]
        times = [t for t in times if now - t < window]
        times.append(now)
        word_repeat_times[uid][word] = times
        if len(times) >= limit:
            word_repeat_times[uid][word] = []
            s2   = get_settings()
            mins = min(s2[f"tier{tier}_minutes"], 40320)
            await purge_user_messages(message.channel, message.author)
            await send_dm(message.author,
                f"🔁 **AutoMod — {message.guild.name}**\n"
                f"You were muted for repeating the same word too many times. "
                f"Tier {tier} mute applied ({mins} minute(s)). Your messages were removed.")
            ok = await apply_timeout(message.author, mins,
                reason=f"Word-repeat spam: '{word}' x{limit} in {window}s (Tier {tier})")
            if ok:
                add_log(f"MUTE_T{tier}", message.author,
                    f"Word-repeat spam: '{word}' x{limit} in {window}s ({mins}m)")
            strikes[uid] = strikes.get(uid, 0) + 1
            save_json("strikes.json", strikes)
            add_log("STRIKE", message.author, f"Word-repeat spam: '{word}'")
            return True
    return False

async def check_banned_words(message):
    content = message.content.lower()
    for word, tier in banned_words.items():
        if word.lower() in content:
            uid = str(message.author.id)
            word_warning_count[uid] += 1
            await message.delete()
            strikes[uid] = strikes.get(uid, 0) + 1
            save_json("strikes.json", strikes)
            add_log("STRIKE", message.author, f"Tier {tier} banned word: '{word}'")
            if word_warning_count[uid] >= 2:
                s    = get_settings()
                mins = min(s[f"tier{tier}_minutes"], 40320)
                await send_dm(message.author,
                    f"🚫 **AutoMod — {message.guild.name}**\n"
                    f"Your message was deleted for containing a Tier {tier} banned word. "
                    f"A mute of {mins} minute(s) has been applied.")
                ok = await apply_timeout(message.author, mins,
                    reason=f"Tier {tier} banned word: '{word}' ({mins}m)")
                if ok:
                    add_log(f"MUTE_T{tier}", message.author,
                        f"Tier {tier} word: '{word}' ({mins}m)")
            else:
                await send_dm(message.author,
                    f"⚠️ **AutoMod — {message.guild.name}**\n"
                    f"Your message was deleted — it contained a Tier {tier} banned word. "
                    f"One more and you will be muted.")
            return True
    return False

# ── Anti-raid detection ───────────────────────────────────────────────────────
async def _lock_guild(guild: discord.Guild):
    """Overwrite @everyone send_messages to False in all text channels."""
    everyone = guild.default_role
    locked = 0
    for ch in guild.text_channels:
        try:
            overwrite = ch.overwrites_for(everyone)
            overwrite.send_messages = False
            await ch.set_permissions(everyone, overwrite=overwrite, reason="AutoMod anti-raid lockdown")
            locked += 1
        except (discord.Forbidden, discord.HTTPException):
            pass
    print(f"[Raid] 🔒 Locked {locked} channel(s) in {guild.name}")

async def _unlock_guild(guild: discord.Guild):
    """Re-enable @everyone send_messages in all text channels and clear raid state."""
    everyone = guild.default_role
    unlocked = 0
    for ch in guild.text_channels:
        try:
            overwrite = ch.overwrites_for(everyone)
            overwrite.send_messages = None  # reset to inherit
            await ch.set_permissions(everyone, overwrite=overwrite, reason="AutoMod anti-raid lockdown lifted")
            unlocked += 1
        except (discord.Forbidden, discord.HTTPException):
            pass
    raid_active.discard(guild.id)
    raid_unlock_tasks.pop(guild.id, None)
    add_log("RAID_UNLOCK", "AutoMod", f"Lockdown lifted — {unlocked} channel(s) re-opened in {guild.name}")
    print(f"[Raid] 🔓 Unlocked {unlocked} channel(s) in {guild.name}")

async def _schedule_unlock(guild: discord.Guild, mins: int):
    await asyncio.sleep(mins * 60)
    if guild.id in raid_active:
        await _unlock_guild(guild)
        for ch in guild.text_channels:
            try:
                await ch.send("✅ **Anti-Raid** — Lockdown lifted automatically. Chat is open again.")
                break
            except (discord.Forbidden, discord.HTTPException):
                pass

async def check_raid(member: discord.Member):
    """Detect join surges and trigger lockdown if threshold is exceeded."""
    guild = member.guild
    if guild.id in raid_active:
        s = get_settings()
        mins = s["raid_timeout_minutes"]
        await apply_timeout(member, mins, reason="Joined during anti-raid lockdown")
        await send_dm(member,
            f"🛡️ **{guild.name}** is currently under an anti-raid lockdown.\n"
            f"You have been timed out for {mins} minute(s). Please try again later.")
        add_log("RAID_JOIN_MUTED", member, f"Joined during lockdown — {mins}m timeout")
        return

    s   = get_settings()
    now = time.time()
    gid = guild.id
    guild_join_times[gid] = [t for t in guild_join_times[gid] if now - t < s["raid_join_window"]]
    guild_join_times[gid].append(now)

    if len(guild_join_times[gid]) >= s["raid_join_limit"]:
        guild_join_times[gid] = []
        raid_active.add(gid)
        add_log("RAID_DETECTED", "AutoMod",
            f"{s['raid_join_limit']} joins in {s['raid_join_window']}s — lockdown triggered in {guild.name}")
        print(f"[Raid] ⚠️  Raid detected in {guild.name} — locking down")

        await _lock_guild(guild)

        for ch in guild.text_channels:
            try:
                await ch.send(
                    f"🚨 **Anti-Raid Mode Activated** — {s['raid_join_limit']} accounts joined in "
                    f"{s['raid_join_window']} seconds. Chat is locked. "
                    f"Lockdown lifts in **{s['raid_lockdown_minutes']} minute(s)**.")
                break
            except (discord.Forbidden, discord.HTTPException):
                pass

        mins = s["raid_timeout_minutes"]
        cutoff = now - s["raid_join_window"]
        for m in guild.members:
            if m.bot or m == member:
                continue
            if m.joined_at and m.joined_at.timestamp() >= cutoff:
                await apply_timeout(m, mins, reason="Anti-raid: mass join detected")
                await send_dm(m,
                    f"🛡️ **{guild.name}** triggered anti-raid mode due to a surge of joins.\n"
                    f"You have been timed out for {mins} minute(s).")
        await apply_timeout(member, mins, reason="Anti-raid: mass join detected")
        await send_dm(member,
            f"🛡️ **{guild.name}** triggered anti-raid mode due to a surge of joins.\n"
            f"You have been timed out for {mins} minute(s).")

        task = asyncio.create_task(_schedule_unlock(guild, s["raid_lockdown_minutes"]))
        raid_unlock_tasks[gid] = task

# ── Background task: process dashboard-queued strikes ─────────────────────────
@tasks.loop(seconds=10)
async def process_pending_strikes():
    pending = load_json("pending_strikes.json", [])
    if not pending:
        return
    save_json("pending_strikes.json", [])
    for entry in pending:
        uid    = str(entry.get("user_id", ""))
        reason = entry.get("reason", "Dashboard strike")
        if not uid:
            continue
        member = None
        guild  = None
        for g in bot.guilds:
            m = g.get_member(int(uid))
            if m:
                member = m
                guild  = g
                break
        if member and guild:
            await add_strike(guild, member, reason)
            print(f"[AutoMod] Dashboard strike processed for {member} — {reason}")
        else:
            print(f"[AutoMod] ⚠️  Pending strike: user {uid} not found in any guild")

@process_pending_strikes.before_loop
async def before_pending():
    await bot.wait_until_ready()

# ── Background task: reset strikes every 12 hours ─────────────────────────────
@tasks.loop(hours=12)
async def reset_strikes_task():
    global strikes
    count  = len(strikes)
    strikes = {}
    save_json("strikes.json", strikes)
    add_log("AUTO_RESET", "AutoMod",
        f"Scheduled 12-hour strike reset cleared {count} user record(s)")
    print(f"[AutoMod] 🔄 Scheduled strike reset — cleared {count} user record(s)")

@reset_strikes_task.before_loop
async def before_reset():
    await bot.wait_until_ready()

# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print(f"   py-cord {discord.__version__}")
    if not process_pending_strikes.is_running():
        process_pending_strikes.start()
    if not reset_strikes_task.is_running():
        reset_strikes_task.start()
    print(f"   Strike auto-reset scheduled every 12 hours")
    count = 0
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot:
                user_names[str(member.id)] = member.display_name
                count += 1
    save_json("user_names.json", user_names)
    print(f"   Cached {count} member name(s) for dashboard lookups")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    if await check_banned_words(message):
        return
    if await check_word_spam(message):
        return
    if await check_spam(message):
        return
    await bot.process_commands(message)

# ── Welcome message ───────────────────────────────────────────────────────────
DEFAULT_WELCOME = {
    "enabled": False,
    "dm": True,
    "channel_id": "",
    "message": "👋 Welcome to **{server}**, {user}!\nPlease read the rules and enjoy your stay.",
}

def get_welcome():
    saved = load_json("welcome.json", {})
    return {**DEFAULT_WELCOME, **saved}

def format_welcome(text: str, member: discord.Member) -> str:
    return (text
        .replace("{user}", member.mention)
        .replace("{username}", member.display_name)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
    )

async def send_welcome(member: discord.Member):
    w = get_welcome()
    if not w.get("enabled"):
        return
    text = format_welcome(w["message"], member)
    if w.get("dm"):
        await send_dm(member, text)
    else:
        ch_id = w.get("channel_id", "")
        if ch_id:
            ch = member.guild.get_channel(int(ch_id)) if ch_id.isdigit() else None
            if ch:
                try:
                    await ch.send(text)
                except (discord.Forbidden, discord.HTTPException):
                    pass

@bot.event
async def on_member_join(member):
    if not member.bot:
        user_names[str(member.id)] = member.display_name
        save_json("user_names.json", user_names)
    add_log("JOIN", member, "Joined the server")
    if not member.bot:
        await check_raid(member)
        await send_welcome(member)

@bot.event
async def on_application_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)

# ── Slash commands ─────────────────────────────────────────────────────────────
@bot.slash_command(name="mute", description="Mute a member for a given number of minutes",
                   default_member_permissions=discord.Permissions(moderate_members=True))
@discord.option("member", description="The member to mute")
@discord.option("minutes", description="Duration in minutes (default 15)", default=15)
async def mute(ctx: discord.ApplicationContext, member: discord.Member, minutes: int = 15):
    ok = await apply_timeout(member, minutes, reason=f"Muted by {ctx.author}")
    if ok:
        add_log("MUTE", member, f"Manual mute {minutes}m by {ctx.author}")
        await ctx.respond(f"🔇 {member.mention} muted for {minutes} minute(s).", ephemeral=True)
    else:
        await ctx.respond(
            f"❌ Could not mute {member.mention} — ensure the bot has **Timeout Members** permission "
            f"and that {member.mention} has a lower role than the bot.", ephemeral=True)

@bot.slash_command(name="unmute", description="Remove a mute from a member",
                   default_member_permissions=discord.Permissions(moderate_members=True))
@discord.option("member", description="The member to unmute")
async def unmute(ctx: discord.ApplicationContext, member: discord.Member):
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        add_log("UNMUTE", member, f"Manual unmute by {ctx.author}")
        await ctx.respond(f"🔊 {member.mention} unmuted.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("❌ Missing permissions to remove timeout.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.respond(f"❌ Failed: {e}", ephemeral=True)

@bot.slash_command(name="strike", description="Manually add a strike to a member",
                   default_member_permissions=discord.Permissions(moderate_members=True))
@discord.option("member", description="The member to strike")
@discord.option("reason", description="Reason for the strike", default="Manual strike")
async def strike_cmd(ctx: discord.ApplicationContext, member: discord.Member, reason: str = "Manual strike"):
    await ctx.defer(ephemeral=True)
    count = await add_strike(ctx.guild, member, f"{reason} (by {ctx.author})")
    s = get_settings()
    tier_hit = next((t for t in TIERS if count >= s[f"tier{t}_strikes"]), None)
    tier_info = f"Tier {tier_hit} mute applied." if tier_hit else ""
    await ctx.followup.send(
        f"⚠️ **{member.display_name}** now has **{count}** strike(s). {tier_info}",
        ephemeral=True)

@bot.slash_command(name="strikes", description="Check how many strikes a user has",
                   default_member_permissions=discord.Permissions(moderate_members=True))
@discord.option("member", description="The member to check")
async def strikes_cmd(ctx: discord.ApplicationContext, member: discord.Member):
    count = strikes.get(str(member.id), 0)
    await ctx.respond(f"⚠️ {member.mention} has **{count}** strike(s).", ephemeral=True)

@bot.slash_command(name="resetstrikes", description="Reset all strikes for a user",
                   default_member_permissions=discord.Permissions(administrator=True))
@discord.option("member", description="The member to reset")
async def reset_strikes_cmd(ctx: discord.ApplicationContext, member: discord.Member):
    strikes.pop(str(member.id), None)
    save_json("strikes.json", strikes)
    add_log("RESET_STRIKES", member, f"Reset by {ctx.author}")
    await ctx.respond(f"✅ Strikes reset for {member.mention}.", ephemeral=True)

@bot.slash_command(name="purge", description="Delete the last N messages in this channel",
                   default_member_permissions=discord.Permissions(manage_messages=True))
@discord.option("amount", description="Number of messages to delete (1–200)")
async def purge_cmd(ctx: discord.ApplicationContext, amount: int):
    if amount < 1 or amount > 200:
        await ctx.respond("❌ Amount must be between 1 and 200.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    try:
        deleted = await ctx.channel.purge(limit=amount)
        add_log("PURGE", ctx.author,
            f"Deleted {len(deleted)} message(s) in #{ctx.channel.name}")
        await ctx.followup.send(f"🗑️ Deleted **{len(deleted)}** message(s).", ephemeral=True)
    except discord.Forbidden:
        await ctx.followup.send(
            "❌ I don't have permission to delete messages here.", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.followup.send(f"❌ Failed: {e}", ephemeral=True)

@bot.slash_command(name="addword", description="Add a word to the banned list",
                   default_member_permissions=discord.Permissions(administrator=True))
@discord.option("word", description="The word to ban")
@discord.option("tier", description="Severity tier 1–5 (default 1)", default=1)
async def addword_cmd(ctx: discord.ApplicationContext, word: str, tier: int = 1):
    w = word.lower().strip()
    t = max(1, min(5, tier))
    banned_words[w] = t
    save_json("banned_words.json", banned_words)
    await ctx.respond(f"✅ `{w}` added as Tier {t} banned word.", ephemeral=True)

@bot.slash_command(name="removeword", description="Remove a word from the banned list",
                   default_member_permissions=discord.Permissions(administrator=True))
@discord.option("word", description="The word to remove")
async def removeword_cmd(ctx: discord.ApplicationContext, word: str):
    w = word.lower().strip()
    if w in banned_words:
        banned_words.pop(w)
        save_json("banned_words.json", banned_words)
        await ctx.respond(f"✅ `{w}` removed.", ephemeral=True)
    else:
        await ctx.respond(f"❌ `{w}` not in the banned list.", ephemeral=True)

@bot.slash_command(name="panel", description="Open the moderation panel",
                   default_member_permissions=discord.Permissions(moderate_members=True))
async def panel_cmd(ctx: discord.ApplicationContext):
    embed = discord.Embed(
        title="🛡️ Moderation Panel",
        description="Use the buttons below for quick actions.",
        color=0x5865F2,
    )
    embed.add_field(name="Dashboard", value="Open the dashboard to manage strikes, words, and settings.", inline=False)
    embed.set_footer(text="AutoMod v2.0 — py-cord")
    await ctx.respond(embed=embed, view=PanelView(), ephemeral=True)

@bot.slash_command(name="raidmode", description="Manually enable or disable anti-raid lockdown",
                   default_member_permissions=discord.Permissions(administrator=True))
@discord.option("action", description="on or off", choices=["on", "off"])
async def raidmode_cmd(ctx: discord.ApplicationContext, action: str):
    guild = ctx.guild
    await ctx.defer(ephemeral=True)
    if action == "on":
        if guild.id in raid_active:
            await ctx.followup.send("⚠️ Raid mode is already active.", ephemeral=True)
            return
        raid_active.add(guild.id)
        await _lock_guild(guild)
        s = get_settings()
        add_log("RAID_MANUAL_ON", ctx.author, f"Manual lockdown activated by {ctx.author}")
        task = asyncio.create_task(_schedule_unlock(guild, s["raid_lockdown_minutes"]))
        raid_unlock_tasks[guild.id] = task
        for ch in guild.text_channels:
            try:
                await ch.send(
                    f"🚨 **Anti-Raid Mode Activated** by {ctx.author.mention}. "
                    f"Chat locked for **{s['raid_lockdown_minutes']} minute(s)**.")
                break
            except (discord.Forbidden, discord.HTTPException):
                pass
        await ctx.followup.send(
            f"🔒 Lockdown active. All channels locked for {s['raid_lockdown_minutes']} minute(s).",
            ephemeral=True)
    else:
        if guild.id not in raid_active:
            await ctx.followup.send("ℹ️ Raid mode is not currently active.", ephemeral=True)
            return
        t = raid_unlock_tasks.pop(guild.id, None)
        if t:
            t.cancel()
        await _unlock_guild(guild)
        add_log("RAID_MANUAL_OFF", ctx.author, f"Manual lockdown lifted by {ctx.author}")
        for ch in guild.text_channels:
            try:
                await ch.send(f"✅ **Anti-Raid Mode lifted** by {ctx.author.mention}. Chat is open again.")
                break
            except (discord.Forbidden, discord.HTTPException):
                pass
        await ctx.followup.send("🔓 Lockdown lifted. All channels re-opened.", ephemeral=True)

# ── Panel buttons ─────────────────────────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="📊 View Logs", style=discord.ButtonStyle.primary)
    async def view_logs(self, button: discord.ui.Button, interaction: discord.Interaction):
        recent = logs[-5:] if logs else []
        if not recent:
            await interaction.response.send_message("No logs yet.", ephemeral=True)
            return
        lines = [f"`{e['timestamp'][:19]}` **{e['action']}** – {e['user']} – {e['reason']}"
                 for e in reversed(recent)]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="⚠️ Top Striked", style=discord.ButtonStyle.danger)
    async def top_striked(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not strikes:
            await interaction.response.send_message("No strikes recorded.", ephemeral=True)
            return
        top   = sorted(strikes.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [f"<@{uid}>: **{count}** strike(s)" for uid, count in top]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="📝 Banned Words", style=discord.ButtonStyle.secondary)
    async def list_words(self, button: discord.ui.Button, interaction: discord.Interaction):
        words = ", ".join(f"`{w}`(T{t})" for w, t in banned_words.items()) if banned_words else "None"
        await interaction.response.send_message(f"🚫 Banned: {words}", ephemeral=True)

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN environment variable is not set. Please add it as a secret.")
        exit(1)
    bot.run(TOKEN)
