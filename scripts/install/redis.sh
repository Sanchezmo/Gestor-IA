#!/usr/bin/env bash
# scripts/install/redis.sh
# Instalar y configurar Redis

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Configurar Redis
cat > /etc/redis/redis.conf << 'EOF'
bind 127.0.0.1
port 6379
maxmemory 512mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfilename "appendonly.aof"
EOF

systemctl enable redis-server
systemctl restart redis-server

log_info "Redis instalado y configurado"
log_warn "Configurar REDIS_PASSWORD en .env"