# Voice AI — Japanese Conversational Practice

## Project goal

A voice AI agent for improving Japanese conversational skill: talk freely, get gentle in-conversation corrections, and review mistakes afterwards against Bunpro grammar points. The agent remembers the learner across sessions through a learner profile.

## Stack

- **Runtime:** Python 3.9+, managed with `uv`
- **Voice pipeline:** LiveKit Agents framework (`livekit-agents`)
- **STT:** Deepgram `nova-3` (cloud, `language="ja"`) via `livekit-plugins-deepgram`
- **LLM (conversation):** Claude Sonnet 5.5 (`claude-sonnet-5-5`) via `livekit-plugins-anthropic`, key from `CLAUDE_API_KEY` (the plugin defaults to `ANTHROPIC_API_KEY`, so it is passed explicitly)
- **TTS:** AivisSpeech (local, VOICEVOX-compatible HTTP API at `http://127.0.0.1:10101`, speaker コハク/ノーマル `1878365376`) — custom `VoicevoxTTS` plugin in `voicevox_tts.py` (also works with VoiceVox on port 50021). Wrapped in `tts.StreamAdapter` with `JapaneseSentenceTokenizer` so replies are synthesized sentence by sentence
- **VAD:** Silero
- **Turn detection:** LiveKit `MultilingualModel` (supports Japanese) — reads the transcript to tell a mid-sentence pause from a finished turn; endpointing waits 0.5–5.0s
- **Grammar feedback:** `grammar_police.py` — Claude Sonnet 5.5 (`claude-sonnet-5-5`) reviews user turns in `transcripts.db`, maps mistakes to `bunpro_grammar_points.csv`, writes to the `grammar_feedback` table (including the turn's text and a full corrected sentence); one API call per session, tracked (with session-level patterns) in `grammar_sessions`
- **Learner profile:** `learner_profile.py` — one Sonnet call per rebuild (details below); injected into the agent's instructions under 「# ユーザーについて」 at session start
- **Unused local fallback:** `kotoba_stt.py` (kotoba-whisper-v2.0 on MPS) is still in the repo but not wired in

## Files

| File | Role |
|---|---|
| `livekit_agent_conversation.py` | The agent: `Assistant` (system prompt + injected profile), `entrypoint` (pipeline config, greeting), shutdown callback that saves the transcript and starts `grammar_police.py` |
| `voicevox_tts.py` | `VoicevoxTTS` plugin (`/audio_query` → `/synthesis`) and `JapaneseSentenceTokenizer` (splits on 。！？) |
| `grammar_police.py` | Post-session grammar analysis; owns `grammar_feedback` / `grammar_sessions` schema and migrations; rebuilds the profile after analyzing |
| `learner_profile.py` | Builds and loads the learner profile; owns the `learner_profile` schema and migrations |
| `review.py` | CLI viewer for mistakes; `session_review()` returns one session as a dict (used by `ui_server.py`), `print_session()` renders it (also used by `grammar_police.py` for its log output) |
| `bunpro_grammar_points.csv` | `level,number,grammar,meaning,url`; keys are `"{level}-{number}"` (e.g. `N5-13`). Gitignored (`*.csv`) |
| `ui_server.py` | Browser UI backend (stdlib `http.server`): serves `index.html`, `GET /api/token` (LiveKit token + new `jp-…` room), `GET /api/review?room=` (`saving` → `analyzing` → `done` + `review.session_review` data + `card`), `GET /api/stats` (`stats.compute_stats`) |
| `stats.py` | Progress statistics: `compute_stats()` (heatmap, streaks, weekly volume/accuracy/mistake types, correct rate, weak spots, fixed points, JLPT levels, turn length, topics) and `session_card()` for the end-of-session card. `uv run python stats.py` prints the JSON |
| `index.html` | Browser UI (Frutiger Aero theme: CSS/SVG sky-and-hills scene, glass pane, nav rail 練習/記録): one gel orb (`#btn`) is Start/Stop, shows the agent state by colour/motion and the mic level (`--level` on `#stage`); layout switches on `body[data-phase|data-state|data-view]`; live captions, end-of-session review with session card; Statistics view (`#stats`) with hand-written SVG charts. Plain JS, `livekit-client` UMD pinned on jsDelivr |
| `Code_Documentation.MD` | Older free-form notes |

## Data flow

1. Session runs (console or, later, browser). The agent loads the latest learner profile once, at session start.
2. On shutdown, every user/assistant message from `session.history` is inserted into `transcripts` with `session = ctx.job.id`.
3. The shutdown callback then starts `grammar_police.py` as a **detached** process (`start_new_session=True`, output appended to `grammar_police.log`), so analysis outlives the agent process.
4. `grammar_police.py` takes an exclusive `fcntl` lock on `transcripts.db.grammar.lock` (a second run waits, then picks up what's left), analyzes each session not in `grammar_sessions`, prints it via `review.print_session`, then calls `learner_profile.build_profile` if anything was analyzed.
5. The next session starts with the new profile.

## Database (`transcripts.db`, SQLite)

Never commit it — it contains personal conversations (`*.db` is gitignored). Schemas are created/migrated in code (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN` for missing columns); keep migrations additive so old databases keep working.

- **`transcripts`** — `id, session, role ('user'|'assistant'), text, interrupted, created_at, room`. `room` is the LiveKit room name (added later, NULL on old rows); the browser UI uses it to find its session, since `session` is the job id. User text is raw STT output; consecutive user rows are often fragments of one utterance.
- **`grammar_feedback`** — one row per issue, or one row per issue-free turn. `transcript_id` is the *last* fragment of the turn. `status` ∈ `correct | incorrect | unnatural | asr_unclear`; `type` ∈ `particle | wa_ga | conjugation | filler | word_choice | register | other` (NULL on issue-free rows); `span → correction`, `note` (English, <15 words), `bunpro_key`/`bunpro_url` (only if the key exists in the CSV), `text` (whole grouped turn), `corrected` (whole turn corrected, same register). Rows from before `text`/`corrected` existed have them NULL.
- **`grammar_sessions`** — `session` (PK), `patterns` (JSON list of session-level habits), `analyzed_at`. A session in here is never re-analyzed; a failed analysis is not inserted, so it's retried next run. Sessions with no user turns are marked done with no patterns.
- **`learner_profile`** — append-only, latest row wins: `level`, `profile` (~120-word English summary), `facts`, `topics`, `recent_sessions` (JSON lists), `created_at`.

## Prompting notes

- Conversation prompt is in Japanese; the injected profile is English with a note that replies must still be Japanese only.
- Agent style: です・ます, 2–4 sentences, react to specifics, at most one question, correct one mistake gently after responding to content, no invented personal experiences, no romaji/alphabet (output is spoken).
- Memory rule: 「覚えていることは自然なときだけ使ってください」 — use facts naturally, don't recite the profile; plus a nudge not to keep returning to old topics.
- Grammar analysis rules treat spoken Japanese leniently: casual/polite/keigo are all valid registers; natural particle drops, contractions (〜てる, 〜ちゃう), inverted order, fillers and sentence-final particles are not mistakes. `register` issues are only wrong keigo or jarring switches within one utterance. ASR errors → `asr_unclear`, no issues.
- Structured output: both Sonnet calls use `output_config={"format": {"type": "json_schema", ...}}` and raise if `stop_reason == "max_tokens"`.

### Learner profile details

- Input: recency-weighted weak spots from `grammar_feedback` (half-life 14 days, grouped by Bunpro key or issue type, top 8 with examples); correct/incorrect/unnatural ratios for the last 3 sessions vs earlier; recent session `patterns`; the full dialogue (both roles, last 150 lines each) of the last 2 sessions with ≥3 user lines (greeting-only sessions are skipped); the previous profile's `facts` and `topics`.
- Output: `level`, `profile`, `facts` (≤15, carried forward and updated when contradicted, only what the learner actually said), `topics` (≤15, most recent first), `recent_sessions` (one 1–2 sentence summary per session, newest first, with follow-up hooks).
- Level is inferred unless `JLPT_LEVEL` (e.g. `N4`) is set in `.env`.

## Running

```bash
uv run python livekit_agent_conversation.py console
```

Requires AivisSpeech running locally before starting. On a fresh machine, download the turn-detector/VAD model files once first:

```bash
uv run python livekit_agent_conversation.py download-files
```

Grammar feedback runs automatically after each session (see Data flow). To run it by hand (or rebuild only the profile with `uv run python learner_profile.py`):

```bash
uv run python grammar_police.py
```

Review mistakes: each wrong sentence (✗ incorrect, △ unnatural), its corrected version (✓), the fixes, the Bunpro grammar points, and session patterns. `-n 3` shows the last 3 analyzed sessions:

```bash
uv run python review.py
```

Testing changes to analysis/profile code: copy `transcripts.db` to a scratch path and point `grammar_police.DB_PATH` / `load_profile(db_path)` at the copy, rather than writing to the real database.

## Browser UI (local first, deployable later)

Plain HTML + JS, no framework, no build step: start/stop, agent state + mic activity, live captions, and the session's mistakes shown when it ends.

### Running locally

```bash
livekit-server --dev                                   # once: brew install livekit
uv run python livekit_agent_conversation.py dev        # AivisSpeech must be running
uv run python ui_server.py                             # open http://localhost:8000 (UI_PORT to change)
```

### Local setup

- **LiveKit server:** `brew install livekit`, `livekit-server --dev` → `ws://127.0.0.1:7880`, key/secret `devkey`/`secret` (local only; never expose).
- **Agent:** run in `dev` mode instead of `console`: `uv run python livekit_agent_conversation.py dev`. It registers with the server and is auto-dispatched into new rooms. Needs `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`.
- **`ui_server.py`**: tiny Python HTTP server that serves `index.html`, issues access tokens (`livekit-api` `AccessToken` with a room-join grant, new room name per session), and exposes the review data (reuse `review.py` query logic, return JSON) for the end-of-session view.
- **`index.html`**: `livekit-client` UMD build from a pinned CDN URL (global `LivekitClient`); fetch token → `createLocalAudioTrack()` (before joining, so a missing/denied mic is reported instead of a silent session) → `room.connect(url, token)` → `publishTrack` → attach the agent's remote audio track. Live captions via the transcription text stream (`room.registerTextStreamHandler("lk.transcription", ...)`): the agent sends one delta stream per `lk.segment_id`, the user's STT arrives as a new full-text stream per update, so the page accumulates per stream and replaces per segment. Agent state comes from the agent participant's `lk.agent.state` attribute. On disconnect, poll the review endpoint until `grammar_police.py` has finished that session.
- Mic access works on `http://localhost` without HTTPS (browsers treat localhost as a secure context).
- Keep server URL and keys in `.env`, never in code, so moving to LiveKit Cloud is a config change.

### Deployment (later)

| Piece | Local | Deployed |
|---|---|---|
| `index.html` | served by `ui_server.py` | any static host (HTTPS) |
| Token server | issues tokens to anyone | must authenticate the user first; the user ID goes into the token identity |
| LiveKit server | `livekit-server --dev` | LiveKit Cloud (simplest) or self-hosted (needs TLS + TURN) |
| Agent | `dev` on the Mac | same code in `start` mode on LiveKit Cloud agent hosting or a container host |
| TTS | local AivisSpeech | **must change**: cloud TTS or AivisSpeech on a GPU host |
| DB | SQLite, single user | Postgres; add `user_id` to `transcripts`, `grammar_feedback`, `grammar_sessions`, `learner_profile`; `load_profile` must load by user instead of "latest row" |
| Grammar analysis | detached subprocess | background job/queue |

Also needed before sharing a public link: HTTPS everywhere (mic access requires it off localhost), login, and per-user usage limits (each session costs Deepgram + Claude + TTS minutes).

### Statistics

- **Practice blocks:** stats count sessions as rows of one `session` split on >30-minute gaps, because all old console runs share `session = 'console'`. Minutes are first-to-last row of a block (audio length isn't stored).
- Heatmap shades days by user lines (quantiles of active days); streaks stay alive until a full day is missed.
- Graded turns = distinct `grammar_feedback` turns excluding `asr_unclear` (same rule as `gather_stats`); weak spots reuse `recency_weight` and its key-or-type grouping.
- "Fixed" = Bunpro keys missed ≥2 times with no miss in the last 3 blocks that had ≥5 graded turns.
- Bunpro keys are only stored on mistakes, so the JLPT chart shows levels of grammar *in mistakes*, not all grammar used.
- Chart colors are CSS variables (issue types use a validated categorical order; levels an ordinal blue ramp; the heatmap an aqua → leaf-green ramp) with light (day) and dark (night) values.

## Other planned features

- 3D avatar representation

## Env

`.env` is gitignored. Keys/config go there: `CLAUDE_API_KEY`, `DEEPGRAM_API_KEY`, optional `JLPT_LEVEL`; for the browser UI also `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
