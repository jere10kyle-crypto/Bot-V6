# AutoMod — Discord Bot + Dashboard

A Discord moderation bot with a web dashboard. Uses discord.py for the bot and Flask for the dashboard.

## Project Structure

```
├── bot/
│   └── bot.py          — Discord bot (discord.py)
├── dashboard/
│   ├── app.py          — Flask web dashboard
│   └── templates/
│       └── index.html  — Dashboard UI
├── data/               — Auto-created JSON storage
│   ├── strikes.json
│   ├── logs.json
│   └── banned_words.json
├── run.py              — Starts both bot + dashboard
└── requirements.txt
```

## Running

`python run.py` starts both the bot and the Flask dashboard (port 5000).

## Environment Variables

- `DISCORD_TOKEN` — Required for the Discord bot to connect. Set this in the Secrets tab.

## Features

### Bot
- Banned word filter (auto-delete + strike)
- Spam detection (5 msg / 5 sec = mute)
- Strike system (1→15 min, 3→24h, 5→permanent mute)
- Anti-raid burst detection
- Slash commands: `/mute`, `/unmute`, `/strikes`, `/resetstrikes`, `/addword`, `/removeword`, `/panel`

### Dashboard (port 5000)
- Live stats: total strikes, users, logs, banned words
- Bar chart of all actions
- Top striked users table with reset button
- Banned words manager (add/remove)
- Scrollable log viewer
- Auto-refreshes every 30 seconds

## Dependencies

- `discord.py>=2.3.2`
- `flask>=3.0.0`
