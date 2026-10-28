from dotenv import load_dotenv
import anthropic
import csv, json, os, sqlite3, time

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
- Look especially for: は vs が confusion, dropped or wrong particles, conjugation errors, excessive fillers (えーと, あの, なんか), wrong register mixing.
- Spoken Japanese commonly drops particles and sentence endings; do not flag casual omissions that a native speaker would also make.
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


def main():
    points = load_grammar_points()
    grammar_block = "\n".join(f"{key}|{g}|{m}" for key, (g, m, _) in points.items())

    con = sqlite3.connect(DB_PATH)
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

    #only analyze rows newer than the last analyzed transcript
    checkpoint = con.execute("SELECT COALESCE(MAX(transcript_id), 0) FROM grammar_feedback").fetchone()[0]
    rows = con.execute(
        "SELECT id, role, text FROM transcripts WHERE id > ? ORDER BY id", (checkpoint,)
    ).fetchall()
    if not any(role == "user" for _, role, _ in rows):
        print("No new user turns to analyze.")
        con.close()
        return

    dialogue = "\n".join(f"[{i}] {role}: {text}" for i, role, text in rows)

    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[
            {"type": "text", "text": INSTRUCTIONS + grammar_block, "cache_control": {"type": "ephemeral"}},
        ],
        output_config={"format": {"type": "json_schema", "schema": FEEDBACK_SCHEMA}},
        messages=[{"role": "user", "content": dialogue}],
    )
    result = json.loads(next(block.text for block in response.content if block.type == "text"))

    #store one row per issue, or one row per turn without issues
    now = time.time()
    for turn in result["turns"]:
        transcript_id = max(turn["turn_ids"])  # last fragment, so the checkpoint covers the whole turn
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
    con.commit()
    con.close()

    #print summary
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


if __name__ == "__main__":
    main()
