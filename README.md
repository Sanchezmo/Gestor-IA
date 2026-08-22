# Gestor-IA

Plataforma empresarial multiempresa y multisector asistida por IA — on-premise, multiinstancia, con aislamiento probado.

---

## Qué es Gestor-IA

Gestor-IA es un **Core genérico** que permite operar múltiples empresas (instancias) sobre infraestructura compartida, manteniendo aislamiento estricto a nivel de datos, configuración y ejecución.

### Principios Innegociables

| Principio | Descripción |
|-----------|-------------|
| **1 empresa = 1 instancia** | No Dolibarr MultiCompany. Cada empresa tiene su Dolibarr, BD, usuario, Telegram Bot, dominio, workflows, agentes y política de IA independientes. |
| **Hermes Core compartido** | Un único proceso FastAPI (Hermes) sirve a todas las instancias. El Core no conoce lógica de negocio específica. |
| **CompanyContext explícito** | Toda operación empresarial lleva su `CompanyContext` inmutable. No hay variables globales mutables, no hay `CURRENT_COMPANY`. |
| **InstanceConfig como fuente de verdad** | Configuración declarativa por instancia en `instances/{instance_id}/config.yml`. |
| **AIProvider abstraction** | Ollama (local), NVIDIA, OpenAI, Anthropic intercambiables. |
| **AIPolicy** | Decide LOCAL vs CLOUD por tarea/sensibilidad (`LOCAL_ONLY` por defecto). |
| **Cloudflare Tunnel compartido** | Inicialmente un tunnel, routing por hostname. Cada empresa tiene sus dominios independientes. |
| **Runtime nativo on-premise** | systemd, Apache, MariaDB, Ollama, cloudflared. Docker solo para tests/CI. |
| **Tests de aislamiento prioritarios** | Empresa A ≠ Empresa B se demuestra con tests antes de cualquier feature de negocio. |

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        INFRAESTRUCTURA COMPARTIDA                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ MariaDB  │  │  Redis   │  │  Ollama  │  │ Cloudfl. │  │   Apache     │ │
│  │ (server) │  │ (server) │  │  (GPU)   │  │ Tunnel   │  │  (reverse)   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
            │ INSTANCIA A  │ │ INSTANCIA B  │ │ INSTANCIA N  │
            │ (empresa-a)  │ │ (empresa-b)  │ │   ...        │
            ├──────────────┤ ├──────────────┤ ├──────────────┤
            │ Dolibarr A   │ │ Dolibarr B   │ │ Dolibarr N   │
            │ DB: dolibarr │ │ DB: dolibarr │ │ DB: dolibarr │
            │ _empresa_a   │ │ _empresa_b   │ │ _empresa_n   │
            │ User: db_    │ │ User: db_    │ │ User: db_    │
            │ _empresa_a   │ │ _empresa_b   │ │ _empresa_n   │
            │ Telegram A   │ │ Telegram B   │ │ Telegram N   │
            │ Bot token A  │ │ Bot token B  │ │ Bot token N  │
            │ /webhook/    │ │ /webhook/    │ │ /webhook/    │
            │ empresa_a    │ │ empresa_b    │ │ empresa_n    │
            │ Dominios A   │ │ Dominios B   │ │ Dominios N   │
            │ dolibarr.    │ │ erp.         │ │ dolibarr.    │
            │ empresa-a.com│ │ empresa-b.es │ │ empresa-n.io │
            │ bot.empresa- │ │ hermes.      │ │ bot.empresa- │
            │ a.com        │ │ empresa-b.es │ │ n.io         │
            │ AI Policy A  │ │ AI Policy B  │ │ AI Policy N  │
            │ LOCAL_ONLY   │ │ CLOUD_ALLOWED│ │ ...          │
            └──────────────┘ └──────────────┘ └──────────────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    ▼
                    ┌─────────────────────────────────┐
                    │      HERMES CORE (FastAPI)      │
                    │  • Instance Resolver (header/   │
                    │    path/domain/api-key →       │
                    │    InstanceConfig)             │
                    │  • CompanyContext (inmutable,  │
                    │    request-scoped)             │
                    │  • DolibarrClient por contexto │
                    │  • TelegramClient por contexto │
                    │  • AIProvider + AIPolicy       │
                    │  • ExtensionRegistry (agents,  │
                    │    tools, workflows por inst.) │
                    │  • AuditLogger (MariaDB)       │
                    └─────────────────────────────────┘
