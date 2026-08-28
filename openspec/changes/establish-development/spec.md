# Especificación: Establecer Entorno DEVELOPMENT Real y Reproducible

## Change: `establish-development`

---

## 1. Requisitos Funcionales

### FR-1: Fix Autorización Crítico (BLOQUEANTE)

| ID | Requisito | Archivo | Línea |
|----|-----------|---------|-------|
| FR-1.1 | `create_dolibarr_client_for_user` implementa FAIL CLOSED: si usuario no tiene `dolibarr_api_key` → error, NUNCA fallback a key admin/instancia | `core/hermes/context.py` | 154-174 |
| FR-1.2 | Eliminar ERP mirrors de `GestorPermissions`: `PRODUCT_READ`, `THIRDPARTY_READ`, `CUSTOMER_INVOICE_READ`, `SUPPLIER_INVOICE_READ` | `core/hermes/identity.py` | 216-220 |
| FR-1.3 | `AuthorizationService` y `CapabilityResolver` solo enforcen capabilities propias de Hermes (ai.use, admin, etc.) | `core/hermes/authorization.py`, `core/hermes/capabilities.py` | - |

**Comportamiento actual (INCORRECTO)**:
```python
# context.py:169 - FALLO: fallback a admin
api_key = identity.dolibarr_api_key if identity and identity.dolibarr_api_key else db.api_key
```

**Comportamiento requerido (FAIL CLOSED)**:
```python
if not identity or not identity.dolibarr_api_key:
    raise ValueError(
        f"User {identity.telegram_user_id if identity else 'unknown'} "
        f"has no Dolibarr API key configured. "
        f"FAIL CLOSED: no admin fallback."
    )
api_key = identity.dolibarr_api_key
```

---

### FR-2: Infraestructura DEVELOPMENT

| ID | Requisito | Detalle |
|----|-----------|---------|
| FR-2.1 | Renombrar `docker-compose.demo.yml` → `docker-compose.development.yml` | Contenedores: `dolibarr-demo`→`dolibarr-development`, `mariadb-demo`→`mariadb-development`, `redis-demo`→`redis-development`, `ollama-demo`→`ollama-development`. Red: `demo-network`→`development-network`. Volúmenes: `dolibarr-demo-*`→`dolibarr-development-*`, etc. |
| FR-2.2 | Renombrar `scripts/demo/` → `scripts/development/` | `start-demo.sh`→`start-development.sh`, `stop-demo.sh`→`stop-development.sh`. Actualizar logs/env vars a DEVELOPMENT. |
| FR-2.3 | Makefile targets: `dev-start`, `dev-stop`, `dev-restart`, `dev-status`, `dev-health` | `dev-start`: docker compose up + hermes. `dev-stop`: docker compose down + pkill hermes. `dev-health`: healthcheck CLI completo. |
| FR-2.4 | Healthcheck CLI valida todos los componentes | Hermes API, Redis, Dolibarr Web, Dolibarr REST, MariaDB, Ollama, Cloudflare, Telegram webhook, AI provider. Resultado: OK/WARNING/FAIL. No expone secretos. |

---

### FR-3: Configuración Instancia DEVELOPMENT

| ID | Requisito | Detalle |
|----|-----------|---------|
| FR-3.1 | Crear `instances/development/` (reutilizar `empresa_a` como base) | Copiar/renombrar `instances/empresa_a/` → `instances/development/` |
| FR-3.2 | `config.yml` referencia servicios `docker-compose.development.yml` | `dolibarr.internal_url: http://dolibarr-development:80`, etc. |
| FR-3.3 | Secrets vía `secrets_refs` → env vars | `.env` global + `instances/development/instance.env` (no en Git) |
| FR-3.4 | Webhook path: `/webhook/development` | Consistente con instance_id |
| FR-3.5 | Reutilizar Telegram bot token existente | Token de `empresa_a/config.yml:16` |

---

### FR-4: Validación E2E Real (19 Fases, Sin Mocks)

| Fase | ID | Descripción | Evidencia Requerida |
|------|----|-------------|---------------------|
| 7 | FR-4.1 | Identity resolution real | Telegram user → instance → Dolibarr user → API key → enabled |
| 8 | FR-4.2 | `/terceros` read real | Lista thirdparties Dolibarr, NO error `thirdparty.read` |
| 9 | FR-4.3 | `/productos` read real | Lista products Dolibarr, NO error `product.read` |
| 10 | FR-4.4 | Natural language real | "Lista primeros clientes", "Busca cliente X", "Lista primeros productos" |
| 11 | FR-4.5 | Create thirdparty preview→confirm | Preview no escribe → Confirm escribe → Dolibarr tiene recurso |
| 12 | FR-4.6 | Cancel thirdparty | Preview → Cancel → NO existe en Dolibarr |
| 13 | FR-4.7 | Idempotencia | Confirm mismo command 2x → 1 sola creación |
| 14 | FR-4.8 | Create product | Preview → Confirm → Dolibarr tiene producto (Decimal money) |
| 15 | FR-4.9 | Create service | Preview → Confirm → Dolibarr tiene servicio |
| 16 | FR-4.10 | 403 real usuario restringido | Dolibarr 403 → "No tienes permisos en Dolibarr..." |
| 17 | FR-4.11 | Business Insights permisos | Query usa user API key → 403 respetado, no admin fallback |
| 18 | FR-4.12 | Full restart survival | Stop all → Start all → Healthcheck → datos previos accesibles |

---

### FR-5: Test Suite

| ID | Requisito |
|----|-----------|
| FR-5.1 | Suite completa pasa: 325 passed, 1 skipped, 0 failed (baseline) |
| FR-5.2 | Cero regresiones introducidas |

