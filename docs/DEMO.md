# Gestor-IA / Hermes - Demo Guide

## Estado Actual (2025-08-25)

### ✅ Funcionando
- **Hermes API**: Healthy en puerto 8000
- **Dolibarr Web Interface**: Accesible en puerto 8081 (admin/admin123)
- **MariaDB**: Corriendo en Docker (puerto 3306)
- **Redis**: Corriendo en Docker (puerto 6379)
- **Ollama**: Corriendo en puerto 11434 (modelos: Hermes3:8b, WormGPT variants)
- **Tests**: 309 passed, 9 failed (fallos en mocks de tests HTTP E2E)

### ⚠️ Limitaciones Conocidas
- **Dolibarr REST API**: Autenticación falla (401 Unauthorized)
  - API module habilitado pero tokens no validan
  - Login API genera tokens pero no autentican en endpoints
  - Workaround: usar Dolibarr web interface directamente

## Arranque Rápido

```bash
# 1. Levantar infraestructura Docker
./scripts/demo/start-demo.sh

# 2. Verificar healthcheck
python -m core.hermes.cli healthcheck

# 3. Hermes API disponible en http://localhost:8000
#    Health: http://localhost:8000/health
#    Docs: http://localhost:8000/docs
```

## Parada

```bash
./scripts/demo/stop-demo.sh
```

## Endpoints de Demo

| Servicio | URL | Credenciales |
|----------|-----|--------------|
| Hermes API | http://localhost:8000 | - |
| Dolibarr Web | http://localhost:8081 | admin / admin123 |
| MariaDB | localhost:3306 | root / ***REMOVED*** |
| Redis | localhost:6379 | password: ***REMOVED*** |
| Ollama | http://localhost:11434 | - |

## Instancia de Demo

- **Instance ID**: `ejemplo` (configurada en `instances/ejemplo/`)
- **Company**: Empresa Ejemplo S.L.
- **Webhook path**: `/webhook/ejemplo`
- **Dolibarr API Key**: Generada via login API (ver abajo)

## Generar API Key para Dolibarr REST

```bash
# Habilitar login API (una sola vez)
docker exec mariadb-demo mysql -u root -p***REMOVED*** dolibarr_demo \
  -e "INSERT INTO llx_const (name, value, type, visible, note, entity) VALUES ('MAIN_MODULE_API_LOGIN_DISABLED', '0', 'chaine', 1, 'Enable login API', 1) ON DUPLICATE KEY UPDATE value='0';"

# Generar token via login API
curl -X POST 'http://localhost:8081/api/index.php/login' \
  -d 'login=admin&password=admin123&entity=1&reset=1'

# Usar el token retornado como DOLAPIKEY
```

**Nota**: Actualmente los tokens generados no autentican correctamente en endpoints REST (401). Issue conocido.

## Query Layer Demo (vía Hermes)

Una vez funcionando la API de Dolibarr:

```bash
# Listar terceros
curl -X POST http://localhost:8000/webhook/ejemplo \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: secret_ejemplo" \
  -d '{"update_id":1,"message":{"message_id":1,"from":{"id":123456},"chat":{"id":123},"date":1234567890,"text":"/terceros"}}'
```

## Command Layer V1 (Producción)

Solo handlers activos:
- `thirdparty.create` - Crear terceros (clientes/proveedores)
- `product.create` - Crear productos (type=0)
- `service.create` - Crear servicios (type=1)

Flujo: Preview → Confirm → Execute (idempotente)

## Troubleshooting

### Dolibarr REST API 401
1. Verificar módulo API habilitado: `MAIN_MODULE_API=1` en `llx_const` (entity 0 y 1)
2. Verificar login API habilitado: `MAIN_MODULE_API_LOGIN_DISABLED=0`
3. Generar token fresco: `curl -X POST .../login -d 'login=admin&password=admin123&reset=1'`
4. Verificar token en BD: `SELECT login, api_key, entity FROM llx_user WHERE login='admin';`

### MariaDB Connection Failed (Healthcheck)
El healthcheck usa socket local pero MariaDB está en Docker (TCP). Usar:
```bash
mysqladmin -h 127.0.0.1 -u root -p***REMOVED*** --skip-ssl ping
```

### Redis Connection
```bash
redis-cli -a ***REMOVED*** ping
```

### Tests Fallando
Los 9 fallos son en `test_http_e2e.py` por:
- Mock de `IdentityStore` en ruta incorrecta (`core.hermes.resolver` vs `core.hermes.identity_store`)
- Dolibarr REST API no disponible para tests de integración

## Plan B (Si Fall LLM/Ollama)

Usar intérprete determinista (parser-first) sin Ollama:
```bash
# En config de instancia
ai:
  default_policy: LOCAL_ONLY
  # ollama_model: ""  # vacío para forzar solo determinista
```

## Próximos Pasos Post-Demo

1. Fix Dolibarr REST API authentication
2. Fix test mocks en `test_http_e2e.py`
3. Command Layer V1 estable → ampliar escritura
4. BC3 integración con Dolibarr
