"""Bounded utterance STT and streamed TTS; no fabricated speech fallback."""

import io
import wave
import httpx
from .runtime import Fault, require, settings


def validate_wav(audio: bytes):
    if not audio or len(audio) > 8_000_000:
        raise Fault("Send a WAV utterance of at most 8 MB.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as stream:
            duration = stream.getnframes() / stream.getframerate()
            if (
                stream.getnchannels() != 1
                or stream.getsampwidth() != 2
                or stream.getframerate() != 16000
                or not 0 < duration <= 120
            ):
                raise ValueError("Use mono 16-bit 16-kHz WAV up to 120 seconds")
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise Fault("Use mono 16-bit 16-kHz PCM WAV, up to 120 seconds.") from None


def transcribe(audio: bytes, transport=None) -> str:
    validate_wav(audio)
    key = settings().get("GROQ_API_KEY", "")
    if not key:
        raise Fault("Configure GROQ_API_KEY for speech recognition.", 503)
    try:
        with httpx.Client(transport=transport, timeout=30) as client:
            response = client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + key},
                files={"file": ("utterance.wav", audio, "audio/wav")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "language": "hi",
                    "response_format": "json",
                },
            )
        if response.status_code != 200:
            raise Fault(
                f"Speech recognition returned HTTP {response.status_code}.", 502
            )
        return require(response.json()["text"], "Recognized transcript", 2000)
    except Fault:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise Fault(
            "Speech recognition did not produce a usable transcript.", 502
        ) from None


async def synthesize(text: str, transport=None):
    text = require(text, "Speech text", 2500)
    key = settings().get("SARVAM_API_KEY", "")
    if not key:
        raise Fault("Configure SARVAM_API_KEY for Hindi speech output.", 503)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=30) as client:
            async with client.stream(
                "POST",
                "https://api.sarvam.ai/text-to-speech/stream",
                headers={"api-subscription-key": key},
                json={
                    "text": text,
                    "language_code": "hi-IN",
                    "speaker": "shubh",
                    "model": "bulbul:v3",
                    "output_audio_codec": "mp3",
                },
            ) as response:
                if response.status_code != 200:
                    raise Fault(
                        f"Speech synthesis returned HTTP {response.status_code}.", 502
                    )
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > 4_000_000:
                        raise Fault("Speech response exceeded its audio budget.", 502)
                    yield chunk
                if not total:
                    raise Fault("Speech synthesis returned empty audio.", 502)
    except Fault:
        raise
    except httpx.HTTPError:
        raise Fault(
            "Hindi speech output was interrupted by the provider.", 502
        ) from None


class SpeechEpoch:
    def __init__(self):
        self.generation = 0

    def interrupt(self):
        self.generation += 1

    async def current_chunks(self, chunks):
        generation = self.generation
        async for chunk in chunks:
            if generation != self.generation:
                return
            yield chunk
