from abc import ABC, abstractmethod


class STTAdapter(ABC):
    """Abstract base for speech-to-text providers."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/webm") -> str:
        """Transcribe audio bytes to text. Raises RuntimeError on failure."""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this adapter has the necessary credentials configured."""
        ...
