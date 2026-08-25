"""
Command Layer - BC3 Handler (EXPERIMENTAL).

⚠️ EXPERIMENTAL / NOT PRODUCTION READY ⚠️

Handlers for importing/exporting BC3 files in Dolibarr.
NOT registered in command_registry by default.
Requires explicit opt-in and validation before production use.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    ImportBC3Args,
    ExportBC3Args,
)
from core.integrations.dolibarr.client import DolibarrException


class ImportBC3Handler(CommandHandler):
    """Handler for importing BC3 files and creating catalog in Dolibarr.

    EXPERIMENTAL: Not registered in production command registry.
    Requires file handling in Telegram endpoint (NOT_IMPLEMENTED).
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.IMPORT_BC3

    @property
    def required_permission(self) -> str:
        return "bc3.import"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        # file_data is bytes, not JSON serializable - handled separately in execute
        validated = {
            "nombre_proyecto": payload.get("nombre_proyecto", "").strip(),
            "vincular_productos": payload.get("vincular_productos", False),
        }

        if not validated["nombre_proyecto"]:
            raise ValueError("Nombre del proyecto es obligatorio")

        return validated

    def _parse_bc3(self, file_data: bytes) -> dict[str, Any]:
        """Parsear archivo BC3 (XML) y extraer capítulos e items.
        
        Retorna estructura:
        {
            "proyecto": {...},
            "capitulos": [
                {"codigo": "01", "descripcion": "...", "items": [...]},
                ...
            ]
        }
        """
        import xml.etree.ElementTree as ET
        
        try:
            # BC3 es XML - parsear
            root = ET.fromstring(file_data.decode('utf-8'))
        except ET.ParseError:
            # Intentar con encoding latin-1
            try:
                root = ET.fromstring(file_data.decode('latin-1'))
            except ET.ParseError as e:
                raise ValueError(f"Archivo BC3 inválido: {e}")

        # Namespace BC3
        ns = {'bc3': 'http://www.bc3.es/'}
        
        # Extraer información del proyecto
        proyecto_elem = root.find('.//bc3:proyecto', ns) or root.find('.//proyecto')
        proyecto = {
            "nombre": proyecto_elem.get('nombre', '') if proyecto_elem is not None else '',
            "codigo": proyecto_elem.get('codigo', '') if proyecto_elem is not None else '',
        }

        # Extraer capítulos e items
        capitulos = []
        for cap_elem in root.findall('.//bc3:capitulo', ns) or root.findall('.//capitulo'):
            cap = {
                "codigo": cap_elem.get('codigo', ''),
                "descripcion": cap_elem.get('descripcion', ''),
                "items": []
            }
            
            for item_elem in cap_elem.findall('.//bc3:item', ns) or cap_elem.findall('.//item'):
                item = {
                    "codigo": item_elem.get('codigo', ''),
                    "descripcion": item_elem.get('descripcion', ''),
                    "unidad": item_elem.get('unidad', ''),
                    "cantidad": float(item_elem.get('cantidad', 0)),
                    "precio": float(item_elem.get('precio', 0)),
                    "tipo": item_elem.get('tipo', ''),  # 1=material, 2=mano de obra, 3=maquinaria, 4=otros
                }
                cap["items"].append(item)
            
            capitulos.append(cap)

        return {
            "proyecto": proyecto,
            "capitulos": capitulos,
        }

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a importar archivo BC3:",
            f"Proyecto: {validated_payload['nombre_proyecto']}",
            f"Vincular productos Dolibarr: {'Sí' if validated_payload['vincular_productos'] else 'No'}",
        ]

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
        # El file_data se maneja por separado en el endpoint de Telegram
        # Aquí solo validamos y preparamos
        return CommandResult(
            success=False,
            error_code="NOT_IMPLEMENTED",
            error_message="Importación BC3 requiere manejo de archivo en endpoint Telegram",
        )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "bc3_import",
            "resource_id": result.resource_id,
        }


class ExportBC3Handler(CommandHandler):
    """Handler for exporting project/budget to BC3 format.

    EXPERIMENTAL: Not registered in production command registry.
    Simplified implementation - exports basic project structure only.
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.EXPORT_BC3

    @property
    def required_permission(self) -> str:
        return "bc3.export"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "project_id": payload.get("project_id"),
        }

        if not validated["project_id"]:
            raise ValueError("project_id es obligatorio")

        return validated

    def _generate_bc3_xml(self, project_data: dict, capitulos: list) -> bytes:
        """Generar XML BC3 a partir de proyecto y capítulos."""
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        root = ET.Element("bc3")
        root.set("version", "1.0")
        root.set("xmlns", "http://www.bc3.es/")

        # Proyecto
        proyecto_elem = ET.SubElement(root, "proyecto")
        proyecto_elem.set("nombre", project_data.get("nombre", ""))
        proyecto_elem.set("codigo", project_data.get("codigo", ""))

        # Capítulos
        for cap in capitulos:
            cap_elem = ET.SubElement(root, "capitulo")
            cap_elem.set("codigo", cap.get("codigo", ""))
            cap_elem.set("descripcion", cap.get("descripcion", ""))

            for item in cap.get("items", []):
                item_elem = ET.SubElement(cap_elem, "item")
                item_elem.set("codigo", item.get("codigo", ""))
                item_elem.set("descripcion", item.get("descripcion", ""))
                item_elem.set("unidad", item.get("unidad", ""))
                item_elem.set("cantidad", str(item.get("cantidad", 0)))
                item_elem.set("precio", str(item.get("precio", 0)))
                item_elem.set("tipo", item.get("tipo", ""))

        # Pretty print
        rough_string = ET.tostring(root, encoding='utf-8')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding='utf-8')
        
        return pretty_xml

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a exportar proyecto a BC3:",
            f"Proyecto ID: {validated_payload['project_id']}",
        ]

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
                # 1. Load project
                project = await client.get_project(validated_payload["project_id"])
                if not project:
                    return CommandResult(
                        success=False,
                        error_code="PROJECT_NOT_FOUND",
                        error_message=f"Proyecto {validated_payload['project_id']} no encontrado",
                    )

                # 2. Get project tasks/lines (simplified)
                # En implementación real, cargar capítulos e items del proyecto
                capitulos = []  # Se cargarían desde BD o estructura del proyecto

                # 3. Generate BC3
                bc3_xml = self._generate_bc3_xml(
                    {"nombre": project.get("title", ""), "codigo": project.get("ref", "")},
                    capitulos
                )

                return CommandResult(
                    success=True,
                    resource_id=validated_payload["project_id"],
                    resource_type="bc3_export",
                    data={
                        "project_id": validated_payload["project_id"],
                        "bc3_size_bytes": len(bc3_xml),
                        "bc3_xml_base64": bc3_xml.decode('utf-8'),  # Para respuesta Telegram
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido exportar BC3",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "bc3_export",
            "resource_id": result.resource_id,
        }