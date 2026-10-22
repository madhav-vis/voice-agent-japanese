# Voice AI — Japanese Conversational Practice

## Project goal

A voice AI agent for improving Japanese conversational skill. Currently a learning project — the codebase is intentionally small and exploratory.

## How to work with me

**Architecture and design decisions are mine to make.** Don't suggest refactors, restructures, or new abstractions unless I ask. When I ask "what should I do", give me options and tradeoffs — not a decision.

**Guide me, don't solve for me.** When I'm debugging, walk me through what to look at and why, rather than just patching it. I want to understand what went wrong.

**No unsolicited improvements.** Don't clean up surrounding code, add error handling I didn't ask for, or introduce abstractions beyond the exact change requested.

## Stack

- **Runtime:** Python 3.9+, managed with `uv`
- **Voice pipeline:** LiveKit Agents framework (`livekit-agents`)
- **STT:** Deepgram (`nova-2`, Japanese)
- **LLM:** Ollama (local, default `qwen2.5`) via OpenAI-compatible endpoint at `http://localhost:11434/v1`
- **TTS:** VoiceVox (local, HTTP API at `http://127.0.0.1:50021`) — custom `VoicevoxTTS` plugin in `voicevox_tts.py`
- **VAD:** Silero

## Running the agent

```bash
uv run python livekit_basic_agent.py console
```

Requires VoiceVox and Ollama running locally before starting.

## Planned features (from NOTES.MD)

- 3D avatar representation
- Daily progress tracking (Anki-style heatmap)
- Transcript saving and progress tracking
- Grammar mistake mapping to Bunpro

## Env

`.env` is gitignored. Keys/config go there.
