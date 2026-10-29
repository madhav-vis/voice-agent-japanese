from dotenv import load_dotenv
import json, os, sqlite3, time

#load env
load_dotenv(".env")

MODEL = "claude-sonnet-5-5"
HALF_LIFE_DAYS = 14  # a mistake counts half as much after two weeks
LEVELS = ["N5", "N4", "N3", "N2", "N1"]

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": LEVELS},
        "profile": {"type": "string"},
    },
    "required": ["level", "profile"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You maintain a short learner profile for a Japanese learner who practices by voice with an AI conversation partner.
The partner reads this profile at the start of every session to pitch its Japanese at the right level and to choose which mistakes to correct.

You get JSON stats built from the learner's analyzed sessions:
- weak_spots: mistakes grouped by Bunpro grammar point or issue type, ranked by a recency-weighted score (recent mistakes count more), with recent examples
- status_trend: share of correct / incorrect / unnatural user turns in recent sessions vs. earlier ones
- patterns: session-level habits noted by the grammar reviewer
- recent_user_turns: the learner's own recent lines (speech recognition output, so expect some recognition errors)
- given_level: the learner's self-reported JLPT level, or null

Write:
- level: the JLPT level whose grammar and vocabulary the partner should mostly use. If given_level is set, use it.
- profile: plain English, under 120 words. Cover the level, the top 3 to 5 weak points (name the Japanese grammar point when there is one), recurring habits, and what is improving. Be concrete and brief. Only state what the data supports; with little data, say less.
- Casual speech (タメ口, 俺, めっちゃ) and natural switching between casual and polite turns are valid, not weak points. Only mention register for wrong keigo or jarring switches within one utterance.
- Don't list speech recognition problems as learner habits."""


def recency_weight(created_at, now):
    age_days = max(0.0, (now - created_at) / 86400)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def gather_stats(con, points):
    """Summarize the raw evidence into the small dict the profile is written from."""
    now = time.time()

    #weak spots: group by bunpro key, falling back to issue type; age is when it was said
    spots = {}
    rows = con.execute("""
        SELECT f.bunpro_key, f.type, f.span, f.correction, t.created_at
        FROM grammar_feedback f JOIN transcripts t ON t.id = f.transcript_id
        WHERE f.type IS NOT NULL
        ORDER BY t.created_at DESC
    """).fetchall()
    for key, issue_type, span, correction, created_at in rows:
        name = f"{key} {points[key][0]}" if key in points else issue_type
        spot = spots.setdefault(name, {"weak_spot": name, "score": 0.0, "count": 0, "examples": []})
        spot["score"] += recency_weight(created_at, now)
        spot["count"] += 1
        if len(spot["examples"]) < 2:
            spot["examples"].append(f"{span} → {correction}")
    weak_spots = sorted(spots.values(), key=lambda s: s["score"], reverse=True)[:8]
    for spot in weak_spots:
        spot["score"] = round(spot["score"], 2)

    #status trend: one status per user turn, last 3 sessions vs. earlier
    sessions = [s for (s,) in con.execute("SELECT session FROM transcripts GROUP BY session ORDER BY MIN(id)")]
    recent = set(sessions[-3:])
    counts = {"recent": {}, "earlier": {}}
    for session, status in con.execute("""
        SELECT t.session, f.status FROM (
            SELECT DISTINCT transcript_id, status FROM grammar_feedback WHERE status != 'asr_unclear'
        ) f JOIN transcripts t ON t.id = f.transcript_id
    """):
        bucket = counts["recent" if session in recent else "earlier"]
        bucket[status] = bucket.get(status, 0) + 1
    status_trend = {
        period: {status: round(n / sum(c.values()), 2) for status, n in c.items()} | {"turns": sum(c.values())}
        for period, c in counts.items() if c
    }

    patterns = [
        p for (raw,) in con.execute(
            "SELECT patterns FROM grammar_sessions WHERE patterns IS NOT NULL ORDER BY analyzed_at DESC LIMIT 5"
        ) for p in json.loads(raw)
    ]

    recent_user_turns = [text for (text,) in con.execute(
        "SELECT text FROM transcripts WHERE role = 'user' AND session IN ({}) ORDER BY id DESC LIMIT 40".format(
            ",".join("?" * len(sessions[-2:]))), sessions[-2:]
    )][::-1]

    return {
        "weak_spots": weak_spots,
        "status_trend": status_trend,
        "patterns": patterns,
        "recent_user_turns": recent_user_turns,
    }


def init_db(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS learner_profile (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            level      TEXT,
            profile    TEXT,
            created_at REAL
        )
    """)


def build_profile(client, con, points):
    """Rewrite the learner profile from the raw evidence and store it as a new row."""
    given_level = os.getenv("JLPT_LEVEL") or None
    stats = gather_stats(con, points) | {"given_level": given_level}
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=INSTRUCTIONS,
        output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(stats, ensure_ascii=False)}],
    )
    result = json.loads(next(block.text for block in response.content if block.type == "text"))
    level = given_level if given_level in LEVELS else result["level"]

    init_db(con)
    con.execute(
        "INSERT INTO learner_profile (level, profile, created_at) VALUES (?, ?, ?)",
        (level, result["profile"], time.time()),
    )
    con.commit()
    print(f"\n=== Learner profile ({level}) ===\n{result['profile']}")


def load_profile(db_path="transcripts.db"):
    """Return the latest (level, profile), or None if no profile has been built yet."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT level, profile FROM learner_profile ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:  # table doesn't exist yet
        return None
    finally:
        con.close()


def main():
    import anthropic
    from grammar_police import DB_PATH, load_grammar_points  # imported here to avoid a circular import

    con = sqlite3.connect(DB_PATH)
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    build_profile(client, con, load_grammar_points())
    con.close()


if __name__ == "__main__":
    main()
