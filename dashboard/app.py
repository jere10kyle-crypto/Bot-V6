from flask import Flask, render_template, request, redirect, url_for, jsonify
import json, os, re
from datetime import datetime
from collections import Counter

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ── Helpers ───────────────────────────────────────────────────────────────────
def load(f, default):
    path = os.path.join(DATA_DIR, f)
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(path) as fp:
            return json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save(f, data):
    path = os.path.join(DATA_DIR, f)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as fp:
        json.dump(data, fp, indent=2)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    strikes     = load("strikes.json", {})
    logs        = load("logs.json", [])
    banned_words= load("banned_words.json", [])

    top_users = sorted(strikes.items(), key=lambda x: x[1], reverse=True)[:10]

    # Action counts for mini chart
    action_counts = Counter(e["action"] for e in logs)

    # Recent 20 logs
    recent_logs = list(reversed(logs[-20:]))

    # Strikes distribution for bar chart
    distribution = Counter(strikes.values())

    return render_template(
        "index.html",
        strikes=strikes,
        top_users=top_users,
        logs=recent_logs,
        banned_words=banned_words,
        action_counts=dict(action_counts),
        distribution={str(k): v for k, v in sorted(distribution.items())},
        total_strikes=sum(strikes.values()),
        total_logs=len(logs),
        total_words=len(banned_words),
    )

@app.route("/add_word", methods=["POST"])
def add_word():
    word = request.form.get("word", "").lower().strip()
    if word:
        words = load("banned_words.json", [])
        if word not in words:
            words.append(word)
            save("banned_words.json", words)
    return redirect(url_for("index"))

@app.route("/remove_word/<word>")
def remove_word(word):
    words = load("banned_words.json", [])
    if word in words:
        words.remove(word)
        save("banned_words.json", words)
    return redirect(url_for("index"))

@app.route("/reset_strikes/<uid>")
def reset_strikes(uid):
    strikes = load("strikes.json", {})
    strikes.pop(uid, None)
    save("strikes.json", strikes)
    return redirect(url_for("index"))

@app.route("/api/stats")
def api_stats():
    strikes  = load("strikes.json", {})
    logs     = load("logs.json", [])
    words    = load("banned_words.json", [])
    return jsonify({
        "total_strikes": sum(strikes.values()),
        "total_users_striked": len(strikes),
        "total_logs": len(logs),
        "total_banned_words": len(words),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
