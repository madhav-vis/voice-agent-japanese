from dotenv import load_dotenv
import json, os, sqlite3, time

#load env
load_dotenv(".env")

MODEL = "claude-sonnet-5-5"
HALF_LIFE_DAYS = 14  # a mistake counts half as much after two weeks
LEVELS = ["N5", "N4", "N3", "N2", "N1"]
MAX_FACTS = 15
MAX_TOPICS = 15
RECENT_SESSIONS = 2
MAX_LINES_PER_SESSION = 150  # keep the last lines of very long sessions
MIN_USER_LINES = 3  # sessions shorter than this (e.g. just a greeting) don't count as recent

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": LEVELS},
        "profile": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "recent_sessions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["level", "profile", "facts", "topics", "recent_sessions"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You maintain a short learner profile for a Japanese learner who practices by voice with an AI conversation partner.
The partner reads this profile at the start of every session to pitch its Japanese at the right level and to choose which mistakes to correct.

You get JSON stats built from the learner's analyzed sessions:
- weak_spots: mistakes grouped by Bunpro grammar point or issue type, ranked by a recency-weighted score (recent mistakes count more), with recent examples
- status_trend: share of correct / incorrect / unnatural user turns in recent sessions vs. earlier ones
- patterns: session-level habits noted by the grammar reviewer
- recent_sessions: the full dialogue of the learner's last sessions, oldest first, with dates. "user" lines are speech recognition output, so expect some recognition errors
- previous: facts and topics from the previous profile, or null
- given_level: the learner's self-reported JLPT level, or null

Write:
- level: the JLPT level whose grammar and vocabulary the partner should mostly use. If given_level is set, use it.
- profile: plain English, under 120 words. Cover the level, the top 3 to 5 weak points (name the Japanese grammar point when there is one), recurring habits, and what is improving. Be concrete and brief. Only state what the data supports; with little data, say less.
- Casual speech (タメ口, 俺, めっちゃ) and natural switching between casual and polite turns are valid, not weak points. Only mention register for wrong keigo or jarring switches within one utterance.
- Don't list speech recognition problems as learner habits.
- facts: stable personal facts the learner has said about themselves (job, studies, hobbies, family, pets, where they live, plans, why they study Japanese). Short English phrases, e.g. "Works as a nurse", "Has a cat named Mochi". Start from previous.facts, add new ones from recent_sessions, and update or drop facts the learner has since contradicted. Only what the learner actually said, never guesses or what the assistant said. At most {max_facts}, most useful first.
- topics: conversation topics already covered, as short English phrases (e.g. "weekend ramen with friends"). Start from previous.topics and add the topics of recent_sessions. Most recent first, at most {max_topics}; drop the oldest.
- recent_sessions: one summary per session in recent_sessions, most recent first. Plain English, 1 to 2 sentences each, starting with the date: what was talked about and anything worth following up on next time (e.g. an upcoming trip or exam).""".format(
    max_facts=MAX_FACTS, max_topics=MAX_TOPICS)


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

    #full dialogue (both roles) of the last sessions, for facts, topics, and summaries
    substantial = [s for (s,) in con.execute(
        "SELECT session FROM transcripts GROUP BY session HAVING SUM(role = 'user') >= ? ORDER BY MIN(id)",
        (MIN_USER_LINES,),
    )]
    recent_sessions = []
    for session in substantial[-RECENT_SESSIONS:]:
        rows = con.execute(
            "SELECT role, text, created_at FROM transcripts WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, MAX_LINES_PER_SESSION),
        ).fetchall()[::-1]
        if rows:
            recent_sessions.append({
                "date": time.strftime("%Y-%m-%d", time.localtime(rows[0][2])),
                "dialogue": [f"{role}: {text}" for role, text, _ in rows],
            })

    return {
        "weak_spots": weak_spots,
        "status_trend": status_trend,
        "patterns": patterns,
        "recent_sessions": recent_sessions,
    }


JSON_COLUMNS = ["facts", "topics", "recent_sessions"]


def init_db(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS learner_profile (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            level           TEXT,
            profile         TEXT,
            facts           TEXT,
            topics          TEXT,
            recent_sessions TEXT,
            created_at      REAL
        )
    """)
    #migrate tables created before facts/topics/recent_sessions existed
    columns = {name for (_, name, *_) in con.execute("PRAGMA table_info(learner_profile)")}
    for column in JSON_COLUMNS:
        if column not in columns:
            con.execute(f"ALTER TABLE learner_profile ADD COLUMN {column} TEXT")


def latest_profile(con):
    """Return the latest profile row as a dict (JSON columns decoded), or None."""
    row = con.execute(
        "SELECT level, profile, facts, topics, recent_sessions FROM learner_profile ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    level, profile, *lists = row
    return {"level": level, "profile": profile} | {
        column: json.loads(raw) if raw else [] for column, raw in zip(JSON_COLUMNS, lists)
    }


def build_profile(client, con, points):
    """Rewrite the learner profile from the raw evidence and store it as a new row."""
    given_level = os.getenv("JLPT_LEVEL") or None
    init_db(con)
    previous = latest_profile(con)
    stats = gather_stats(con, points) | {
        "previous": {"facts": previous["facts"], "topics": previous["topics"]} if previous else None,
        "given_level": given_level,
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=INSTRUCTIONS,
        output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(stats, ensure_ascii=False)}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError("response hit max_tokens; output is truncated")
    result = json.loads(next(block.text for block in response.content if block.type == "text"))
    level = given_level if given_level in LEVELS else result["level"]
    facts = result["facts"][:MAX_FACTS]
    topics = result["topics"][:MAX_TOPICS]
    recent = result["recent_sessions"][:RECENT_SESSIONS]

    con.execute(
        """INSERT INTO learner_profile (level, profile, facts, topics, recent_sessions, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (level, result["profile"], *(json.dumps(x, ensure_ascii=False) for x in (facts, topics, recent)), time.time()),
    )
    con.commit()
    print(f"\n=== Learner profile ({level}) ===\n{result['profile']}")
    print("\nFacts:\n" + "\n".join(f"  - {f}" for f in facts))
    print("\nTopics:\n" + "\n".join(f"  - {t}" for t in topics))
    print("\nRecent sessions:\n" + "\n".join(f"  - {r}" for r in recent))


def load_profile(db_path="transcripts.db"):
    """Return the latest profile as a dict, or None if no profile has been built yet."""
    con = sqlite3.connect(db_path)
    try:
        init_db(con)  # adds missing columns to an older table
        return latest_profile(con)
    except sqlite3.OperationalError:  # e.g. database locked
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
