"""Show the sentences you got wrong in recent sessions, with corrections and the grammar points behind them.

    uv run python review.py        # last analyzed session
    uv run python review.py -n 3   # last 3 analyzed sessions
"""
import argparse, json, sqlite3, sys, time

USE_COLOR = sys.stdout.isatty()  # plain text when written to grammar_police.log


def color(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def print_session(con, session, points):
    """Print one analyzed session: score, each wrong turn with its correction and grammar points, patterns."""
    rows = con.execute("""
        SELECT f.transcript_id, f.status, COALESCE(f.text, t.text), f.corrected,
               f.type, f.span, f.correction, f.note, f.bunpro_key
        FROM grammar_feedback f JOIN transcripts t ON t.id = f.transcript_id
        WHERE t.session = ?
        ORDER BY f.transcript_id, f.id
    """, (session,)).fetchall()
    started = con.execute("SELECT MIN(created_at) FROM transcripts WHERE session = ?", (session,)).fetchone()[0]
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else session

    #one row per issue (or per issue-free turn); regroup into turns
    turns = {}
    for tid, status, text, corrected, issue_type, span, correction, note, key in rows:
        turn = turns.setdefault(tid, {"status": status, "text": text, "corrected": corrected, "issues": []})
        if issue_type:
            turn["issues"].append((issue_type, span, correction, note, key))

    judged = [t for t in turns.values() if t["status"] != "asr_unclear"]
    correct = sum(t["status"] == "correct" for t in judged)
    print(color(f"\n=== {date} ===", "1"), f"{correct}/{len(judged)} turns correct" if judged else "no user turns")

    for turn in turns.values():
        if turn["status"] not in ("incorrect", "unnatural"):
            continue
        label = "✗" if turn["status"] == "incorrect" else "△"
        print(f"\n{color(label, '31')} {turn['text']}")
        if turn["corrected"] and turn["corrected"] != turn["text"]:
            print(f"{color('✓', '32')} {color(turn['corrected'], '32')}")
        for issue_type, span, correction, note, key in turn["issues"]:
            print(f"   • {span} → {correction}  ({issue_type}) {note}")
            if key in points:
                grammar, meaning, url = points[key]
                print(color(f"     文法 {key} {grammar} — {meaning}  {url}", "36"))

    patterns = con.execute("SELECT patterns FROM grammar_sessions WHERE session = ?", (session,)).fetchone()
    patterns = json.loads(patterns[0]) if patterns and patterns[0] else []
    if patterns:
        print("\nPatterns:")
        for p in patterns:
            print(f"  - {p}")


def main():
    from grammar_police import DB_PATH, init_db, load_grammar_points  # imported here to avoid a circular import

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--sessions", type=int, default=1, help="how many recent analyzed sessions to show")
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH)
    init_db(con)  # adds the text/corrected columns to an older table
    sessions = [s for (s,) in con.execute("""
        SELECT t.session FROM transcripts t JOIN grammar_feedback f ON f.transcript_id = t.id
        GROUP BY t.session ORDER BY MIN(t.id) DESC LIMIT ?
    """, (args.sessions,))][::-1]
    if not sessions:
        print("No analyzed sessions yet. Run: uv run python grammar_police.py")
    points = load_grammar_points()
    for session in sessions:
        print_session(con, session, points)
    con.close()


if __name__ == "__main__":
    main()
