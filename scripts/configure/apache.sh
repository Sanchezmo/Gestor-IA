#!/usr/bin/env bash
# scripts/configure/apache.sh
# Configurar Apache VirtualHost para Dolibarr de una instancia
# Uso: ./scripts/configure/apache.sh [INSTANCE=empresa_a]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

INSTANCE="${INSTANCE:-${1:-}}"

if [[ -z "$INSTANCE" ]]; then
    echo "Uso: $0 INSTANCE=empresa_a"
    exit 1
fi

# Cargar config
INSTANCE_DIR="${PROJECT_ROOT}/instances/${INSTANCE}"
CONFIG_YML="${INSTANCE_DIR}/config.yml"

if [[ ! -f "$CONFIG_YML" ]]; then
    echo "Instancia ${INSTANCE} no encontrada"
    exit 1
fi

eval $("${PROJECT_ROOT}/.venv/bin/python" -c "
import yaml
with open('$CONFIG_YML') as f:
    cfg = yaml.safe_load(f)
db = cfg['database']
print(f'DOLIBARR_APACHE_PORT={cfg[\"dolibarr_apache_port\"]}')
print(f'DOLIBARR_HTDOCS=/var/www/dolibarr/${INSTANCE}/htdocs')
print(f'DOLIBARR_VHOST=dolibarr-${INSTANCE}.local')
")

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

VHOST_FILE="/etc/apache2/sites-available/dolibarr-${INSTANCE}.conf"

log_info "Creando VirtualHost para ${INSTANCE} en puerto ${DOLIBARR_APACHE_PORT}..."

cat > "${VHOST_FILE}" << EOF
<VirtualHost *:${DOLIBARR_APACHE_PORT}>
    ServerName ${DOLIBARR_VHOST}
    ServerAdmin webmaster@localhost
    DocumentRoot ${DOLIBARR_HTDOCS}
    
    # Logs
    ErrorLog \${APACHE_LOG_DIR}/dolibarr-${INSTANCE}-error.log
    CustomLog \${APACHE_LOG_DIR}/dolibarr-${INSTANCE}-access.log combined
    
    # Seguridad
    ServerTokens Prod
    ServerSignature Off
    
    <Directory ${DOLIBARR_HTDOCS}>
        Options -Indexes +FollowSymLinks
        AllowOverride All
        Require all granted
        
        # PHP
        <FilesMatch \.php$>
            SetHandler "proxy:unix:/run/php/php8.3-fpm.sock|fcgi://localhost"
        </FilesMatch>
    </Directory>
    
    # Seguridad: denegar acceso a archivos sensibles
    <FilesMatch "(conf\.php|install\.php|upgrade\.php|\.git|\.env)">
        Require all denied
    </FilesMatch>
    
    # Headers de seguridad
    Header always set X-Content-Type-Options nosniff
    Header always set X-Frame-Options SAMEORIGIN
    Header always set Referrer-Policy strict-origin-when-cross-origin
</VirtualHost>
EOF

# Habilitar sitio
a2ensite "dolibarr-${INSTANCE}" >/dev/null

# Verificar configuración
apache2ctl configtest

log_info "VirtualHost creado: ${VHOST_FILE}"
log_warn "Recuerda reiniciar Apache: systemctl reload apache2"