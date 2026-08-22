"""
Model Router + AI Policy - Routing entre proveedores según privacidad.

ADAPTADO desde Transvega Animal - integration-api/app/core/model_router.py
"""

from typing import Any

import structlog

from core.hermes.ai import NvidiaProvider, OllamaProvider, OpenAIProvider
from core.hermes.instance_config import AIConfig, AIPolicyScope

logger = structlog.get_logger()


class ModelRouter:
    """
    Router que selecciona proveedor según AIPolicyScope.

    LOCAL_ONLY -> Ollama (local, nunca sale del servidor)
    CLOUD_ALLOWED -> NVIDIA/OpenAI (cloud, para tareas públicas)
    """

    def __init__(
        self,
        ollama: OllamaProvider,
        nvidia: NvidiaProvider | None = None,
        openai: OpenAIProvider | None = None,
    ) -> None:
        self.ollama = ollama
        self.nvidia = nvidia
        self.openai = openai
        self.logger = logger.bind(component="ModelRouter")

        # Validar que hay al menos un proveedor cloud si se permite CLOUD_ALLOWED
        self._has_cloud = nvidia is not None or openai is not None

    async def generate(
        self,
        *,
        privacy_scope: AIPolicyScope,
        prompt: str,
        model: str | None = None,
        preferred_cloud: str = "nvidia",  # "nvidia" | "openai"
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.logger.debug("routing_generate", privacy_scope=privacy_scope, prompt_len=len(prompt))

        if privacy_scope == AIPolicyScope.LOCAL_ONLY:
            return await self.ollama.generate(prompt, model=model, **kwargs)

        elif privacy_scope == AIPolicyScope.CLOUD_ALLOWED:
            if not self._has_cloud:
                self.logger.warning("no_cloud_provider_configured_falling_back_to_local")
                return await self.ollama.generate(prompt, model=model, **kwargs)

            # Preferir NVIDIA, fallback a OpenAI
            if preferred_cloud == "nvidia" and self.nvidia:
                return await self.nvidia.generate(prompt, model=model, **kwargs)
            elif preferred_cloud == "openai" and self.openai:
                return await self.openai.generate(prompt, model=model, **kwargs)
            elif self.nvidia:
                return await self.nvidia.generate(prompt, model=model, **kwargs)
            elif self.openai:
                return await self.openai.generate(prompt, model=model, **kwargs)
            else:
                return await self.ollama.generate(prompt, model=model, **kwargs)

        else:
            raise ValueError(f"Unknown privacy scope: {privacy_scope}")

    async def vision(
        self,
        *,
        privacy_scope: AIPolicyScope,
        image_path: str,
        prompt: str | None = None,
        model: str | None = None,
        preferred_cloud: str = "nvidia",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.logger.debug("routing_vision", privacy_scope=privacy_scope, image_path=image_path)

        if privacy_scope == AIPolicyScope.LOCAL_ONLY:
            return await self.ollama.vision(image_path, prompt, model=model, **kwargs)

        elif privacy_scope == AIPolicyScope.CLOUD_ALLOWED:
            if not self._has_cloud:
                self.logger.warning("no_cloud_provider_configured_falling_back_to_local")
                return await self.ollama.vision(image_path, prompt, model=model, **kwargs)

            if preferred_cloud == "nvidia" and self.nvidia:
                return await self.nvidia.vision(image_path, prompt, model=model, **kwargs)
            elif preferred_cloud == "openai" and self.openai:
                return await self.openai.vision(image_path, prompt, model=model, **kwargs)
            elif self.nvidia:
                return await self.nvidia.vision(image_path, prompt, model=model, **kwargs)
            elif self.openai:
                return await self.openai.vision(image_path, prompt, model=model, **kwargs)
            else:
                return await self.ollama.vision(image_path, prompt, model=model, **kwargs)

        else:
            raise ValueError(f"Unknown privacy scope: {privacy_scope}")

    async def aclose(self) -> None:
        await self.ollama.aclose()
        if self.nvidia:
            await self.nvidia.aclose()
        if self.openai:
            await self.openai.aclose()


def create_model_router_from_config(ai_config: AIConfig) -> ModelRouter:
    """Crear ModelRouter desde InstanceConfig.ai."""

    # Ollama (siempre disponible - local)
    ollama = OllamaProvider(
        endpoint=ai_config.ollama_endpoint,
        model=ai_config.ollama_model,
        vision_model=ai_config.ollama_vision_model,
        default_timeout=600.0,
    )

    # NVIDIA (opcional - cloud)
    nvidia = None
    if ai_config.nvidia_api_key:
        nvidia = NvidiaProvider(
            api_key=ai_config.nvidia_api_key,
            base_url=ai_config.nvidia_base_url,
        )

    # OpenAI (opcional - cloud)
    openai = None
    if ai_config.openai_api_key:
        openai = OpenAIProvider(
            api_key=ai_config.openai_api_key,
            base_url=ai_config.openai_base_url,
        )

    return ModelRouter(ollama=ollama, nvidia=nvidia, openai=openai)


# =========================================================================
# AI POLICY - Decide LOCAL vs CLOUD por tarea/sensibilidad
# =========================================================================


class AIPolicy:
    """
    Política de IA para decidir routing basado en:
    - Instancia (config.default_policy)
    - Tarea (config.task_policies)
    - Sensibilidad de datos
    - Capacidad requerida
    """

    def __init__(self, ai_config: AIConfig):
        self.config = ai_config

    def get_scope_for_task(self, task: str) -> AIPolicyScope:
        """Obtener política para una tarea específica."""
        return self.config.task_policies.get(task, self.config.default_policy)

    def get_scope_for_data(self, data_sensitivity: str) -> AIPolicyScope:
        """
        Obtener política basada en sensibilidad de datos.

        Niveles:
        - "high": facturas, datos clientes, documentos internos -> LOCAL_ONLY
        - "medium": datos operacionales -> LOCAL_ONLY (por defecto)
        - "low": contenido público, marketing -> CLOUD_ALLOWED
        """
        if data_sensitivity == "high":
            return AIPolicyScope.LOCAL_ONLY
        elif data_sensitivity == "low":
            return AIPolicyScope.CLOUD_ALLOWED
        else:
            return self.config.default_policy

    def can_use_cloud(self, task: str, data_sensitivity: str = "medium") -> bool:
        """Verificar si se puede usar proveedor cloud para esta tarea/datos."""
        task_scope = self.get_scope_for_task(task)
        data_scope = self.get_scope_for_data(data_sensitivity)

        # La más restrictiva gana
        if task_scope == AIPolicyScope.LOCAL_ONLY or data_scope == AIPolicyScope.LOCAL_ONLY:
            return False
        return True

    def get_preferred_provider(self, task: str) -> str:
        """Obtener proveedor cloud preferido para una tarea."""
        # Por defecto NVIDIA para tareas técnicas, OpenAI para creativas
        creative_tasks = {"content_generation", "marketing", "translation", "creative_writing"}
        if task in creative_tasks:
            return "openai"
        return "nvidia"


# =========================================================================
# DEPENDENCY PARA FASTAPI
# =========================================================================


def get_ai_policy(ctx: "CompanyContext" = None) -> AIPolicy:
    """FastAPI dependency para AIPolicy de la instancia actual."""
    if ctx is None:

        raise RuntimeError("get_ai_policy requiere CompanyContext")
    return AIPolicy(ctx.ai_config)


def get_model_router(ctx: "CompanyContext" = None) -> ModelRouter:
    """FastAPI dependency para ModelRouter de la instancia actual."""
    if ctx is None:

        raise RuntimeError("get_model_router requiere CompanyContext")
    return create_model_router_from_config(ctx.ai_config)
