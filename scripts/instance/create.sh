#!/usr/bin/env bash
# scripts/instance/create.sh
# Crear nueva instancia de empresa
# Uso: ./scripts/instance/create.sh <instance_id> <base_domain> [company_name]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

INSTANCE_ID="${1:-}"
BASE_DOMAIN="${2:-}"
COMPANY_NAME="${3:-$INSTANCE_ID}"

if [[ -z "$INSTANCE_ID" || -z "$BASE_DOMAIN" ]]; then
    echo "Uso: $0 <instance_id> <base_domain> [company_name]"
    echo "Ejemplo: $0 empresa_a empresa-a.com \"Empresa A S.L.\""
    exit 1
fi

# Validar instance_id (solo lowercase, números, guiones, underscores)
if [[ ! "$INSTANCE_ID" =~ ^[a-z0-9_-]+$ ]]; then
    echo "ERROR: instance_id solo puede contener letras minúsculas, números, guiones y underscores"
    exit 1
fi

# Validar dominio
if [[ ! "$BASE_DOMAIN" =~ \. ]]; then
    echo "ERROR: base_domain debe ser un dominio válido (ej: empresa.com)"
    exit 1
fi

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

# Verificar si ya existe
INSTANCE_DIR="${PROJECT_ROOT}/instances/${INSTANCE_ID}"
if [[ -d "$INSTANCE_DIR" ]]; then
    log_error "Instancia ${INSTANCE_ID} ya existe en ${INSTANCE_DIR}"
    exit 1
fi

# Asignar puerto Dolibarr (buscar siguiente disponible)
DOLIBARR_PORT=8081
for dir in "${PROJECT_ROOT}/instances"/*/; do
    if [[ -f "${dir}config.yml" ]]; then
        port=$(grep "dolibarr_apache_port:" "${dir}config.yml" | awk '{print $2}')
        if [[ "$port" -ge "$DOLIBARR_PORT" ]]; then
            DOLIBARR_PORT=$((port + 1))
        fi
    fi
done

# Generar passwords
DB_PASSWORD=$(openssl rand -base64 32)
DB_ROOT_PASSWORD=$(openssl rand -base64 32)
DOLIBARR_API_KEY=$(openssl rand -base64 32)
TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)

# Subdominios
DOLIBARR_DOMAIN="dolibarr.${BASE_DOMAIN}"
HERMES_DOMAIN="bot.${BASE_DOMAIN}"

# Generar config.yml usando Python
log_step "Generando configuración para ${INSTANCE_ID}..."
cat > /tmp/gen_config.py << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from core.hermes.instance_config import generate_instance_template

template = generate_instance_template(
    instance_id=sys.argv[2],
    company_name=sys.argv[3],
    base_domain=sys.argv[4],
    dolibarr_port=int(sys.argv[5]),
)

# Reemplazar placeholders de passwords
template = template.replace("CHANGE_ME_DB_PASSWORD", sys.argv[6])
template = template.replace("CHANGE_ME_DOLIBARR_API_KEY", sys.argv[7])
template = template.replace("CHANGE_ME_TELEGRAM_BOT_TOKEN", sys.argv[8])
template = template.replace("CHANGE_ME_WEBHOOK_SECRET", sys.argv[9])

print(template)
PYEOF

CONFIG_YML=$("${PROJECT_ROOT}/.venv/bin/python" /tmp/gen_config.py \
    "${PROJECT_ROOT}" \
    "${INSTANCE_ID}" \
    "${COMPANY_NAME}" \
    "${BASE_DOMAIN}" \
    "${DOLIBARR_PORT}" \
    "${DB_PASSWORD}" \
    "${DOLIBARR_API_KEY}" \
    "CHANGE_ME_TELEGRAM_BOT_TOKEN" \
    "${TELEGRAM_WEBHOOK_SECRET}")

# Crear directorios
log_step "Creando estructura de directorios..."
mkdir -p "${INSTANCE_DIR}"
mkdir -p "${INSTANCE_DIR}/secrets"
mkdir -p "/var/lib/gestor-ia/${INSTANCE_ID}/documents"
mkdir -p "/var/lib/gestor-ia/${INSTANCE_ID}/runtime"
mkdir -p "/var/backups/gestor-ia/${INSTANCE_ID}"

# Escribir config.yml
echo "${CONFIG_YML}" > "${INSTANCE_DIR}/config.yml"

# Crear instance.env (NO versionado)
cat > "${INSTANCE_DIR}/instance.env" << EOF
# Instance environment - NO COMMITAR
# Generado automáticamente - Editar si necesario

DOLIBARR_DB_PASSWORD=${DB_PASSWORD}
DOLIBARR_DB_ROOT_PASSWORD=${DB_ROOT_PASSWORD}
DOLIBARR_API_KEY=${DOLIBARR_API_KEY}
TELEGRAM_BOT_TOKEN=CHANGE_ME_TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET}
EOF

# Crear .gitignore para secrets
cat > "${INSTANCE_DIR}/.gitignore" << 'EOF'
# Ignorar secretos reales
instance.env
secrets/
*.key
*.pem
*.crt
EOF

# Crear README de la instancia
cat > "${INSTANCE_DIR}/README.md" << EOF
# Instancia: ${INSTANCE_ID}

**Empresa**: ${COMPANY_NAME}
**Dominio base**: ${BASE_DOMAIN}

## Configuración

- **Dolibarr**: http://127.0.0.1:${DOLIBARR_PORT} (interno) / https://${DOLIBARR_DOMAIN} (público)
- **Hermes/Bot**: https://${HERMES_DOMAIN}
- **Telegram Webhook**: /webhook/${INSTANCE_ID}

## Puertos asignados

- Dolibarr Apache: ${DOLIBARR_PORT}
- Redis DB: $(echo -n "${INSTANCE_ID}" | md5sum | cut -c1-2 | xargs printf "%d" 0x) (mod 16)

## Secretos

Los secretos reales están en \`instance.env\` (gitignored).
Configurar:
- TELEGRAM_BOT_TOKEN: Obtener de @BotFather
- DOLIBARR_API_KEY: Generar en Dolibarr > Configuración > API > Claves API

## Próximos pasos

1. Editar \`instance.env\` con tokens reales
2. Configurar DNS en Cloudflare para ${DOLIBARR_DOMAIN} y ${HERMES_DOMAIN}
3. Ejecutar: \`make configure-dolibarr INSTANCE=${INSTANCE_ID}\`
4. Ejecutar: \`make configure-cloudflare\`
5. Iniciar servicios: \`make start\`
EOF

log_info "Instancia ${INSTANCE_ID} creada en ${INSTANCE_DIR}"
log_info "Puerto Dolibarr asignado: ${DOLIBARR_PORT}"
log_warn "IMPORTANTE: Edita ${INSTANCE_DIR}/instance.env con tokens reales:"
echo "  - TELEGRAM_BOT_TOKEN (de @BotFather)"
echo "  - DOLIBARR_API_KEY (se generará tras instalar Dolibarr)"
echo ""
log_info "Próximos pasos:"
echo "  1. make configure-dolibarr INSTANCE=${INSTANCE_ID}"
echo "  2. make configure-cloudflare"
echo "  3. make start"