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
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # Optional DB credentials for fallback queries
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password

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
    def from_instance_config(
        cls,
        config: DolibarrConfig,
        user_api_key: str | None = None,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
    ) -> "DolibarrClient":
        """Crear cliente desde InstanceConfig.dolibarr.
        
        Args:
            config: DolibarrConfig from instance
            user_api_key: Optional per-user API key. If provided, uses user's key
                          for ERP authorization. If None, uses instance admin key.
            db_*: Optional database credentials for fallback queries.
        """
        return cls(
            base_url=config.internal_url,
            api_key=user_api_key or config.api_key,
            timeout=30,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
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

            # Dolibarr devuelve 404 en listas vacías (comportamiento conocido de Dolibarr)
            # Para GET en endpoints de lista, 404 = lista vacía, no error
            if response.status_code == 404 and method == "GET":
                return {"data": []}

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
        page: int = 0,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
        sqlfilters: str | None = None,
    ) -> list[dict[str, Any]]:
        if page < 0:
            page = 0
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
        rest_data = result.get("data", []) if isinstance(result, dict) else result
        
        # Fallback: if REST API returns empty/null fields, use direct DB query
        if rest_data and self._is_rest_api_broken(rest_data):
            return await self._list_thirdparties_db(limit, page, sortfield, sortorder, sqlfilters, rest_data)
        
        return rest_data

    def _is_rest_api_broken(self, data: list[dict]) -> bool:
        """Detect if REST API is returning null field values (Dolibarr 23.0.4 bug)."""
        if not data:
            return False
        # Check if first item has null values for key fields
        first = data[0]
        key_fields = ["name", "nom", "client", "fournisseur", "ref", "email"]
        null_count = sum(1 for f in key_fields if first.get(f) is None)
        # If more than half the key fields are null, API is broken
        return null_count >= len(key_fields) // 2

    async def _list_thirdparties_db(
        self,
        limit: int = 100,
        page: int = 0,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
        sqlfilters: str | None = None,
        rest_data: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Direct database query fallback for listing thirdparties."""
        import aiomysql
        
        # Parse sqlfilters for customer/supplier filter
        where_clauses = ["s.entity = %s"]
        params = [1]  # entity = 1
        
        if sqlfilters:
            # Parse simple filters like "t.client:=1"
            import re
            for match in re.finditer(r'(\w+)\s*:=\s*([^&\s]+)', sqlfilters):
                field, value = match.groups()
                if field == "t.client":
                    where_clauses.append("s.client = %s")
                    params.append(int(value))
                elif field == "t.fournisseur":
                    where_clauses.append("s.fournisseur = %s")
                    params.append(int(value))
                elif field == "t.status":
                    where_clauses.append("s.statut = %s")
                    params.append(int(value))
        
        # Map Dolibarr sort fields to DB columns
        sort_map = {
            "rowid": "s.rowid",
            "nom": "s.nom",
            "name": "s.nom",
            "ref": "s.ref",
            "date_creation": "s.date_creation",
            "date_modification": "s.tms",
            "email": "s.email",
            "phone": "s.phone",
            "client": "s.client",
            "fournisseur": "s.fournisseur",
            "status": "s.statut",
        }
        order_by = sort_map.get(sortfield, "s.nom")
        order_dir = "DESC" if sortorder.upper() == "DESC" else "ASC"
        
        offset = page * limit
        
        query = f"""
            SELECT 
                s.rowid as id,
                s.nom as name,
                s.code_client as ref,
                s.client,
                s.fournisseur,
                s.statut as status,
                s.email,
                s.phone,
                s.datec as date_creation,
                s.tms as date_modification,
                s.entity
            FROM llx_societe s
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_by} {order_dir}
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        
        try:
            # Use instance DB credentials if available, otherwise fall back to defaults
            db_host = self.db_host or self.base_url.replace("http://", "").split(":")[0] or "127.0.0.1"
            db_port = self.db_port or 3306
            db_user = self.db_user or "dolibarr_development"
            db_password = self.db_password or "nyKvdT4NC0tMV1tRQrwqP4NXWiD-ZuL6pTnT-xNszJI"
            db_name = self.db_name or "dolibarr_development"
            
            conn = await aiomysql.connect(
                host=db_host,
                port=db_port,
                user=db_user,
                password=db_password,
                db=db_name,
                autocommit=True,
            )
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()
            conn.close()
            
            # Convert to Dolibarr API format
            return [
                {
                    "id": r["id"],
                    "nom": r["name"],
                    "name": r["name"],
                    "ref": r["ref"],
                    "client": r["client"],
                    "fournisseur": r["fournisseur"],
                    "statut": r["status"],
                    "status": r["status"],
                    "email": r["email"],
                    "phone": r["phone"],
                    "date_creation": r["date_creation"],
                    "date_modification": r["date_modification"],
                    "entity": r["entity"],
                }
                for r in rows
            ]
        except Exception:
            # If DB fallback fails, return original REST API data
            return rest_data or []

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
            page: Número de página 0-based (default 0)
            sortfield: Campo de ordenación (rowid, ref, label, type, status, price, etc.)
            sortorder: Orden (ASC, DESC)
            type: Filtrar por tipo (0=PRODUCT, 1=SERVICE)
            status: Filtrar por estado (0=borrador, 1=activo, etc.)
            sqlfilters: Filtros SQL avanzados de Dolibarr
            pagination_data: Si true, devuelve metadata de paginación (total, page, etc.)

        Returns:
            Lista de productos, o dict con 'data' y 'pagination' si pagination_data=True
        """
        if page < 0:
            page = 0
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
                    },
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
                    },
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
                    },
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
    # PROPUESTAS COMERCIALES (PROPALS)
    # =========================================================================

    async def list_proposals(
        self,
        limit: int = 100,
        page: int = 1,
        status: int | None = None,
        thirdparty_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        sortfield: str = "date",
        sortorder: str = "DESC",
    ) -> list[dict[str, Any]]:
        """Listar propuestas comerciales (propals)."""
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
        if status is not None:
            params["status"] = status
        if thirdparty_id is not None:
            params["thirdparty_ids"] = str(thirdparty_id)
        if date_from is not None:
            params["date_from"] = int(datetime.combine(date_from, datetime.min.time()).timestamp())
        if date_to is not None:
            params["date_to"] = int(datetime.combine(date_to, datetime.max.time()).timestamp())

        result = await self._request("GET", "propals", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

    async def get_proposal(self, proposal_id: int) -> dict[str, Any]:
        """Obtener propuesta por ID."""
        return await self._request("GET", f"propals/{proposal_id}")

    async def create_proposal(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear propuesta comercial."""
        result: dict[str, Any] | int = await self._request("POST", "propals", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_proposal(result)
        return result

    async def add_proposal_line(self, proposal_id: int, line_data: dict[str, Any]) -> dict[str, Any]:
        """Añadir línea a propuesta."""
        return await self._request("POST", f"propals/{proposal_id}/lines", json=line_data)

    async def validate_proposal(self, proposal_id: int) -> dict[str, Any]:
        """Validar propuesta (estado 0 → 1)."""
        return await self._request("POST", f"propals/{proposal_id}/validate")

    # =========================================================================
    # FACTURAS CLIENTE (INVOICES) - V3
    # =========================================================================

    async def create_invoice(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear factura de cliente."""
        result: dict[str, Any] | int = await self._request("POST", "invoices", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_invoice(result)
        return result

    async def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Obtener factura de cliente por ID."""
        return await self._request("GET", f"invoices/{invoice_id}")

    async def add_invoice_line(self, invoice_id: int, line_data: dict[str, Any]) -> dict[str, Any]:
        """Añadir línea a factura de cliente."""
        return await self._request("POST", f"invoices/{invoice_id}/lines", json=line_data)

    async def validate_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Validar factura (estado 0 → 1)."""
        return await self._request("POST", f"invoices/{invoice_id}/validate")

    async def cancel_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Anular factura (estado 1 → 3)."""
        return await self._request("POST", f"invoices/{invoice_id}/cancel")

    async def create_invoice_from_proposal(self, proposal_id: int, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Crear factura copiando datos de una propuesta validada."""
        # Obtener propuesta
        proposal = await self.get_proposal(proposal_id)
        if not proposal:
            raise DolibarrException(
                message=f"Propuesta {proposal_id} no encontrada",
                endpoint=f"propals/{proposal_id}",
                status_code=404,
            )
        
        # Verificar que la propuesta está validada (status=1)
        if proposal.get("status", 0) != 1:
            raise DolibarrException(
                message="Solo se pueden facturar propuestas validadas (status=1)",
                endpoint=f"propals/{proposal_id}",
                status_code=400,
            )

        # Preparar datos de factura basados en propuesta
        invoice_data = {
            "fk_soc": proposal.get("fk_soc"),
            "date": int(datetime.now().timestamp()),
            "date_lim_reglement": proposal.get("date_valid", int((datetime.now() + timedelta(days=30)).timestamp())),
            "cond_reglement_id": proposal.get("cond_reglement_id"),
            "mode_reglement_id": proposal.get("mode_reglement_id"),
            "note_private": proposal.get("note_private", ""),
            "note_public": proposal.get("note_public", ""),
            "fk_origin": proposal_id,
            "origin_type": "propal",
        }

        # Aplicar overrides si se proporcionan
        if overrides:
            invoice_data.update(overrides)

        # Crear factura
        invoice = await self.create_invoice(invoice_data)
        invoice_id = invoice.get("id")
        if not invoice_id:
            raise DolibarrException(
                message="No se pudo crear la factura desde la propuesta",
                endpoint="invoices",
                status_code=500,
            )

        # Copiar líneas de la propuesta
        proposal_lines = await self._request("GET", f"propals/{proposal_id}/lines")
        lines = proposal_lines.get("data", []) if isinstance(proposal_lines, dict) else proposal_lines

        for line in lines:
            line_data = {
                "label": line.get("label") or line.get("description"),
                "qty": line.get("qty"),
                "price_ht": line.get("price_ht"),
                "tva_tx": line.get("tva_tx"),
                "remise_percent": line.get("remise_percent", 0),
                "fk_product": line.get("fk_product"),
                "fk_origin_line": line.get("id"),
                "origin_type": "propal",
            }
            await self.add_invoice_line(invoice_id, line_data)

        # Retornar factura completa
        return await self.get_invoice(invoice_id)

    # =========================================================================
    # FACTURAS PROVEEDOR (SUPPLIER INVOICES) - V3 (ya existían parcialmente)
    # =========================================================================

    # create_supplier_invoice, add_supplier_invoice_line, validate_supplier_invoice ya existen

    # =========================================================================
    # PEDIDOS (ORDERS) - V3
    # =========================================================================

    async def create_order(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear pedido de cliente."""
        result: dict[str, Any] | int = await self._request("POST", "orders", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_order(result)
        return result

    async def get_order(self, order_id: int) -> dict[str, Any]:
        """Obtener pedido por ID."""
        return await self._request("GET", f"orders/{order_id}")

    async def add_order_line(self, order_id: int, line_data: dict[str, Any]) -> dict[str, Any]:
        """Añadir línea a pedido."""
        return await self._request("POST", f"orders/{order_id}/lines", json=line_data)

    async def validate_order(self, order_id: int) -> dict[str, Any]:
        """Validar pedido (estado 0 → 1)."""
        return await self._request("POST", f"orders/{order_id}/validate")

    # =========================================================================
    # PAGOS / COBROS (PAYMENTS) - V3
    # =========================================================================

    async def create_payment(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear cobro (pago de cliente)."""
        result: dict[str, Any] | int = await self._request("POST", "payments", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_payment(result)
        return result

    async def get_payment(self, payment_id: int) -> dict[str, Any]:
        """Obtener cobro por ID."""
        return await self._request("GET", f"payments/{payment_id}")

    async def create_supplier_payment(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear pago a proveedor."""
        result: dict[str, Any] | int = await self._request("POST", "supplier_payments", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_supplier_payment(result)
        return result

    async def get_supplier_payment(self, payment_id: int) -> dict[str, Any]:
        """Obtener pago a proveedor por ID."""
        return await self._request("GET", f"supplier_payments/{payment_id}")

    # =========================================================================
    # MOVIMIENTOS DE STOCK - V3
    # =========================================================================

    async def create_stock_movement(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear movimiento de stock.
        
        Tipos:
        - 0: Entrada (receipt)
        - 1: Salida (delivery)
        - 2: Traslado (transfer)
        - 3: Inventario (inventory adjustment)
        """
        result: dict[str, Any] | int = await self._request("POST", "stockmovements", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_stock_movement(result)
        return result

    async def get_stock_movement(self, movement_id: int) -> dict[str, Any]:
        """Obtener movimiento de stock por ID."""
        return await self._request("GET", f"stockmovements/{movement_id}")

    async def get_stock(self, product_id: int, warehouse_id: int) -> dict[str, Any]:
        """Obtener stock actual de un producto en un almacén."""
        return await self._request("GET", f"products/{product_id}/stock", params={"warehouse_id": warehouse_id})

    # =========================================================================
    # PROYECTOS - V3
    # =========================================================================

    async def create_project(self, data: dict[str, Any]) -> dict[str, Any]:
        """Crear proyecto."""
        result: dict[str, Any] | int = await self._request("POST", "projects", json=data)
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        elif isinstance(result, int):
            return await self.get_project(result)
        return result

    async def get_project(self, project_id: int) -> dict[str, Any]:
        """Obtener proyecto por ID."""
        return await self._request("GET", f"projects/{project_id}")

    async def add_project_task(self, project_id: int, task_data: dict[str, Any]) -> dict[str, Any]:
        """Añadir tarea a proyecto."""
        return await self._request("POST", f"projects/{project_id}/tasks", json=task_data)

    async def impute_hours(self, project_id: int, task_id: int, hours_data: dict[str, Any]) -> dict[str, Any]:
        """Imputar horas a tarea de proyecto."""
        return await self._request("POST", f"projects/{project_id}/tasks/{task_id}/imputations", json=hours_data)

    # =========================================================================
    # BC3 (CONSTRUCCIÓN) - V3
    # =========================================================================

    async def import_bc3(self, file_data: bytes, project_name: str) -> dict[str, Any]:
        """Importar archivo BC3 y crear catálogo."""
        # BC3 se importa como documento y luego se procesa
        # Por simplicidad, subimos como documento y retornamos info para procesamiento posterior
        url = f"/api/index.php/documents/upload"
        
        files = {"file": (f"{project_name}.bc3", file_data, "application/xml")}
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
                raise DolibarrException(
                    message=f"Error subiendo BC3: {response.text}",
                    endpoint=url,
                    status_code=response.status_code,
                )
            
            result = response.json()
            doc_id = result.get("data", {}).get("id") if isinstance(result.get("data"), dict) else result.get("id")
            
            return {
                "document_id": doc_id,
                "project_name": project_name,
                "status": "uploaded",
                "message": "BC3 subido. Procesar con procesador BC3 externo o script.",
            }
            
        except httpx.TimeoutException:
            raise DolibarrException(message="Timeout importando BC3", endpoint=url, status_code=504)
        except httpx.RequestError as e:
            raise DolibarrException(message=f"Error importando BC3: {e}", endpoint=url, status_code=502)

    async def export_bc3(self, project_id: int) -> bytes:
        """Exportar proyecto/presupuesto a formato BC3.
        
        Nota: La exportación BC3 nativa puede no estar disponible en Dolibarr API estándar.
        Requiere implementación personalizada o módulo específico.
        """
        # Por ahora, retornar error indicando que requiere implementación
        raise DolibarrException(
            message="Exportación BC3 requiere módulo específico o implementación personalizada",
            endpoint=f"projects/{project_id}/export_bc3",
            status_code=501,
        )

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
