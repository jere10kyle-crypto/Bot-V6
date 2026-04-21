from flask import Flask, render_template, request, redirect, url_for, jsonify
import json, os, re
from datetime import datetime
from collections import Counter

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

DEFAULT_SETTINGS = {
    "tier1_strikes": 1,  "tier1_minutes": 5,
    "tier2_strikes": 2,  "tier2_minutes": 15,
    "tier3_strikes": 3,  "tier3_minutes": 60,
    "tier4_strikes": 4,  "tier4_minutes": 1440,
    "tier5_strikes": 5,  "tier5_minutes": 40320,
    "spam_word_limit":  5,
    "spam_word_window": 10,
    "spam_word_tier":   2,
}

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
    _bw         = load("banned_words.json", {})
    banned_words = {w: t for w, t in _bw.items()} if isinstance(_bw, dict) else {w: 1 for w in _bw}

    top_users = sorted(strikes.items(), key=lambda x: x[1], reverse=True)[:10]

    # Action counts for mini chart
    action_counts = Counter(e["action"] for e in logs)

    # Recent 20 logs
    recent_logs = list(reversed(logs[-20:]))

    # Strikes distribution for bar chart
    distribution = Counter(strikes.values())

    settings = {**DEFAULT_SETTINGS, **load("settings.json", {})}

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
        settings=settings,
    )

@app.route("/add_word", methods=["POST"])
def add_word():
    word = request.form.get("word", "").lower().strip()
    tier = max(1, min(5, int(request.form.get("tier", 1))))
    if word:
        words = load("banned_words.json", {})
        if isinstance(words, list):
            words = {w: 1 for w in words}
        words[word] = tier
        save("banned_words.json", words)
    return redirect(url_for("index"))

@app.route("/remove_word/<word>")
def remove_word(word):
    words = load("banned_words.json", {})
    if isinstance(words, list):
        words = {w: 1 for w in words}
    words.pop(word, None)
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

@app.route("/save_settings", methods=["POST"])
def save_settings():
    s = {**DEFAULT_SETTINGS, **load("settings.json", {})}
    try:
        for t in range(1, 6):
            s[f"tier{t}_strikes"] = max(1, int(request.form.get(f"tier{t}_strikes", DEFAULT_SETTINGS[f"tier{t}_strikes"])))
            s[f"tier{t}_minutes"] = max(1, int(request.form.get(f"tier{t}_minutes", DEFAULT_SETTINGS[f"tier{t}_minutes"])))
        s["spam_word_limit"]  = max(2, int(request.form.get("spam_word_limit",  DEFAULT_SETTINGS["spam_word_limit"])))
        s["spam_word_window"] = max(1, int(request.form.get("spam_word_window", DEFAULT_SETTINGS["spam_word_window"])))
        s["spam_word_tier"]   = max(1, min(5, int(request.form.get("spam_word_tier", DEFAULT_SETTINGS["spam_word_tier"]))))
    except ValueError:
        pass
    save("settings.json", s)
    return redirect(url_for("index") + "#settings")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
