# AGENTS.md — Instrucciones para Agentes IA (OpenCode, Claude, etc.)

> **Este archivo define las reglas obligatorias para cualquier agente IA que trabaje en este repositorio.**
> El incumplimiento sistemático de estas reglas será causa de rechazo de PRs.

---

## 1. Arquitectura Básica del Proyecto

```
Gestor-IA/
├── core/                          # CORE GENÉRICO (compartido, SIN lógica de negocio)
│   ├── hermes/                    # FastAPI multi-instancia
│   │   ├── config.py              # GlobalSettings (infra global)
│   │   ├── instance_config.py     # InstanceConfig, DatabaseConfig, DolibarrConfig, etc.
│   │   ├── context.py             # CompanyContext (INMUTABLE, request-scoped)
│   │   ├── resolver.py            # InstanceResolver (middleware + deps)
│   │   ├── ai.py                  # AIProvider abstraction (Ollama/NVIDIA/OpenAI)
│   │   ├── policy.py              # AIPolicy, ModelRouter
│   │   ├── audit.py               # AuditLogger (MariaDB)
│   │   ├── extensions.py          # ExtensionRegistry (agents/tools/workflows por instancia)
│   │   ├── cli/                   # CLI interno
│   │   └── main.py                # FastAPI app + endpoints multi-instancia
│   └── integrations/
│       ├── dolibarr/client.py     # DolibarrClient (config explícita por CompanyContext)
│       ├── telegram/client.py     # TelegramClient
│       └── cloudflare/manager.py  # CloudflareAdapter + Manager
├── companies/                     # CÓDIGO ESPECÍFICO POR EMPRESA (fuera del core)
│   └── {instancia}/               # Extensiones, agentes, workflows propios
├── instances/                     # Configuraciones runtime (generadas, NO versionadas salvo example)
│   └── {instancia}/
│       ├── config.yml             # InstanceConfig (sin secretos reales)
│       ├── instance.env           # Secretos (GITIGNORED)
│       └── .gitignore
├── scripts/                       # Operaciones nativas (bash + Python CLI)
├── tests/                         # Tests organizados por tipo
│   ├── unit/                      # Unitarios (rápidos, sin BD)
│   ├── integration/               # Integración (requieren BD nativas)
│   ├── isolation/                 # CRÍTICOS: aislamiento cross-instancia
│   ├── e2e/                       # End-to-end HTTP
│   ├── commands/                  # Tests de capa de comandos
│   └── insights/                  # Tests de business insights
├── Makefile                       # Interfaz fina (delega a scripts/CLI)
├── pyproject.toml                 # Ruff, MyPy, Pytest config
└── README.md                      # Arquitectura real, no aspiracional
```

### Principios Innegociables (del README)

| Principio | Descripción |
|-----------|-------------|
| **1 empresa = 1 instancia** | No Dolibarr MultiCompany. Cada empresa: BD propia, usuario propio, Telegram Bot propio, dominio, workflows, agentes, política IA independientes. |
| **Hermes Core compartido** | Un único proceso FastAPI sirve a todas las instancias. El Core NO conoce lógica de negocio específica. |
| **CompanyContext explícito** | Toda operación empresarial lleva su `CompanyContext` inmutable. No hay variables globales mutables, no hay `CURRENT_COMPANY`. |
| **InstanceConfig como fuente de verdad** | Configuración declarativa por instancia en `instances/{instance_id}/config.yml`. |
| **AIProvider abstraction** | Ollama (local), NVIDIA, OpenAI, Anthropic intercambiables. |
| **AIPolicy** | Decide LOCAL vs CLOUD por tarea/sensibilidad (`LOCAL_ONLY` por defecto). |
| **Tests de aislamiento prioritarios** | Empresa A ≠ Empresa B se demuestra con tests antes de cualquier feature de negocio. |

---

## 2. Zonas que NO Deben Modificarse Sin Motivo Justificado

### 🔴 PROHIBIDO tocar sin aprobación explícita y razón arquitectónica documentada:

- `core/hermes/config.py` — GlobalSettings (infraestructura global)
- `core/hermes/instance_config.py` — Estructura InstanceConfig / DatabaseConfig / DolibarrConfig
- `core/hermes/context.py` — CompanyContext, CompanyContextBuilder (inmutabilidad sagrada)
- `core/hermes/resolver.py` — InstanceResolver (orden de resolución estricto)
- `core/hermes/ai.py` — AIProvider abstraction
- `core/hermes/policy.py` — AIPolicy, ModelRouter
- `core/hermes/audit.py` — AuditLogger (inmutabilidad, query por instancia)
- `core/hermes/extensions.py` — ExtensionRegistry
- `core/integrations/dolibarr/client.py` — DolibarrClient multi-instancia
- `core/integrations/telegram/client.py` — TelegramClient multi-instancia
- `core/integrations/cloudflare/manager.py` — CloudflareAdapter/Manager
- `tests/isolation/` — **Tests de aislamiento cross-instancia (CRÍTICOS)**
- `instances/{id}/config.yml` — Configuraciones de instancias reales
- `.env`, `instances/{id}/instance.env` — **Secretos (NUNCA en Git)**

