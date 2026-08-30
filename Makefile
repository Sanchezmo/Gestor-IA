# Makefile - Gestor-IA Core
# Interfaz simple para gestión de infraestructura nativa
# Uso: make help

.PHONY: help install configure start stop restart status check backup restore test lint format type-check clean instance-create instance-list instance-status instance-enable instance-disable check-instance dev-install dev-start dev-stop dev-restart dev-logs dev-status dev-health docker-test-up docker-test-down

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
# GESTIÓN DE INSTANCIAS (vía CLI interno)
# =============================================================================

instance-create: ## Crear nueva instancia (INSTANCE=empresa_a DOMAIN=empresa.com)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@if [ -z "$(DOMAIN)" ]; then echo "$(RED)Especificar DOMAIN=empresa.com$(NC)"; exit 1; fi
	@echo "$(GREEN)=== Creando instancia $(INSTANCE) ===$(NC)"
	@$(SCRIPTS_DIR)/instance/create.sh $(INSTANCE) $(DOMAIN)

instance-list: ## Listar instancias configuradas
	@echo "$(GREEN)=== Instancias Configuradas ===$(NC)"
	@$(VENV_PYTHON) -m core.hermes.cli list-instances

instance-status: ## Ver estado de una instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@echo "$(GREEN)=== Estado instancia $(INSTANCE) ===$(NC)"
	@$(VENV_PYTHON) -m core.hermes.cli instance-status $(INSTANCE)

instance-enable: ## Habilitar instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@$(VENV_PYTHON) -m core.hermes.cli instance-enable $(INSTANCE)

instance-disable: ## Deshabilitar instancia (INSTANCE=empresa_a)
	@if [ -z "$(INSTANCE)" ]; then echo "$(RED)Especificar INSTANCE=empresa_a$(NC)"; exit 1; fi
	@$(VENV_PYTHON) -m core.hermes.cli instance-disable $(INSTANCE)

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
	@echo "$(GREEN)=== Verificando instancia $(INSTANCE) ===$(NC)"
	@$(VENV_PYTHON) -m core.hermes.cli check-instance $(INSTANCE)

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
	@if [ -n "$$(find tests/unit -name 'test_*.py' 2>/dev/null)" ]; then \
		$(VENV_PYTHON) -m pytest tests/unit -v --tb=short; \
	else \
		echo "$(YELLOW)No unit tests found, skipping$(NC)"; \
	fi

test-integration: ## Tests de integración (requiere BD nativas corriendo)
	@echo "$(GREEN)=== Tests Integración ===$(NC)"
	@if [ -n "$$(find tests/integration -name 'test_*.py' 2>/dev/null)" ]; then \
		$(VENV_PYTHON) -m pytest tests/integration -v --tb=short --asyncio-mode=auto; \
	else \
		echo "$(YELLOW)No integration tests found, skipping$(NC)"; \
	fi

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
# DEVELOPMENT (Native services via systemd)
# =============================================================================

dev-install: install-python install-hermes ## Instalación solo desarrollo (sin BD ni Apache)

dev-start: ## Iniciar entorno DEVELOPMENT nativo (systemd)
	@echo "$(GREEN)=== Iniciando DEVELOPMENT GESTOR-IA (native) ===$(NC)"
	@sudo -n systemctl start mariadb redis-server apache2 cloudflared ollama 2>/dev/null || true
	@sudo -n systemctl start hermes-development
	@echo "$(GREEN)Esperando servicios...$(NC)"
	@sleep 3
	@curl -sf http://localhost:8000/health 2>/dev/null && echo "Hermes API: $(GREEN)OK$(NC)" || echo "Hermes API: $(RED)DOWN$(NC)"

dev-stop: ## Parar entorno DEVELOPMENT nativo
	@echo "$(YELLOW)=== Parando DEVELOPMENT GESTOR-IA (native) ===$(NC)"
	@sudo -n systemctl stop hermes-development
	@sudo -n systemctl stop ollama cloudflared apache2 redis-server mariadb 2>/dev/null || true

dev-restart: dev-stop dev-start ## Reiniciar entorno DEVELOPMENT nativo

dev-status: ## Ver estado de servicios DEVELOPMENT nativos
	@echo "$(GREEN)=== Estado DEVELOPMENT (native) ===$(NC)"
	@sudo -n systemctl is-active mariadb >/dev/null 2>&1 && echo "MariaDB:    $(GREEN)active$(NC)" || echo "MariaDB:    $(RED)inactive$(NC)"
	@sudo -n systemctl is-active redis-server >/dev/null 2>&1 && echo "Redis:      $(GREEN)active$(NC)" || echo "Redis:      $(RED)inactive$(NC)"
	@sudo -n systemctl is-active apache2 >/dev/null 2>&1 && echo "Apache:     $(GREEN)active$(NC)" || echo "Apache:     $(RED)inactive$(NC)"
	@sudo -n systemctl is-active cloudflared >/dev/null 2>&1 && echo "cloudflared:$(GREEN)active$(NC)" || echo "cloudflared:$(RED)inactive$(NC)"
	@sudo -n systemctl is-active ollama >/dev/null 2>&1 && echo "Ollama:     $(GREEN)active$(NC)" || echo "Ollama:     $(RED)inactive$(NC)"
	@sudo -n systemctl is-active hermes-development >/dev/null 2>&1 && echo "Hermes:     $(GREEN)active$(NC)" || echo "Hermes:     $(RED)inactive$(NC)"
	@echo ""
	@curl -sf http://localhost:8000/health 2>/dev/null && echo "Hermes API: $(GREEN)OK$(NC)" || echo "Hermes API: $(RED)DOWN$(NC)"

dev-health: ## Healthcheck completo DEVELOPMENT nativo
	@echo "$(GREEN)=== Healthcheck DEVELOPMENT (native) ===$(NC)"
	@.venv/bin/python -m core.hermes.cli healthcheck

dev-logs: ## Ver logs Hermes DEVELOPMENT (systemd journal)
	@sudo -n journalctl -u hermes-development -f

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