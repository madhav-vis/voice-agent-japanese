"""Progress statistics for the browser UI's Statistics view and end-of-session card.

    uv run python stats.py      # print the stats JSON for the real database

Sessions are counted as *practice blocks*: rows of one `session` split wherever two consecutive rows are
more than 30 minutes apart. Old console runs all share session = 'console', so the session column alone
would merge days of practice into one. Minutes are estimated from the first to the last row of a block
(audio length isn't stored).
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json, re, sqlite3, time
from learner_profile import latest_profile, recency_weight

BLOCK_GAP = 30 * 60
ROLLING = 3
FIXED_MIN_MISSES, FIXED_RECENT_BLOCKS, FIXED_MIN_TURNS = 2, 3, 5
LEVELS = ["N5", "N4", "N3", "N2", "N1"]
_NOT_COUNTED = re.compile(r"[\s、。，．,.！？!?「」『』…ー〜~]")


def chars(text):
    """Characters actually spoken: no spaces or punctuation."""
    return len(_NOT_COUNTED.sub("", text or ""))


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def week_start(ts):
    d = datetime.fromtimestamp(ts).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def practice_blocks(con):
    """Split every session on 30-minute gaps. Returns (blocks, {transcript_id: block index})."""
    blocks, block_of, prev = [], {}, None
    for tid, session, role, text, ts in con.execute(
        "SELECT id, session, role, text, created_at FROM transcripts WHERE created_at IS NOT NULL ORDER BY session, id"
    ):
        if prev is None or session != prev[0] or ts - prev[1] > BLOCK_GAP:
            blocks.append({"session": session, "start": ts, "end": ts, "user_turns": 0, "user_chars": 0, "longest": ""})
        b = blocks[-1]
        b["end"] = max(b["end"], ts)
        if role == "user":
            b["user_turns"] += 1
            b["user_chars"] += chars(text)
        block_of[tid] = len(blocks) - 1
        prev = (session, ts)
    order = sorted(range(len(blocks)), key=lambda i: blocks[i]["start"])
    remap = {old: new for new, old in enumerate(order)}
    blocks = [blocks[i] for i in order]
    for b in blocks:
        b["minutes"] = max(1, round((b["end"] - b["start"]) / 60)) if b["user_turns"] else 0
        b["date"] = day(b["start"])
    return blocks, {tid: remap[i] for tid, i in block_of.items()}


def judged_turns(con, block_of):
    """One row per graded user turn (asr_unclear excluded): transcript id, time, status, text, block."""
    rows = con.execute("""
        SELECT f.transcript_id, t.created_at, f.status, COALESCE(MAX(f.text), t.text)
        FROM grammar_feedback f JOIN transcripts t ON t.id = f.transcript_id
        WHERE f.status != 'asr_unclear'
        GROUP BY f.transcript_id, f.status
    """).fetchall()
    return [
        {"id": tid, "ts": ts, "status": status, "text": text, "block": block_of.get(tid)}
        for tid, ts, status, text in rows if ts is not None
    ]


def issues(con, block_of):
    rows = con.execute("""
        SELECT f.transcript_id, t.created_at, f.type, f.bunpro_key, f.span, f.correction
        FROM grammar_feedback f JOIN transcripts t ON t.id = f.transcript_id
        WHERE f.type IS NOT NULL AND t.created_at IS NOT NULL
    """).fetchall()
    return [
        {"id": tid, "ts": ts, "type": typ, "key": key, "span": span, "correction": corr, "block": block_of.get(tid)}
        for tid, ts, typ, key, span, corr in rows
    ]


def streaks(active_days, today):
    days = sorted(date.fromisoformat(d) for d in active_days)
    longest = run = 0
    for i, d in enumerate(days):
        run = run + 1 if i and (d - days[i - 1]).days == 1 else 1
        longest = max(longest, run)
    current = 0
    if days and (today - days[-1]).days <= 1:  # a streak is still alive until a full day is missed
        current, d = 1, days[-1]
        active = set(days)
        while d - timedelta(days=1) in active:
            current, d = current + 1, d - timedelta(days=1)
    return longest, current


def compute_stats(con, points, now=None):
    now = now or time.time()
    today = datetime.fromtimestamp(now).date()
    blocks, block_of = practice_blocks(con)
    turns = judged_turns(con, block_of)
    found = issues(con, block_of)

    # ---- heatmap: user lines and minutes per local day
    days = defaultdict(lambda: {"turns": 0, "minutes": 0})
    for tid, role, ts in con.execute("SELECT id, role, created_at FROM transcripts WHERE created_at IS NOT NULL"):
        if role == "user":
            days[day(ts)]["turns"] += 1
    for b in blocks:
        if b["user_turns"]:
            days[b["date"]]["minutes"] += b["minutes"]
    active = [d for d, v in days.items() if v["turns"]]
    longest, current = streaks(active, today)
    first = min(active) if active else None
    span_days = (today - date.fromisoformat(first)).days + 1 if first else 0
    heatmap = {
        "days": {d: v for d, v in sorted(days.items()) if v["turns"]},
        "daily_average": round(sum(days[d]["turns"] for d in active) / len(active)) if active else 0,
        "days_learned_pct": round(100 * len(active) / span_days) if span_days else 0,
        "longest_streak": longest,
        "current_streak": current,
    }

    # ---- weekly volume / accuracy / mistake types (every week from the first to now, gaps included)
    weekly = {}
    if first:
        w = date.fromisoformat(week_start(time.mktime(date.fromisoformat(first).timetuple())))
        while w <= today:
            weekly[w.isoformat()] = {"week": w.isoformat(), "chars": 0, "minutes": 0, "correct": 0, "incorrect": 0,
                                     "unnatural": 0, "graded_chars": 0, "issues": 0, "issues_by_type": {}}
            w += timedelta(days=7)
    for b in blocks:
        wk = weekly.get(week_start(b["start"]))
        if wk:
            wk["chars"] += b["user_chars"]
            wk["minutes"] += b["minutes"]
    for t in turns:
        wk = weekly.get(week_start(t["ts"]))
        if wk:
            wk[t["status"]] = wk.get(t["status"], 0) + 1
            wk["graded_chars"] += chars(t["text"])
    for i in found:
        wk = weekly.get(week_start(i["ts"]))
        if wk:
            wk["issues"] += 1
            wk["issues_by_type"][i["type"]] = wk["issues_by_type"].get(i["type"], 0) + 1
    for wk in weekly.values():
        wk["mistakes_per_100"] = round(100 * wk["issues"] / wk["graded_chars"], 1) if wk["graded_chars"] else None

    # ---- correct-turn rate per practice block, with a rolling average
    per_block = defaultdict(Counter)
    for t in turns:
        if t["block"] is not None:
            per_block[t["block"]][t["status"]] += 1
    correct_rate = []
    for bi in sorted(per_block):
        c = per_block[bi]
        judged = sum(c.values())
        correct_rate.append({"date": blocks[bi]["date"], "rate": round(c["correct"] / judged, 3), "turns": judged})
    for i, p in enumerate(correct_rate):
        window = correct_rate[max(0, i - ROLLING + 1): i + 1]
        p["rolling"] = round(sum(w["rate"] * w["turns"] for w in window) / sum(w["turns"] for w in window), 3)

    # ---- weak spots: same grouping and recency weighting as learner_profile.gather_stats
    spots = {}
    for i in found:
        name = i["key"] if i["key"] in points else i["type"]
        s = spots.setdefault(name, {"key": i["key"] if i["key"] in points else None, "type": i["type"],
                                    "count": 0, "score": 0.0, "last_seen": 0, "example": None})
        s["count"] += 1
        s["score"] += recency_weight(i["ts"], now)
        if i["ts"] > s["last_seen"]:
            s["last_seen"], s["example"] = i["ts"], f"{i['span']} → {i['correction']}"
    weak_spots = []
    for s in sorted(spots.values(), key=lambda s: s["score"], reverse=True)[:8]:
        if s["key"]:
            grammar, meaning, url = points[s["key"]]
            s.update(level=s["key"].split("-")[0], grammar=grammar, meaning=meaning, url=url)
        s["score"] = round(s["score"], 2)
        s["last_seen"] = day(s["last_seen"])
        weak_spots.append(s)

    # ---- fixed: missed at least twice before, not missed in the last few substantial blocks
    substantial = [bi for bi in sorted(per_block) if sum(per_block[bi].values()) >= FIXED_MIN_TURNS]
    recent = set(substantial[-FIXED_RECENT_BLOCKS:])
    fixed, enough_data = [], len(substantial) >= FIXED_RECENT_BLOCKS
    if enough_data:
        misses = defaultdict(list)
        for i in found:
            if i["key"] in points and i["block"] is not None:
                misses[i["key"]].append(i)
        for key, hits in misses.items():
            if len(hits) >= FIXED_MIN_MISSES and not any(h["block"] in recent for h in hits):
                grammar, meaning, url = points[key]
                fixed.append({"key": key, "level": key.split("-")[0], "grammar": grammar, "meaning": meaning,
                              "url": url, "misses": len(hits), "last_missed": day(max(h["ts"] for h in hits))})
        fixed.sort(key=lambda f: f["misses"], reverse=True)

    # ---- JLPT level of the grammar in mistakes, per month
    levels = defaultdict(Counter)
    for i in found:
        if i["key"] in points:
            levels[time.strftime("%Y-%m", time.localtime(i["ts"]))][i["key"].split("-")[0]] += 1
    levels = [{"month": m, **{lv: c.get(lv, 0) for lv in LEVELS}} for m, c in sorted(levels.items())]

    # ---- average turn length per practice block (grouped turns, so STT fragments don't drag it down)
    lengths = defaultdict(list)
    for t in turns:
        if t["block"] is not None:
            lengths[t["block"]].append(chars(t["text"]))
    turn_length = [{"date": blocks[bi]["date"], "avg": round(sum(v) / len(v), 1), "turns": len(v)}
                   for bi, v in sorted(lengths.items())]

    profile = latest_profile(con) if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learner_profile'").fetchone() else None
    topics = profile["topics"] if profile else []

    practiced = [b for b in blocks if b["user_turns"]]
    return {
        "generated": day(now),
        "totals": {
            "sessions": len(practiced),
            "minutes": sum(b["minutes"] for b in practiced),
            "user_turns": sum(b["user_turns"] for b in practiced),
            "chars": sum(b["user_chars"] for b in practiced),
            "first_day": first,
        },
        "heatmap": heatmap,
        "weekly": list(weekly.values()),
        "correct_rate": correct_rate,
        "weak_spots": weak_spots,
        "fixed": fixed,
        "fixed_enough_data": enough_data,
        "levels": levels,
        "turn_length": turn_length,
        "topics": topics,
    }


def session_card(con, session, points):
    """Headline numbers for one finished session, compared with the learner's other sessions."""
    blocks, block_of = practice_blocks(con)
    mine = [i for i, b in enumerate(blocks) if b["session"] == session and b["user_turns"]]
    if not mine:
        return None
    turns = judged_turns(con, block_of)
    rate_of = defaultdict(Counter)
    for t in turns:
        if t["block"] is not None:
            rate_of[t["block"]][t["status"]] += 1

    def rate(bis):
        c = sum((rate_of[b] for b in bis), Counter())
        n = sum(c.values())
        return round(c["correct"] / n, 3) if n else None

    others = [i for i, b in enumerate(blocks) if b["user_turns"] and i not in mine]
    other_rates = [r for r in (rate([i]) for i in others) if r is not None]
    texts = [t["text"] for t in turns if t["block"] in mine] or [
        text for (text,) in con.execute("SELECT text FROM transcripts WHERE session = ? AND role = 'user'", (session,))]
    keys = Counter(k for (k,) in con.execute("""
        SELECT f.bunpro_key FROM grammar_feedback f JOIN transcripts t ON t.id = f.transcript_id
        WHERE t.session = ? AND f.bunpro_key IS NOT NULL""", (session,)) if k in points)
    review = None
    if keys:
        key = keys.most_common(1)[0][0]
        grammar, meaning, url = points[key]
        review = {"key": key, "level": key.split("-")[0], "grammar": grammar, "meaning": meaning, "url": url}
    return {
        "minutes": sum(blocks[i]["minutes"] for i in mine),
        "chars": sum(blocks[i]["user_chars"] for i in mine),
        "rate": rate(mine),
        "avg_rate": round(sum(other_rates) / len(other_rates), 3) if other_rates else None,
        "avg_chars": round(sum(blocks[i]["user_chars"] for i in others) / len(others)) if others else None,
        "longest": max(texts, key=chars) if texts else "",
        "review": review,
    }


if __name__ == "__main__":
    from grammar_police import DB_PATH, init_db, load_grammar_points
    con = sqlite3.connect(DB_PATH)
    init_db(con)
    print(json.dumps(compute_stats(con, load_grammar_points()), ensure_ascii=False, indent=1))