---

## 2. Requisitos No Funcionales

| ID | Requisito |
|----|-----------|
| NFR-1 | **Código único**: Sin duplicación demo/development/production. Diferencias solo en CONFIG/SECRETS/ENDPOINTS/DATOS/SERVICIOS EXTERNOS. |
| NFR-2 | **Secretos fuera de Git**: Solo en `.env` / `instance.env` |
| NFR-3 | **Reutilizar > Recrear**: Telegram bot, Cloudflare Tunnel, Docker infra, scripts, Makefile |
| NFR-4 | **Development usable diariamente**: Arranque simple, healthcheck, restart survival |
| NFR-5 | **Sin PRODUCTION ahora**: Solo garantizar que código no tiene hardcodes que impidan deploy futuro |

---

## 3. Escenarios (Given/When/Then)

### Escenario 1: FAIL CLOSED Authorization
```gherkin
Given un usuario Telegram vinculado a usuario Dolibarr SIN dolibarr_api_key configurado
When el usuario envía cualquier comando que requiera acceso Dolibarr
Then Hermes lanza error "User X has no Dolibarr API key configured. FAIL CLOSED: no admin fallback."
And NO se hace request a Dolibarr con admin/instance API key
```

### Escenario 2: /terceros Read Real
```gherkin
Given stack DEVELOPMENT corriendo (MariaDB, Dolibarr, Redis, Hermes, Cloudflare)
And usuario Telegram con identity válida y Dolibarr API key
When el usuario envía "/terceros"
Then Hermes resuelve identity → crea DolibarrClient user-scoped → llama Dolibarr REST
And Dolibarr devuelve lista thirdparties (2xx)
And Telegram recibe lista formateada
And NO aparece error "thirdparty.read missing"
```

### Escenario 3: Create Thirdparty Preview/Confirm
```gherkin
Given usuario envía "Crea el cliente TEST SL con CIF B12345678"
When Hermes muestra preview con botones "Confirmar" / "Cancelar"
And usuario pulsa "Confirmar"
Then DolibarrClient (user-scoped) POST a Dolibarr /thirdparties
And Dolibarr devuelve thirdparty creado con ID
And Hermes confirma creación con resource ID
And GET posterior a Dolibarr verifica existencia
```

### Escenario 4: 403 Permission Denied
```gherkin
Given usuario Dolibarr SIN permiso societe.thirdparty_customer.write
When usuario confirma creación thirdparty
Then Dolibarr devuelve 403
And Hermes responde "No tienes permisos en Dolibarr para crear terceros"
And NO se crea thirdparty
And NO se intenta admin fallback
```

### Escenario 5: Full Restart Survival
```gherkin
Given DEVELOPMENT corriendo con datos de prueba creados (thirdparty, product, service)
When `make dev-restart` ejecuta (stop all → start all)
And healthcheck pasa
When usuario envía "Busca el cliente TEST"
Then los datos creados previamente siguen accesibles
```

---

## 4. Criterios de Aceptación (Definition of Done)

### Infraestructura
- [ ] Existe único entorno DEVELOPMENT
- [ ] No dependemos conceptualmente de DEMO independiente
- [ ] Telegram bot anterior reutilizado
- [ ] Cloudflare Tunnel anterior reutilizado
- [ ] Dolibarr development funciona (containers MariaDB + Dolibarr)
- [ ] Redis funciona
- [ ] Hermes funciona (API responde)
- [ ] Webhook Telegram → Hermes funciona

### Autorización
- [ ] API key Dolibarr por usuario (FAIL CLOSED)
- [ ] No existe admin fallback
- [ ] Dolibarr controla permisos ERP
- [ ] Hermes NO replica `thirdparty.read`/`product.read`/etc.

### E2E Real
- [ ] `/terceros` funciona realmente
- [ ] `/productos` funciona realmente
- [ ] Lenguaje natural funciona realmente
- [ ] Preview NO escribe
- [ ] Cancel NO escribe
- [ ] Confirm escribe
- [ ] Idempotencia evita duplicados
- [ ] 403 Dolibarr se comunica correctamente
- [ ] Business Insights respeta permisos usuario
- [ ] Restart completo funciona

### Calidad
- [ ] Tests: 325 passed, 0 failed
- [ ] Development usable diariamente para seguir programando

---

## 5. Trazabilidad a Exploración

| Hallazgo Exploración | Requisito Spec | Prioridad |
|---------------------|----------------|-----------|
| Admin fallback en context.py:169 | FR-1.1 | CRÍTICA |
| ERP mirrors en identity.py:216-220 | FR-1.2 | CRÍTICA |
| docker-compose.demo.yml nomenclatura | FR-2.1 | ALTA |
| scripts/demo/ hardcodeado | FR-2.2 | ALTA |
| Healthcheck servicios caídos | FR-2.4 | ALTA |
| Instancia empresa_a reutilizable | FR-3.1 | ALTA |
| Tests 325 passed baseline | FR-5.1 | ALTA |

---

## 6. Fuera de Alcance (Explicito)

- ❌ Nuevas operaciones ERP (invoices, orders, proposals, BC3, stock, projects)
- ❌ Entorno PRODUCTION (bot, tunnel, Dolibarr, DNS, secrets separados)
- ❌ Migración a Ollama si API cloud actual funciona
- ❌ Limpieza cosmética nombres "demo" (solo tras DEVELOPMENT funcionando, Fase 20)
- ❌ Cambios en Command Layer V2+ (proposals, invoices, orders, payments, BC3)

---