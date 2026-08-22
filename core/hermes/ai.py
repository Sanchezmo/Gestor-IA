"""
AI Provider Abstraction - Proveedores de IA intercambiables.

ADAPTADO desde Transvega Animal - integration-api/app/core/model_router.py
"""

from pathlib import Path
from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class AIProvider(ABC):
    """Base abstracta para proveedores de IA."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generar texto."""
        pass

    @abstractmethod
    async def vision(
        self,
        image_path: str,
        prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generar texto desde imagen."""
        pass

    @abstractmethod
    async def aclose(self) -> None:
        """Cerrar conexiones."""
        pass


class OllamaProvider(AIProvider):
    """Proveedor para Ollama local."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        vision_model: str | None = None,
        default_timeout: float = 600.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.vision_model = vision_model or model
        self.default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint,
                timeout=timeout or self.default_timeout,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request_timeout = kwargs.pop("request_timeout", None)
        client = await self._get_client(timeout=request_timeout)

        format_schema = kwargs.pop("format", None)
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "temperature": temperature,
            "num_predict": max_tokens,
            **kwargs,
        }
        if format_schema:
            payload["format"] = json.loads(format_schema) if isinstance(format_schema, str) else format_schema

        resp = await client.post("/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return {"text": data.get("response", ""), "raw": data}

    async def vision(
        self,
        image_path: str,
        prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request_timeout = kwargs.pop("request_timeout", None)
        client = await self._get_client(timeout=request_timeout)

        with Path(image_path).open("rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        payload = {
            "model": model or self.vision_model,
            "prompt": prompt or "",
            "images": [b64],
            "stream": False,
            "temperature": temperature,
            "num_predict": max_tokens,
        }

        resp = await client.post("/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return {"text": data.get("response", ""), "raw": data}

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class NvidiaProvider(AIProvider):
    """Proveedor para NVIDIA NIM API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        default_timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.default_timeout,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "model": model or "meta/llama-3.1-70b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"text": text, "raw": data}

    async def vision(
        self,
        image_path: str,
        prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with Path(image_path).open("rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        client = await self._get_client()
        image_url = f"data:image/png;base64,{b64}"
        payload = {
            "model": model or "meta/llama-3.2-90b-vision-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "Describe this image."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"text": text, "raw": data}

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class OpenAIProvider(AIProvider):
    """Proveedor para OpenAI API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.default_timeout,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client = await self._get_client()
        payload = {
            "model": model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"text": text, "raw": data}

    async def vision(
        self,
        image_path: str,
        prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with Path(image_path).open("rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        client = await self._get_client()
        image_url = f"data:image/png;base64,{b64}"
        payload = {
            "model": model or "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "Describe this image."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"text": text, "raw": data}

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# =========================================================================
# FACTORY
# =========================================================================


def create_ai_provider(
    provider_type: str,
    **config,
) -> AIProvider:
    """Factory para crear proveedor de IA."""
    if provider_type == "ollama":
        return OllamaProvider(
            endpoint=config.get("endpoint", "http://127.0.0.1:11434"),
            model=config.get("model", "qwen3.5:4b"),
            vision_model=config.get("vision_model"),
            default_timeout=config.get("timeout", 600.0),
        )
    elif provider_type == "nvidia":
        return NvidiaProvider(
            api_key=config["api_key"],
            base_url=config.get("base_url", "https://integrate.api.nvidia.com/v1"),
            default_timeout=config.get("timeout", 120.0),
        )
    elif provider_type == "openai":
        return OpenAIProvider(
            api_key=config["api_key"],
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            default_timeout=config.get("timeout", 120.0),
        )
    else:
        raise ValueError(f"Unknown AI provider: {provider_type}")