```

---

## Estructura del Repositorio

```
Gestor-IA/
├── core/                          # Core genérico (compartido)
│   ├── hermes/
│   │   ├── config.py              # GlobalSettings (infra global)
│   │   ├── instance_config.py     # DatabaseConfig, DolibarrConfig, InstanceConfig, etc.
│   │   ├── context.py             # CompanyContext, CompanyContextBuilder
│   │   ├── resolver.py            # InstanceResolver (middleware + deps)
│   │   ├── ai.py                  # AIProvider (Ollama/NVIDIA/OpenAI)
│   │   ├── policy.py              # AIPolicy, ModelRouter
│   │   ├── audit.py               # AuditLogger (MariaDB)
│   │   ├── extensions.py          # ExtensionRegistry (agents/tools/workflows)
│   │   ├── cli/                   # CLI interno (instance list/status/enable/disable/check)
│   │   └── main.py                # FastAPI app + endpoints multi-instancia
│   └── integrations/
│       ├── dolibarr/client.py     # DolibarrClient (config explícita por instancia)
│       ├── telegram/client.py     # TelegramClient
│       └── cloudflare/manager.py  # CloudflareAdapter + Manager (DNS, Access, Tunnel)
├── companies/                     # Código específico por empresa (FUERA del core)
│   └── ejemplo/                   # (vacío - para futuras extensiones)
├── instances/                     # Configuraciones runtime (generadas, no versionadas salvo example)
│   └── ejemplo/                   # Template de referencia
│       ├── config.yml             # InstanceConfig (sin secretos reales)
│       ├── instance.env           # Secretos (gitignored)
│       └── .gitignore
├── scripts/                       # Operaciones nativas (bash + Python CLI)
│   ├── install/                   # Instalación por componente
│   ├── configure/                 # Configuración post-instalación
│   ├── instance/                  # Gestión de instancias (create.sh)
│   ├── services/                  # systemd start/stop/restart/status
│   └── backup/                    # Backup/restore por instancia
├── config/systemd/                # Unit files (templates con ${GESTOR_IA_ROOT})
├── tests/
│   └── isolation/                 # Tests CRÍTICOS de aislamiento cross-instancia
├── .env.example                   # Solo infraestructura global
├── Makefile                       # Interfaz fina (delega a scripts/CLI)
├── pyproject.toml                 # Ruff, MyPy, Pytest config
└── README.md
```

---

## Desarrollo

```bash
# Ver comandos disponibles
make help

# Tests
make test              # Todos los tests (unit + integration + isolation)
make test-unit         # Solo unitarios
make test-isolation    # CRÍTICOS: aislamiento cross-instancia
make test-cov          # Con cobertura

# Calidad de código
make lint              # Ruff
make format            # Ruff format
make type-check        # MyPy strict
make pre-commit        # lint + format + type-check

# Instancias
make instance-list                    # Listar instancias
make instance-status INSTANCE=ejemplo # Ver estado
make instance-enable INSTANCE=ejemplo # Habilitar
make instance-disable INSTANCE=ejemplo# Deshabilitar
make check-instance INSTANCE=ejemplo  # Verificar DB/Redis/Dolibarr API

