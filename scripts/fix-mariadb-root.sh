#!/usr/bin/env bash
# Fix MariaDB root password - must run with sudo
# Usage: sudo ./fix-mariadb-root.sh

set -euo pipefail

NEW_ROOT_PASS="ey5K_DpBy4ReDMlX3XUP_4wHLcZf+KMbLZB0OJNUxRrT4o9E"

log() { echo -e "\033[1;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*"; }

# Check root
if [[ $EUID -ne 0 ]]; then
    err "Este script debe ejecutarse con sudo"
    exit 1
fi

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
    log "✅ Conexión exitosa con nuevo password"
else
    err "❌ Falló la conexión con el nuevo password"
    exit 1
fi

# 7. Create audit database
log "Creando base de datos de auditoría..."
mysql -u root -p"${NEW_ROOT_PASS}" -e "CREATE DATABASE IF NOT EXISTS gestor_ia_audit;"

log "=== Fix completado ==="
log "Root password: ${NEW_ROOT_PASS}"
log "Ahora puedes iniciar Hermes: make dev-start"