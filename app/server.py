"""
The Everyball website.

  python app/server.py                         # http://localhost:5000 (reads data/everyball.db)
  gunicorn app.server:app                      # how Render runs it

Pages: home (duel finder, Ask box, coverage), /duel, /player/<id>, /credits.  JSON under /api/.
Everything is read-only: the site opens the database with mode=ro and never writes to it.
"""
import os
import sys
import threading
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ask_ai  # noqa: E402
import queries as Q  # noqa: E402



def load_env(path=ROOT / ".env"):
    """Keys for running the site in the Codespace: a .env file (never committed). On Render: its Environment tab."""
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
DB = Path(os.environ.get("EVERYBALL_DB", ROOT / "data" / "everyball.db"))
app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.sort_keys = False

_ready = threading.Lock()


def ensure_db():
    """If the database isn't there yet (e.g. a fresh Render box), unpack it from data/web/."""
    if DB.exists():
        return True
    with _ready:
        if DB.exists():
            return True
        try:
            import publish_db
            return publish_db.unpack(DB, quiet=True)
        except Exception as e:  # noqa: BLE001
            app.logger.error("unpack failed: %s", e)
            return False


def db():
    if "db" not in g:
        if not ensure_db():
            abort(503, "The database isn't built yet.")
        g.db = Q.connect_ro(DB)
        g.db.execute("PRAGMA query_only = 1")
    return g.db


@app.teardown_appcontext
def close_db(_):
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ---------------------------------------------------------------- small helpers
def fmt_arg():
    f = request.args.get("format", "")
    return f if f in ("Test", "ODI") else None


def int_arg(name):
    try:
        return int(request.args.get(name, ""))
    except ValueError:
        return None


class Limiter:
    """A few Ask questions per minute per visitor, so nobody can run up the AI bill."""

    def __init__(self, per_min=6, per_day=60):
        self.per_min, self.per_day, self.hits = per_min, per_day, defaultdict(deque)
        self.lock = threading.Lock()

    def ok(self, key):
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 86400:
                q.popleft()
            if len(q) >= self.per_day or sum(1 for t in q if now - t < 60) >= self.per_min:
                return False
            q.append(now)
            return True


limiter = Limiter(int(os.environ.get("ASK_PER_MIN", 6)), int(os.environ.get("ASK_PER_DAY", 60)))
answers = OrderedDict()           # same question asked again -> same answer, no new AI call


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return render_template("home.html", page="home")


@app.route("/duel")
def duel_page():
    return render_template("duel.html", page="duel")


@app.route("/player/<int:pid>")
def player_page(pid):
    p = Q.person_row(db(), pid)
    if not p:
        abort(404)
    return render_template("player.html", page="player", p=p)


@app.route("/ask")
def ask_page():
    return render_template("home.html", page="ask")


@app.route("/credits")
def credits():
    return render_template("credits.html", page="credits")


@app.route("/healthz")
def healthz():
    return {"ok": True, "database": DB.exists()}


# ---------------------------------------------------------------- JSON
@app.route("/api/search")
def api_search():
    return jsonify(Q.search(db(), request.args.get("q", ""), limit=8))


@app.route("/api/duel")
def api_duel():
    b, w = int_arg("batter"), int_arg("bowler")
    if not b or not w:
        return jsonify({"error": "Pick a batter and a bowler."}), 400
    con = db()
    if not Q.person_row(con, b) or not Q.person_row(con, w):
        return jsonify({"error": "Player not found."}), 404
    return jsonify(Q.duel_ids(con, [b], [w], fmt_arg()))


@app.route("/api/player/<int:pid>")
def api_player(pid):
    out = Q.player_page(db(), pid, fmt_arg())
    if not out:
        return jsonify({"error": "Player not found."}), 404
    return jsonify(out)


@app.route("/api/coverage")
def api_coverage():
    return jsonify(Q.coverage(db()))


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()[:500]
    key = " ".join(question.lower().split())
    if key in answers:
        answers.move_to_end(key)
        return jsonify(answers[key])
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
    if not limiter.ok(ip):
        return jsonify({"error": "That's a lot of questions! Wait a minute and ask again."}), 429
    try:
        out = ask_ai.ask(db(), question)
    except ask_ai.AskError as e:
        return jsonify({"error": str(e)}), 400
    answers[key] = out
    if len(answers) > 500:
        answers.popitem(last=False)
    return jsonify(out)


@app.errorhandler(503)
def not_ready(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "The database isn't built yet."}), 503
    return render_template("base.html", page="", message="The database isn't built yet."), 503


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return render_template("base.html", page="", message="That page doesn't exist."), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
