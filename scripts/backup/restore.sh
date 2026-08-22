#!/usr/bin/env bash
# scripts/backup/restore.sh
# Restaurar backup
# Uso: ./scripts/backup/restore.sh <backup_file.tar.gz>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
    echo "Uso: $0 <backup_file.tar.gz>"
    echo "Ejemplo: $0 /var/backups/gestor-ia/gestor-ia-full-20240115_020000.tar.gz"
    exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Archivo no encontrado: $BACKUP_FILE"
    exit 1
fi

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
    
    log_warn "=== RESTAURACIÓN DE BACKUP ==="
    log_warn "Archivo: ${BACKUP_FILE}"
    log_warn "ESTO SOBRESCRIBIRÁ DATOS EXISTENTES"
    echo ""
    read -p "¿Continuar? (escribe 'SI' para confirmar): " CONFIRM
    if [[ "$CONFIRM" != "SI" ]]; then
        log_info "Cancelado"
        exit 0
    fi
    
    TMPDIR=$(mktemp -d)
    trap "rm -rf ${TMPDIR}" EXIT
    
    log_info "Extrayendo backup..."
    tar -xzf "${BACKUP_FILE}" -C "${TMPDIR}"
    
    # Verificar metadata
    if [[ -f "${TMPDIR}/BACKUP_METADATA.json" ]]; then
        log_info "Metadata del backup:"
        cat "${TMPDIR}/BACKUP_METADATA.json" | jq .
    fi
    
    # 1. Restaurar MariaDB
    if ls "${TMPDIR}"/dolibarr_*.sql.gz 1> /dev/null 2>&1; then
        log_info "Restaurando bases de datos MariaDB..."
        for sql_file in "${TMPDIR}"/dolibarr_*.sql.gz; do
            db_name=$(basename "$sql_file" .sql.gz)
            log_info "  Restaurando ${db_name}..."
            
            # Crear DB si no existe
            mysql -u root -p"${MARIADB_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS \`${db_name}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            
            # Restaurar
            gunzip -c "$sql_file" | mysql -u root -p"${MARIADB_ROOT_PASSWORD}" "${db_name}"
        done
    fi
    
    # 2. Restaurar configs de instancias
    if [[ -d "${TMPDIR}/instances" ]]; then
        log_info "Restaurando configs de instancias..."
        rsync -a "${TMPDIR}/instances/" "${PROJECT_ROOT}/instances/"
    fi
    
    # 3. Restaurar .env global
    if [[ -f "${TMPDIR}/.env.global" ]]; then
        log_info "Restaurando .env global..."
        cp "${TMPDIR}/.env.global" "${PROJECT_ROOT}/.env"
    fi
    
    # 4. Restaurar Apache configs
    if [[ -d "${TMPDIR}/apache" ]]; then
        log_info "Restaurando Apache configs..."
        cp "${TMPDIR}/apache"/dolibarr-*.conf /etc/apache2/sites-available/ 2>/dev/null || true
        a2ensite dolibarr-* >/dev/null 2>&1 || true
        apache2ctl configtest
        systemctl reload apache2
    fi
    
    log_info "Restauración completada"
    log_warn "Reinicia servicios: make restart"
}

main "$@"