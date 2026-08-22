# Makefile - Gestor-IA Core
# Interfaz simple para gestión de infraestructura nativa
# Uso: make help

.PHONY: help install configure start stop restart status check backup restore test lint format type-check clean instance-create instance-list instance-status dev-install dev-start dev-stop dev-restart docker-test-up docker-test-down

# Variables
SCRIPTS_DIR = ./scripts
PROJECT_ROOT = $(shell pwd)
PYTHON = python3
VENV_DIR = $(PROJECT_ROOT)/.venv
VENV_PYTHON = $(VENV_DIR)/bin/python
VENV_PIP = $(VENV_DIR)/bin/pip

# Colores
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
BLUE = \033[0;34m
NC = \033[0m

help: ## Mostrar esta ayuda
	@echo "$(GREEN)Gestor-IA Core - Comandos Disponibles$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-25s$(NC) %s\n", $$1, $$2}'

# =============================================================================
# INSTALACIÓN Y CONFIGURACIÓN
# =============================================================================

install: ## Instalación completa nativa (requiere root)
	@echo "$(GREEN)=== Instalación Nativa Gestor-IA ===$(NC)"
	@sudo $(SCRIPTS_DIR)/install.sh

install-deps: ## Solo dependencias base del sistema
	@sudo $(SCRIPTS_DIR)/install/dependencies.sh

install-db: ## Solo MariaDB
	@sudo $(SCRIPTS_DIR)/install/mariadb.sh

install-redis: ## Solo Redis
	@sudo $(SCRIPTS_DIR)/install/redis.sh

install-php: ## Solo PHP + Apache
	@sudo $(SCRIPTS_DIR)/install/php.sh
	@sudo $(SCRIPTS_DIR)/install/apache.sh

install-ollama: ## Solo Ollama nativo
	@sudo $(SCRIPTS_DIR)/install/ollama.sh

install-cloudflare: ## Solo Cloudflare Tunnel
	@sudo $(SCRIPTS_DIR)/install/cloudflare.sh

install-python: ## Solo Python virtualenv + dependencias
	@$(SCRIPTS_DIR)/install/python.sh

install-hermes: ## Solo Hermes Core (API + systemd)
	@$(SCRIPTS_DIR)/install/hermes.sh

configure: ## Configuración post-instalación (requiere root)
	@echo "$(GREEN)=== Configuración Post-Instalación ===$(NC)"
	@sudo $(SCRIPTS_DIR)/configure.sh

configure-apache: ## Configurar Apache VirtualHost para Dolibarr
	@sudo $(SCRIPTS_DIR)/configure/apache.sh

configure-dolibarr: ## Verificar/regenerar conf.php Dolibarr
	@sudo $(SCRIPTS_DIR)/configure/dolibarr.sh

configure-cloudflare: ## Configurar Cloudflare Tunnel ingress
	@$(SCRIPTS_DIR)/configure/cloudflare.sh

configure-services: ## Instalar servicios systemd
	@sudo $(SCRIPTS_DIR)/configure/services.sh

configure-hermes: ## Configurar variables entorno para Hermes systemd
	@$(SCRIPTS_DIR)/configure/hermes.sh

# =============================================================================
# GESTIÓN DE SERVICIOS
# =============================================================================

start: ## Iniciar todos los servicios (requiere root)
	@echo "$(GREEN)=== Iniciando Servicios ===$(NC)"
	@sudo $(SCRIPTS_DIR)/services/start.sh

stop: ## Detener todos los servicios (requiere root)
	@echo "$(YELLOW)=== Deteniendo Servicios ===$(NC)"
	@sudo $(SCRIPTS_DIR)/services/stop.sh

restart: ## Reiniciar todos los servicios (requiere root)
	@echo "$(BLUE)=== Reiniciando Servicios ===$(NC)"
	@sudo $(SCRIPTS_DIR)/services/restart.sh

status: ## Ver estado de todos los servicios con health checks
	@echo "$(GREEN)=== Estado Servicios Gestor-IA ===$(NC)"
	@$(SCRIPTS_DIR)/services/status.sh

# =============================================================================
# GESTIÓN DE INSTANCIAS
# =============================================================================

instance-create: ## Crear nueva instancia (INSTANCE=empresa_a DOMAIN=empresa.com)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@if [ -z "$(DOMAIN)" ]; then echo "$(RED)Especificar DOMAIN=empresa.com$(NC)"; exit 1; fi
	@echo "$(GREEN)=== Creando instancia $(INSTANCE) ===$(NC)"
	@$(SCRIPTS_DIR)/instance/create.sh $(INSTANCE) $(DOMAIN)

instance-list: ## Listar instancias configuradas
	@echo "$(GREEN)=== Instancias Configuradas ===$(NC)"
	@$(VENV_PYTHON) -c "
import sys
sys.path.insert(0, '$(PROJECT_ROOT)')
from core.hermes.instance_config import list_instances, load_instance_config
for iid in list_instances():
    cfg = load_instance_config(iid)
    if cfg:
        print(f'  {cfg.instance_id:20s} | {cfg.company_name:30s} | {\"active\" if cfg.active else \"inactive\"} | {cfg.domains.base}')
"

instance-status: ## Ver estado de una instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@echo "$(GREEN)=== Estado instancia $(INSTANCE) ===$(NC)"
	@$(VENV_PYTHON) -c "
import sys
sys.path.insert(0, '$(PROJECT_ROOT)')
from core.hermes.instance_config import load_instance_config
cfg = load_instance_config('$(INSTANCE)')
if cfg:
    print(f'Instance ID: {cfg.instance_id}')
    print(f'Company:     {cfg.company_name}')
    print(f'Active:      {cfg.active}')
    print(f'Dolibarr:    {cfg.database.internal_url} (DB: {cfg.database.db_name})')
    print(f'Telegram:    {cfg.telegram.webhook_path}')
    print(f'Domain:      {cfg.domains.base}')
    print(f'  Dolibarr:  {cfg.domains.dolibarr}')
    print(f'  Hermes:    {cfg.domains.hermes}')
    print(f'Agents:      {\", \".join(cfg.enabled_agents) or \"none\"}')
    print(f'Workflows:   {\", \".join(cfg.enabled_workflows) or \"none\"}')
else:
    print('Instance not found')
"

instance-enable: ## Habilitar instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@$(VENV_PYTHON) -c "
import sys, yaml
sys.path.insert(0, '$(PROJECT_ROOT)')
from pathlib import Path
from core.hermes.instance_config import load_instance_config, clear_config_cache
cfg = load_instance_config('$(INSTANCE)')
if cfg:
    cfg.active = True
    config_path = Path('$(PROJECT_ROOT)') / 'instances' / '$(INSTANCE)' / 'config.yml'
    with open(config_path, 'w') as f:
        yaml.dump(cfg.model_dump(), f, default_flow_style=False, sort_keys=False)
    clear_config_cache()
    print('Instance enabled')
else:
    print('Instance not found')
    sys.exit(1)
"

instance-disable: ## Deshabilitar instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@$(VENV_PYTHON) -c "
import sys, yaml
sys.path.insert(0, '$(PROJECT_ROOT)')
from pathlib import Path
from core.hermes.instance_config import load_instance_config, clear_config_cache
cfg = load_instance_config('$(INSTANCE)')
if cfg:
    cfg.active = False
    config_path = Path('$(PROJECT_ROOT)') / 'instances' / '$(INSTANCE)' / 'config.yml'
    with open(config_path, 'w') as f:
        yaml.dump(cfg.model_dump(), f, default_flow_style=False, sort_keys=False)
    clear_config_cache()
    print('Instance disabled')
else:
    print('Instance not found')
    sys.exit(1)
"

# =============================================================================
# VERIFICACIÓN Y DIAGNÓSTICO
# =============================================================================

check: ## Verificación profunda del entorno
	@echo "$(GREEN)=== Verificación Profunda ===$(NC)"
	@$(SCRIPTS_DIR)/check.sh

check-dolibarr: ## Healthcheck granular Dolibarr
	@$(SCRIPTS_DIR)/dolibarr-health.sh

check-apache: ## Verificar configuración Apache
	@sudo apache2ctl configtest

check-instance: ## Verificar configuración de instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@$(VENV_PYTHON) -c "
import sys
sys.path.insert(0, '$(PROJECT_ROOT)')
from core.hermes.instance_config import load_instance_config
cfg = load_instance_config('$(INSTANCE)')
if not cfg:
    print('ERROR: Instance not found')
    sys.exit(1)
errors = []
# Verificar DB
import mysql.connector
try:
    conn = mysql.connector.connect(
        host=cfg.database.db_host, port=cfg.database.db_port,
        user=cfg.database.db_user, password=cfg.database.db_password,
        database=cfg.database.db_name
    )
    conn.close()
    print('OK: MariaDB connection')
except Exception as e:
    errors.append(f'MariaDB: {e}')
# Verificar Redis
import redis
try:
    r = redis.Redis(host='127.0.0.1', port=6379, db=cfg.get_redis_db(), decode_responses=True)
    r.ping()
    r.close()
    print('OK: Redis connection')
except Exception as e:
    errors.append(f'Redis: {e}')
# Verificar Dolibarr API
import httpx
try:
    import asyncio
    async def check():
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f'{cfg.database.internal_url}/api/index.php/thirdparties?limit=1',
                          headers={'DOLAPIKEY': cfg.database.api_key})
            r.raise_for_status()
    asyncio.run(check())
    print('OK: Dolibarr API')
