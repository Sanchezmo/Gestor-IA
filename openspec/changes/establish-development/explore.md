# Exploración: Establecer Entorno DEVELOPMENT

## Resumen de Arquitectura Actual

**Gestor-IA / Hermes** - Interfaz inteligente para operar Dolibarr desde Telegram.

### Stack Tecnológico
- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Telegram**: Bot API via webhook
- **ERP**: Dolibarr REST API (core/integrations/dolibarr/client.py)
- **Cache/Idempotencia**: Redis (namespace por instancia: `hermes:{instance_id}:...`)
- **Persistencia identidades**: SQLite por instancia (`instances/{instance_id}/identities.db`)
- **IA**: Ollama (local) + proveedores cloud (NVIDIA, OpenAI) - política configurable
- **Infraestructura**: Docker Compose (MariaDB, Dolibarr, Redis, Ollama)
- **Túnel**: Cloudflare Tunnel para webhook público

### Arquitectura de Autorización (Refactorizada Recientemente)
```
Telegram User
    ↓
IdentityResolver (core/hermes/identity_resolver.py)
    ↓
CompanyContext (core/hermes/context.py)
    ↓
Dolibarr User + API KEY DEL USUARIO
    ↓
DolibarrClient user-scoped
    ↓
Dolibarr REST API
    ↓
Dolibarr DECIDE PERMISOS (2xx/401/403/404/409/400/5xx)
```

**Principio**: Dolibarr = AUTORIDAD DE PERMISOS ERP. Hermes = IDENTIDAD + AISLAMIENTO + WORKFLOW + CAPABILITIES PROPIAS.

### Capabilities de Hermes (capabilities.py)
Solo capabilities PROPIAS de Gestor-IA:
- `ai.use`, `ai.external_provider`
- `admin`, `telegram.manage`, `instance.manage`, `audit.read`
- `content.generate`
- `bc3.import`, `mass_operations`, `media.publish`, `system.manage` (experimentales)

**ERP mirrors REMOVIDOS**: `thirdparty.read`, `product.read`, `customer_invoice.read`, etc. → Dolibarr los controla.

### Command Layer V1 (Estable)
- `CREATE_THIRDPARTY` (thirdparty.create)
- `CREATE_PRODUCT` (product.create)
- `CREATE_SERVICE` (service.create)

Flujo: Preview → PendingCommand Redis → [Confirmar] [Cancelar] → Callback → Revalidación → DolibarrClient user-scoped → Dolibarr → Audit → Telegram

### Tests
- **325 passed, 1 skipped, 0 failed** (baseline)
- Cobertura: identity, authorization, commands, isolation, e2e, insights

---

## Problemas Críticos Encontrados

### 1. Admin Fallback PROHIBIDO - `core/hermes/context.py:169`
```python
def create_dolibarr_client_for_user(self, identity) -> DolibarrClient:
    api_key = identity.dolibarr_api_key if identity and identity.dolibarr_api_key else db.api_key  # FALLO: fallback a admin
```
**Debe ser FAIL CLOSED**: si el usuario no tiene API key → error, NO usar admin key.

### 2. ERP Mirrors Residuales en GestorPermissions - `core/hermes/identity.py:216-220`
```python
PRODUCT_READ = "product.read"
THIRDPARTY_READ = "thirdparty.read"
CUSTOMER_INVOICE_READ = "customer_invoice.read"
SUPPLIER_INVOICE_READ = "supplier_invoice.read"
```
**Deben eliminarse**. Dolibarr decide permisos ERP.

### 3. Infraestructura "demo" hardcodeada
- `docker-compose.demo.yml` - nombres de contenedores/redes/volúmenes con "demo"
- `scripts/demo/start-demo.sh` y `stop-demo.sh` - variables hardcoded para demo
- Healthcheck busca servicios systemd que no existen en Docker

### 4. Healthcheck Actual (servicios no corriendo)
```
[WARN] MariaDB: CONEXIÓN FALLÓ (¿docker compose up?)
[WARN] Hermes API: NO RESPONDE (¿make dev-start?)
[WARN] Dolibarr API: NO RESPONDE
```
MariaDB/Dolibarr/Redis/Hermes necesitan arrancar via Docker Compose.

---

## Inventario de Infraestructura Reutilizable (NO recrear)

| Componente | Estado | Ubicación | Reutilizable |
|------------|--------|-----------|--------------|
| **Telegram Bot** | Funcional | `instances/empresa_a/config.yml:16` | ✅ Token ya configurado |
| **Cloudflare Tunnel** | Funcional | Credenciales existentes | ✅ Tunnel ID, DNS, hostnames, ingress |
| **Dolibarr Docker** | Definido | `docker-compose.demo.yml` | ✅ MariaDB + Dolibarr + Redis + Ollama |
| **Redis** | Corriendo local | Puerto 6379, password en .env | ✅ Ya funcional |
| **Instance Configs** | Definidos | `instances/empresa_a/`, `empresa_b/` | ✅ Estructura InstanceConfig |
| **Scripts utilidad** | Existentes | `scripts/configure/`, `scripts/install/`, `scripts/services/` | ✅ Reutilizar |
| **Makefile** | Completo | `Makefile` | ✅ Comandos dev-* existentes |
| **Identidades SQLite** | Schema v2 | `core/hermes/identity_store.py` | ✅ Con columna `dolibarr_api_key` |

