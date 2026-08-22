#!/usr/bin/env bash
# scripts/install/mariadb.sh
# Instalar y configurar MariaDB

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Configurar MariaDB para permitir conexiones remotas si necesario
sed -i 's/^bind-address\s*=\s*127.0.0.1/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf 2>/dev/null || true

systemctl enable mariadb
systemctl restart mariadb

# Esperar a que MariaDB esté listo
sleep 3

# Ejecutar mysql_secure_installation equivalente
mysql -u root << EOF
DELETE FROM mysql.user WHERE User='';
DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');
DROP DATABASE IF EXISTS test;
DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';
FLUSH PRIVILEGES;
EOF

log_info "MariaDB instalado y configurado"
log_warn "Configurar MARIADB_ROOT_PASSWORD en .env"