except Exception as e:
    errors.append(f'Dolibarr API: {e}')

if errors:
    for e in errors:
        print(f'ERROR: {e}')
    sys.exit(1)
else:
    print('All checks passed')
"

# =============================================================================
# BACKUP Y RESTAURACIÓN
# =============================================================================

backup: ## Backup completo (MariaDB + config + documentos)
	@echo "$(GREEN)=== Backup Completo ===$(NC)"
	@sudo $(SCRIPTS_DIR)/backup/database.sh

backup-instance: ## Backup de una instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@echo "$(GREEN)=== Backup instancia $(INSTANCE) ===$(NC)"
	@sudo $(SCRIPTS_DIR)/backup/instance.sh $(INSTANCE)

restore: ## Restaurar backup (BACKUP_FILE=archivo.tar.gz)
	@if [ -z "$(BACKUP_FILE)" ]; then echo "$(RED)Especificar BACKUP_FILE=archivo.tar.gz$(NC)"; exit 1; fi
	@sudo $(SCRIPTS_DIR)/backup/restore.sh $(BACKUP_FILE)

# =============================================================================
# TESTING Y CALIDAD
# =============================================================================

test: test-unit test-integration test-isolation ## Ejecutar todos los tests

test-unit: ## Tests unitarios
	@echo "$(GREEN)=== Tests Unitarios ===$(NC)"
	@$(VENV_PYTHON) -m pytest tests/unit -v --tb=short

