#!/usr/bin/env bash
# Fix MariaDB root password - must run with sudo
# Usage: sudo ./fix-mariadb-root.sh
# Reads MARIADB_ROOT_PASSWORD from environment or .env file.
# If not set, generates a secure random password.

set -euo pipefail

log() { echo -e "\033[1;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*"; }

# Check root
if [[ $EUID -ne 0 ]]; then
    err "Este script debe ejecutarse con sudo"
    exit 1
fi

# Get project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read MARIADB_ROOT_PASSWORD from environment first
if [[ -n "${MARIADB_ROOT_PASSWORD:-}" ]]; then
    NEW_ROOT_PASS="${MARIADB_ROOT_PASSWORD}"
    log "Usando MARIADB_ROOT_PASSWORD del entorno"
# Otherwise try to read from .env file
elif [[ -f "${PROJECT_ROOT}/.env" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/.env"
    if [[ -n "${MARIADB_ROOT_PASSWORD:-}" ]]; then
        NEW_ROOT_PASS="${MARIADB_ROOT_PASSWORD}"
        log "Usando MARIADB_ROOT_PASSWORD de .env"
    fi
fi

# If still not set, generate a secure random password
if [[ -z "${NEW_ROOT_PASS:-}" ]]; then
    warn "MARIADB_ROOT_PASSWORD no configurado. Generando uno seguro..."
    # Generate 48-char password safe for URLs (no special chars that break connection strings)
    NEW_ROOT_PASS=$(openssl rand -base64 48 | tr -d '/+=' | cut -c1-48)
    log "Generado nuevo password de root (longitud: ${#NEW_ROOT_PASS})"
fi

# Mask password for logging
masked_pass="${NEW_ROOT_PASS:0:4}****${NEW_ROOT_PASS: -4}"

log "=== Fix MariaDB Root Password ==="

# 1. Stop MariaDB
log "Parando MariaDB..."
systemctl stop mariadb
sleep 2

# 2. Start with skip-grant-tables
log "Iniciando MariaDB con --skip-grant-tables..."
systemctl set-environment MYSQLD_OPTS="--skip-grant-tables --skip-networking"
systemctl start mariadb
sleep 3

# 3. Update root password in mysql.global_priv
log "Actualizando password de root..."
mysql -u root -e "
UPDATE mysql.global_priv 
SET Priv=JSON_SET(Priv, '\$.password', '${NEW_ROOT_PASS}') 
WHERE User='root' AND Host='localhost'; 
FLUSH PRIVILEGES;
"

# 4. Verify the change
log "Verificando cambio..."
mysql -u root -e "SELECT User, Host, JSON_EXTRACT(Priv, '\$.password') FROM mysql.global_priv WHERE User='root';"

# 5. Stop and restart normally
log "Reiniciando MariaDB normalmente..."
systemctl stop mariadb
sleep 2
systemctl set-environment MYSQLD_OPTS=""
systemctl start mariadb
sleep 3

# 6. Test connection
log "Probando conexión con nuevo password..."
if mysql -u root -p"${NEW_ROOT_PASS}" -e "SELECT 1;"; then
    log "✅ Conexión exitosa con nuevo password (${masked_pass})"
else
    err "❌ Falló la conexión con el nuevo password"
    exit 1
fi

# 7. Create audit database
log "Creando base de datos de auditoría..."
mysql -u root -p"${NEW_ROOT_PASS}" -e "CREATE DATABASE IF NOT EXISTS gestor_ia_audit;"

log "=== Fix completado ==="
log "Root password: ${masked_pass}"
log "Ahora puedes iniciar Hermes: make dev-start"