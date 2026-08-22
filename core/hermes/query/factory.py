from __future__ import annotations

"""
Factory para crear intérpretes de intención configurados por instancia.

Utiliza InstanceConfig.ai para obtener modelo, endpoint, policy, etc.
"""

from typing import Any

from core.hermes.ai import create_ai_provider
from core.hermes.instance_config import AIConfig, InstanceConfig
from core.hermes.query.interpreter import (
    CompositeIntentInterpreter,
    DeterministicIntentInterpreter,
    IntentInterpreter,
    OllamaIntentInterpreter,
)


def create_deterministic_interpreter() -> DeterministicIntentInterpreter:
    """Crear intérprete determinista (siempre disponible, sin config)."""
    return DeterministicIntentInterpreter()


def create_ollama_interpreter(
    instance_config: InstanceConfig,
    timeout: float = 30.0,
) -> OllamaIntentInterpreter | None:
    """
    Crear intérprete Ollama para una instancia específica.

    Args:
        instance_config: Configuración de la instancia (contiene ai.ollama_model, etc.)
        timeout: Timeout en segundos para llamadas a Ollama

    Returns:
        OllamaIntentInterpreter o None si la configuración no permite Ollama
    """
    ai_config: AIConfig = instance_config.ai

    # Verificar que el modelo esté configurado
    if not ai_config.ollama_model:
        return None

    # Verificar política LOCAL_ONLY para consultas de terceros
    # (por defecto LOCAL_ONLY, pero se puede verificar explícitamente)
    if ai_config.default_policy.value != "LOCAL_ONLY":
        # Si la política permite cloud, podríamos usar proveedor externo
        # Pero para esta fase, solo Ollama local
        pass

    # Crear provider Ollama
    provider = create_ai_provider(
        "ollama",
        endpoint=ai_config.ollama_endpoint,
        model=ai_config.ollama_model,
        vision_model=ai_config.ollama_vision_model,
        timeout=timeout,
    )

    return OllamaIntentInterpreter(
        ai_provider=provider,
        model=ai_config.ollama_model,
        timeout=timeout,
        temperature=0.1,
    )


def create_intent_interpreter(
    instance_config: InstanceConfig,
    timeout: float = 30.0,
    use_ollama: bool = True,
) -> IntentInterpreter:
    """
    Crear intérprete compuesto (parser-first con fallback a Ollama).

    Args:
        instance_config: Configuración de la instancia
        timeout: Timeout para Ollama
        use_ollama: Si intentar crear intérprete Ollama (default True)

    Returns:
        CompositeIntentInterpreter listo para usar
    """
    deterministic = create_deterministic_interpreter()

    ollama = None
    if use_ollama:
        ollama = create_ollama_interpreter(instance_config, timeout)

    return CompositeIntentInterpreter(
        deterministic=deterministic,
        ollama=ollama,
    )


def create_interpreter_for_company_context(
    company_context: Any,  # CompanyContext - evitar import circular
    timeout: float = 30.0,
    use_ollama: bool = True,
) -> IntentInterpreter:
    """
    Crear intérprete para un CompanyContext específico.

    Args:
        company_context: Contexto de empresa ya resuelto
        timeout: Timeout para Ollama
        use_ollama: Si intentar crear intérprete Ollama

    Returns:
        CompositeIntentInterpreter
    """
    return create_intent_interpreter(
        instance_config=company_context.instance_config,
        timeout=timeout,
        use_ollama=use_ollama,
    )
