from dotenv import load_dotenv
import anthropic
import csv, json, os, sqlite3, time
import learner_profile

#load env
load_dotenv(".env")

DB_PATH = "transcripts.db"
CSV_PATH = "bunpro_grammar_points.csv"
MODEL = "claude-sonnet-5-5"

ISSUE_TYPES = ["particle", "wa_ga", "conjugation", "filler", "word_choice", "register", "other"]

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "turn_ids": {"type": "array", "items": {"type": "integer"}},
                    "text": {"type": "string"},
                    "status": {"type": "string", "enum": ["correct", "incorrect", "unnatural", "asr_unclear"]},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ISSUE_TYPES},
                                "span": {"type": "string"},
                                "correction": {"type": "string"},
                                "note": {"type": "string"},
                                "bunpro_key": {"type": ["string", "null"]},
                            },
                            "required": ["type", "span", "correction", "note", "bunpro_key"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["turn_ids", "text", "status", "issues"],
                "additionalProperties": False,
            },
        },
        "patterns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["turns", "patterns"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You review a Japanese learner's speech from a voice conversation practice session.
The dialogue lines are formatted as "[id] role: text". Only evaluate "user" lines; "assistant" lines are context.

Rules:
- User text comes from speech recognition. Consecutive user lines are often fragments of one utterance; group them into one turn and list all their ids in turn_ids.
- Every user id must appear in exactly one turn.
- status: "correct" if natural and grammatical, "incorrect" for grammar errors, "unnatural" if grammatical but a native speaker would not say it that way, "asr_unclear" if the text looks like a speech recognition error rather than a learner mistake. Do not report issues for asr_unclear turns.
- Look especially for: は vs が confusion, wrong particles, particles dropped where a native speaker would not drop them, conjugation errors.
- This is spoken Japanese, not written. Never mark these as mistakes on their own:
  - dropped particles that natives also drop (コーヒー飲む?)
  - contractions (〜てる, 〜とく, 〜ちゃう, 〜なきゃ, 〜んだ)
  - inverted word order (おいしいね、これ)
  - fragments and unfinished sentences, when the meaning is clear from context
  - fillers (えーと, あの, なんか); only report overuse, in patterns
  - sentence-final particles (ね, よ, な, さ, じゃん)
- Registers: the user may speak casually (タメ口), in plain polite です・ます, or formally (敬語). All three are valid. Infer the register of each turn and judge it by the norms of that register; do not "correct" casual speech into polite speech or the reverse.
- Only use the "register" issue type for: wrong keigo (e.g. humble form used for the listener's actions), or switching register in a way a native speaker would find jarring within one utterance. Natural style shifts between turns are fine.
- note: very short, in English, under 15 words.
- bunpro_key: the key (e.g. "N5-13") of the grammar point below that the mistake is about, only if there is a clear match. Otherwise null. Never invent keys.
- patterns: short session-level observations about recurring habits (empty if none).

Grammar points (key|grammar|meaning):
"""


def load_grammar_points():
    """Return {key: (grammar, meaning, url)} keyed by "level-number"."""
    points = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 5:
                continue
            level, number, grammar, meaning, url = row[:5]
            points[f"{level}-{number}"] = (grammar, meaning, url)
    return points


def init_db(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS grammar_feedback (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            transcript_id INTEGER,
            status        TEXT,
            type          TEXT,
            span          TEXT,
            correction    TEXT,
            note          TEXT,
            bunpro_key    TEXT,
            bunpro_url    TEXT,
            created_at    REAL
        )
    """)
    is_new = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'grammar_sessions'"
    ).fetchone() is None
    con.execute("""
        CREATE TABLE IF NOT EXISTS grammar_sessions (
            session     TEXT PRIMARY KEY,
            patterns    TEXT,
            analyzed_at REAL
        )
    """)
    if is_new:
        #migrate from the old global checkpoint: sessions whose user turns it already covered are done
        con.execute("""
            INSERT INTO grammar_sessions (session, analyzed_at)
            SELECT session, ? FROM transcripts GROUP BY session
            HAVING COALESCE(MAX(CASE WHEN role = 'user' THEN id END), 0)
                   <= (SELECT COALESCE(MAX(transcript_id), 0) FROM grammar_feedback)
        """, (time.time(),))
    con.commit()


