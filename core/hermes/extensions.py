"""
Extension Registry - Registro de capacidades por instancia.

Cada empresa registra sus agents, tools, workflows, prompts.
El Core NO conoce implementaciones específicas.
"""

from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import structlog

logger = structlog.get_logger()


@dataclass
class AgentSpec:
    """Especificación de un agente."""
    name: str
    factory: Callable  # (config: dict) -> Agent
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass
class ToolSpec:
    """Especificación de una herramienta."""
    name: str
    func: Callable  # Función async que implementa la tool
    description: str = ""
    parameters: dict = field(default_factory=dict)  # JSON Schema
    requires_approval: bool = False
    allowed_instances: list[str] = field(default_factory=list)  # Vacío = todas


@dataclass
class WorkflowSpec:
    """Especificación de un workflow."""
    name: str
    steps: list[dict]  # Definición de pasos
    description: str = ""
    trigger: str = "manual"  # manual, scheduled, webhook, event
    requires_approval: bool = False


class ExtensionRegistry:
    """
    Registro global de extensiones por instancia.
    
    - Agents, Tools, Workflows se registran POR INSTANCIA
    - El Core usa el registry para descubrir qué está disponible
    - NO hay framework de plugins complejo - simple dict por instance_id
    """
    
    def __init__(self):
        self._agents: dict[str, dict[str, AgentSpec]] = defaultdict(dict)
        self._tools: dict[str, dict[str, ToolSpec]] = defaultdict(dict)
        self._workflows: dict[str, dict[str, WorkflowSpec]] = defaultdict(dict)
        self._prompts: dict[str, dict[str, str]] = defaultdict(dict)
    
    # =========================================================================
    # AGENTS
    # =========================================================================
    
    def register_agent(
        self,
        instance_id: str,
        name: str,
        factory: Callable,
        description: str = "",
        capabilities: list[str] | None = None,
        restrictions: list[str] | None = None,
        requires_approval: bool = False,
    ) -> None:
        """Registrar agente para una instancia."""
        spec = AgentSpec(
            name=name,
            factory=factory,
            description=description,
            capabilities=capabilities or [],
            restrictions=restrictions or [],
            requires_approval=requires_approval,
        )
        self._agents[instance_id][name] = spec
        logger.info("agent_registered", instance_id=instance_id, agent=name)
    
    def get_agent(self, instance_id: str, name: str) -> Optional[AgentSpec]:
        """Obtener spec de agente."""
        return self._agents.get(instance_id, {}).get(name)
    
    def list_agents(self, instance_id: str) -> list[AgentSpec]:
        """Listar agentes disponibles para una instancia."""
        return list(self._agents.get(instance_id, {}).values())
    
    def create_agent(self, instance_id: str, name: str, config: dict) -> Any:
        """Instanciar agente para una instancia."""
        spec = self.get_agent(instance_id, name)
        if not spec:
            raise ValueError(f"Agent '{name}' not registered for instance '{instance_id}'")
        return spec.factory(config)
    
    # =========================================================================
    # TOOLS
    # =========================================================================
    
    def register_tool(
        self,
        instance_id: str,
        name: str,
        func: Callable,
        description: str = "",
        parameters: dict | None = None,
        requires_approval: bool = False,
        allowed_instances: list[str] | None = None,
    ) -> None:
        """Registrar herramienta para una instancia."""
        spec = ToolSpec(
            name=name,
            func=func,
            description=description,
            parameters=parameters or {},
            requires_approval=requires_approval,
            allowed_instances=allowed_instances or [],
        )
        self._tools[instance_id][name] = spec
        logger.info("tool_registered", instance_id=instance_id, tool=name)
    
    def get_tool(self, instance_id: str, name: str) -> Optional[ToolSpec]:
        """Obtener spec de herramienta."""
        return self._tools.get(instance_id, {}).get(name)
    
    def list_tools(self, instance_id: str) -> list[ToolSpec]:
        """Listar herramientas disponibles para una instancia."""
        return list(self._tools.get(instance_id, {}).values())
    
    async def call_tool(self, instance_id: str, name: str, **kwargs) -> Any:
        """Ejecutar herramienta para una instancia."""
        spec = self.get_tool(instance_id, name)
        if not spec:
            raise ValueError(f"Tool '{name}' not registered for instance '{instance_id}'")
        return await spec.func(**kwargs)
    
    # =========================================================================
    # WORKFLOWS
    # =========================================================================
    
    def register_workflow(
        self,
        instance_id: str,
        name: str,
        steps: list[dict],
        description: str = "",
        trigger: str = "manual",
        requires_approval: bool = False,
    ) -> None:
        """Registrar workflow para una instancia."""
        spec = WorkflowSpec(
            name=name,
            steps=steps,
            description=description,
            trigger=trigger,
            requires_approval=requires_approval,
        )
        self._workflows[instance_id][name] = spec
        logger.info("workflow_registered", instance_id=instance_id, workflow=name)
    
    def get_workflow(self, instance_id: str, name: str) -> Optional[WorkflowSpec]:
        return self._workflows.get(instance_id, {}).get(name)
    
    def list_workflows(self, instance_id: str) -> list[WorkflowSpec]:
        return list(self._workflows.get(instance_id, {}).values())
    
    # =========================================================================
    # PROMPTS
    # =========================================================================
    
    def register_prompt(self, instance_id: str, name: str, template: str) -> None:
        """Registrar prompt template para una instancia."""
        self._prompts[instance_id][name] = template
        logger.info("prompt_registered", instance_id=instance_id, prompt=name)
    
    def get_prompt(self, instance_id: str, name: str) -> Optional[str]:
        return self._prompts.get(instance_id, {}).get(name)
    
    def list_prompts(self, instance_id: str) -> list[str]:
        return list(self._prompts.get(instance_id, {}).keys())
    
    def render_prompt(self, instance_id: str, name: str, **kwargs) -> str:
        """Renderizar prompt con variables."""
        template = self.get_prompt(instance_id, name)
        if not template:
            raise ValueError(f"Prompt '{name}' not found for instance '{instance_id}'")
        return template.format(**kwargs)
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def get_instance_summary(self, instance_id: str) -> dict:
        """Resumen de extensiones de una instancia."""
        return {
            "agents": {name: {"description": s.description, "capabilities": s.capabilities} 
                       for name, s in self._agents.get(instance_id, {}).items()},
            "tools": {name: {"description": s.description} 
                      for name, s in self._tools.get(instance_id, {}).items()},
            "workflows": {name: {"description": s.description, "trigger": s.trigger} 
                          for name, s in self._workflows.get(instance_id, {}).items()},
            "prompts": list(self._prompts.get(instance_id, {}).keys()),
        }
    
    def clear_instance(self, instance_id: str) -> None:
        """Limpiar todas las extensiones de una instancia (para tests/reload)."""
        self._agents.pop(instance_id, None)
        self._tools.pop(instance_id, None)
        self._workflows.pop(instance_id, None)
        self._prompts.pop(instance_id, None)
        logger.info("instance_extensions_cleared", instance_id=instance_id)
    
    def list_instances(self) -> list[str]:
        """Listar instance_ids que tienen extensiones registradas."""
        all_ids = set()
        all_ids.update(self._agents.keys())
        all_ids.update(self._tools.keys())
        all_ids.update(self._workflows.keys())
        all_ids.update(self._prompts.keys())
        return sorted(all_ids)


