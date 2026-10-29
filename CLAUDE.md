# Voice AI — Japanese Conversational Practice

## Project goal

A voice AI agent for improving Japanese conversational skill.

## Stack

- **Runtime:** Python 3.9+, managed with `uv`
- **Voice pipeline:** LiveKit Agents framework (`livekit-agents`)
- **STT:** Deepgram `nova-3` (cloud, `language="ja"`) via `livekit-plugins-deepgram`
- **LLM:** Claude Haiku 4.5 (`claude-haiku-4-5`) via `livekit-plugins-anthropic`, key from `CLAUDE_API_KEY`
- **TTS:** VoiceVox (local, HTTP API at `http://127.0.0.1:50021`) — custom `VoicevoxTTS` plugin in `voicevox_tts.py`
- **VAD:** Silero
- **Turn detection:** LiveKit `MultilingualModel` (supports Japanese) — reads the transcript to tell a mid-sentence pause from a finished turn; endpointing waits 0.5–5.0s
- **Grammar feedback:** `grammar_police.py` — Claude Sonnet 5.5 (`claude-sonnet-5-5`) reviews user turns in `transcripts.db`, maps mistakes to `bunpro_grammar_points.csv`, writes to the `grammar_feedback` table; one API call per session, tracked (with session-level patterns) in `grammar_sessions`
- **Learner profile:** `learner_profile.py` — recency-weighted SQL stats over `grammar_feedback` + a Sonnet-written ~120-word English summary, stored as a new row in `learner_profile` (latest row wins). Rebuilt automatically after `grammar_police.py` analyzes a session; loaded into the agent's instructions at session start. Level is inferred unless `JLPT_LEVEL` (e.g. `N4`) is set in `.env`
- **Unused local fallback:** `kotoba_stt.py` (kotoba-whisper-v2.0 on MPS) is still in the repo but not wired in

## Running the agent

```bash
uv run python livekit_agent_conversation.py console
```

Requires VoiceVox  running locally before starting. On a fresh machine, download the turn-detector/VAD model files once first:

```bash
uv run python livekit_agent_conversation.py download-files
```

Transcripts are saved to `transcripts.db` (SQLite; don't commit it — it contains personal conversations) when a session ends.

Grammar feedback (run after a session; only analyzes sessions not yet in `grammar_sessions`; failed sessions are retried next run; rebuilds the learner profile afterwards — or rebuild it alone with `uv run python learner_profile.py`):

```bash
uv run python grammar_police.py
```

## Planned features 

- 3D avatar representation
- Daily progress tracking (Anki-style heatmap)
- Transcript saving and progress tracking
- Grammar mistake mapping to Bunpro

## Env

`.env` is gitignored. Keys/config go there.
