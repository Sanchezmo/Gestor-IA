# Propuesta: Establecer Entorno DEVELOPMENT Real y Reproducible

## Contexto
Proyecto: **Gestor-IA / Hermes** - Interfaz inteligente Telegram → Dolibarr ERP.

**Objetivo**: Un único entorno DEVELOPMENT estable, usable diariamente para desarrollo continuo.
**NO**: Features ERP nuevas. **SÍ**: Infraestructura, autorización, validación E2E real.

---

## Decisiones Clave (Ya Tomadas)

| Decisión | Valor |
|----------|-------|
| Entornos | **AHORA**: DEVELOPMENT | **FUTURO**: PRODUCTION |
| Código | **ÚNICO** - sin duplicación, sin if demo/prod |
| Diferencias entornos | Solo: CONFIG, SECRETS, ENDPOINTS, DATOS, SERVICIOS EXTERNOS |
| Telegram Bot | **Reutilizar** el existente (token en empresa_a) |
| Cloudflare Tunnel | **Reutilizar** el existente (credentials, DNS, hostnames) |
| Dolibarr | **Reutilizar** Docker Compose actual (MariaDB + Dolibarr) |
| Redis | **Reutilizar** local (puerto 6379) |
| API Key Dolibarr | **Por usuario** - FAIL CLOSED (sin admin fallback) |
| Permisos ERP | **Dolibarr decide** - Hermes NO replica thirdparty.read/product.read/etc. |

---

## Scope de Esta Propuesta

### INCLUIDO (Must Have)
1. **Fix autorización crítica**: Admin fallback → FAIL CLOSED + eliminar ERP mirrors residuales
2. **Infraestructura DEVELOPMENT**: Docker Compose, scripts, Makefile, healthcheck
3. **Instancia DEVELOPMENT**: Config reutilizando empresa_a, secrets fuera de Git
4. **Lifecycle**: `make dev-start/stop/restart/status/health`
5. **Validación E2E real** (19 fases): Identity → /terceros → /productos → NL → Create thirdparty (preview/confirm/cancel/idempotency) → Product → Service → 403 → Business Insights → Restart → Tests

### EXCLUIDO (Out of Scope)
- Nuevas operaciones ERP (invoices, orders, proposals, BC3, etc.)
- Entorno PRODUCTION (crear bot, tunnel, Dolibarr, DNS, secrets separados)
- Migración a Ollama si API cloud funciona
- Limpieza cosmética de nombres "demo" (fase 20, solo tras funcionar)

---

## Approach

### 1. Fix Autorización (Bloqueante - Hacer PRIMERO)
**Archivos**: `core/hermes/context.py`, `core/hermes/identity.py`

```python
# context.py:154-174 - ANTES (con fallback PROHIBIDO)
api_key = identity.dolibarr_api_key if identity and identity.dolibarr_api_key else db.api_key

# DESPUÉS (FAIL CLOSED)
if not identity or not identity.dolibarr_api_key:
    raise ValueError(f"User {identity.telegram_user_id if identity else 'unknown'} has no Dolibarr API key configured. FAIL CLOSED: no admin fallback.")
api_key = identity.dolibarr_api_key
```

```python
# identity.py:216-220 - ELIMINAR
PRODUCT_READ = "product.read"
THIRDPARTY_READ = "thirdparty.read"
CUSTOMER_INVOICE_READ = "customer_invoice.read"
SUPPLIER_INVOICE_READ = "supplier_invoice.read"
```

### 2. Infraestructura DEVELOPMENT
**Renombrar/Repurposar** (no recrear):
- `docker-compose.demo.yml` → `docker-compose.development.yml`
  - Contenedores: `dolibarr-demo` → `dolibarr-development`, `mariadb-demo` → `mariadb-development`, etc.
  - Red: `demo-network` → `development-network`
  - Volúmenes: `dolibarr-demo-*` → `dolibarr-development-*`
- `scripts/demo/` → `scripts/development/`
  - `start-demo.sh` → `start-development.sh` (vars: DEVELOPMENT en lugar de DEMO)
  - `stop-demo.sh` → `stop-development.sh`
- `instances/empresa_a/` → `instances/development/` (o mantener y documentar como development)

**Makefile**: Verificar/añadir targets:
```makefile
dev-start:    # docker compose -f docker-compose.development.yml up -d + hermes
dev-stop:     # docker compose down + pkill hermes
dev-restart:  # dev-stop && dev-start
dev-status:   # docker compose ps + curl health
dev-health:   # python -m core.hermes.cli healthcheck
```

