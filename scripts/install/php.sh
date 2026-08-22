#!/usr/bin/env bash
# scripts/install/php.sh
# Instalar PHP 8.3 y extensiones para Dolibarr

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# PHP 8.3 ya debería estar instalado via dependencies.sh
# Verificar extensiones requeridas para Dolibarr
REQUIRED_EXTENSIONS=(
    "mysql" "gd" "mbstring" "xml" "curl" "zip" "intl" "bcmath" "opcache"
)

for ext in "${REQUIRED_EXTENSIONS[@]}"; do
    if php -m | grep -q "^${ext}$"; then
        log_info "Extensión PHP ${ext}: OK"
    else
        log_warn "Extensión PHP ${ext}: NO INSTALADA"
    fi
done

# Configurar PHP-FPM
sed -i 's/^;cgi.fix_pathinfo=1/cgi.fix_pathinfo=0/' /etc/php/8.3/fpm/php.ini
sed -i 's/^memory_limit = 128M/memory_limit = 512M/' /etc/php/8.3/fpm/php.ini
sed -i 's/^upload_max_filesize = 2M/upload_max_filesize = 100M/' /etc/php/8.3/fpm/php.ini
sed -i 's/^post_max_size = 8M/post_max_size = 100M/' /etc/php/8.3/fpm/php.ini
sed -i 's/^max_execution_time = 30/max_execution_time = 300/' /etc/php/8.3/fpm/php.ini
sed -i 's/^max_input_time = 60/max_input_time = 300/' /etc/php/8.3/fpm/php.ini

# Configurar pool para Dolibarr
cat > /etc/php/8.3/fpm/pool.d/dolibarr.conf << 'EOF'
[dolibarr]
user = www-data
group = www-data
listen = /run/php/php8.3-fpm-dolibarr.sock
listen.owner = www-data
listen.group = www-data
pm = dynamic
pm.max_children = 50
pm.start_servers = 5
pm.min_spare_servers = 5
pm.max_spare_servers = 35
pm.max_requests = 500
php_admin_value[error_log] = /var/log/php8.3-fpm-dolibarr.log
php_admin_flag[log_errors] = on
EOF

systemctl enable php8.3-fpm
systemctl restart php8.3-fpm

log_info "PHP 8.3 configurado para Dolibarr"