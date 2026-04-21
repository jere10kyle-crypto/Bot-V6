# 🛡️ AutoMod — Free Discord Bot + Dashboard

A fully free Discord moderation bot with a web dashboard.  
**No paid APIs. No credit card required.**

---

## 📁 Project Structure

```
discord-bot/
├── bot/
│   └── bot.py          ← Discord bot (discord.py)
├── dashboard/
│   ├── app.py          ← Flask web dashboard
│   └── templates/
│       └── index.html  ← Dashboard UI
├── data/               ← Auto-created JSON storage
│   ├── strikes.json
│   ├── logs.json
│   └── banned_words.json
├── run.py              ← Starts both bot + dashboard
├── requirements.txt
└── .replit             ← Replit config
```

---

## ✅ Features

### 🤖 Bot
| Feature | Details |
|---|---|
| Banned word filter | Auto-delete + strike |
| Spam detection | 5 msg / 5 sec = mute |
| Strike system | 1→15 min, 3→24h, 5→permanent mute |
| Anti-raid | Burst detection, auto-warn |
| `/mute` `/unmute` | Manual mod controls |
| `/strikes` | Check user strikes |
| `/resetstrikes` | Clear a user's strikes |
| `/addword` `/removeword` | Manage banned words |
| `/panel` | Button-based mod panel in Discord |

### 🌐 Dashboard (localhost:5000)
- Live stats: total strikes, users, logs, banned words
- Bar chart of all actions
- Top striked users table with reset button
- Banned words manager (add/remove)
- Scrollable log viewer
- Auto-refreshes every 30 seconds

---

## 🚀 Setup Guide

### Step 1 — Create a Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → give it a name
3. Go to **Bot** tab → click **Add Bot**
4. Under **Token**, click **Reset Token** → copy it
5. Under **Privileged Gateway Intents**, enable:
   - ✅ Server Members Intent
   - ✅ Message Content Intent
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Moderate Members`, `Manage Messages`, `Send Messages`, `Read Messages`
7. Copy the URL and invite the bot to your server

---

### Step 2 — Run on Replit (Free, easiest)

1. Go to https://replit.com → **Create Repl** → **Import from GitHub**
2. Paste your GitHub repo URL
3. In the **Secrets** tab (🔒 icon), add:
   ```
   Key:   DISCORD_TOKEN
   Value: YOUR_BOT_TOKEN_HERE
   ```
4. Click **Run** — both the bot and dashboard start automatically
5. The dashboard will be available at your Replit URL on port 80

> ⚠️ Replit free tier may sleep after inactivity.  
> Use **UptimeRobot** (free) to ping your Replit URL every 5 minutes to keep it awake.

---

### Step 3 — Run on Railway (More reliable free option)

1. Go to https://railway.app → New Project → **Deploy from GitHub repo**
2. Add environment variable: `DISCORD_TOKEN = your_token`
3. Railway auto-detects Python and runs `run.py`
4. Add a **Public Domain** in Railway settings to access the dashboard

---

### Step 4 — Run Locally (development)

```bash
git clone https://github.com/YOUR_USERNAME/discord-bot.git
cd discord-bot

# Set your token
export DISCORD_TOKEN="your_token_here"

# Install dependencies
pip install -r requirements.txt

# Run everything
python run.py
```

Dashboard → http://localhost:5000

---

## 🐙 GitHub Setup

```bash
# In your project folder:
git init
git add .
git commit -m "Initial commit: AutoMod bot + dashboard"

# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/discord-bot.git
git push -u origin main
```

> ⚠️ **Never commit your bot token!**  
> Always use environment variables / Replit Secrets.  
> `.env` and `data/` are already in `.gitignore`.

---

## ⚙️ Configuration

Edit `bot/bot.py` to change:
```python
SPAM_LIMIT  = 5   # messages before spam trigger
SPAM_WINDOW = 5   # seconds window for spam detection
```

Default banned words are set in `bot/bot.py` and managed live via `/addword` or the dashboard.

---

## 🆓 Fully Free Stack

| Component | Tool | Cost |
|---|---|---|
| Bot | discord.py | FREE |
| Dashboard | Flask | FREE |
| Database | JSON files | FREE |
| Hosting | Replit / Railway | FREE |
| AI Moderation | Keyword detection | FREE |
| Uptime pinger | UptimeRobot | FREE |

---

## 🛠️ Troubleshooting

**Bot doesn't respond to slash commands?**  
→ Wait up to 1 hour for commands to sync globally, or re-invite the bot.

**Dashboard shows no data?**  
→ The `data/` folder is created automatically when the bot first takes an action.

**Permission errors on mute?**  
→ Make sure the bot's role is **above** the member's role in Server Settings → Roles.