### 🟡 MODIFICABLE con precaución y tests:

- `core/hermes/main.py` — Endpoints (añadir SOLO si es genérico multi-instancia)
- `core/hermes/commands/` — Capa de comandos genérica
- `core/hermes/tools/` — Herramientas genéricas (ToolRegistry)
- `core/hermes/insights/` — Business insights genéricos
- `core/hermes/query/` — Query layer genérica (read-only)
- `companies/{instancia}/` — **Aquí va la lógica de negocio específica**
- `scripts/` — Scripts operativos (idempotentes, PROJECT_ROOT auto-detectado)

### 🟢 LIBRE para features nuevos:

- `companies/{instancia}/agents/` — Agentes específicos de empresa
- `companies/{instancia}/workflows/` — Workflows específicos
- `companies/{instancia}/tools/` — Herramientas específicas
- Tests nuevos en `tests/` correspondientes a funcionalidad nueva

---

## 3. Flujo Obligatorio: Rama + Pull Request

### ❌ NUNCA:
- Hacer commit directamente en `main`
- Hacer force-push en `main` o `main` protegida
- Borrar ramas existentes
- Saltarse la revisión humana

### ✅ SIEMPRE:
1. **Crear rama nueva desde `main`** con nombre predecible:
   - Issues: `oc/issue-{número}-{descripción-corta}`
   - Features: `feat/{descripción-corta}`
   - Fixes: `fix/{descripción-corta}`
   - Chores: `chore/{descripción-corta}`
2. **Implementar exclusivamente el alcance definido** en el Issue/PR
3. **Ejecutar tests relevantes** (ver sección 4)
4. **Ejecutar quality checks**: `make pre-commit`
5. **Commit convencional** con mensaje claro
6. **Push de la rama**
7. **Abrir Pull Request contra `main`**
8. **Esperar aprobación humana** para merge

---

## 4. Obligación de Ejecutar Tests

### Tests MÍNIMOS obligatorios antes de cualquier PR:

```bash
make test-unit          # Unitarios (siempre)
make test-isolation     # CRÍTICOS: aislamiento cross-instancia (SIEMPRE)
make pre-commit         # lint + format + type-check (SIEMPRE)
```

### Tests adicionales según alcance:

```bash
make test-integration   # Si tocas BD, DolibarrClient, Redis, APIs externas
make test-e2e           # Si tocas webhooks, HTTP endpoints, flujos completos
make test-commands      # Si tocas capa de comandos
make test-insights      # Si tocas business insights
```

### Cobertura:
```bash
make test-cov           # Para verificar cobertura (objetivo: >80% en core)
```

---

## 5. Prohibiciones Absolutas

| Prohibición | Explicación |
|-------------|-------------|
| **Saltarse tests para conseguir verde** | Si un test falla, el código está roto. Arregla el código, no desactives el test. |
| **Modificar tests para ocultar un bug** | Los tests son la especificación viva. Cambiarlos sin cambiar requisitos = fraude técnico. |
| **Hacer commit de secretos** | `.env`, `instance.env`, tokens, API keys, claves Telegram, Dolibarr, Cloudflare, NVIDIA — **NUNCA en Git**. |
| **Acceso directo a MariaDB** | Si la arquitectura exige Dolibarr REST/adapters, usa DolibarrClient. No SQL directo. |
| **Modificar core de Dolibarr** | Dolibarr es externo. Extiende vía `companies/{instancia}/` o API REST. |
| **Romper aislamiento entre empresas** | `CompanyContext.instance_id` debe coincidir en DolibarrClient, UserContext, ToolRegistry, AuditLogger. |
| **Ignorar diffs antes de commit** | Siempre `git diff` y `git diff --staged` antes de commit. |

---

## 6. Política de Secretos

- **NUNCA** imprimir secretos en logs, workflows, commits, comentarios
- **NUNCA** hardcodear credenciales en código
- Usar **GitHub Secrets** para CI/CD
- Usar `.env` (global) + `instances/{id}/instance.env` (por instancia) en local
- `secrets_refs` en `config.yml` apuntan a vault/env, no contienen valores
- Rotación: `scripts/rotate-secrets.py` para credenciales operativas

---

## 7. Política Multiempresa (Multi-Instance)