test-integration: ## Tests de integración (requiere BD nativas corriendo)
	@echo "$(GREEN)=== Tests Integración ===$(NC)"
	@$(VENV_PYTHON) -m pytest tests/integration -v --tb=short --asyncio-mode=auto

test-isolation: ## Tests de aislamiento cross-instancia (CRÍTICOS)
	@echo "$(RED)=== Tests Aislamiento Cross-Instancia (CRÍTICOS) ===$(NC)"
	@$(VENV_PYTHON) -m pytest tests/isolation -v --tb=short --asyncio-mode=auto

test-cov: ## Tests con cobertura
	@$(VENV_PYTHON) -m pytest tests/ --cov=core --cov-report=term-missing --cov-report=html

lint: ## Linting con ruff
	@$(VENV_PYTHON) -m ruff check $(PROJECT_ROOT)/core $(PROJECT_ROOT)/tests/

format: ## Formateo con ruff
	@$(VENV_PYTHON) -m ruff format $(PROJECT_ROOT)/core $(PROJECT_ROOT)/tests/

type-check: ## Verificación de tipos con mypy
	@$(VENV_PYTHON) -m mypy $(PROJECT_ROOT)/core

pre-commit: lint format type-check ## Ejecutar todos los checks pre-commit

# =============================================================================
# DESARROLLO
# =============================================================================

dev-install: install-python install-hermes ## Instalación solo desarrollo (sin BD ni Apache)

dev-start: ## Iniciar solo Hermes Core (requiere MariaDB/Redis corriendo)
	@echo "$(GREEN)=== Iniciando Hermes Core ===$(NC)"
	@sudo systemctl start gestor-ia

dev-stop: ## Detener solo Hermes Core
	@echo "$(YELLOW)=== Deteniendo Hermes Core ===$(NC)"
	@sudo systemctl stop gestor-ia

dev-restart: ## Reiniciar solo Hermes Core
	@echo "$(BLUE)=== Reiniciando Hermes Core ===$(NC)"
	@sudo systemctl restart gestor-ia

dev-logs: ## Ver logs Hermes Core
	@journalctl -u gestor-ia -f

# =============================================================================
# DOCKER (SOLO PARA TESTS/CI - NO PRODUCCIÓN)
# =============================================================================

docker-test-up: ## Levantar MariaDB/Redis de test en puertos 55432/56379
	@docker compose -f docker-compose.test.yml --env-file .env.test up -d

docker-test-down: ## Bajar servicios de test
	@docker compose -f docker-compose.test.yml --env-file .env.test down -v

# =============================================================================
# LIMPIEZA (SEGURA - NO DESTRUCTIVA)
# =============================================================================

clean: ## Limpiar archivos temporales y cache (NO borra BD ni datos)
	@echo "$(YELLOW)=== Limpieza Segura ===$(NC)"
	@find $(PROJECT_ROOT) -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find $(PROJECT_ROOT) -type f -name "*.pyc" -delete 2>/dev/null || true
	@find $(PROJECT_ROOT) -type f -name "*.pyo" -delete 2>/dev/null || true
	@find $(PROJECT_ROOT) -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find $(PROJECT_ROOT) -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find $(PROJECT_ROOT) -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(PROJECT_ROOT)/htmlcov 2>/dev/null || true
	@rm -rf $(PROJECT_ROOT)/.coverage 2>/dev/null || true
	@echo "$(GREEN)✅ Limpieza completada$(NC)"

clean-logs: ## Limpiar logs systemd (requiere root)
	@sudo journalctl --vacuum-time=7d

# =============================================================================
# COMANDO POR DEFECTO
# =============================================================================

.DEFAULT_GOAL := help