### 3. Instancia DEVELOPMENT Config
`instances/development/config.yml`:
- Referencia a `docker-compose.development.yml` services
- Secrets via `secrets_refs` → env vars (`.env` + `instance.env`)
- Webhook path: `/webhook/development`
- Telegram bot token: reutilizar el de empresa_a

### 4. Validación E2E (19 Fases)
Cada fase produce **evidencia real** (no mocks):
- Fases 7-10: READ operations (identity, /terceros, /productos, NL)
- Fases 11-15: WRITE operations (preview/confirm/cancel/idempotency/product/service)
- Fase 16: 403 real con usuario restringido
- Fase 17: Business Insights respeta permisos
- Fase 18: Full restart sobrevivido
- Fase 19: 325 tests passed

---

## Tradeoffs

| Opción | Elegida | Razón |
|--------|---------|-------|
| Renombrar docker-compose | **Sí** | Claridad: "development" ≠ "demo"; bajo riesgo (solo nombres) |
| Nueva instancia vs reutilizar empresa_a | **Reutilizar empresa_a → development** | Menos cambios, config ya funcional, bot/token ya puestos |
| Fix auth ANTES de infra | **Sí** | Bloqueante: sin auth correcta, E2E falla silenciosamente |
| Cloudflare tunnel nuevo | **No** | Reutilizar existente evita DNS/credentials nuevos |
| PRODUCTION ahora | **No** | Usuario explícito: "NO crear production todavía" |

---

## Riesgos y Mitigación

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Admin fallback causa bypass real | Alta | Crítico | Fix PRIMERO, test unitario + E2E 403 |
| ERP mirrors causan confusión tests | Media | Alto | Eliminar ANTES de tests E2E |
| Docker rename rompe volumes/data | Baja | Medio | Volúmenes nuevos (development-*), no migrar datos demo |
| Webhook Telegram cambia | Media | Alto | Reutilizar bot/token/webhook existente exactamente |
| Healthcheck falsos positivos | Media | Medio | Healthcheck real valida conectividad, no solo procesos |

---

## Criterios de Éxito (Definition of Done)

- [ ] Existe único entorno DEVELOPMENT
- [ ] No dependemos conceptualmente de DEMO
- [ ] Telegram bot reutilizado
- [ ] Cloudflare Tunnel reutilizado
- [ ] Dolibarr development funciona (MariaDB + Dolibarr containers)
- [ ] Redis funciona
- [ ] Hermes funciona (API responde)
- [ ] Webhook Telegram → Hermes funciona
- [ ] API key Dolibarr por usuario (FAIL CLOSED)
- [ ] No existe admin fallback
- [ ] Dolibarr controla permisos ERP
- [ ] Hermes NO replica thirdparty.read/product.read/etc.
- [ ] `/terceros` funciona real
- [ ] `/productos` funciona real
- [ ] Lenguaje natural funciona real
- [ ] Preview NO escribe
- [ ] Cancel NO escribe
- [ ] Confirm escribe
- [ ] Idempotencia evita duplicados
- [ ] 403 Dolibarr se comunica correctamente
- [ ] Business Insights respeta permisos usuario
- [ ] Restart completo funciona
- [ ] Tests: 325 passed, 0 failed
- [ ] Development usable diariamente

---

## Estimación de Esfuerzo

| Trabajo | Archivos | Complejidad |
|---------|----------|-------------|
| Fix auth (2 archivos) | 2 | Baja |
| Docker Compose rename | 1 | Baja |
| Scripts development | 2 | Baja |
| Makefile targets | 1 | Baja |
| Instancia development config | 1-2 | Baja |
| Healthcheck mejoras | 1 | Media |
| Validación E2E 19 fases | Manual + tests | Alta (tiempo) |
| Tests suite | Existentes | Media |

**Total estimado**: ~2-3 días de trabajo enfocado.

---

## Preguntas para Aclarar (Propuesta Round)

1. **¿Confirmas reutilizar `empresa_a` como `development` renombrando la carpeta?** (vs crear nueva `instances/development/`)
2. **¿El bot Telegram actual (`***REMOVED***`) es el correcto para DEVELOPMENT?**
3. **¿Cloudflare Tunnel actual ya apunta a un hostname que podemos usar para development webhook?** (ej: `bot.empresa-a.com` → `/webhook/development`)
4. **¿MariaDB/Dolibarr datos demo deben preservarse o limpiar para development limpio?**
5. **¿Ejecutamos validación E2E con usuario admin Dolibarr primero, y usuario restringido después (fase 16)?**