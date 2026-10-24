import asyncio
import time
import numpy as np
from math import gcd

from livekit import rtc
from livekit.agents import stt
from livekit.agents.utils import AudioBuffer
from livekit.agents.utils import shortuuid
from livekit.agents.types import NOT_GIVEN, NotGivenOr, APIConnectOptions


class KotobaSTT(stt.STT):
    def __init__(self, *, device: str = "mps"):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._device = device
        self._pipe = None
        self._load_lock: asyncio.Lock | None = None

    @property
    def model(self) -> str:
        return "kotoba-whisper-v2.0"

    @property
    def provider(self) -> str:
        return "kotoba-tech"

    def _load_model(self) -> None:
        import torch
        from transformers import pipeline as hf_pipeline

        dtype = torch.float32 if self._device == "cpu" else torch.float16
        self._pipe = hf_pipeline(
            "automatic-speech-recognition",
            model="kotoba-tech/kotoba-whisper-v2.0",
            dtype=dtype,
            device=self._device,
            model_kwargs={"attn_implementation": "eager"},
        )

    async def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        async with self._load_lock:
            if self._pipe is None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._load_model)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        await self._ensure_loaded()

        frame = rtc.combine_audio_frames(buffer)

        # int16 PCM bytes → float32 numpy array normalised to [-1, 1]
        audio = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0

        # stereo → mono
        if frame.num_channels > 1:
            audio = audio.reshape(-1, frame.num_channels).mean(axis=1)

        # resample to 16 kHz if needed (Whisper expects 16 kHz)
        if frame.sample_rate != 16000:
            from scipy.signal import resample_poly
            g = gcd(frame.sample_rate, 16000)
            audio = resample_poly(audio, 16000 // g, frame.sample_rate // g)

        audio_input = {"array": audio, "sampling_rate": 16000}
        generate_kwargs = {"language": "japanese", "task": "transcribe"}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._pipe(audio_input, generate_kwargs=generate_kwargs),
        )

        text = result.get("text", "").strip()

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=shortuuid(),
            alternatives=[
                stt.SpeechData(
                    language="ja",
                    text=text,
                    start_time=0.0,
                    end_time=float(len(audio)) / 16000,
                    confidence=1.0,
                )
            ],
        )