# Limpieza
make clean             # __pycache__, .pytest_cache, .coverage, htmlcov
```

---

## Instancias

### Concepto: InstanceConfig / CompanyContext

- **InstanceConfig** (`instances/{id}/config.yml`): Configuración declarativa completa de una empresa.
  - `database`: DatabaseConfig (host, port, name, user, password)
  - `dolibarr`: DolibarrConfig (internal_url, public_url, api_key, documents_path, version)
  - `telegram`: TelegramConfig (bot_token, webhook_path, webhook_secret, ...)
  - `domains`: DomainConfig (base, dolibarr, hermes, custom)
  - `ai`: AIConfig (default_policy, ollama_model, nvidia_*, openai_*, task_policies)
  - `enabled_agents/workflows/tools`: Extensiones habilitadas
  - `secrets_refs`: Referencias a secretos (NO valores reales)

- **CompanyContext**: Contexto inmutable creado por request (middleware/resolver).
  - Lleva `instance_config` + `actor_type` + `actor_id` + trazabilidad (`request_id`, `correlation_id`)
  - Propagado explícitamente como dependency de FastAPI (`Depends(get_company_context)`)
  - Métodos de conveniencia: `create_dolibarr_client()`, `create_telegram_client()`, `get_ai_policy_for_task()`

### Flujo de Resolución de Instancia (orden estricto)

1. Header `X-Instance-ID` (API calls explícitos)
2. Path `/webhook/{instance_id}/...` (Telegram webhook)
3. Host header → DomainConfig lookup (dominios personalizados)
4. API Key `Authorization: Bearer gsk_{b64_instance_id}_{key}`

Si no se resuelve → `HTTPException 400 INSTANCE_RESOLUTION_FAILED`.

---

## Seguridad

- **Aislamiento**: Cada instancia tiene BD propia, usuario propio, Telegram Bot propio, directorios propios, Redis DB lógica distinta.
- **Localhost by default**: Hermes (127.0.0.1:8000), Ollama (127.0.0.1:11434), MariaDB (127.0.0.1:3306), Redis (127.0.0.1:6379). No puertos públicos directos.
- **Secretos**: Nunca en Git. `.env` (global) + `instances/{id}/instance.env` (por instancia) + `secrets_refs` en config.yml apuntan a vault/env.
- **Cloudflare Tunnel**: Punto de entrada controlado. HTTPS, WAF, Access (Zero Trust) por hostname.
- **Redis DB number**: NO es frontera de seguridad real (solo aislamiento lógico de claves 0-15). Para aislamiento real: namespace/prefix, ACL, o instancias Redis separadas.

---

## Estado del Proyecto

| Componente | Estado | Notas |
|------------|--------|-------|
| GlobalSettings / InstanceConfig | ✅ IMPLEMENTADO | Separación semántica DatabaseConfig vs DolibarrConfig |
| CompanyContext + InstanceResolver | ✅ IMPLEMENTADO | Inmutable, request-scoped, propagación explícita |
| DolibarrClient (multi-instancia) | ✅ IMPLEMENTADO | Config explícita por CompanyContext |
| TelegramClient + Webhook multi-instancia | ✅ IMPLEMENTADO | Idempotencia Redis por instancia |
| AIProvider (Ollama/NVIDIA/OpenAI) | ✅ IMPLEMENTADO | Abstracción + routing por AIPolicy |
| AIPolicy (LOCAL_ONLY / CLOUD_ALLOWED) | ✅ IMPLEMENTADO | Por tarea + default por instancia |
| AuditLogger (MariaDB) | ✅ IMPLEMENTADO | Inmutable, query por instancia |
| ExtensionRegistry (agents/tools/workflows) | ✅ IMPLEMENTADO | Registrado por instance_id |
| Cloudflare Adapter/Manager | ✅ IMPLEMENTADO | DNS, Access, Tunnel, Ingress dinámico |
| Tests de aislamiento (44 tests) | ✅ IMPLEMENTADO | CRÍTICOS - pasan en CI |
| Makefile + CLI interno | ✅ IMPLEMENTADO | Interfaz fina, sin lógica Python inline |
| Scripts nativos (install/configure/backup) | ✅ IMPLEMENTADO | Idempotentes, PROJECT_ROOT auto-detectado |
| Systemd templates | ✅ IMPLEMENTADO | `${GESTOR_IA_ROOT}` configurable |
| README / Documentación | ✅ IMPLEMENTADO | Arquitectura real, no aspiracional |

### En Desarrollo / Planificado

| Componente | Estado | Notas |
|------------|--------|-------|
| Cloudflare ingress apply (dinámico) | 🔄 EN DESARROLLO | Generación OK, apply + validación en progreso |
| Cloudflare Access per-instance Dolibarr | 📋 PLANIFICADO | Requiere emails admins por instancia |
| JobQueue abstraction + Celery opcional | 📋 PLANIFICADO | Diferido hasta carga real |
| Primera vertical E2E (Telegram → Context → Hermes → Dolibarr) | 📋 PLANIFICADO | Próxima fase |

### No en el Core (van en `companies/{instancia}/`)

- InvoiceProcessor, OCR pipeline
- DogIntake, Transport, Publishing, Milanuncios
- Media Agent, agentes sectoriales
- Migración completa de Transvega Animal

---

## Próximos Pasos

1. **Completar Cloudflare ingress apply + validación dry-run**
2. **Vertical E2E mínima**: Telegram webhook → CompanyContext → Hermes → Dolibarr (listar terceros)
3. **Documentar ADRs** en `docs/architecture/` (multi-instancia, CompanyContext, Redis no-seguridad, Cloudflare compartido)
4. **Configurar CI/CD** con tests de aislamiento obligatorios