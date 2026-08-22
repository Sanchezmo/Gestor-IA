"""
Cloudflare Adapter - DNS, Access, Tunnels, WAF.

REUTILIZADO desde Transvega Animal - adapters/cloudflare/manager.py
Adaptado para usar InstanceConfig y generar ingress dinámico.
"""

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from core.hermes.instance_config import InstanceConfig

logger = structlog.get_logger()


class CloudflareAdapter:
    """Adaptador bajo nivel para Cloudflare API."""

    def __init__(self, api_token: str, account_id: str):
        self.api_token = api_token
        self.account_id = account_id
        self.base_url = "https://api.cloudflare.com/client/v4"
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self):
        await self.client.aclose()

    async def _request(self, method: str, endpoint: str, json: dict = None, params: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        resp = await self.client.request(method, url, json=json, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise Exception(f"Cloudflare API error: {data.get('errors')}")
        return data.get("result", data)

    # =========================================================================
    # DNS
    # =========================================================================

    async def list_dns_records(self, zone_id: str, type: str = None, name: str = None) -> list[dict]:
        params = {}
        if type:
            params["type"] = type
        if name:
            params["name"] = name
        result = await self._request("GET", f"/zones/{zone_id}/dns_records", params=params)
        return result if isinstance(result, list) else result.get("result", [])

    async def create_dns_record(
        self,
        zone_id: str,
        type: str = "A",
        name: str = "",
        content: str = "",
        ttl: int = 300,
        proxied: bool = False,
        comment: str = "",
    ) -> dict:
        data = {
            "type": type,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
            "comment": comment,
        }
        return await self._request("POST", f"/zones/{zone_id}/dns_records", json=data)

    async def update_dns_record(self, zone_id: str, record_id: str, **kwargs) -> dict:
        return await self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", json=kwargs)

    async def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        await self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return True

    # =========================================================================
    # CLOUDFLARE ACCESS (Zero Trust)
    # =========================================================================

    async def list_access_applications(self) -> list[dict]:
        result = await self._request("GET", f"/accounts/{self.account_id}/access/apps")
        return result if isinstance(result, list) else result.get("result", [])

    async def create_access_application(
        self,
        name: str,
        domain: str,
        policies: list[dict] = None,
        session_duration: str = "24h",
    ) -> dict:
        data = {
            "name": name,
            "domain": domain,
            "type": "self_hosted",
            "session_duration": session_duration,
            "policies": policies or [],
        }
        return await self._request("POST", f"/accounts/{self.account_id}/access/apps", json=data)

    async def create_access_policy(
        self,
        application_id: str,
        name: str,
        decision: str = "allow",
        include: list[dict] = None,
        require: list[dict] = None,
    ) -> dict:
        data = {
            "name": name,
            "decision": decision,
            "include": include or [],
            "require": require or [],
        }
        return await self._request(
            "POST", f"/accounts/{self.account_id}/access/apps/{application_id}/policies", json=data
        )

    # =========================================================================
    # TUNNELS
    # =========================================================================

    async def list_tunnels(self) -> list[dict]:
        result = await self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel")
        return result if isinstance(result, list) else result.get("result", [])

    async def create_tunnel(self, name: str, config_src: str = "cloudflare") -> dict:
        data = {"name": name, "config_src": config_src}
        return await self._request("POST", f"/accounts/{self.account_id}/cfd_tunnel", json=data)

    async def update_tunnel_config(self, tunnel_id: str, config: dict) -> dict:
        return await self._request(
            "PUT", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations", json=config
        )

    async def get_tunnel_config(self, tunnel_id: str) -> dict:
        result = await self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations")
        return result if isinstance(result, dict) else result.get("result", {})

    # =========================================================================
    # WAF / FIREWALL
    # =========================================================================

    async def create_firewall_rule(
        self,
        zone_id: str,
        filter: str,
        action: str = "block",
        description: str = "",
        action_parameters: dict = None,
    ) -> dict:
        data = {
            "filter": filter,
            "action": action,
            "description": description,
            "action_parameters": action_parameters or {},
        }
        return await self._request("POST", f"/zones/{zone_id}/firewall/rules", json=data)


class CloudflareManager:
    """Gestor de alto nivel para Cloudflare."""

    def __init__(self, api_token: str, account_id: str, zone_id: str):
        self.adapter = CloudflareAdapter(api_token, account_id)
        self.zone_id = zone_id

    # =========================================================================
    # INGRESS GENERATION (MULTI-INSTANCIA)
    # =========================================================================

    def generate_ingress_config(self, instances: list["InstanceConfig"]) -> dict:
        """
        Generar configuración de ingress para TODAS las instancias activas.

        Args:
            instances: Lista de InstanceConfig activas

        Returns:
            Config dict para cloudflared
        """
        ingress_rules = []

        for instance in instances:
            if not instance.active:
                continue

            domains = instance.domains
            dolibarr_port = instance.dolibarr_apache_port

            # Dolibarr hostname
            if domains.dolibarr:
                ingress_rules.append(
                    {
                        "hostname": domains.dolibarr,
                        "service": f"http://127.0.0.1:{dolibarr_port}",
                        "originRequest": {
                            "connectTimeout": "30s",
                            "noTLSVerify": True,
                        },
                    }
                )

            # Hermes/Bot hostname
            if domains.hermes:
                ingress_rules.append(
                    {
                        "hostname": domains.hermes,
                        "service": "http://127.0.0.1:8000",  # Hermes Core puerto único
                        "originRequest": {
                            "connectTimeout": "30s",
                            "noTLSVerify": True,
                        },
                    }
                )

            # Custom hostnames
            for name, hostname in domains.custom.items():
                # Determinar puerto según nombre
                port = 8000  # default Hermes
                if "dolibarr" in name.lower():
                    port = dolibarr_port

                ingress_rules.append(
                    {
                        "hostname": hostname,
                        "service": f"http://127.0.0.1:{port}",
                        "originRequest": {
                            "connectTimeout": "30s",
                            "noTLSVerify": True,
                        },
                    }
                )

        # Catch-all 404
        ingress_rules.append({"service": "http_status:404"})

        return {
            "config": {
                "ingress": ingress_rules,
            }
        }

    async def apply_ingress_config(self, tunnel_id: str, instances: list["InstanceConfig"]) -> dict:
        """Aplicar configuración de ingress validada."""
        config = self.generate_ingress_config(instances)

        # Validar antes de aplicar
        validation = self._validate_ingress_config(config, instances)
        if not validation["valid"]:
            raise ValueError(f"Invalid ingress config: {validation['errors']}")

        await self.adapter.update_tunnel_config(tunnel_id, config)

        return {
            "success": True,
            "config": config,
            "rules_count": len(config["config"]["ingress"]) - 1,  # -1 por catch-all
        }

    def _validate_ingress_config(self, config: dict, instances: list["InstanceConfig"]) -> dict:
        """Validar configuración de ingress antes de aplicar."""
        errors = []
        seen_hostnames = set()

        for rule in config["config"]["ingress"][:-1]:  # Excluir catch-all
            hostname = rule.get("hostname")
            if not hostname:
                errors.append("Rule missing hostname")
                continue

            if hostname in seen_hostnames:
                errors.append(f"Duplicate hostname: {hostname}")
            seen_hostnames.add(hostname)

            # Verificar que el hostname pertenece a alguna instancia configurada
            # Y que el puerto del service coincide con la instancia dueña del hostname
            valid = False
            for instance in instances:
                domains = instance.domains
                all_domains = [domains.base]
                if domains.dolibarr:
                    all_domains.append(domains.dolibarr)
                if domains.hermes:
                    all_domains.append(domains.hermes)
                all_domains.extend(domains.custom.values())

                if hostname in all_domains:
                    valid = True
                    # Verificar que el puerto del service coincide con esta instancia
                    service = rule.get("service", "")
                    expected_port = None

                    # Determinar puerto esperado según el hostname
                    if hostname == domains.dolibarr:
                        expected_port = instance.dolibarr_apache_port
                    elif hostname == domains.hermes:
                        expected_port = 8000  # Hermes Core puerto único
                    else:
                        # Custom hostname - determinar por nombre
                        for name, hn in domains.custom.items():
                            if hn == hostname:
                                if "dolibarr" in name.lower():
                                    expected_port = instance.dolibarr_apache_port
                                else:
                                    expected_port = 8000
                                break

                    if expected_port and f":{expected_port}" not in service:
                        errors.append(
                            f"Hostname {hostname} belongs to instance {instance.instance_id} "
                            f"but service points to wrong port: {service} (expected port {expected_port})"
                        )
                    break

            if not valid:
                errors.append(f"Hostname {hostname} not configured in any instance")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    # =========================================================================
    # DNS MANAGEMENT PARA INSTANCIA
    # =========================================================================

    async def ensure_dns_for_instance(self, instance: "InstanceConfig", server_ip: str) -> dict:
        """Asegurar que DNS existe para todos los hostnames de una instancia."""
        results = []
        domains = instance.domains

        records_to_ensure = []

        if domains.dolibarr:
            records_to_ensure.append(("A", domains.dolibarr, server_ip, True))
        if domains.hermes:
            records_to_ensure.append(("A", domains.hermes, server_ip, True))
        for _name, hostname in domains.custom.items():
            records_to_ensure.append(("A", hostname, server_ip, True))

        for rtype, name, content, proxied in records_to_ensure:
            try:
                # Verificar si existe
                existing = await self.adapter.list_dns_records(self.zone_id, type=rtype, name=name)

                if existing:
                    # Actualizar si cambió
                    record = existing[0]
                    if record.get("content") != content or record.get("proxied") != proxied:
                        await self.adapter.update_dns_record(
                            self.zone_id, record["id"], content=content, proxied=proxied
                        )
                        results.append({"hostname": name, "action": "updated", "success": True})
                    else:
                        results.append({"hostname": name, "action": "unchanged", "success": True})
                else:
                    # Crear
                    await self.adapter.create_dns_record(
                        self.zone_id, type=rtype, name=name, content=content, proxied=proxied
                    )
                    results.append({"hostname": name, "action": "created", "success": True})

            except Exception as e:
                results.append({"hostname": name, "action": "error", "success": False, "error": str(e)})

        return {
            "success": all(r["success"] for r in results),
            "results": results,
        }

    # =========================================================================
    # ACCESS PARA DOLIBARR
    # =========================================================================

    async def setup_access_for_dolibarr(
        self,
        instance: "InstanceConfig",
        admin_emails: list[str],
    ) -> dict:
        """Configurar Cloudflare Access para Dolibarr de una instancia."""
        if not instance.domains.dolibarr:
            return {"success": False, "error": "No dolibarr domain configured"}

        domain = instance.domains.dolibarr
        app_name = f"Dolibarr - {instance.company_name}"

        # Crear aplicación Access
        app = await self.adapter.create_access_application(
            name=app_name,
            domain=domain,
        )

        # Política para emails autorizados
        include = [{"email": {"email": email}} for email in admin_emails]
        policy = await self.adapter.create_access_policy(
            application_id=app["id"],
            name=f"Admins - {instance.company_name}",
            decision="allow",
            include=include,
        )

        return {
            "success": True,
            "application": app,
            "policy": policy,
        }

    async def close(self):
        await self.adapter.close()


# =========================================================================
# FACTORY
# =========================================================================


def create_cloudflare_manager(global_settings=None) -> CloudflareManager | None:
    """Crear CloudflareManager desde GlobalSettings."""
    if global_settings is None:
        from core.hermes.config import get_global_settings

        global_settings = get_global_settings()

    if not all(
        [
            global_settings.CLOUDFLARE_API_TOKEN,
            global_settings.CLOUDFLARE_ACCOUNT_ID,
            global_settings.CLOUDFLARE_ZONE_ID,
        ]
    ):
        return None

    return CloudflareManager(
        api_token=global_settings.CLOUDFLARE_API_TOKEN,
        account_id=global_settings.CLOUDFLARE_ACCOUNT_ID,
        zone_id=global_settings.CLOUDFLARE_ZONE_ID,
    )
