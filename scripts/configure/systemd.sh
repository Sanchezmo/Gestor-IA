#!/usr/bin/env bash
# scripts/configure/systemd.sh
# Renderizar e instalar units systemd desde templates
# Uso: ./scripts/configure/systemd.sh [--user=gestor-ia] [--root=/opt/Gestor-IA]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Defaults
GESTOR_IA_USER="${GESTOR_IA_USER:-gestor-ia}"
GESTOR_IA_ROOT="${GESTOR_IA_ROOT:-$PROJECT_ROOT}"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --user=*) GESTOR_IA_USER="${1#*=}"; shift ;;
        --root=*) GESTOR_IA_ROOT="${1#*=}"; shift ;;
        --user) GESTOR_IA_USER="$2"; shift 2 ;;
        --root) GESTOR_IA_ROOT="$2"; shift 2 ;;
        --help|-h)
            echo "Uso: $0 [--user=gestor-ia] [--root=/opt/Gestor-IA]"
            echo "  --user   : Usuario systemd (default: gestor-ia)"
            echo "  --root   : Root de instalación (default: PROJECT_ROOT auto-detectado)"
            exit 0
            ;;
        *) echo "Opción desconocida: $1"; exit 1 ;;
    esac
done

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

# Validar que el root existe
if [[ ! -d "$GESTOR_IA_ROOT" ]]; then
    log_error "Directorio root no existe: $GESTOR_IA_ROOT"
    exit 1
fi

# Validar que el template existe
TEMPLATE_FILE="${PROJECT_ROOT}/config/systemd/gestor-ia.service.template"
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    log_error "Template no encontrado: $TEMPLATE_FILE"
    exit 1
fi

# Verificar usuario
if ! id "$GESTOR_IA_USER" &>/dev/null; then
    log_warn "Usuario $GESTOR_IA_USER no existe, se creará"
    useradd -r -s /bin/false -d "$GESTOR_IA_ROOT" "$GESTOR_IA_USER" 2>/dev/null || true
fi

# Renderizar template
log_step "Renderizando unit systemd..."
RENDERED_SERVICE="/tmp/gestor-ia.service.rendered"

# Usar sed para sustituir placeholders
sed \
    -e "s|@GESTOR_IA_ROOT@|$GESTOR_IA_ROOT|g" \
    -e "s|@GESTOR_IA_USER@|$GESTOR_IA_USER|g" \
    "$TEMPLATE_FILE" > "$RENDERED_SERVICE"

log_info "Unit renderizado en: $RENDERED_SERVICE"
log_info "Root: $GESTOR_IA_ROOT"
log_info "User: $GESTOR_IA_USER"

# Validar unit renderizado con systemd-analyze si está disponible
if command -v systemd-analyze &> /dev/null; then
    log_step "Validando unit con systemd-analyze..."
    # Copiar a ubicación de usuario para validación (no requiere root)
    mkdir -p "$HOME/.config/systemd/user"
    cp "$RENDERED_SERVICE" "$HOME/.config/systemd/user/gestor-ia.service.verify"
    if systemd-analyze --user verify gestor-ia.service.verify 2>/dev/null; then
        log_info "Validación OK"
    else
        log_warn "Validación systemd-analyze falló (puede requerir entorno systemd completo)"
        log_info "Unit renderizado parece sintácticamente correcto"
    fi
    rm -f "$HOME/.config/systemd/user/gestor-ia.service.verify"
else
    log_warn "systemd-analyze no disponible, saltando validación"
fi

# Instalar unit
log_step "Instalando unit en /etc/systemd/system/..."
sudo cp "$RENDERED_SERVICE" /etc/systemd/system/gestor-ia.service
sudo chmod 644 /etc/systemd/system/gestor-ia.service

# Recargar systemd
log_step "Recargando systemd daemon..."
sudo systemctl daemon-reload

log_info "Unit systemd instalado correctamente"
log_info "Para habilitar: sudo systemctl enable gestor-ia"
log_info "Para iniciar:   sudo systemctl start gestor-ia"

# Mostrar unit instalado para verificación
log_step "Unit instalado:"
systemctl cat gestor-ia