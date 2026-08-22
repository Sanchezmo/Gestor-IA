# ADR 004: Shared Cloudflare Tunnel + Independent Company Domains

## Status
Accepted

## Context
Opciones para exposición pública:
1. **Tunnel por instancia** - Un `cloudflared` + tunnel ID por empresa. Aislamiento total, pero N procesos, N logins, N configs.
2. **Tunnel compartido + routing por hostname** - Un `cloudflared`, un tunnel ID, ingress rules dinámicas. Operación simple, un punto de fallo.
3. **IPs públicas + firewall** - No viable (seguridad, costos, IPv4 escaso).

## Decision
**Inicialmente: Un tunnel Cloudflare compartido + routing por hostname**.

### Arquitectura
```
Internet
    │
    ▼
Cloudflare Edge (WAF, DDoS, Access)
    │
    ▼
cloudflared (systemd, 1 proceso)
    │
    ├── dolibarr.empresa-a.com → http://127.0.0.1:8081
    ├── bot.empresa-a.com      → http://127.0.0.1:8000
    ├── erp.empresa-b.es       → http://127.0.0.1:8082
    ├── hermes.empresa-b.es    → http://127.0.0.1:8000
    └── ... (ingress dinámico desde InstanceConfig de todas las instancias activas)
```

### Dominios por empresa (configurables, NO hardcoded)
- `InstanceConfig.domains.base` → `empresa.com` (dominio raíz)
- `InstanceConfig.domains.dolibarr` → `dolibarr.empresa.com` (ERP)
- `InstanceConfig.domains.hermes` → `bot.empresa.com` (Bot/API)
- `InstanceConfig.domains.custom` → dict adicional (`{"portal": "portal.empresa.com"}`)

### Validación de Ingress (IMPLEMENTADO)
`CloudflareManager._validate_ingress_config()` verifica:
- No hay hostnames duplicados
- Cada hostname pertenece a alguna instancia configurada
- El puerto del service coincide con la instancia dueña del hostname
  - `dolibarr.*` → `instance.dolibarr_apache_port`
  - `hermes/bot.*` → `8000` (Hermes Core puerto único)

### Cloudflare Access (PLANIFICADO)
- Una Access Application por `dolibarr.{domain}` 
- Políticas por emails de admins de la instancia (`InstanceConfig` futuro: `dolibarr_access_emails`)

## Consequences
### Positivos
- Un solo `cloudflared` process, un tunnel ID, un `cloudflared tunnel login`
- Ingress dinámico: añadir instancia = regenerar config + reload (sin tocar Cloudflare dashboard)
- Dominios independientes por empresa (no forzamos `gestor-ia.com` ni subdominios fijos)
- WAF/DDoS/Access centralizados en Cloudflare Edge

### Negativos
- Punto de fallo único: si `cloudflared` cae, todas las instancias caen
- Ingress grande = reload más lento (mitigación: validación dry-run antes de apply)
- Migración futura a multi-tunnel requiere diseño cuidadoso (evitar acoplamiento irreversible)

## Decision: No Acoplamiento Irreversible
El Core **NO** depende de:
- Exactamente 1 Cloudflare account
- Exactamente 1 tunnel ID
- Namespace de dominios fijo

`CloudflareManager` recibe `api_token`, `account_id`, `zone_id` vía `GlobalSettings`. Si mañana hay 2 accounts, se instancian 2 managers.

## Implementation Notes
- `core/integrations/cloudflare/manager.py`: `generate_ingress_config(instances)`, `apply_ingress_config()`, `_validate_ingress_config()`
- `core/integrations/cloudflare/adapter.py`: API baja nivel (DNS, Access, Tunnels, WAF)
- `scripts/configure/cloudflare.sh`: **PLANIFICADO** - apply automático + validación dry-run
- `main.py`: `_cloudflare_manager` inicializado en lifespan, pero **sin endpoints API** aún