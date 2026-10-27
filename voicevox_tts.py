import io, re, wave
import aiohttp
from livekit.agents import tts, tokenize
from livekit.agents.tokenize import token_stream
from livekit.agents.utils import shortuuid
from livekit import rtc

# cut after 。、！？ (plus any closing bracket), keeping the punctuation with the chunk
_JA_CHUNK = re.compile(r".+?(?:[。、！？!?\n]+[」』）)]*|$)", re.S)

def split_japanese(text: str) -> list[str]:
    return [c for c in _JA_CHUNK.findall(text) if c.strip()]

class JapaneseSentenceTokenizer(tokenize.SentenceTokenizer):
    def __init__(self, min_chunk_len: int = 4):
        self._min_chunk_len = min_chunk_len

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        return split_japanese(text)

    def stream(self, *, language: str | None = None) -> tokenize.SentenceStream:
        return token_stream.BufferedSentenceStream(
            tokenizer=split_japanese,
            min_token_len=self._min_chunk_len,
            min_ctx_len=self._min_chunk_len,
        )

class VoicevoxTTS(tts.TTS):
    def __init__(self, speaker: int = 1, base_url: str = "http://127.0.0.1:50021"):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )
        self._speaker = speaker
        self._base_url = base_url

    def synthesize(self, text: str, *, conn_options=None) -> "tts.ChunkedStream":
        return VoicevoxStream(self, text, conn_options=conn_options)

class VoicevoxStream(tts.ChunkedStream):
    def __init__(self, tts_instance, text, *, conn_options=None):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)

    async def _run(self, output_emitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/pcm",
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._tts._base_url}/audio_query",
                params={"text": self._input_text, "speaker": self._tts._speaker}
            ) as r:
                query = await r.json()

            async with session.post(
                f"{self._tts._base_url}/synthesis",
                params={"speaker": self._tts._speaker},
                json=query
            ) as r:
                wav_bytes = await r.read()

        with io.BytesIO(wav_bytes) as buf:
            with wave.open(buf) as wf:
                sample_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                pcm = wf.readframes(wf.getnframes())

        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=sample_rate,
            num_channels=n_channels,
            samples_per_channel=len(pcm) // (2 * n_channels),
        )
        output_emitter.push_frame(frame)
        output_emitter.flush()
