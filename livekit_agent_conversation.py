from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, tts
from livekit.agents.llm import function_tool
from livekit.plugins import anthropic, deepgram, silero
from datetime import datetime
from voicevox_tts import VoicevoxTTS, JapaneseSentenceTokenizer
import os, asyncio, sqlite3

#load env
load_dotenv(".env")

class Assistant(Agent):
    """Basic voice agent"""

    def __init__(self):
        super().__init__(
            instructions="""# 役割
            あなたは日本語を勉強している人の会話練習の相手です。
            目的は、ユーザーが日本語でたくさん話せるようになることです。

            # 話し方
            - 必ず日本語だけで話してください。英語のアルファベットやローマ字は使わないでください。カタカナや和製英語は問題ありません。
            - やさしい言葉と短い文を使って、です・ます体で話してください。
            - 返事はとても短くしてください。基本は一文か二文です。長い説明や前置きはしないでください。
            - 友達と話すように、テンポよく自然に返事してください。
            - 返事の最後に、答えやすい質問を一つだけしてください。

            # 間違いの直し方
            - ユーザーの日本語に間違いがあったら、まず話の内容にちゃんと返事をしてください。
            - そのあと、やさしく一言で正しい言い方を教えてください。
              例：ユーザー「昨日、映画を見るました」→「いいですね！ちなみに『見るました』じゃなくて『見ました』ですよ。何の映画を見ましたか？」
            - 一回の返事で直す間違いは一つだけにしてください。一番大事な間違いを選んでください。
            - ユーザーの言葉は音声認識で文字になっているので、認識ミスがあるかもしれません。
              意味がわからないときは、直さずに「もう一度言ってもらえますか？」と聞いてください。

            # 出力のルール（声で読み上げます）
            - 普通の文だけで答えてください。箇条書き、記号、絵文字は使わないでください。
            - 漢字の読み方を括弧で書かないでください。"""
        )

async def entrypoint(ctx: agents.JobContext):
    """Entry point for the agent."""

    #Configure the voice pipeline
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="ja"),
        llm=anthropic.LLM(
            model="claude-haiku-4-5",
            api_key=os.getenv("CLAUDE_API_KEY"),  # plugin defaults to ANTHROPIC_API_KEY
        ),
        tts=tts.StreamAdapter(  #text to speech, synthesized chunk by chunk in order
            tts=VoicevoxTTS(speaker=46),
            sentence_tokenizer=JapaneseSentenceTokenizer(),
        ),
        vad=silero.VAD.load(),
    )
    
    #start the session
    await session.start(
        room = ctx.room,
        agent=Assistant()
    )

    async def save_transcript():
        con = sqlite3.connect("transcripts.db")
        con.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                session   TEXT,
                role      TEXT,
                text      TEXT,
                interrupted INTEGER,
                created_at  REAL
            )
        """)
        session_id = ctx.room.name
        for msg in session.history.messages():
            if msg.role not in ("user", "assistant"):
                continue
            con.execute(
                "INSERT INTO transcripts (session, role, text, interrupted, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, msg.role, msg.text_content, int(msg.interrupted), msg.created_at),
            )
        con.commit()
        con.close()

    ctx.add_shutdown_callback(save_transcript)

    #generate intial greeting
    await session.generate_reply(
        instructions="""短く挨拶して、日本語の会話練習を手伝えてうれしいと伝えてください。
        質問はしないでください。
        例：「こんにちは！今日は一緒に日本語で話しましょう。」
        毎回、例と同じ言い方にしないでください。"""
    )

if __name__ == "__main__":
    #run the agent
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))