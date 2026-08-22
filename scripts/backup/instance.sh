#!/usr/bin/env bash
# scripts/backup/instance.sh
# Backup de una instancia específica
# Uso: ./scripts/backup/instance.sh <instance_id>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

INSTANCE="${1:-}"

if [[ -z "$INSTANCE" ]]; then
    echo "Uso: $0 <instance_id>"
    exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/gestor-ia}"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/${INSTANCE}-${DATE}.tar.gz"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Este script requiere root (sudo)"
        exit 1
    fi
}

main() {
    check_root
    
    INSTANCE_DIR="${PROJECT_ROOT}/instances/${INSTANCE}"
    if [[ ! -d "$INSTANCE_DIR" ]]; then
        log_error "Instancia ${INSTANCE} no encontrada"
        exit 1
    fi
    
    log_info "Iniciando backup de instancia ${INSTANCE}..."
    log_info "Destino: ${BACKUP_FILE}"
    
    mkdir -p "${BACKUP_DIR}"
    
    TMPDIR=$(mktemp -d)
    trap "rm -rf ${TMPDIR}" EXIT
    
    # Cargar config
    eval $("${PROJECT_ROOT}/.venv/bin/python" -c "
import yaml
with open('${INSTANCE_DIR}/config.yml') as f:
    cfg = yaml.safe_load(f)
db = cfg['database']
print(f'DB_NAME={db[\"db_name\"]}')
print(f'DB_USER={db[\"db_user\"]}')
print(f'DOCUMENTS_PATH={db[\"documents_path\"]}')
")
    
    # 1. Backup MariaDB de la instancia
    log_info "Backup MariaDB: ${DB_NAME}..."
    mysqldump -u root -p"${MARIADB_ROOT_PASSWORD}" \
        --single-transaction --routines --triggers \
        "${DB_NAME}" | gzip > "${TMPDIR}/${DB_NAME}.sql.gz"
    
    # 2. Backup documentos Dolibarr
    if [[ -d "${DOCUMENTS_PATH}" ]]; then
        log_info "Backup documentos: ${DOCUMENTS_PATH}..."
        tar -czf "${TMPDIR}/documents.tar.gz" -C "$(dirname "${DOCUMENTS_PATH}")" "$(basename "${DOCUMENTS_PATH}")"
    fi
    
    # 3. Backup config de la instancia
    log_info "Backup config..."
    cp -r "${INSTANCE_DIR}" "${TMPDIR}/instance_config"
    
    # 4. Backup Apache VirtualHost
    log_info "Backup Apache config..."
    mkdir -p "${TMPDIR}/apache"
    cp "/etc/apache2/sites-available/dolibarr-${INSTANCE}.conf" "${TMPDIR}/apache/" 2>/dev/null || true
    
    # 5. Metadata
    cat > "${TMPDIR}/BACKUP_METADATA.json" << EOF
{
    "timestamp": "$(date -Iseconds)",
    "instance_id": "${INSTANCE}",
    "type": "instance",
    "database": "${DB_NAME}",
    "documents_path": "${DOCUMENTS_PATH}"
}
EOF
    
    # 6. Comprimir
    log_info "Comprimiendo..."
    tar -czf "${BACKUP_FILE}" -C "${TMPDIR}" .
    
    if [[ -f "${BACKUP_FILE}" ]]; then
        SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
        log_info "Backup completado: ${BACKUP_FILE} (${SIZE})"
    else
        log_error "Backup falló"
        exit 1
    fi
}

main "$@"