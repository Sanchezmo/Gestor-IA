# ADR 001: Multi-Instance instead of Dolibarr MultiCompany

## Status
Accepted

## Context
Dolibarr nativamente soporta "MultiCompany" (múltiples empresas en una misma instalación compartiendo código, BD y configuración). Sin embargo, esto presenta riesgos para nuestro caso de uso:
- Fuga de datos entre empresas (terceros, facturas, productos visibles cruzados)
- Configuración compartida (módulos, plantillas, permisos) que no permite especialización por sector
- Un fallo afecta a todas las empresas
- Escalabilidad limitada (una BD, una cola, un pool de conexiones)
- Migración futura a infraestructura separada muy compleja

## Decision
Cada empresa = **una instancia independiente completa**:
- Su propio Dolibarr (directorio `/var/www/dolibarr/{instance_id}/`)
- Su propia base de datos MariaDB (`dolibarr_{instance_id}`)
- Su propio usuario BD (`db_{instance_id}`)
- Su propio Telegram Bot (token, webhook path, secret)
- Sus propios dominios (`dolibarr.empresa.com`, `bot.empresa.com`)
- Sus propios workflows, agentes, herramientas, políticas de IA
- Sus propios directorios de documentos, backups, runtime

El Core (Hermes) es **compartido y genérico** — una sola instalación FastAPI que sirve a todas las instancias via `CompanyContext`.

## Consequences
### Positivos
- Aislamiento total probado por tests (44 tests de aislamiento cross-instancia)
- Independencia operativa (deploy, backup, restore, escalado por instancia)
- Especialización por sector (agentes/herramientas específicos en `companies/{id}/`)
- Migración gradual posible (una instancia a la vez)

### Negativos
- Más recursos (RAM, CPU, puertos) por instancia
- Gestión de múltiples Dolibarr (actualizaciones, módulos)
- Cloudflare ingress más complejo (routing por hostname)

## Implementation Notes
- `InstanceConfig` en `core/hermes/instance_config.py` define el modelo completo
- `CompanyContext` en `core/hermes/context.py` propaga configuración por request
- `InstanceResolver` en `core/hermes/resolver.py` resuelve instancia ANTES de procesar contenido
- Tests en `tests/isolation/test_cross_instance.py` (obligatorios antes de cualquier feature)