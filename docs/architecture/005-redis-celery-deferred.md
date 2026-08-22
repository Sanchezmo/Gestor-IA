# ADR 005: Redis/Celery Deferred Until Justified

## Status
Accepted

## Context
Transvega Animal usaba:
- **Redis**: Broker/result backend para Celery, ConversationManager (sesiones), Idempotency keys, Rate limiting
- **Celery**: Tareas periódicas (expedientes, publicaciones, facturación), colas priorizadas, workers separados

En Gestor-IA fase inicial:
- No hay tareas periódicas reales aún
- No hay workers de background jobs
- La única necesidad real de Redis: **idempotencia de Telegram webhook** (evitar procesar updates duplicados)

## Decision
**Mantener Redis opcional para desarrollo, requerido en producción**.
**Posponer Celery hasta que carga real lo justifique** (>10 tareas concurrentes reales).

### Redis: Uso Actual (MÍNIMO)
1. **Idempotencia Telegram webhook** (`core/hermes/main.py:398-414`):
   - Key: `telegram:update:{update_id}`
   - TTL: 24h
   - DB lógica por instancia: `hash(instance_id) % 16`
   - Si Redis no disponible → webhook falla (comportamiento correcto: Telegram reintentará)

2. **Health check readiness** (`/health/ready`): Verifica Redis responde ping.

### Redis DB Number: NO Frontera de Seguridad
```python
def get_redis_db(self) -> int:
    """Número de base de datos Redis (0-15) para esta instancia.
    
    NOTA: Redis DB number NO es frontera de seguridad real.
    Es solo aislamiento lógico de claves.
    Para aislamiento real usar:
    - namespace/prefix por instancia (ej: "empresa_a:telegram:update:123")
    - credenciales/ACL cuando proceda
    - o instancias Redis separadas cuando la sensibilidad lo requiera.
    """
    return int(hashlib.md5(self.instance_id.encode()).hexdigest(), 16) % 16
```

### Celery: Abstracción JobQueue Primero
```python
# FASE 1 (actual): JobQueue en memoria / simple
class JobQueue:
    async def enqueue(self, job: Job) -> str: ...
    async def dequeue(self) -> Job | None: ...

# FASE 2 (futuro): CeleryJobQueue cuando carga real lo requiera
class CeleryJobQueue(JobQueue):
    # Usa Redis broker + Celery workers
```

### Criterios para Activar Celery
- [ ] >10 tareas concurrentes reales en producción
- [ ] Tareas largas (>30s) que bloquearían webhooks/API
- [ ] Necesidad de retries automáticos con backoff
- [ ] Visibilidad/monitoring de colas (Flower)
- [ ] Tareas programadas (cron) complejas

## Consequences
### Positivos
- Stack mínimo: menos componentes, menos fallos, menos monitoring
- Redis solo para lo estrictamente necesario (idempotencia webhook)
- Abstracción `JobQueue` permite migrar a Celery sin romper handlers
- Ahorro de recursos (RAM, CPU, complejidad operativa)

### Negativos
- Si aparece necesidad real de background jobs, requiere refactor
- Idempotencia webhook falla si Redis caído (Telegram reintenta, OK)

## Implementation Notes
- `.env.example`: Redis documentado como "OPCIONAL para desarrollo, REQUERIDO en producción"
- `pyproject.toml`: `celery` en `optional-dependencies` (no en `dependencies`)
- `main.py`: Health check `/health/ready` verifica Redis pero no bloquea `/health`
- `instance_config.py`: `get_redis_db()` con docstring explícito sobre no-seguridad