- **Aislamiento estricto**: BD distinta, usuario BD distinto, Redis namespace/prefix distinto, Telegram Bot distinto, dominio distinto
- **CompanyContext** es la ÚNICA forma de acceder a datos de una instancia
- **InstanceResolver** resuelve instancia por: Header `X-Instance-ID` → Path `/webhook/{id}` → Host header → API Key
- **ExtensionRegistry** registra agents/tools/workflows POR `instance_id`
- **AuditLogger** query siempre filtrado por `instance_id`
- **Redis DB number (0-15) NO es frontera de seguridad** — usar namespace/prefix/ACL

---

## 8. Revisar Diffs Antes de Commit

```bash
# Obligatorio antes de cada commit:
git diff           # Cambios no stageados
git diff --staged  # Cambios stageados
```

Verifica:
- No hay secretos
- No hay código comentado innecesario
- No hay `print()` / `console.log()` de debug
- Cambios mínimos y enfocados
- Nomenclatura consistente con el proyecto

---

## 9. Estrategia de Modelos IA

### 🧠 Nemotron 3 Ultra (Modelo Principal - Análisis Profundo)
**Usar para:**
- Arquitectura y decisiones de diseño
- Análisis complejo de bugs/regressiones
- Debugging difícil (root cause analysis)
- Revisión crítica de seguridad
- Revisiones de código adversariales (Judgment Day)
- Decisiones que afectan multi-instancia, aislamiento, seguridad
- ADRs (Architecture Decision Records)

### ⚡ Nemotron 3.5 Lightning (Modelo Rápido - Implementación)
**Usar para:**
- Implementación bien especificada (scope claro)
- Refactors mecánicos (renombrar, mover, extraer)
- Escritura de tests (unit, integration, isolation)
- Tareas repetitivas / boilerplate
- Formateo, linting, type hints
- Documentación técnica rutinaria

### 📋 Regla de Decisión:
> **¿Requiere juicio arquitectónico, seguridad, o análisis de causa raíz? → Nemotron 3 Ultra**  
> **¿Es implementación directa de spec ya validada? → Nemotron 3.5 Lightning**

---

## 10. Comandos de Referencia Rápida

```bash
# Ver todo disponible
make help

# Tests
make test                 # Todos (unit + integration + isolation)
make test-unit            # Solo unitarios
make test-integration     # Integración (requiere BD)
make test-isolation       # CRÍTICOS: aislamiento cross-instancia
make test-e2e             # End-to-end HTTP
make test-commands        # Capa de comandos
make test-insights        # Business insights
make test-cov             # Con cobertura HTML

# Calidad
make lint                 # Ruff check
make format               # Ruff format
make type-check           # MyPy strict
make pre-commit           # lint + format + type-check (OBLIGATORIO pre-PR)

# Instancias
make instance-list                    # Listar
make instance-status INSTANCE=xxx     # Estado
make instance-enable INSTANCE=xxx     # Habilitar
make instance-disable INSTANCE=xxx    # Deshabilitar
make check-instance INSTANCE=xxx      # Verificar DB/Redis/Dolibarr API

# Limpieza
make clean              # __pycache__, .pytest_cache, .coverage, htmlcov
make clean-logs         # journalctl vacuum 7d (requiere root)

# Docker (SOLO tests/CI)
make docker-test-up     # MariaDB/Redis test en puertos 55432/56379
make docker-test-down   # Bajar servicios test
```

---

## 11. Checklist Pre-PR (Obligatorio)

- [ ] Rama creada desde `main` con nombre correcto
- [ ] Alcance limitado al Issue/PR original
- [ ] `make test-unit` ✅
- [ ] `make test-isolation` ✅ (CRÍTICO)
- [ ] `make pre-commit` ✅ (lint + format + type-check)
- [ ] Tests adicionales según alcance (integration, e2e, commands, insights)
- [ ] `git diff` revisado — sin secretos, sin debug, cambios mínimos
- [ ] Commit convencional: `tipo(scope): descripción`
- [ ] PR description incluye: qué cambió, tests ejecutados, riesgos, ref al Issue
- [ ] No se modificó funcionalidad de negocio ajena al scope
- [ ] No se tocó core genérico sin justificación arquitectónica

---

## 12. Referencias

- **README.md** — Arquitectura completa, principios, estructura
- **docs/architecture/** — ADRs (cuando existan)
- **pyproject.toml** — Configuración de herramientas (ruff, mypy, pytest)
- **Makefile** — Interfaz de comandos operativos
- **.github/workflows/** — CI/CD y automatización OpenCode

---

*Última actualización: 2025-09-04 — Versión inicial para configuración GitHub + OpenCode*