---

## Enfoque Recomendado por Fases

### FASE 1: Auditoría Demo (Análisis)
Clasificar cada artefacto "demo":
- **A. Genérico reutilizable** → Mover a development
- **B. Config → development** → Renombrar referencias
- **C. Nomenclatura histórica** → Mantener temporalmente
- **D. Obsoleto real** → Eliminar después

### FASE 2: Definir DEVELOPMENT
- Reutilizar `InstanceConfig` existente (no nuevo framework)
- `instances/development/` o reutilizar `empresa_a` como development
- Config NO secreta en repo, secretos en `.env` / `instance.env`

### FASE 3: Migración Conceptual
- `docker-compose.demo.yml` → `docker-compose.development.yml` (o mantener y documentar)
- `scripts/demo/` → `scripts/development/` 
- Renombrar SOLO donde aporte claridad y NO rompa servicios

### FASE 4: Lifecycle DEVELOPMENT
Reutilizar Makefile/scripts existentes:
```bash
make dev-start    # docker compose up + hermes
make dev-stop     # docker compose down + stop hermes
make dev-restart
make dev-status
make dev-health   # healthcheck completo
```

### FASE 5-18: Validación E2E Real
Arrancar stack → Healthcheck → Identity real → /terceros → /productos → NL query → Create thirdparty (preview/confirm) → Cancel → Idempotency → Product → Service → 403 restricted → Business Insights → Full restart → Tests verdes.

### FASE 19: Tests
- Ejecutar suite completa tras cualquier corrección
- No introducir regresiones (baseline: 325 passed)

### FASE 20: Limpieza Demo
Solo tras DEVELOPMENT funcionando. No borrar bot/tokens/Cloudflare/Dolibarr funcionales por estética.

---

## Riesgos

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Admin fallback activo | Seguridad: bypass permisos ERP | Fix PRIMERO (context.py:169) |
| ERP mirrors residuales | Confusión, tests falsos positivos | Fix identity.py ANTES de tests E2E |
| Docker Compose demo names | Confusión, pero funcional | Renombrar gradual, no romper |
| Telegram webhook URL | Cambiar rompe integración | Reutilizar bot/token/webhook existente |
| Cloudflare Tunnel | Recrear pierde DNS/credentials | Reutilizar tunnel existente |
| MariaDB local vs Docker | Puerto 3306 conflicto | Usar Docker Compose consistentemente |

---

## Preguntas para Propuesta

1. **Renombrar docker-compose.demo.yml?** → Recomendado: `docker-compose.development.yml` para claridad
2. **Instancia development nueva o reutilizar empresa_a?** → Reutilizar `empresa_a` renombrando a `development` (menos cambios)
3. **Fix admin fallback + ERP mirrors ANTES de infra?** → SÍ, son bloqueantes para autorización correcta
4. **Telegram bot token:** Ya en `instances/empresa_a/config.yml:16` - reutilizar
5. **Cloudflare webhook:** Verificar que apunta a Hermes development

---

## Archivos Clave a Modificar

### Críticos (autorización)
- `core/hermes/context.py:154-174` - `create_dolibarr_client_for_user` → FAIL CLOSED
- `core/hermes/identity.py:216-220` - Eliminar ERP mirrors de `GestorPermissions`

### Infraestructura
- `docker-compose.demo.yml` → `docker-compose.development.yml` (renombrar contenedores/redes/volúmenes)
- `scripts/demo/start-demo.sh` → `scripts/development/start-development.sh`
- `scripts/demo/stop-demo.sh` → `scripts/development/stop-development.sh`
- `Makefile` - Añadir/verificar `dev-start`, `dev-stop`, `dev-restart`, `dev-health`

### Instancia
- `instances/empresa_a/` → `instances/development/` (o symlink/documentar)
- `instances/development/config.yml` - Referenciar secrets via env vars

### Tests/Validación
- `tests/e2e/test_telegram_dolibarr_e2e.py` - Validar flujo real
- Healthcheck CLI - Verificar todos los componentes

---

## Próximos Pasos

1. **Propuesta** (sdd-propose): Plan detallado con scope, approach, tradeoffs
2. **Especificación** (sdd-spec): Requisitos y escenarios por fase
3. **Diseño** (sdd-design): Arquitectura técnica, migración infra, fix autorización
4. **Tareas** (sdd-tasks): Lista implementable ordenada por dependencias
5. **Aplicar** (sdd-apply): Ejecutar en batches
6. **Verificar** (sdd-verify): Tests + E2E real desde Telegram
7. **Archivar** (sdd-archive): Documentar estado final