"""
Cliente HTTP para comunicación con Dolibarr API REST.

REUTILIZADO desde Transvega Animal - adapters/dolibarr/client.py
Adaptado para recibir config explícita por instancia (NO settings globales).
"""

from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from core.hermes.identity import DolibarrGroup, DolibarrUser
from core.hermes.instance_config import DolibarrConfig

if TYPE_CHECKING:
    from core.hermes.context import CompanyContext

logger = structlog.get_logger()


class DolibarrException(Exception):
    """Excepción para errores de Dolibarr API."""

    def __init__(
        self,
        message: str,
        endpoint: str = "",
        status_code: int = 0,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.endpoint = endpoint
        self.status_code = status_code
        self.details = details or {}


class DolibarrClient:
    """
    Cliente para API REST de Dolibarr.

    Recibe configuración explícita por constructor (InstanceConfig.database).
    NO usa settings globales - cada instancia tiene su cliente independiente.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DolibarrClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "DOLAPIKEY": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError("DolibarrClient not initialized. Use async context manager.")
        return self._client

    @classmethod
    def from_instance_config(cls, config: DolibarrConfig) -> "DolibarrClient":
        """Crear cliente desde InstanceConfig.dolibarr."""
        return cls(
            base_url=config.internal_url,
            api_key=config.api_key,
            timeout=30,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Realizar petición HTTP con manejo de errores."""
        # Dolibarr API usa /api/index.php/{endpoint}
        url = f"/api/index.php/{endpoint.lstrip('/')}"

        try:
            response = await self.client.request(
                method=method,
                url=url,
                params=params,
                json=json,
                data=data,
            )

            # Dolibarr devuelve 200/201 en éxito, 400+ en error
            if response.status_code >= 400:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {"message": response.text}

                raise DolibarrException(
                    message=error_data.get("error", {}).get("message", f"HTTP {response.status_code}"),
                    endpoint=endpoint,
                    status_code=response.status_code,
                    details=error_data,
                )

            if response.status_code == 204:  # No content
                return {}

            return response.json()

        except httpx.TimeoutException:
            raise DolibarrException(
                message="Timeout conectando con Dolibarr",
                endpoint=endpoint,
                status_code=504,
            )
        except httpx.RequestError as e:
            raise DolibarrException(
                message=f"Error de conexión: {e}",
                endpoint=endpoint,
                status_code=502,
            )

    # =========================================================================
    # USUARIOS Y GRUPOS (User Management)
    # =========================================================================

    async def get_user(
        self,
        user_id: int,
        include_permissions: bool = True,
    ) -> DolibarrUser:
        """
        Obtener usuario por ID.

        Args:
            user_id: ID del usuario en Dolibarr
            include_permissions: Si cargar permisos (includepermissions=1)

        Returns:
            DolibarrUser con datos del usuario

        Raises:
            DolibarrException: Si el usuario no existe (404) o error de API
        """
        params = {"includepermissions": 1} if include_permissions else {}
        data = await self._request("GET", f"users/{user_id}", params=params)
        return self._map_dolibarr_user(data)

    async def get_user_by_login(
        self,
        login: str,
        include_permissions: bool = True,
    ) -> DolibarrUser:
        """
        Obtener usuario por login.

        Args:
            login: Login del usuario en Dolibarr
            include_permissions: Si cargar permisos (includepermissions=1)

        Returns:
            DolibarrUser con datos del usuario

        Raises:
            DolibarrException: Si el usuario no existe (404) o error de API
        """
        params = {"includepermissions": 1} if include_permissions else {}
        data = await self._request("GET", f"users/login/{login}", params=params)
        return self._map_dolibarr_user(data)

    async def get_user_groups(self, user_id: int) -> list[DolibarrGroup]:
        """
        Obtener grupos de un usuario.

        Args:
            user_id: ID del usuario en Dolibarr

        Returns:
            Lista de DolibarrGroup a los que pertenece el usuario

        Raises:
            DolibarrException: Error de API
        """
        data = await self._request("GET", f"users/{user_id}/groups")
        if not isinstance(data, list):
            return []
        return [self._map_dolibarr_group(g) for g in data]

    async def get_group_permissions(self, group_id: int) -> dict[str, Any]:
        """
        Obtener permisos de un grupo.

        Args:
            group_id: ID del grupo en Dolibarr

        Returns:
            Dict con permisos (módulo -> submódulo -> permiso -> nivel)

        Raises:
            DolibarrException: Error de API
        """
        data = await self._request("GET", f"groups/{group_id}", params={"includepermissions": 1})
        if not isinstance(data, dict):
            return {}
        return data.get("rights", {})

    @staticmethod
    def _map_dolibarr_user(data: dict[str, Any]) -> DolibarrUser:
        """Mapear respuesta de Dolibarr a DolibarrUser."""
        # Extraer grupos si vienen en la respuesta
        user_groups = []
        if "user_group_list" in data and isinstance(data["user_group_list"], list):
            user_groups = [DolibarrClient._map_dolibarr_group(g) for g in data["user_group_list"]]

        return DolibarrUser(
            id=int(data.get("id", 0)),
            login=str(data.get("login", "")),
            firstname=str(data.get("firstname", "")),
            lastname=str(data.get("lastname", "")),
            email=str(data.get("email", "")),
            active=bool(data.get("status", 1)),
            entity=int(data.get("entity", 1)),
            rights=data.get("rights", {}),
            user_group_list=user_groups,
        )

    @staticmethod
    def _map_dolibarr_group(data: dict[str, Any]) -> DolibarrGroup:
        """Mapear respuesta de Dolibarr a DolibarrGroup."""
        return DolibarrGroup(
            id=int(data.get("id", 0) or data.get("rowid", 0)),
            name=str(data.get("name", "") or data.get("nom", "")),
            entity=int(data.get("entity", 1)),
            rights=data.get("rights"),
        )

    # =========================================================================
    # TERCEROS (Thirdparties)
    # =========================================================================

    async def list_thirdparties(
        self,
        limit: int = 100,
        page: int = 1,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
        sqlfilters: str | None = None,
    ) -> list[dict[str, Any]]:
        if page < 1:
            page = 1
        if limit < 1 or limit > 100:
            limit = 100

        params = {
            "limit": limit,
            "page": page,
            "sortfield": sortfield,
            "sortorder": sortorder,
        }
        if sqlfilters:
            params["sqlfilters"] = sqlfilters

        result = await self._request("GET", "thirdparties", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

    async def find_thirdparty_by_tax_id(
        self,
        tax_id: str,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> dict[str, Any] | None:
        """Buscar tercero por NIF/CIF normalizado usando paginación."""
        if not tax_id:
            return None
        normalized_search = self._normalize_tax_id(tax_id)

        page = 1
        pages_checked = 0
        while pages_checked < max_pages:
            parties = await self.list_thirdparties(
                limit=page_size,
                page=page,
            )
            if not parties:
                break

            for party in parties:
                party_vat = self._normalize_tax_id(party.get("vat_number", "") or party.get("vatnumber", ""))
                if party_vat == normalized_search:
                    return party

            if len(parties) < page_size:
                break
            page += 1
            pages_checked += 1

        return None

    @staticmethod
    def _normalize_tax_id(tax_id: str) -> str:
        """Normalizar NIF/CIF para comparación (quitar espacios, guiones, upper)."""
        return tax_id.replace(" ", "").replace("-", "").upper()

    async def get_thirdparty(self, thirdparty_id: int) -> dict[str, Any]:
        return await self._request("GET", f"thirdparties/{thirdparty_id}")

    async def create_thirdparty(self, data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] | int = await self._request("POST", "thirdparties", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_thirdparty(result)
        return result

    async def update_thirdparty(self, thirdparty_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"thirdparties/{thirdparty_id}", json=data)

    async def delete_thirdparty(self, thirdparty_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"thirdparties/{thirdparty_id}")

    # =========================================================================
    # PRODUCTOS
    # =========================================================================

    async def list_products(
        self,
        limit: int = 100,
        page: int = 1,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
        type: int | None = None,
        status: int | None = None,
        sqlfilters: str | None = None,
        pagination_data: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Listar productos/servicios con paginación page-based y filtros opcionales.

        Args:
            limit: Número máximo de resultados por página (default 100, max 100)
            page: Número de página 1-based (default 1)
            sortfield: Campo de ordenación (rowid, ref, label, type, status, price, etc.)
            sortorder: Orden (ASC, DESC)
            type: Filtrar por tipo (0=PRODUCT, 1=SERVICE)
            status: Filtrar por estado (0=borrador, 1=activo, etc.)
            sqlfilters: Filtros SQL avanzados de Dolibarr
            pagination_data: Si true, devuelve metadata de paginación (total, page, etc.)

        Returns:
            Lista de productos, o dict con 'data' y 'pagination' si pagination_data=True
        """
        if page < 1:
            page = 1
        if limit < 1 or limit > 100:
            limit = 100

        params: dict[str, Any] = {
            "limit": limit,
            "page": page,
            "sortfield": sortfield,
            "sortorder": sortorder,
        }

        # Build sqlfilters
        sqlfilters_parts: list[str] = []
        if sqlfilters:
            sqlfilters_parts.append(sqlfilters)
        if type is not None:
            if type not in (0, 1):
                raise ValueError("type must be 0 (PRODUCT) or 1 (SERVICE)")
            sqlfilters_parts.append(f"t.type:={type}")
        if status is not None:
            if status < 0:
                raise ValueError("status must be >= 0")
            sqlfilters_parts.append(f"t.status:={status}")

        if sqlfilters_parts:
            params["sqlfilters"] = " AND ".join(sqlfilters_parts)

        if pagination_data:
            params["pagination_data"] = "1"

        result = await self._request("GET", "products", params=params)

        if isinstance(result, dict):
            if pagination_data:
                return {
                    "data": result.get("data", []),
                    "pagination": {
                        "total": result.get("pagination", {}).get("total", 0),
                        "page": result.get("pagination", {}).get("page", page),
                        "limit": result.get("pagination", {}).get("limit", limit),
                        "pages": result.get("pagination", {}).get("pages", 0),
                    }
                }
            return result.get("data", [])
        return result

    async def search_products(
        self,
        query: str,
        limit: int = 20,
        page: int = 1,
        type: int | None = None,
        status: int | None = None,
        sortfield: str = "label",
        sortorder: str = "ASC",
    ) -> list[dict[str, Any]]:
        """
        Buscar productos/servicios por texto en ref, label, description.
        Usa sqlfilters con condiciones OR.
        """
        if not query or not query.strip():
            return []
        if page < 1:
            page = 1
        if limit < 1 or limit > 100:
            limit = 100

        # Escape for Dolibarr LIKE
        escaped = query.strip().replace("'", "''").replace("%", "\\%").replace("_", "\\_")

        search_conditions = [
            f"t.ref:like:'%{escaped}%'",
            f"t.label:like:'%{escaped}%'",
            f"t.description:like:'%{escaped}%'",
        ]

        sqlfilters_parts = [f"({' OR '.join(search_conditions)})"]

        if type is not None:
            if type not in (0, 1):
                raise ValueError("type must be 0 (PRODUCT) or 1 (SERVICE)")
            sqlfilters_parts.append(f"t.type:={type}")
        if status is not None:
            if status < 0:
                raise ValueError("status must be >= 0")
            sqlfilters_parts.append(f"t.status:={status}")

        params = {
            "limit": limit,
            "page": page,
            "sortfield": sortfield,
            "sortorder": sortorder,
            "sqlfilters": " AND ".join(sqlfilters_parts),
        }

        result = await self._request("GET", "products", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

    async def get_product_by_ref(self, ref: str) -> dict[str, Any] | None:
        """
        Obtener producto por referencia exacta (campo ref).
        Usa list_products con sqlfilters para coincidencia exacta.
        """
        if not ref or not ref.strip():
            return None

        escaped = ref.strip().replace("'", "''").replace("%", "\\%").replace("_", "\\_")

        result = await self.list_products(
            limit=1,
            page=1,
            sqlfilters=f"t.ref:='{escaped}'",
        )

        if isinstance(result, dict) and "data" in result:
            data = result["data"]
        else:
            data = result

        return data[0] if data else None

    async def get_product(self, product_id: int) -> dict[str, Any]:
        return await self._request("GET", f"products/{product_id}")

    async def create_product(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "products", json=data)

    async def update_product(self, product_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"products/{product_id}", json=data)

    async def delete_product(self, product_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"products/{product_id}")

    # =========================================================================
    # FACTURAS CLIENTE (Invoices)
    # =========================================================================

    async def list_invoices(
        self,
        limit: int = 100,
        page: int = 1,
        status: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        thirdparty_id: int | None = None,
        sortfield: str = "date",
        sortorder: str = "DESC",
        sqlfilters: str | None = None,
        pagination_data: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Listar facturas de cliente con filtros opcionales.

        Args:
            limit: Número máximo de resultados por página (default 100, max 100)
            page: Número de página 1-based (default 1)
            status: Filtrar por estado (0=borrador, 1=validada, 2=pagada, 3=anulada)
            date_from: Fecha factura desde (inclusive)
            date_to: Fecha factura hasta (inclusive)
            due_from: Fecha vencimiento desde (inclusive)
            due_to: Fecha vencimiento hasta (inclusive)
            thirdparty_id: Filtrar por ID de tercero
            sortfield: Campo de ordenación (date, ref, total_ttc, date_lim_reglement)
            sortorder: Orden (ASC, DESC)
            sqlfilters: Filtros SQL avanzados de Dolibarr
            pagination_data: Si true, devuelve metadata de paginación (total, page, etc.)

        Returns:
            Lista de facturas, o dict con 'data' y 'pagination' si pagination_data=True
        """
        if page < 1:
            page = 1
        if limit < 1 or limit > 100:
            limit = 100

        params: dict[str, Any] = {"limit": limit, "page": page, "sortfield": sortfield, "sortorder": sortorder}

        if status is not None:
            params["status"] = status
        if thirdparty_id is not None:
            params["thirdparty_ids"] = str(thirdparty_id)
        if date_from is not None:
            # Dolibarr espera timestamp para date
            params["date_from"] = int(datetime.combine(date_from, datetime.min.time()).timestamp())
        if date_to is not None:
            params["date_to"] = int(datetime.combine(date_to, datetime.max.time()).timestamp())
        if due_from is not None:
            params["date_lim_reglement_from"] = int(datetime.combine(due_from, datetime.min.time()).timestamp())
        if due_to is not None:
            params["date_lim_reglement_to"] = int(datetime.combine(due_to, datetime.max.time()).timestamp())
        if sqlfilters is not None:
            params["sqlfilters"] = sqlfilters
        if pagination_data:
            params["pagination_data"] = "1"

        result = await self._request("GET", "invoices", params=params)
        if isinstance(result, dict):
            if pagination_data:
                return {
                    "data": result.get("data", []),
                    "pagination": {
                        "total": result.get("pagination", {}).get("total", 0),
                        "page": result.get("pagination", {}).get("page", page),
                        "limit": result.get("pagination", {}).get("limit", limit),
                        "pages": result.get("pagination", {}).get("pages", 0),
                    }
                }
            return result.get("data", [])
        return result

    async def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        return await self._request("GET", f"invoices/{invoice_id}")

    async def create_invoice(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "invoices", json=data)

    async def update_invoice(self, invoice_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"invoices/{invoice_id}", json=data)

    async def validate_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Validar factura (pasar de borrador a validada)."""
        return await self._request("POST", f"invoices/{invoice_id}/validate")

    async def cancel_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Anular factura."""
        return await self._request("POST", f"invoices/{invoice_id}/cancel")

    # =========================================================================
    # FACTURAS PROVEEDOR (Supplier Invoices) - CRÍTICO para multi-empresa
    # =========================================================================

    async def list_supplier_invoices(
        self,
        limit: int = 100,
        page: int = 1,
        status: int | None = None,
        thirdparty_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        sortfield: str = "date",
        sortorder: str = "DESC",
        sqlfilters: str | None = None,
        pagination_data: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Listar facturas de proveedor con filtros opcionales.

        Args:
            limit: Número máximo de resultados por página (default 100, max 100)
            page: Número de página 1-based (default 1)
            status: Filtrar por estado (0=borrador, 1=validada, 2=pagada, 3=anulada)
            thirdparty_id: Filtrar por ID de proveedor (socid)
            date_from: Fecha factura desde (inclusive)
            date_to: Fecha factura hasta (inclusive)
            due_from: Fecha vencimiento desde (inclusive)
            due_to: Fecha vencimiento hasta (inclusive)
            sortfield: Campo de ordenación (date, ref, total_ttc, date_lim_reglement)
            sortorder: Orden (ASC, DESC)
            sqlfilters: Filtros SQL avanzados de Dolibarr
            pagination_data: Si true, devuelve metadata de paginación (total, page, etc.)

        Returns:
            Lista de facturas, o dict con 'data' y 'pagination' si pagination_data=True
        """
        if page < 1:
            page = 1
        if limit < 1 or limit > 100:
            limit = 100

        params: dict[str, Any] = {"limit": limit, "page": page, "sortfield": sortfield, "sortorder": sortorder}

        if status is not None:
            params["status"] = status
        if thirdparty_id is not None:
            params["thirdparty_ids"] = str(thirdparty_id)
        if date_from is not None:
            # Dolibarr espera timestamp para date
            params["date_from"] = int(datetime.combine(date_from, datetime.min.time()).timestamp())
        if date_to is not None:
            params["date_to"] = int(datetime.combine(date_to, datetime.max.time()).timestamp())
        if due_from is not None:
            params["date_lim_reglement_from"] = int(datetime.combine(due_from, datetime.min.time()).timestamp())
        if due_to is not None:
            params["date_lim_reglement_to"] = int(datetime.combine(due_to, datetime.max.time()).timestamp())
        if sqlfilters is not None:
            params["sqlfilters"] = sqlfilters
        if pagination_data:
            params["pagination_data"] = "1"

        result = await self._request("GET", "supplierinvoices", params=params)
        if isinstance(result, dict):
            if pagination_data:
                return {
                    "data": result.get("data", []),
                    "pagination": {
                        "total": result.get("pagination", {}).get("total", 0),
                        "page": result.get("pagination", {}).get("page", page),
                        "limit": result.get("pagination", {}).get("limit", limit),
                        "pages": result.get("pagination", {}).get("pages", 0),
                    }
                }
            return result.get("data", [])
        return result

    async def get_supplier_invoice(self, invoice_id: int) -> dict[str, Any]:
        return await self._request("GET", f"supplierinvoices/{invoice_id}")

    async def create_supplier_invoice(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear factura de proveedor. Usa 'socid' para proveedor."""
        if "thirdparty_id" in data and "socid" not in data:
            data["socid"] = data.pop("thirdparty_id")
        result: dict[str, Any] | int = await self._request("POST", "supplierinvoices", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_supplier_invoice(result)
        return result

    async def update_supplier_invoice(self, invoice_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"supplierinvoices/{invoice_id}", json=data)

    async def validate_supplier_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Validar factura proveedor."""
        return await self._request("POST", f"supplierinvoices/{invoice_id}/validate")

    async def cancel_supplier_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Anular factura proveedor."""
        return await self._request("POST", f"supplierinvoices/{invoice_id}/cancel")

    async def add_supplier_invoice_line(self, invoice_id: int, line_data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"supplierinvoices/{invoice_id}/lines", json=line_data)

    # =========================================================================
    # DOCUMENTOS ADJUNTOS
    # =========================================================================

    async def upload_document(
        self, resource_type: str, resource_id: int, file_data: bytes, filename: str
    ) -> dict[str, Any]:
        """Subir documento adjunto usando multipart/form-data."""
        url = f"/api/index.php/{resource_type}/{resource_id}/documents"

        files = {"file": (filename, file_data)}

        headers = {
            "DOLAPIKEY": self.api_key,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            ) as client:
                response = await client.post(url, files=files)

            if response.status_code >= 400:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {"message": response.text}
                raise DolibarrException(
                    message=error_data.get("error", {}).get("message", f"HTTP {response.status_code}"),
                    endpoint=url,
                    status_code=response.status_code,
                    details=error_data,
                )

            if response.status_code == 204:
                return {}

            return response.json()

        except httpx.TimeoutException:
            raise DolibarrException(
                message="Timeout subiendo documento a Dolibarr",
                endpoint=url,
                status_code=504,
            )
        except httpx.RequestError as e:
            raise DolibarrException(
                message=f"Error de conexión subiendo documento: {e}",
                endpoint=url,
                status_code=502,
            )

    async def list_documents(self, resource_type: str, resource_id: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"{resource_type}/{resource_id}/documents")
        return result.get("data", []) if isinstance(result, dict) else result


# =========================================================================
# FASTAPI DEPENDENCY
# =========================================================================


async def get_dolibarr_client(
    ctx: "CompanyContext" = None,
) -> AsyncGenerator[DolibarrClient, None]:
    """
    FastAPI dependency que provee DolibarrClient para la instancia actual.

    Uso:
        @app.get("/api/thirdparties")
        async def list_thirdparties(client: DolibarrClient = Depends(get_dolibarr_client)):
            return await client.list_thirdparties()
    """
    if ctx is None:
        # Esto requiere que el endpoint use Depends(get_company_context) también
        raise RuntimeError(
            "get_dolibarr_client requiere CompanyContext. Usa Depends(get_company_context) en el endpoint."
        )

    client = DolibarrClient.from_instance_config(ctx.dolibarr_config)
    async with client as c:
        yield c


def create_dolibarr_client_dependency():
    """Factory para crear dependency con CompanyContext inyectado."""

    async def _dependency(ctx: "CompanyContext" = None) -> AsyncGenerator[DolibarrClient, None]:
        return get_dolibarr_client(ctx)

    return _dependency