def analyze_session(client, system, rows):
    """Send one session's dialogue to the model and return the parsed feedback."""
    dialogue = "\n".join(f"[{i}] {role}: {text}" for i, role, text in rows)
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": FEEDBACK_SCHEMA}},
        messages=[{"role": "user", "content": dialogue}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError("response hit max_tokens; output is truncated")
    return json.loads(next(block.text for block in response.content if block.type == "text"))


def save_feedback(con, session, result, points):
    """Store one row per issue (or per issue-free turn) and mark the session analyzed, atomically."""
    now = time.time()
    for turn in result["turns"]:
        transcript_id = max(turn["turn_ids"])  # last fragment of the turn
        issues = turn["issues"] if turn["status"] != "asr_unclear" else []
        if not issues:
            con.execute(
                "INSERT INTO grammar_feedback (transcript_id, status, created_at) VALUES (?, ?, ?)",
                (transcript_id, turn["status"], now),
            )
        for issue in issues:
            key = issue["bunpro_key"] if issue["bunpro_key"] in points else None
            url = points[key][2] if key else None
            con.execute(
                """INSERT INTO grammar_feedback
                   (transcript_id, status, type, span, correction, note, bunpro_key, bunpro_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (transcript_id, turn["status"], issue["type"], issue["span"],
                 issue["correction"], issue["note"], key, url, now),
            )
    con.execute(
        "INSERT INTO grammar_sessions (session, patterns, analyzed_at) VALUES (?, ?, ?)",
        (session, json.dumps(result["patterns"], ensure_ascii=False), now),
    )
    con.commit()


def print_summary(result, points):
    for turn in result["turns"]:
        if turn["status"] in ("correct", "asr_unclear"):
            continue
        print(f"\n[{', '.join(map(str, turn['turn_ids']))}] {turn['status']}: {turn['text']}")
        for issue in turn["issues"]:
            print(f"  - ({issue['type']}) {issue['span']} → {issue['correction']}: {issue['note']}")
            if issue["bunpro_key"] in points:
                grammar, _, url = points[issue["bunpro_key"]]
                print(f"    {grammar}: {url}")
    if result["patterns"]:
        print("\nPatterns:")
        for p in result["patterns"]:
            print(f"  - {p}")


def main():
    points = load_grammar_points()
    grammar_block = "\n".join(f"{key}|{g}|{m}" for key, (g, m, _) in points.items())
    #cached so every session after the first in a run reads the grammar list from cache
    system = [{"type": "text", "text": INSTRUCTIONS + grammar_block, "cache_control": {"type": "ephemeral"}}]

    con = sqlite3.connect(DB_PATH)
    init_db(con)

    sessions = [s for (s,) in con.execute("""
        SELECT session FROM transcripts
        WHERE session NOT IN (SELECT session FROM grammar_sessions)
        GROUP BY session ORDER BY MIN(id)
    """)]
    if not sessions:
        print("No new sessions to analyze.")
        con.close()
        return

    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    analyzed = False
    for session in sessions:
        rows = con.execute(
            "SELECT id, role, text FROM transcripts WHERE session = ? ORDER BY id", (session,)
        ).fetchall()
        print(f"\n=== Session {session} ===")
        if not any(role == "user" for _, role, _ in rows):
            print("No user turns.")
            con.execute("INSERT INTO grammar_sessions (session, analyzed_at) VALUES (?, ?)", (session, time.time()))
            con.commit()
            continue
        try:
            result = analyze_session(client, system, rows)
        except Exception as e:
            #leave the session unmarked so the next run retries it
            print(f"Failed, will retry next run: {e}")
            continue
        save_feedback(con, session, result, points)
        print_summary(result, points)
        analyzed = True

    #rewrite the learner profile from the updated evidence
    if analyzed:
        try:
            learner_profile.build_profile(client, con, points)
        except Exception as e:
            print(f"\nLearner profile update failed: {e}")
    con.close()


if __name__ == "__main__":
    main()
