#!/usr/bin/env python3
"""
run.py – Start BOTH the Discord bot and the Flask dashboard in parallel.
Usage:  python run.py
"""
import subprocess, sys, os, signal, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))

def stream(proc, prefix):
    for line in iter(proc.stdout.readline, b''):
        print(f"[{prefix}] {line.decode().rstrip()}", flush=True)

procs = []

def shutdown(sig, frame):
    print("\nShutting down…")
    for p in procs:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

bot_env = {**os.environ, "PYTHONUNBUFFERED": "1"}

# Check for token before launching
if not os.environ.get("DISCORD_TOKEN"):
    print("❌ DISCORD_TOKEN environment variable is not set!", flush=True)
    print("   Add it in Render → your service → Environment tab.", flush=True)
    # Still launch the dashboard so Render doesn't mark the deploy as failed
    # The bot just won't connect to Discord.

bot_proc = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "bot", "bot.py")],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=bot_env,
)
dash_proc = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "dashboard", "app.py")],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=bot_env,
)
procs.extend([bot_proc, dash_proc])

threading.Thread(target=stream, args=(bot_proc,  "BOT "), daemon=True).start()
threading.Thread(target=stream, args=(dash_proc, "DASH"), daemon=True).start()

# Keep alive: restart bot if it crashes, but keep dashboard running always
while True:
    ret = bot_proc.poll()
    if ret is not None:
        print(f"[BOT ] exited with code {ret}, restarting in 5s…", flush=True)
        time.sleep(5)
        bot_proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "bot", "bot.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=bot_env,
        )
        threading.Thread(target=stream, args=(bot_proc, "BOT "), daemon=True).start()
        procs[0] = bot_proc

    if dash_proc.poll() is not None:
        print("[DASH] dashboard exited unexpectedly!", flush=True)
        break  # Dashboard dying is fatal

    time.sleep(3)
