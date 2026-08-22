"""
Cliente HTTP para comunicación con Dolibarr API REST.

REUTILIZADO desde Transvega Animal - adapters/dolibarr/client.py
Adaptado para recibir config explícita por instancia (NO settings globales).
"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
import structlog

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
    # HEALTH CHECK
    # =========================================================================

    async def health_check(self) -> bool:
        """Verificar conectividad con Dolibarr."""
        try:
            await self._request("GET", "thirdparties", params={"limit": 1})
            return True
        except Exception:
            return False

    # =========================================================================
    # TERCEROS (Thirdparties)
    # =========================================================================

    async def list_thirdparties(
        self,
        limit: int = 100,
        offset: int = 0,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
        sqlfilters: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "limit": limit,
            "offset": offset,
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

        offset = 0
        pages_checked = 0
        while pages_checked < max_pages:
            parties = await self.list_thirdparties(
                limit=page_size,
                offset=offset,
            )
            if not parties:
                break

            for party in parties:
                party_vat = self._normalize_tax_id(party.get("vat_number", "") or party.get("vatnumber", ""))
                if party_vat == normalized_search:
                    return party

            if len(parties) < page_size:
                break
            offset += page_size
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
        offset: int = 0,
        sortfield: str = "rowid",
        sortorder: str = "ASC",
    ) -> list[dict[str, Any]]:
        params = {"limit": limit, "offset": offset, "sortfield": sortfield, "sortorder": sortorder}
        result = await self._request("GET", "products", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

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
        offset: int = 0,
        status: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        result = await self._request("GET", "invoices", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

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
        offset: int = 0,
        status: int | None = None,
        thirdparty_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if thirdparty_id is not None:
            params["thirdparty_ids"] = str(thirdparty_id)
        result = await self._request("GET", "supplierinvoices", params=params)
        return result.get("data", []) if isinstance(result, dict) else result

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
