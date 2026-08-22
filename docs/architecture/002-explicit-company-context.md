# ADR 002: Explicit CompanyContext

## Status
Accepted

## Context
En arquitecturas multi-tenant tradicionales, se suele usar:
- Variables globales mutables (`CURRENT_COMPANY`, `current_tenant`)
- Middleware que setea `request.tenant` y los handlers leen de ahí
- Clientes (DB, HTTP) reconfigurables globalmente (`db.set_tenant(id)`)

Estos patrones son frágiles:
- Fugas en async/concurrencia (task switching pierde contexto)
- Tests difíciles (requieren setup/teardown de estado global)
- Imposible paralelizar operaciones de distintas empresas
- Debugging opaco (¿qué empresa estaba activa en este log?)

## Decision
**CompanyContext inmutable, request-scoped, propagado explícitamente**.

```python
@dataclass(frozen=True, slots=True)
class CompanyContext:
    instance_config: InstanceConfig  # Config completa de la instancia
    actor_type: str                  # "telegram_user", "api_key", "system", "webhook"
    actor_id: str                    # user_id, api_key_id, "system", "cron"
    request_id: str                  # UUID para trazabilidad
    correlation_id: str | None       # Para rastrear requests relacionados
    # ... metadata HTTP (ip, user_agent, endpoint, method)
```

### Reglas innegociables
1. **NO variables globales mutables** para cambiar de empresa
2. **NO clientes reconfigurables** — `create_dolibarr_client()` usa `ctx.instance_config.dolibarr`
3. **NO `CURRENT_COMPANY`** — el contexto se inyecta como dependency: `Depends(get_company_context)`
4. **Inmutable** — `frozen=True, slots=True` evita mutación accidental
5. **Explícito en firma** — todo handler que necesite contexto lo declara en parámetros

### Flujo
```
Request
  ↓
InstanceResolver (middleware/dependency)
  - Header X-Instance-ID
  - Path /webhook/{instance_id}/
  - Host header → DomainConfig
  - API Key gsk_{b64_instance_id}_{key}
  ↓
InstanceConfig cargada + validada (activa, existe)
  ↓
CompanyContextBuilder → CompanyContext (inmutable)
  ↓
Inyectado en handlers vía Depends(get_company_context)
```

## Consequences
### Positivos
- Trazabilidad completa: cada log incluye `instance_id`, `request_id`, `actor`
- Tests triviales: `create_test_context(instance_id="test")` sin mocks globales
- Concurrencia segura: contextos independientes por task/request
- Refactoring seguro: firma de funciones expone dependencias

### Negativos
- Verbosidad en handlers (parámetro extra)
- Curva de aprendizaje para equipo acostumbrado a globals

## Implementation Notes
- `core/hermes/context.py`: `CompanyContext`, `CompanyContextBuilder`, extractors
- `core/hermes/resolver.py`: `resolve_instance_config()`, `get_company_context()`, `InstanceResolutionMiddleware`
- `core/hermes/main.py`: endpoints usan `ctx: CompanyContext = Depends(get_company_context)`