# Configuración Manual Requerida en GitHub (Branch Protection & Rulesets)

> **Estas configuraciones NO se pueden hacer desde código**. Deben activarse manualmente en la interfaz de GitHub.
> Este documento sirve como checklist y registro de lo que se debe configurar.

---

## 1. Protección de Rama `main` (Branch Protection Rules)

### Configuración mínima requerida:

**Settings → Branches → Branch protection rules → Add rule**

| Configuración | Valor | Justificación |
|--------------|-------|---------------|
| **Branch name pattern** | `main` | Rama principal de producción |
| **Require a pull request before merging** | ✅ ON | Obliga flujo PR → Review → Merge |
| **Require approvals** | `1` (mínimo) | Aprobación humana obligatoria |
| **Dismiss stale PR approvals when new commits are pushed** | ✅ ON | Nuevos commits invalidan reviews antiguas |
| **Require review from Code Owners** | ✅ ON (si existe CODEOWNERS) | Expertos revisan áreas críticas |
| **Restrict who can dismiss PR reviews** | ✅ ON (admins only) | Solo admins pueden descartar reviews |
| **Require status checks to pass before merging** | ✅ ON | Tests y checks obligatorios |
| **Status checks required** | Ver lista abajo | Verificar CI pasa |
| **Require branches to be up to date before merging** | ✅ ON | Evita merges desactualizados |
| **Require conversation resolution before merging** | ✅ ON | Todos los comentarios resueltos |
| **Require signed commits** | ⭕ Opcional | Si la org usa commit signing |
| **Require linear history** | ✅ ON | Sin merge commits, solo squash/rebase |
| **Include administrators** | ✅ ON | Las reglas aplican a TODOS, incluso admins |
| **Restrict who can push to matching branches** | ✅ ON | Solo GitHub Actions / bot autorizado |
| **Allow force pushes** | ❌ OFF | **NUNCA** force-push en main |
| **Allow deletions** | ❌ OFF | **NUNCA** borrar main |

### Status Checks Requeridos (deben pasar antes de merge):

Desde el workflow `ci.yml` (se crean automáticamente al ejecutar CI):

| Check Name | Job | Requerido |
|------------|-----|-----------|
| `Lint & Type Check` | `lint-and-typecheck` | ✅ |
| `Unit Tests` | `unit-tests` | ✅ |
| `Integration Tests` | `integration-tests` | ✅ |
| `Isolation Tests (Critical)` | `isolation-tests` | ✅ |
| `Test Summary` | `test-summary` | ✅ |

> **Nota**: Estos checks aparecerán automáticamente después de la primera ejecución exitosa del workflow `ci.yml` en un PR.

---

## 2. Rulesets (Alternativa Moderna a Branch Protection)

**Settings → Rules → Rulesets → New ruleset**

| Configuración | Valor |
|--------------|-------|
| **Name** | `Protect main branch` |
| **Target branches** | `main` (default branch) |
| **Enforcement** | `Active` |
| **Restrictions** | Ver abajo |

### Restricciones en Ruleset:

| Restricción | Configuración |
|------------|---------------|
| **Required status checks** | `Lint & Type Check`, `Unit Tests`, `Integration Tests`, `Isolation Tests (Critical)`, `Test Summary` |
| **Required pull request reviews** | `Required approving reviews: 1`, `Dismiss stale reviews: ON`, `Require review from code owners: ON` |
| **Block force pushes** | ✅ ON |
| **Block branch deletion** | ✅ ON |
| **Required linear history** | ✅ ON |
| **Required conversation resolution** | ✅ ON |
| **Restrict pushes** | ✅ ON — Solo `github-actions[bot]` y admins |

> **Recomendación**: Usar **Rulesets** en lugar de Branch Protection Rules legacy. Son más potentes, auditables y soportan bypass controls.

---

## 3. Configuración de GitHub Actions (Permisos Globales)

**Settings → Actions → General → Workflow permissions**

| Configuración | Valor Recomendado |
|--------------|-------------------|
| **Workflow permissions** | `Read repository contents permission` (contents: read) |
| **Allow GitHub Actions to create and approve pull requests** | ✅ ON (necesario para workflows OpenCode) |
| **Allow GitHub Actions to create and approve pull requests** | Solo para workflows específicos si es posible |

> **Principio**: `contents: read` por defecto. Los workflows que necesiten `contents: write` o `pull-requests: write` lo declaran en su YAML (ver `.github/workflows/opencode-issue.yml` y `opencode-pr.yml`).

---

## 4. Secrets Requeridos para OpenCode

**Settings → Secrets and variables → Actions → Repository secrets**

