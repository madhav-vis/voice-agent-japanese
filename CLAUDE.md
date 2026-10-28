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
- **Grammar feedback:** `grammar_police.py` — Claude Sonnet 5.5 (`claude-sonnet-5-5`) reviews user turns in `transcripts.db`, maps mistakes to `bunpro_grammar_points.csv`, writes to the `grammar_feedback` table
- **Unused local fallback:** `kotoba_stt.py` (kotoba-whisper-v2.0 on MPS) is still in the repo but not wired in

## Running the agent

```bash
uv run python livekit_agent_conversation.py console
```

Requires VoiceVox  running locally before starting. 

Transcripts are saved to `transcripts.db` (SQLite; don't commit it — it contains personal conversations) when a session ends.

Grammar feedback (run after a session; only analyzes new transcript rows):

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
