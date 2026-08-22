#!/usr/bin/env bash
# scripts/backup/database.sh
# Backup completo de todas las bases de datos MariaDB + configs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/gestor-ia}"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/gestor-ia-full-${DATE}.tar.gz"

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
    
    log_info "Iniciando backup completo..."
    log_info "Destino: ${BACKUP_FILE}"
    
    mkdir -p "${BACKUP_DIR}"
    
    # Directorio temporal
    TMPDIR=$(mktemp -d)
    trap "rm -rf ${TMPDIR}" EXIT
    
    # 1. Backup MariaDB (todas las BDs dolibarr_*)
    log_info "Backup MariaDB..."
    mysql -u root -p"${MARIADB_ROOT_PASSWORD}" -e "SHOW DATABASES LIKE 'dolibarr_%';" | tail -n +2 | while read db; do
        log_info "  Dumping ${db}..."
        mysqldump -u root -p"${MARIADB_ROOT_PASSWORD}" \
            --single-transaction --routines --triggers \
            "${db}" | gzip > "${TMPDIR}/${db}.sql.gz"
    done
    
    # 2. Backup configs de instancias
    log_info "Backup configs de instancias..."
    cp -r "${PROJECT_ROOT}/instances" "${TMPDIR}/instances"
    
    # 3. Backup .env global
    log_info "Backup .env global..."
    cp "${PROJECT_ROOT}/.env" "${TMPDIR}/.env.global" 2>/dev/null || true
    
    # 4. Backup Apache configs
    log_info "Backup Apache configs..."
    mkdir -p "${TMPDIR}/apache"
    cp /etc/apache2/sites-available/dolibarr-*.conf "${TMPDIR}/apache/" 2>/dev/null || true
    
    # 5. Crear metadata
    cat > "${TMPDIR}/BACKUP_METADATA.json" << EOF
{
    "timestamp": "$(date -Iseconds)",
    "hostname": "$(hostname)",
    "gestor_ia_version": "0.1.0",
    "mariadb_databases": $(mysql -u root -p"${MARIADB_ROOT_PASSWORD}" -e "SHOW DATABASES LIKE 'dolibarr_%';" | tail -n +2 | jq -R . | jq -s .),
    "instances": $(ls "${PROJECT_ROOT}/instances" | jq -R . | jq -s .)
}
EOF
    
    # 6. Comprimir
    log_info "Comprimiendo backup..."
    tar -czf "${BACKUP_FILE}" -C "${TMPDIR}" .
    
    # 7. Verificar
    if [[ -f "${BACKUP_FILE}" ]]; then
        SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
        log_info "Backup completado: ${BACKUP_FILE} (${SIZE})"
        
        # Limpiar backups antiguos (retención 30 días)
        find "${BACKUP_DIR}" -name "gestor-ia-full-*.tar.gz" -mtime +30 -delete
        log_info "Backups antiguos (>30 días) eliminados"
    else
        log_error "Backup falló: archivo no creado"
        exit 1
    fi
}

main "$@"