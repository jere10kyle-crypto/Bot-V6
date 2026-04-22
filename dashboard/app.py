from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json, os, secrets
from datetime import datetime
from collections import Counter

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
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

# ── Auth helpers ──────────────────────────────────────────────────────────────
def get_users():
    return load("users.json", {})

def save_users(users):
    save("users.json", users)

def current_user():
    uname = session.get("username")
    if not uname:
        return None
    users = get_users()
    u = users.get(uname)
    if not u:
        return None
    return {"username": uname, **u}

def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapped

def edit_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if not (u.get("is_admin") or u.get("can_edit")):
            flash("You don't have edit permission. Ask the admin to grant it.", "error")
            return redirect(url_for("index"))
        return view(*a, **kw)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if not u.get("is_admin"):
            flash("Admin only.", "error")
            return redirect(url_for("index"))
        return view(*a, **kw)
    return wrapped

@app.context_processor
def inject_user():
    u = current_user()
    return {
        "current_user": u,
        "can_edit": bool(u and (u.get("is_admin") or u.get("can_edit"))),
        "is_admin": bool(u and u.get("is_admin")),
    }

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = request.form.get("username", "").strip().lower()
        pw    = request.form.get("password", "")
        users = get_users()
        u = users.get(uname)
        if u and check_password_hash(u["password_hash"], pw):
            session["username"] = uname
            return redirect(url_for("index"))
        flash("Wrong username or password.", "error")
    if not get_users():
        return redirect(url_for("signup"))
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    users = get_users()
    first_user = len(users) == 0
    if request.method == "POST":
        uname = request.form.get("username", "").strip().lower()
        pw    = request.form.get("password", "")
        if not uname or not pw:
            flash("Username and password required.", "error")
        elif len(pw) < 4:
            flash("Password must be at least 4 characters.", "error")
        elif uname in users:
            flash("That username is taken.", "error")
        else:
            users[uname] = {
                "password_hash": generate_password_hash(pw),
                "is_admin": first_user,
                "can_edit": first_user,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_users(users)
            session["username"] = uname
            if first_user:
                flash("Welcome! You're the admin.", "ok")
            else:
                flash("Account created. Waiting for admin to grant edit access.", "ok")
            return redirect(url_for("index"))
    return render_template("signup.html", first_user=first_user)

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))

@app.route("/users")
@admin_required
def users_page():
    users = get_users()
    return render_template("users.html", users=users)

@app.route("/users/toggle_edit/<username>")
@admin_required
def toggle_edit(username):
    users = get_users()
    if username in users and not users[username].get("is_admin"):
        users[username]["can_edit"] = not users[username].get("can_edit", False)
        save_users(users)
    return redirect(url_for("users_page"))

@app.route("/users/make_admin/<username>")
@admin_required
def make_admin(username):
    users = get_users()
    if username in users:
        users[username]["is_admin"] = True
        users[username]["can_edit"] = True
        save_users(users)
    return redirect(url_for("users_page"))

@app.route("/users/delete/<username>")
@admin_required
def delete_user(username):
    me = current_user()
    if me and username == me["username"]:
        flash("You can't delete yourself.", "error")
        return redirect(url_for("users_page"))
    users = get_users()
    users.pop(username, None)
    save_users(users)
    return redirect(url_for("users_page"))

# ── Main routes ───────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    strikes     = load("strikes.json", {})
    logs        = load("logs.json", [])
    _bw         = load("banned_words.json", {})
    banned_words = {w: t for w, t in _bw.items()} if isinstance(_bw, dict) else {w: 1 for w in _bw}

    top_users = sorted(strikes.items(), key=lambda x: x[1], reverse=True)[:10]
    action_counts = Counter(e["action"] for e in logs)
    recent_logs = list(reversed(logs[-20:]))
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
@edit_required
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
@edit_required
def remove_word(word):
    words = load("banned_words.json", {})
    if isinstance(words, list):
        words = {w: 1 for w in words}
    words.pop(word, None)
    save("banned_words.json", words)
    return redirect(url_for("index"))

@app.route("/reset_strikes/<uid>")
@edit_required
def reset_strikes(uid):
    strikes = load("strikes.json", {})
    strikes.pop(uid, None)
    save("strikes.json", strikes)
    return redirect(url_for("index"))

@app.route("/api/stats")
@login_required
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
@edit_required
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