| Secret Name | Descripción | Quién lo usa |
|-------------|-------------|--------------|
| `GITHUB_TOKEN` | **Automático** (GitHub lo provee) | Todos los workflows |
| `OPENCODE_API_KEY` | API Key de OpenCode (si requiere auth) | `opencode-issue.yml`, `opencode-pr.yml` |

> **IMPORTANTE**: `GITHUB_TOKEN` ya existe automáticamente con permisos limitados al repo. **NO crear un PAT clásico** salvo que sea estrictamente necesario y con scopes mínimos.
>
> Si OpenCode requiere una API key externa, añadir `OPENCODE_API_KEY` como secret del repositorio. El workflow la usará como variable de entorno.

---

## 5. Configuración de OpenCode (Si Requiere GitHub App)

> **VERIFICAR**: Si la integración OpenCode/GitHub requiere una **GitHub App** oficial:

1. Ir a: https://github.com/apps/opencode (o la URL oficial de OpenCode)
2. Instalar la App en la organización/usuario `Sanchezmo`
3. Conceder permisos:
   - **Repository contents**: Read & Write (para crear ramas, commits, PRs)
   - **Pull requests**: Read & Write (para crear/modificar PRs, leer reviews)
   - **Issues**: Read & Write (para leer issues, comentar)
   - **Metadata**: Read (básico)
4. Copiar el **App ID**, **Private Key**, **Installation ID**
5. Añadir como secrets en GitHub:
   - `OPENCODE_APP_ID`
   - `OPENCODE_PRIVATE_KEY`
   - `OPENCODE_INSTALLATION_ID`

> **SI NO EXISTE GitHub App oficial de OpenCode**: Los workflows actuales usan `opencode run` CLI directo con `GITHUB_TOKEN`. Esto funciona para ejecución en Actions pero **no** para que OpenCode "escuche" eventos de GitHub en tiempo real (webhooks). Para eso sí se necesitaría la App.

---

## 6. CODEOWNERS (Opcional pero Recomendado)

Crear archivo `.github/CODEOWNERS`:

```
# Core architecture - requieren revisión experta
/core/hermes/config.py @tu-usuario
/core/hermes/instance_config.py @tu-usuario
/core/hermes/context.py @tu-usuario
/core/hermes/resolver.py @tu-usuario
/core/hermes/ai.py @tu-usuario
/core/hermes/policy.py @tu-usuario
/core/hermes/audit.py @tu-usuario
/core/integrations/dolibarr/client.py @tu-usuario
/core/integrations/telegram/client.py @tu-usuario
/tests/isolation/ @tu-usuario

# Scripts críticos
/scripts/rotate-secrets.py @tu-usuario
/scripts/backup/ @tu-usuario

# Documentación arquitectura
/docs/architecture/ @tu-usuario
```

---

## 7. Environments (Para Deployments Futuros)

**Settings → Environments → New environment**

| Environment | Protection Rules | Secrets |
|-------------|------------------|---------|
| `staging` | Required reviewers: 1, Wait timer: 0 | Staging-specific |
| `production` | Required reviewers: 2, Wait timer: 5min | Production-specific |

> Actualmente no hay deployment automático. Configurar cuando exista pipeline de deploy.

---

## 8. Resumen de Pasos Manuales (Checklist)

- [ ] **Branch Protection Rule** o **Ruleset** para `main` con configuración arriba
- [ ] **Status checks** aparecen tras primer CI exitoso → marcarlos como required
- [ ] **Workflow permissions**: `Read repository contents` + `Allow Actions to create PRs`
- [ ] **Secrets**: Verificar `GITHUB_TOKEN` automático, añadir `OPENCODE_API_KEY` si aplica
- [ ] **GitHub App OpenCode**: Instalar si existe y es necesaria para webhooks tiempo real
- [ ] **CODEOWNERS**: Crear `.github/CODEOWNERS` con owners de áreas críticas
- [ ] **Environments**: Crear `staging`/`production` cuando haya deploy pipeline
- [ ] **Dependabot**: Verificar que `.github/dependabot.yml` genera PRs semanales

---

## 9. Verificación Post-Configuración

Después de configurar todo:

1. Crear un PR de prueba (cambio trivial: typo en README)
2. Verificar que:
   - CI se ejecuta automáticamente
   - Todos los checks pasan
   - No se puede mergear sin approval
   - No se puede force-push a main
   - No se puede borrar main
   - Linear history enforced (squash merge)
3. Probar workflow OpenCode:
   - Crear issue con label `opencode` o comentar `/opencode`
   - Verificar que crea rama, PR, y comenta en issue
   - En PR, comentar `/opencode` en review → verificar que hace commit en misma rama

---

*Documento generado como parte de la configuración `chore/github-opencode-workflow`*
*Fecha: 2025-09-04*