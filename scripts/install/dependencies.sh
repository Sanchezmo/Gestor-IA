#!/usr/bin/env bash
# scripts/install/dependencies.sh
# Instalar dependencias base del sistema

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
    curl wget gnupg2 ca-certificates \
    git build-essential pkg-config \
    python3 python3-venv python3-dev python3-pip \
    redis-server \
    mariadb-server mariadb-client \
    apache2 libapache2-mod-php8.3 php8.3 php8.3-fpm \
    php8.3-mysql php8.3-gd php8.3-mbstring php8.3-xml php8.3-curl php8.3-zip php8.3-intl php8.3-bcmath \
    certbot python3-certbot-apache \
    jq htop iotop net-tools \
    software-properties-common apt-transport-https

log_info "Dependencias base instaladas"