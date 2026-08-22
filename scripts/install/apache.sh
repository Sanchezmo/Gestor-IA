#!/usr/bin/env bash
# scripts/install/apache.sh
# Instalar y configurar Apache2

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Habilitar módulos necesarios
a2enmod proxy proxy_fcgi proxy_http rewrite headers ssl http2 >/dev/null

# Configuración base de Apache
cat > /etc/apache2/conf-available/gestor-ia-security.conf << 'EOF'
# Security headers
Header always set X-Content-Type-Options nosniff
Header always set X-Frame-Options SAMEORIGIN
Header always set Referrer-Policy strict-origin-when-cross-origin
Header always set Permissions-Policy "geolocation=(), microphone=()"

# Hide server info
ServerTokens Prod
ServerSignature Off

# Disable directory listing
<Directory />
    Options -Indexes
    AllowOverride None
    Require all denied
</Directory>
EOF

a2enconf gestor-ia-security >/dev/null

# Configurar puertos adicionales para Dolibarr (se añaden dinámicamente por instancia)
# Puerto base 8080 ya configurado en /etc/apache2/ports.conf

systemctl enable apache2
systemctl restart apache2

log_info "Apache2 instalado y configurado"