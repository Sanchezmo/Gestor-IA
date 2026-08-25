"""
Command Layer V3 - Project Handlers.

Handlers for creating projects and tasks in Dolibarr.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreateProjectArgs,
    AddProjectTaskArgs,
)
from core.integrations.dolibarr.client import DolibarrException


class CreateProjectHandler(CommandHandler):
    """Handler for creating projects in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_PROJECT

    @property
    def required_permission(self) -> str:
        return "project.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "nombre": payload.get("nombre", "").strip(),
            "descripcion": payload.get("descripcion"),
            "cliente": payload.get("cliente"),
            "fecha_inicio": payload.get("fecha_inicio"),
            "fecha_fin": payload.get("fecha_fin"),
            "presupuesto": payload.get("presupuesto"),
            "estado": payload.get("estado", "planificacion"),
        }

        if not validated["nombre"]:
            raise ValueError("Nombre del proyecto es obligatorio")

        return validated

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a crear proyecto:",
            f"Nombre: {validated_payload['nombre']}",
        ]

        if validated_payload.get("descripcion"):
            lines.append(f"Descripción: {validated_payload['descripcion']}")
        if validated_payload.get("cliente"):
            lines.append(f"Cliente: {validated_payload['cliente']}")
        if validated_payload.get("fecha_inicio"):
            lines.append(f"Inicio: {validated_payload['fecha_inicio']}")
        if validated_payload.get("fecha_fin"):
            lines.append(f"Fin estimado: {validated_payload['fecha_fin']}")
        if validated_payload.get("presupuesto") is not None:
            lines.append(f"Presupuesto: {validated_payload['presupuesto']:.2f}€")
        lines.append(f"Estado: {validated_payload['estado']}")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any]
    ) -> CommandResult:
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve client if provided
                thirdparty_id = None
                if validated_payload.get("cliente"):
                    cliente_query = validated_payload["cliente"]
                    thirdparty = await client.find_thirdparty_by_tax_id(cliente_query)
                    if not thirdparty:
                        thirdparties = await client.search_thirdparties(
                            query=cliente_query,
                            filter_customer=True,
                            limit=1,
                        )
                        if thirdparties:
                            thirdparty = thirdparties[0]

                    if not thirdparty:
                        thirdparty_result = await client.create_thirdparty({
                            "name": validated_payload["cliente"],
                            "client": 1,
                        })
                        thirdparty_id = thirdparty_result.get("id")
                    else:
                        thirdparty_id = thirdparty.get("id")

                # 2. Prepare project data
                today = date.today()
                fecha_inicio = date.fromisoformat(validated_payload["fecha_inicio"]) if validated_payload.get("fecha_inicio") else today
                fecha_fin = date.fromisoformat(validated_payload["fecha_fin"]) if validated_payload.get("fecha_fin") else None

                project_data = {
                    "ref": validated_payload["nombre"][:30].upper().replace(" ", "-"),
                    "title": validated_payload["nombre"],
                    "description": validated_payload.get("descripcion", ""),
                    "date_start": int(fecha_inicio.timestamp()),
                    "date_end": int(fecha_fin.timestamp()) if fecha_fin else None,
                    "budget_amount": validated_payload.get("presupuesto"),
                    "status": {"planificacion": 0, "en_curso": 1, "finalizado": 2}.get(validated_payload["estado"], 0),
                }

                if thirdparty_id:
                    project_data["fk_soc"] = thirdparty_id

                # 3. Create project
                project = await client.create_project(project_data)
                project_id = project.get("id")

                if not project_id:
                    return CommandResult(
                        success=False,
                        error_code="PROJECT_CREATE_FAILED",
                        error_message="No se pudo crear el proyecto",
                    )

                return CommandResult(
                    success=True,
                    resource_id=project_id,
                    resource_type="project",
                    data={
                        "id": project_id,
                        "ref": project.get("ref"),
                        "nombre": validated_payload["nombre"],
                        "estado": validated_payload["estado"],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el proyecto",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "project",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
        }


class AddProjectTaskHandler(CommandHandler):
    """Handler for adding tasks to projects in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.ADD_PROJECT_TASK

    @property
    def required_permission(self) -> str:
        return "project.manage"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "project_id": payload.get("project_id"),
            "nombre": payload.get("nombre", "").strip(),
            "descripcion": payload.get("descripcion"),
            "fecha_inicio": payload.get("fecha_inicio"),
            "fecha_fin": payload.get("fecha_fin"),
            "horas_estimadas": payload.get("horas_estimadas"),
            "coste_estimado": payload.get("coste_estimado"),
        }

        if not validated["project_id"]:
            raise ValueError("project_id es obligatorio")
        if not validated["nombre"]:
            raise ValueError("Nombre de la tarea es obligatorio")

        return validated

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a añadir tarea al proyecto:",
            f"Proyecto ID: {validated_payload['project_id']}",
            f"Tarea: {validated_payload['nombre']}",
        ]

        if validated_payload.get("descripcion"):
            lines.append(f"Descripción: {validated_payload['descripcion']}")
        if validated_payload.get("fecha_inicio"):
            lines.append(f"Inicio: {validated_payload['fecha_inicio']}")
        if validated_payload.get("fecha_fin"):
            lines.append(f"Fin: {validated_payload['fecha_fin']}")
        if validated_payload.get("horas_estimadas"):
            lines.append(f"Horas estimadas: {validated_payload['horas_estimadas']}")
        if validated_payload.get("coste_estimado"):
            lines.append(f"Coste estimado: {validated_payload['coste_estimado']:.2f}€")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any]
    ) -> CommandResult:
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Verify project exists
                project = await client.get_project(validated_payload["project_id"])
                if not project:
                    return CommandResult(
                        success=False,
                        error_code="PROJECT_NOT_FOUND",
                        error_message=f"Proyecto {validated_payload['project_id']} no encontrado",
                    )

                # 2. Prepare task data
                task_data = {
                    "label": validated_payload["nombre"],
                    "description": validated_payload.get("descripcion", ""),
                    "date_start": int(date.fromisoformat(validated_payload["fecha_inicio"]).timestamp()) if validated_payload.get("fecha_inicio") else None,
                    "date_end": int(date.fromisoformat(validated_payload["fecha_fin"]).timestamp()) if validated_payload.get("fecha_fin") else None,
                    "duration_estimated": validated_payload.get("horas_estimadas"),
                    "cost_estimated": validated_payload.get("coste_estimado"),
                }

                # 3. Add task
                task = await client.add_project_task(validated_payload["project_id"], task_data)
                task_id = task.get("id")

                if not task_id:
                    return CommandResult(
                        success=False,
                        error_code="TASK_CREATE_FAILED",
                        error_message="No se pudo crear la tarea",
                    )

                return CommandResult(
                    success=True,
                    resource_id=task_id,
                    resource_type="project_task",
                    data={
                        "id": task_id,
                        "project_id": validated_payload["project_id"],
                        "nombre": validated_payload["nombre"],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear la tarea",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "project_task",
            "resource_id": result.resource_id,
            "project_id": result.data.get("project_id") if result.data else None,
        }