from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import openai, deepgram, silero 
from datetime import datetime
from voicevox_tts import VoicevoxTTS
import os, asyncio

#load env
load_dotenv(".env")

class Assistant(Agent):
    """Basic voice agent"""

    def __init__(self):
        super().__init__(
            instructions="""あなたは親切なボイスアシスタントです。
            自然な会話のように、簡潔に話してください。
            必ず全て日本語で答えてください。英語のアルファベットやローマ字は絶対に使わないでください。
            カタカナや和製英語は問題ありません。"""
        )

async def entrypoint(ctx: agents.JobContext):
    """Entry point for the agent."""

    #Configure the voice pipeline
    session = AgentSession(
        stt=deepgram.STT(model="nova-2", language="ja"), #speech to text
        llm=openai.LLM(
            model=os.getenv("LLM_CHOICE", "qwen2.5"),
            base_url="http://localhost:11434/v1",
            api_key="ollama",  # Ollama doesn't require a real key, but the client needs a non-empty string
        ),
        tts=VoicevoxTTS(speaker=109),  #text to speech
        vad=silero.VAD.load(),
    )
    
    #start the session
    await session.start(
        room = ctx.room,
        agent=Assistant()
    )

    #generate intial greeting
    await session.generate_reply(
        instructions="日本語でやさしい挨拶をして、どのようにお役に立てるか聞いてください。"
    )

if __name__ == "__main__":
    #run the agent
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))