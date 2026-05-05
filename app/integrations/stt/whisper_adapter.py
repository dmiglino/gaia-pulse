import io
import logging

import httpx

from app.core.config import get_settings
from app.integrations.stt.base import STTAdapter

logger = logging.getLogger(__name__)
settings = get_settings()


class WhisperSTTAdapter(STTAdapter):
    """OpenAI Whisper API adapter for speech-to-text."""

    @property
    def is_available(self) -> bool:
        return bool(settings.effective_stt_key)

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/webm") -> str:
        if not self.is_available:
            raise RuntimeError("STT not configured: set OPENAI_API_KEY or STT_API_KEY")

        # Determine file extension from content type
        ext_map = {
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mp4": "mp4",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
        }
        ext = ext_map.get(content_type, "webm")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.openai_base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.effective_stt_key}"},
                files={
                    "file": (f"audio.{ext}", io.BytesIO(audio_bytes), content_type),
                    "model": (None, "whisper-1"),
                    "language": (None, "es"),  # Default to Spanish; auto-detects well
                    "response_format": (None, "text"),
                },
            )

        if response.status_code != 200:
            logger.error("Whisper API error: %s %s", response.status_code, response.text)
            raise RuntimeError(f"STT failed: {response.status_code}")

        return response.text.strip()


def get_stt_adapter() -> STTAdapter:
    return WhisperSTTAdapter()
