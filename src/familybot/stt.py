from __future__ import annotations

from pathlib import Path
import httpx


class SpeechToText:
    """Cloud speech-to-text using the OpenAI Audio Transcriptions API."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini-transcribe", language: str = "de"):
        self.model = model
        self.language = language
        self.client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )

    async def close(self):
        await self.client.aclose()

    async def transcribe(self, path: Path) -> str:
        with path.open("rb") as audio:
            response = await self.client.post(
                "/audio/transcriptions",
                data={
                    "model": self.model,
                    "language": self.language,
                },
                files={"file": (path.name, audio, "audio/ogg")},
            )
        response.raise_for_status()
        return response.json().get("text", "").strip()