# Instancia global
extension_registry = ExtensionRegistry()


# =========================================================================
# HELPERS PARA REGISTRO DESDE INSTANCE CONFIG
# =========================================================================

def load_extensions_from_config(instance_config: "InstanceConfig", registry: ExtensionRegistry = None) -> None:
    """
    Cargar extensiones declaradas en InstanceConfig.
    
    InstanceConfig.enabled_agents = ["invoice_processing", "custom_agent"]
    InstanceConfig.enabled_tools = ["dolibarr_search", "pdf_extract"]
    InstanceConfig.enabled_workflows = ["invoice_approval", "monthly_report"]
    
    Esta función importa dinámicamente y registra.
    """
    if registry is None:
        registry = extension_registry
    
    instance_id = instance_config.instance_id
    
    # Cargar agents
    for agent_name in instance_config.enabled_agents:
        try:
            # Buscar en companies/{instance_id}/agents/ o core agents
            agent_module = _import_agent(agent_name, instance_id)
            if agent_module and hasattr(agent_module, "create_agent"):
                registry.register_agent(
                    instance_id=instance_id,
                    name=agent_name,
                    factory=agent_module.create_agent,
                    description=getattr(agent_module, "__doc__", ""),
                )
        except Exception as e:
            logger.warning("failed_to_load_agent", instance_id=instance_id, agent=agent_name, error=str(e))
    
    # Cargar tools
    for tool_name in instance_config.enabled_tools:
        try:
            tool_func = _import_tool(tool_name, instance_id)
            if tool_func:
                registry.register_tool(
                    instance_id=instance_id,
                    name=tool_name,
                    func=tool_func,
                )
        except Exception as e:
            logger.warning("failed_to_load_tool", instance_id=instance_id, tool=tool_name, error=str(e))
    
    # Cargar workflows
    for wf_name in instance_config.enabled_workflows:
        try:
            wf_def = _import_workflow(wf_name, instance_id)
            if wf_def:
                registry.register_workflow(
                    instance_id=instance_id,
                    name=wf_name,
                    steps=wf_def.get("steps", []),
                    description=wf_def.get("description", ""),
                    trigger=wf_def.get("trigger", "manual"),
                )
        except Exception as e:
            logger.warning("failed_to_load_workflow", instance_id=instance_id, workflow=wf_name, error=str(e))


def _import_agent(name: str, instance_id: str):
    """Importar agente desde companies/{instance_id}/agents/ o core."""
    import importlib
    
    # 1. Intentar instancia específica
    try:
        return importlib.import_module(f"companies.{instance_id}.agents.{name}")
    except ImportError:
        pass
    
    # 2. Intentar core agents (genéricos)
    try:
        return importlib.import_module(f"core.hermes.agents.{name}")
    except ImportError:
        pass
    
    return None


def _import_tool(name: str, instance_id: str):
    """Importar tool desde companies/{instance_id}/tools/ o core."""
    import importlib
    
    try:
        module = importlib.import_module(f"companies.{instance_id}.tools.{name}")
        return getattr(module, name, None) or getattr(module, "execute", None)
    except ImportError:
        pass
    
    try:
        module = importlib.import_module(f"core.hermes.tools.{name}")
        return getattr(module, name, None) or getattr(module, "execute", None)
    except ImportError:
        pass
    
    return None


def _import_workflow(name: str, instance_id: str):
    """Importar workflow definition desde companies/{instance_id}/workflows/."""
    import importlib
    
    try:
        module = importlib.import_module(f"companies.{instance_id}.workflows.{name}")
        return getattr(module, "WORKFLOW_DEFINITION", None)
    except ImportError:
        return None