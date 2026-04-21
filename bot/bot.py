import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import time
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ── Data helpers ─────────────────────────────────────────────────────────────
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

# ── Shared state (also written to JSON so dashboard can read) ─────────────────
strikes     = load_json("strikes.json", {})       # {user_id: count}
logs        = load_json("logs.json", [])           # list of log entries
banned_words= load_json("banned_words.json",       # list of strings
    ["spam", "badword1", "badword2", "hate", "slur"])

# Anti-raid / spam tracking (in-memory only)
message_times      = defaultdict(list)   # {user_id: [timestamps]}
warned_users       = set()               # users already warned this burst
word_warning_count = defaultdict(int)    # {user_id: count of banned word uses}

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Logging helper ────────────────────────────────────────────────────────────
def add_log(action, user, reason, moderator="AutoMod"):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "user": str(user),
        "reason": reason,
        "moderator": str(moderator),
    }
    logs.append(entry)
    if len(logs) > 500:          # keep last 500 entries
        logs.pop(0)
    save_json("logs.json", logs)

DEFAULT_SETTINGS = {
    "tier1_strikes": 1,
    "tier1_minutes": 15,
    "tier2_strikes": 3,
    "tier2_minutes": 1440,
    "tier3_strikes": 5,
}

def get_settings():
    return load_json("settings.json", DEFAULT_SETTINGS)

# ── Strike helper ─────────────────────────────────────────────────────────────
async def add_strike(guild, member, reason):
    uid = str(member.id)
    strikes[uid] = strikes.get(uid, 0) + 1
    save_json("strikes.json", strikes)
    add_log("STRIKE", member, reason)

    count = strikes[uid]
    s = get_settings()

    if count >= s["tier3_strikes"]:
        try:
            await member.timeout(timedelta(days=28), reason=f"{s['tier3_strikes']} strikes – permanent mute")
            add_log("PERM_MUTE", member, f"Reached {s['tier3_strikes']} strikes")
        except discord.Forbidden:
            pass
    elif count >= s["tier2_strikes"]:
        mins = s["tier2_minutes"]
        try:
            await member.timeout(timedelta(minutes=mins), reason=f"{s['tier2_strikes']} strikes – {mins}m mute")
            add_log("MUTE_24H", member, f"Reached {s['tier2_strikes']} strikes ({mins}m)")
        except discord.Forbidden:
            pass
    elif count >= s["tier1_strikes"]:
        mins = s["tier1_minutes"]
        try:
            await member.timeout(timedelta(minutes=mins), reason=f"Strike {count} – {mins}m mute")
            add_log("MUTE_15M", member, f"Strike {count} ({mins}m)")
        except discord.Forbidden:
            pass

    return count

# ── Anti-spam / anti-raid detection ───────────────────────────────────────────
SPAM_LIMIT   = 5   # messages
SPAM_WINDOW  = 5   # seconds
RAID_LIMIT   = 10  # messages across server in window

async def check_spam(message):
    uid  = str(message.author.id)
    now  = time.time()
    message_times[uid] = [t for t in message_times[uid] if now - t < SPAM_WINDOW]
    message_times[uid].append(now)

    if len(message_times[uid]) >= SPAM_LIMIT:
        if uid not in warned_users:
            warned_users.add(uid)
            await message.delete()
            await message.channel.send(
                f"⚠️ {message.author.mention} slow down! Auto-muting for spam.",
                delete_after=5,
            )
            count = await add_strike(message.guild, message.author, "Spam detection")
            return True
    return False

async def check_banned_words(message):
    content = message.content.lower()
    for word in banned_words:
        if word.lower() in content:
            uid = str(message.author.id)
            word_warning_count[uid] += 1
            if word_warning_count[uid] >= 2:
                await message.delete()
                await message.channel.send(
                    f"🚫 {message.author.mention} final warning — message deleted for using a banned word.",
                    delete_after=5,
                )
                count = await add_strike(message.guild, message.author, f"Banned word (2nd offence): {word}")
            else:
                await message.channel.send(
                    f"⚠️ {message.author.mention} that word is not allowed here. Next time your message will be deleted.",
                    delete_after=8,
                )
                add_log("WARN", message.author, f"Banned word warning: {word}")
            return True
    return False

# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"   Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"   Sync error: {e}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # Banned word check
    if await check_banned_words(message):
        return

    # Spam check
    if await check_spam(message):
        return

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    add_log("JOIN", member, "Joined the server")

# ── Slash commands ─────────────────────────────────────────────────────────────
@bot.tree.command(name="mute", description="Mute a member for a given number of minutes")
@app_commands.describe(member="The member to mute", minutes="Duration in minutes (default 15)")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 15):
    until = timedelta(minutes=minutes)
    await member.timeout(until, reason=f"Muted by {interaction.user}")
    add_log("MUTE", member, f"Manual mute {minutes}m by {interaction.user}")
    await interaction.response.send_message(
        f"🔇 {member.mention} muted for {minutes} minute(s).", ephemeral=True
    )

@bot.tree.command(name="unmute", description="Remove a mute from a member")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None, reason=f"Unmuted by {interaction.user}")
    add_log("UNMUTE", member, f"Manual unmute by {interaction.user}")
    await interaction.response.send_message(f"🔊 {member.mention} unmuted.", ephemeral=True)

@bot.tree.command(name="strikes", description="Check how many strikes a user has")
@app_commands.checks.has_permissions(moderate_members=True)
async def check_strikes(interaction: discord.Interaction, member: discord.Member):
    count = strikes.get(str(member.id), 0)
    await interaction.response.send_message(
        f"⚠️ {member.mention} has **{count}** strike(s).", ephemeral=True
    )

@bot.tree.command(name="resetstrikes", description="Reset strikes for a user")
@app_commands.checks.has_permissions(administrator=True)
async def reset_strikes(interaction: discord.Interaction, member: discord.Member):
    strikes.pop(str(member.id), None)
    save_json("strikes.json", strikes)
    add_log("RESET_STRIKES", member, f"Reset by {interaction.user}")
    await interaction.response.send_message(
        f"✅ Strikes reset for {member.mention}.", ephemeral=True
    )

@bot.tree.command(name="addword", description="Add a banned word")
@app_commands.checks.has_permissions(administrator=True)
async def add_word(interaction: discord.Interaction, word: str):
    w = word.lower().strip()
    if w not in banned_words:
        banned_words.append(w)
        save_json("banned_words.json", banned_words)
    await interaction.response.send_message(f"✅ `{w}` added to banned words.", ephemeral=True)

@bot.tree.command(name="removeword", description="Remove a banned word")
@app_commands.checks.has_permissions(administrator=True)
async def remove_word(interaction: discord.Interaction, word: str):
    w = word.lower().strip()
    if w in banned_words:
        banned_words.remove(w)
        save_json("banned_words.json", banned_words)
        await interaction.response.send_message(f"✅ `{w}` removed.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ `{w}` not found.", ephemeral=True)

@bot.tree.command(name="panel", description="Open the moderation panel")
@app_commands.checks.has_permissions(moderate_members=True)
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Moderation Panel",
        description="Use the buttons below for quick actions.",
        color=0x5865F2,
    )
    embed.add_field(name="Dashboard", value="http://localhost:5000", inline=False)
    embed.set_footer(text="AutoMod v1.0")
    await interaction.response.send_message(embed=embed, view=PanelView(), ephemeral=True)

# ── Panel buttons ─────────────────────────────────────────────────────────────
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="📊 View Logs", style=discord.ButtonStyle.primary)
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        recent = logs[-5:] if logs else []
        if not recent:
            await interaction.response.send_message("No logs yet.", ephemeral=True)
            return
        lines = [f"`{e['timestamp'][:19]}` **{e['action']}** – {e['user']} – {e['reason']}"
                 for e in reversed(recent)]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="⚠️ Top Striked", style=discord.ButtonStyle.danger)
    async def top_striked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not strikes:
            await interaction.response.send_message("No strikes recorded.", ephemeral=True)
            return
        top = sorted(strikes.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [f"<@{uid}>: **{count}** strike(s)" for uid, count in top]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="📝 Banned Words", style=discord.ButtonStyle.secondary)
    async def list_words(self, interaction: discord.Interaction, button: discord.ui.Button):
        words = ", ".join(f"`{w}`" for w in banned_words) if banned_words else "None"
        await interaction.response.send_message(f"🚫 Banned: {words}", ephemeral=True)

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
