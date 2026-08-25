"""
BC3 Dolibarr Linker - Vinculación de recursos BC3 con productos/servicios Dolibarr.

Proporciona:
- Vinculación automática BC3 Resource ↔ Dolibarr Product/Service
- Sincronización bidireccional
- Mapeo de códigos y descripciones
- Gestión de variantes y referencias
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from .models import BC3Item, BC3ResourceType, BC3Unit
from core.integrations.dolibarr.client import DolibarrClient, DolibarrException


# =============================================================================
# LINK MODELS
# =============================================================================

@dataclass(frozen=True, slots=True)
class BC3DolibarrLink:
    """Vínculo entre recurso BC3 y producto/servicio Dolibarr."""
    
    id: UUID = field(default_factory=uuid4)
    bc3_item_code: str = ""
    bc3_resource_type: BC3ResourceType = BC3ResourceType.MATERIAL
    dolibarr_id: int = 0
    dolibarr_ref: str = ""
    dolibarr_type: str = "product"  # product | service
    dolibarr_label: str = ""
    match_confidence: Decimal = Decimal("0")  # 0-100%
    match_method: str = "manual"  # exact | fuzzy | manual | auto
    is_active: bool = True
    created_at: date = field(default_factory=date.today)
    updated_at: date = field(default_factory=date.today)
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# DOLIBARR LINKER
# =============================================================================

class BC3DolibarrLinker:
    """
    Vinculador de recursos BC3 con productos/servicios Dolibarr.
    
    Funciones:
    - Vinculación automática por código, descripción, referencia
    - Búsqueda fuzzy para coincidencias aproximadas
    - Gestión de vínculos bidireccionales
    - Sincronización de precios y descripciones
    - Detección de conflictos
    """
    
    def __init__(self, dolibarr_client: DolibarrClient):
        self.client = dolibarr_client
        self._links: dict[str, BC3DolibarrLink] = {}  # bc3_code -> link
        self._reverse_links: dict[int, BC3DolibarrLink] = {}  # dolibarr_id -> link
    
    # =========================================================================
    # LINK MANAGEMENT
    # =========================================================================
    
    def create_link(
        self,
        bc3_item_code: str,
        bc3_resource_type: BC3ResourceType,
        dolibarr_id: int,
        dolibarr_ref: str,
        dolibarr_type: str,
        dolibarr_label: str,
        match_confidence: Decimal = Decimal("100"),
        match_method: str = "manual",
    ) -> BC3DolibarrLink:
        """Crear vínculo manual entre item BC3 y producto/servicio Dolibarr."""
        link = BC3DolibarrLink(
            bc3_item_code=bc3_item_code,
            bc3_resource_type=bc3_resource_type,
            dolibarr_id=dolibarr_id,
            dolibarr_ref=dolibarr_ref,
            dolibarr_type=dolibarr_type,
            dolibarr_label=dolibarr_label,
            match_confidence=match_confidence,
            match_method=match_method,
        )
        
        self._links[bc3_item_code] = link
        self._reverse_links[dolibarr_id] = link
        
        return link
    
    def get_link_by_bc3_code(self, bc3_item_code: str) -> BC3DolibarrLink | None:
        """Obtener vínculo por código BC3."""
        return self._links.get(bc3_item_code)
    
    def get_link_by_dolibarr_id(self, dolibarr_id: int) -> BC3DolibarrLink | None:
        """Obtener vínculo por ID Dolibarr."""
        return self._reverse_links.get(dolibarr_id)
    
    def remove_link(self, bc3_item_code: str) -> bool:
        """Eliminar vínculo."""
        link = self._links.pop(bc3_item_code, None)
        if link:
            self._reverse_links.pop(link.dolibarr_id, None)
            return True
        return False
    
    def get_all_links(self) -> list:
        """Obtener todos los vínculos."""
        return list(self._links.values())
    
    # =========================================================================
    # AUTO LINKING
    # =========================================================================
    
    async def auto_link_item(
        self,
        item: Any,  # BC3Item
        confidence_threshold: Decimal = Decimal("80"),
    ) -> BC3DolibarrLink | None:
        """
        Vincular automáticamente un item BC3 con Dolibarr.
        
        Estrategia de búsqueda (en orden):
        1. Coincidencia exacta por código/referencia
        2. Coincidencia exacta por descripción
        3. Búsqueda fuzzy por descripción (si confidence >= threshold)
        
        Returns:
            Link creado o None si no se encuentra coincidencia suficiente
        """
        # 1. Buscar por código exacto (ref en Dolibarr)
        if item.code:
            exact_by_ref = await self._search_dolibarr_by_ref(item.code)
            if exact_by_ref:
                return self.create_link(
                    bc3_item_code=item.code,
                    bc3_resource_type=item.resource_type,
                    dolibarr_id=exact_by_ref["id"],
                    dolibarr_ref=exact_by_ref.get("ref", ""),
                    dolibarr_type="product" if exact_by_ref.get("type", 0) == 0 else "service",
                    dolibarr_label=exact_by_ref.get("label", ""),
                    match_confidence=Decimal("100"),
                    match_method="exact_ref",
                )
        
        # 2. Buscar por descripción exacta
        exact_by_desc = await self._search_dolibarr_by_description(item.description, exact=True)
        if exact_by_desc:
            return self.create_link(
                bc3_item_code=item.code,
                bc3_resource_type=item.resource_type,
                dolibarr_id=exact_by_desc["id"],
                dolibarr_ref=exact_by_desc.get("ref", ""),
                dolibarr_type="product" if exact_by_desc.get("type", 0) == 0 else "service",
                dolibarr_label=exact_by_desc.get("label", ""),
                match_confidence=Decimal("95"),
                match_method="exact_description",
            )
        
        # 3. Búsqueda fuzzy por descripción
        fuzzy_results = await self._search_dolibarr_fuzzy(item.description, limit=5)
        if fuzzy_results:
            best_match = fuzzy_results[0]
            if best_match["score"] >= confidence_threshold:
                return self.create_link(
                    bc3_item_code=item.code,
                    bc3_resource_type=item.resource_type,
                    dolibarr_id=best_match["id"],
                    dolibarr_ref=best_match.get("ref", ""),
                    dolibarr_type="product" if best_match.get("type", 0) == 0 else "service",
                    dolibarr_label=best_match.get("label", ""),
                    match_confidence=Decimal(str(best_match["score"])),
                    match_method="fuzzy",
                )
        
        return None
    
    async def auto_link_items(
        self,
        items: list,
        confidence_threshold: Decimal = Decimal("80"),
    ) -> dict[str, Any]:
        """
        Vincular automáticamente múltiples items.
        
        Returns:
            Dict con estadísticas: linked, failed, conflicts
        """
        results = {
            "linked": [],
            "failed": [],
            "conflicts": [],
        }
        
        for item in items:
            try:
                # Verificar si ya está vinculado
                existing = self.get_link_by_bc3_code(item.code)
                if existing:
                    results["conflicts"].append({
                        "item_code": item.code,
                        "reason": "already_linked",
                        "existing_link": existing,
                    })
                    continue
                
                link = await self.auto_link_item(item)
                if link:
                    results["linked"].append({
                        "item_code": item.code,
                        "dolibarr_id": link.dolibarr_id,
                        "confidence": link.match_confidence,
                        "method": link.match_method,
                    })
                else:
                    results["failed"].append({
                        "item_code": item.code,
                        "reason": "no_match_found",
                    })
            except Exception as e:
                results["failed"].append({
                    "item_code": item.code,
                    "reason": f"error: {e}",
                })
        
        return results
    
    # =========================================================================
    # SYNCHRONIZATION
    # =========================================================================
    
    async def sync_from_dolibarr(self, dolibarr_id: int) -> BC3DolibarrLink | None:
        """
        Sincronizar datos desde Dolibarr hacia BC3.
        
        Actualiza precio, descripción, referencia si han cambiado en Dolibarr.
        """
        link = self._reverse_links.get(dolibarr_id)
        if not link:
            return None
        
        try:
            if link.dolibarr_type == "product":
                dolibarr_data = await self.client.get_product(link.dolibarr_id)
            else:
                # Para servicios, usar endpoint genérico o productos tipo servicio
                dolibarr_data = await self.client._request("GET", f"products/{dolibarr_id}")
        except DolibarrException:
            return None
        
        if not dolibarr_data:
            return None
        
        # Detectar cambios
        changes = {}
        if dolibarr_data.get("label") != link.dolibarr_label:
            changes["label"] = {"old": link.dolibarr_label, "new": dolibarr_data.get("label")}
        if dolibarr_data.get("ref") != link.dolibarr_ref:
            changes["ref"] = {"old": link.dolibarr_ref, "new": dolibarr_data.get("ref")}
        if dolibarr_data.get("price") != link.metadata.get("price"):
            changes["price"] = {"old": link.metadata.get("price"), "new": dolibarr_data.get("price")}
        
        if changes:
            # Actualizar link
            link = BC3DolibarrLink(
                **{**link.__dict__, "dolibarr_label": dolibarr_data.get("label", link.dolibarr_label),
                   "dolibarr_ref": dolibarr_data.get("ref", link.dolibarr_ref),
                   "metadata": {**link.metadata, "price": dolibarr_data.get("price")},
                   "updated_at": date.today()}
            )
            self._links[link.bc3_item_code] = link
            self._reverse_links[dolibarr_id] = link
        
        return link
    
    async def sync_all(self) -> dict[str, Any]:
        """Sincronizar todos los vínculos activos."""
        results = {"updated": 0, "errors": 0}
        
        for link in list(self._links.values()):
            if link.is_active:
                try:
                    updated = await self.sync_from_dolibarr(link.dolibarr_id)
                    if updated and updated.metadata.get("changed"):
                        results["updated"] += 1
                except Exception:
                    results["errors"] += 1
        
        return results
    
    async def push_to_dolibarr(self, bc3_item_code: str) -> bool:
        """
        Enviar datos BC3 a Dolibarr (crear/actualizar producto/servicio).
        
        Útil cuando se crea item en BC3 y se quiere reflejar en Dolibarr.
        """
        link = self._links.get(bc3_item_code)
        if not link:
            return False
        
        # Obtener item BC3 completo (necesario para datos completos)
        # En implementación real, obtener item completo desde catálogo BC3
        
        # Por ahora, solo actualizar si ya existe
        if link.dolibarr_id:
            try:
                update_data = {
                    "label": link.dolibarr_label,
                    "price": link.metadata.get("price"),
                }
                if link.dolibarr_type == "product":
                    await self.client.update_product(link.dolibarr_id, {})
                return True
            except DolibarrException:
                return False
        
        return False
    
    # =========================================================================
    # PRIVATE SEARCH METHODS
    # =========================================================================
    
    async def _search_dolibarr_by_ref(self, ref: str) -> dict | None:
        """Buscar producto/servicio por referencia exacta."""
        try:
            # Buscar en productos
            products = await self.client.search_products(query=ref, limit=1)
            if products:
                p = products[0]
                if p.get("ref") == ref:
                    return {"id": p["id"], "ref": p.get("ref"), "label": p.get("label"), "type": 0}
            
            # Buscar en servicios (productos tipo 1)
            # Dolibarr usa mismo endpoint con type=1 para servicios
            # Simplificado por ahora
        except DolibarrException:
            pass
        return None
    
    async def _search_dolibarr_by_description(
        self,
        description: str,
        exact: bool = False,
    ) -> dict | None:
        """Buscar por descripción."""
        try:
            if exact:
                # Búsqueda exacta por label
                products = await self.client.list_products(limit=100)
                for p in products:
                    if p.get("label", "").lower() == description.lower():
                        return {"id": p["id"], "ref": p.get("ref"), "label": p.get("label"), "type": p.get("type", 0)}
            else:
                # Búsqueda parcial
                products = await self.client.search_products(query=description, limit=1)
                if products:
                    p = products[0]
                    return {"id": p["id"], "ref": p.get("ref"), "label": p.get("label"), "type": p.get("type", 0)}
        except DolibarrException:
            pass
        return None
    
    async def _search_dolibarr_fuzzy(self, description: str, limit: int = 5) -> list[dict]:
        """Búsqueda fuzzy por descripción."""
        try:
            # Usar búsqueda de Dolibarr
            results = await self.client.search_products(query=description, limit=limit)
            
            scored_results = []
            for p in results:
                # Calcular score simple basado en similitud de strings
                score = self._calculate_similarity(description, p.get("label", ""))
                if score > 0:
                    scored_results.append({
                        "id": p["id"],
                        "ref": p.get("ref"),
                        "label": p.get("label"),
                        "type": p.get("type", 0),
                        "score": score,
                    })
            
            # Ordenar por score descendente
            scored_results.sort(key=lambda x: x["score"], reverse=True)
            return scored_results[:limit]
        except DolibarrException:
            return []
    
    def _calculate_similarity(self, str1: str, str2: str) -> int:
        """Calcular similitud simple entre dos strings (0-100)."""
        if not str1 or not str2:
            return 0
        
        str1_lower = str1.lower().strip()
        str2_lower = str2.lower().strip()
        
        if str1_lower == str2_lower:
            return 100
        
        # Similitud simple basada en palabras comunes
        words1 = set(str1_lower.split())
        words2 = set(str2_lower.split())
        
        if not words1 or not words2:
            return 0
        
        intersection = words1 & words2
        union = words1 | words2
        
        jaccard = len(intersection) / len(union)
        return int(jaccard * 100)


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_dolibarr_linker(dolibarr_client: DolibarrClient) -> BC3DolibarrLinker:
    """Crear vinculador BC3-Dolibarr."""
    return BC3DolibarrLinker(dolibarr_client)