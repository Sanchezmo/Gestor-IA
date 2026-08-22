# ADR 003: Native On-Premise Runtime

## Status
Accepted

## Context
Opciones consideradas:
1. **Kubernetes + Helm** - Complejidad operativa alta, overkill para on-premise single-server
2. **Docker Compose (producción)** - Complejidad de red, volumes, logs, updates; no aprovecha systemd nativo
3. **Systemd nativo + binarios del sistema** - Simple, probado, integra con journalctl, logrotate, monitoring estándar

## Decision
**Runtime nativo on-premise** como única opción soportada para producción.

### Stack
| Componente | Gestión | Notas |
|------------|---------|-------|
| MariaDB | Paquete distro (`mariadb-server`) | systemd, config en `/etc/mysql/` |
| Redis | Paquete distro (`redis-server`) | systemd, bind 127.0.0.1 |
| Ollama | Binario oficial + systemd | GPU accesible via `--gpus all` si aplica |
| Apache | Paquete distro (`apache2`) | Proxy reverso para Dolibarr por instancia |
| cloudflared | Binario oficial + systemd | Tunnel + config dinámico |
| Hermes (FastAPI) | Python venv + systemd | `.venv/bin/uvicorn`, user `gestor-ia` |

### Docker solo para
- **Tests/CI**: `docker compose -f docker-compose.test.yml` (MariaDB/Redis en puertos 55432/56379)
- **Desarrollo opcional**: `docker compose -f docker-compose.dev.yml` si se prefiere

### NO en producción
- Docker Compose para servicios principales
- Kubernetes/Helm
- Contenedores para MariaDB, Redis, Apache, Ollama

## Consequences
### Positivos
- Simplicidad operativa: `systemctl status/start/stop/restart gestor-ia`
- Logs centralizados: `journalctl -u gestor-ia -f`
- Integración nativa: logrotate, monitoring (Prometheus node_exporter), backups
- Menos capas: mejor performance, debugging directo
- Actualizaciones: `apt upgrade` + `systemctl restart` vs rebuild images

### Negativos
- Gestión de dependencias Python via venv (mitigado: `make install-python`)
- Distro-specific (asumimos Debian/Ubuntu/.deb)
- GPU access para Ollama requiere configuración systemd (`ExecStartPre=/usr/bin/nvidia-smi`)

## Implementation Notes
- `config/systemd/gestor-ia.service`: Template con `${GESTOR_IA_ROOT}`
- `scripts/install/*.sh`: Idempotentes, detectan `PROJECT_ROOT` automáticamente
- `scripts/services/*.sh`: Wrappers sobre systemctl
- `Makefile`: `make install/start/stop/restart/status`