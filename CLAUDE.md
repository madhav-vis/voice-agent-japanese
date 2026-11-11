# Voice AI — Japanese Conversational Practice

## Project goal

A voice AI agent for improving Japanese conversational skill.

## Stack

- **Runtime:** Python 3.9+, managed with `uv`
- **Voice pipeline:** LiveKit Agents framework (`livekit-agents`)
- **STT:** Deepgram `nova-3` (cloud, `language="ja"`) via `livekit-plugins-deepgram`
- **LLM:** Claude Haiku 4.5 (`claude-haiku-4-5`) via `livekit-plugins-anthropic`, key from `CLAUDE_API_KEY`
- **TTS:** AivisSpeech (local, VOICEVOX-compatible HTTP API at `http://127.0.0.1:10101`, speaker コハク/ノーマル `1878365376`) — custom `VoicevoxTTS` plugin in `voicevox_tts.py` (also works with VoiceVox on port 50021)
- **VAD:** Silero
- **Turn detection:** LiveKit `MultilingualModel` (supports Japanese) — reads the transcript to tell a mid-sentence pause from a finished turn; endpointing waits 0.5–5.0s
- **Grammar feedback:** `grammar_police.py` — Claude Sonnet 5.5 (`claude-sonnet-5-5`) reviews user turns in `transcripts.db`, maps mistakes to `bunpro_grammar_points.csv`, writes to the `grammar_feedback` table (including the turn's text and a full corrected sentence); one API call per session, tracked (with session-level patterns) in `grammar_sessions`
- **Learner profile:** `learner_profile.py` — one Sonnet call over recency-weighted SQL stats from `grammar_feedback`, the full dialogue of the last 2 sessions with at least 3 user lines, and the previous profile's facts/topics. It writes a ~120-word English level/weak-points summary plus `facts` (stable personal facts, carried over from the previous profile), `topics` (topics already covered) and `recent_sessions` (one summary per recent session), stored as a new row in `learner_profile` (latest row wins). Rebuilt automatically after `grammar_police.py` analyzes a session; injected into the agent's instructions under 「# ユーザーについて」 at session start. Level is inferred unless `JLPT_LEVEL` (e.g. `N4`) is set in `.env`
- **Unused local fallback:** `kotoba_stt.py` (kotoba-whisper-v2.0 on MPS) is still in the repo but not wired in

## Running the agent

```bash
uv run python livekit_agent_conversation.py console
```

Requires AivisSpeech running locally before starting. On a fresh machine, download the turn-detector/VAD model files once first:

```bash
uv run python livekit_agent_conversation.py download-files
```

Transcripts are saved to `transcripts.db` (SQLite; don't commit it — it contains personal conversations) when a session ends.

Grammar feedback runs automatically when a session ends: the agent starts `grammar_police.py` as a detached background process (output appended to `grammar_police.log`, with a file lock so only one run happens at a time). It only analyzes sessions not yet in `grammar_sessions`, retries failed sessions on the next run, and rebuilds the learner profile afterwards. To run it by hand (or rebuild only the profile with `uv run python learner_profile.py`):

```bash
uv run python grammar_police.py
```

Review mistakes (each wrong sentence, its corrected version, the fixes, and the Bunpro grammar points; `-n 3` for the last 3 analyzed sessions). Sessions analyzed before the `corrected` column existed show the fixes without a full corrected sentence:

```bash
uv run python review.py
```

## Planned features 

- 3D avatar representation
- Daily progress tracking (Anki-style heatmap)
- Transcript saving and progress tracking
- Grammar mistake mapping to Bunpro

## Env

`.env` is gitignored. Keys/config go there.
