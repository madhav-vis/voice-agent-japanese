"""Local browser UI: serves index.html, issues LiveKit tokens, and returns a session's grammar review as JSON.

    uv run python ui_server.py      # then open http://localhost:8000

Needs LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env (local dev server: ws://127.0.0.1:7880, devkey/secret).
"""
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from livekit import api
import json, os, secrets, sqlite3, time, warnings
import grammar_police, review, stats

load_dotenv(".env")
warnings.filterwarnings("ignore", message=".*HMAC key.*")  # the dev secret "secret" is short; fine locally

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("UI_PORT", "8000"))
POINTS = grammar_police.load_grammar_points()


def make_token():
    #local only: anyone who can reach this server gets a token. Before deploying, authenticate the
    #user first and put their user ID in the identity (see CLAUDE.md, Deployment)
    room = f"jp-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    identity = f"learner-{secrets.token_hex(4)}"
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"url": os.environ["LIVEKIT_URL"], "token": token, "room": room}


def all_stats():
    con = sqlite3.connect(os.path.join(HERE, grammar_police.DB_PATH))
    try:
        grammar_police.init_db(con)
        return stats.compute_stats(con, POINTS)
    finally:
        con.close()


def room_review(room):
    """saving → the agent hasn't written the transcript yet; analyzing → grammar_police.py hasn't finished it."""
    con = sqlite3.connect(os.path.join(HERE, grammar_police.DB_PATH))
    try:
        columns = {name for (_, name, *_) in con.execute("PRAGMA table_info(transcripts)")}
        if "room" not in columns:
            return {"status": "saving"}
        row = con.execute("SELECT session FROM transcripts WHERE room = ? LIMIT 1", (room,)).fetchone()
        if not row:
            return {"status": "saving"}
        grammar_police.init_db(con)
        if not con.execute("SELECT 1 FROM grammar_sessions WHERE session = ?", (row[0],)).fetchone():
            return {"status": "analyzing"}
        return {"status": "done", **review.session_review(con, row[0], POINTS),
                "card": stats.session_card(con, row[0], POINTS)}
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self.send(200, f.read(), "text/html; charset=utf-8")
            elif url.path == "/api/token":
                self.send(200, make_token())
            elif url.path == "/api/stats":
                self.send(200, all_stats())
            elif url.path == "/api/review":
                room = parse_qs(url.query).get("room", [""])[0]
                if not room:
                    self.send(400, {"error": "missing room"})
                else:
                    self.send(200, room_review(room))
            else:
                self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        if "/api/review" not in self.path:  # don't log every poll
            super().log_message(fmt, *args)


if __name__ == "__main__":
    for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not os.getenv(key):
            raise SystemExit(f"{key} is missing from .env")
    print(f"Open http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
