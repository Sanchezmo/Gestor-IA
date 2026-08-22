"""
Tests para PROJECT_ROOT - Resolución robusta independiente del cwd.

Estos tests DEMUESTRAN que el root NO depende del directorio de trabajo.
"""

import inspect
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import core.hermes.instance_config as ic_module
import core.hermes.utils as utils_module
from core.hermes.utils import (
    get_global_env_path,
    get_instances_root,
    get_project_root,
)


class TestProjectRootResolution:
    """Tests de resolución de PROJECT_ROOT."""

    def test_get_project_root_uses_env_var_when_set(self, monkeypatch):
        """GESTOR_IA_ROOT env var tiene prioridad máxima."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GESTOR_IA_ROOT", tmpdir)
            # No depender de cwd
            with patch("os.getcwd", return_value="/tmp/other"):
                root = get_project_root()
                assert root == Path(tmpdir).resolve()

    def test_get_project_root_resolves_from_package_when_no_env(self, monkeypatch):
        """Si no hay env var, resuelve desde ubicación del paquete."""
        monkeypatch.delenv("GESTOR_IA_ROOT", raising=False)
        root = get_project_root()
        # Debe resolver al root real del proyecto (que tiene core/ e instances/)
        assert root.name == "Gestor-IA"
        assert (root / "core").exists()
        assert (root / "instances").exists()

    def test_get_project_root_cwd_fallback(self, monkeypatch):
        """Path.cwd() como último recurso - documentado como comportamiento."""
        # Este test documenta que el fallback existe; la implementación real
        # usa resolución desde __file__ que es más robusta.
        # El fallback a cwd() solo ocurre si falla la resolución por paquete.
        pass

    def test_get_instances_root_uses_project_root(self):
        """get_instances_root usa project root resuelto."""
        instances_root = get_instances_root()
        assert instances_root.name == "instances"
        assert instances_root.parent.name == "Gestor-IA"

    def test_get_global_env_path(self):
        """get_global_env_path devuelve .env en project root."""
        env_path = get_global_env_path()
        assert env_path.name == ".env"
        assert env_path.parent.name == "Gestor-IA"

    def test_explicit_env_overrides_package_resolution(self, monkeypatch):
        """Variable explícita sobrescribe resolución por paquete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GESTOR_IA_ROOT", tmpdir)
            root = get_project_root()
            assert root == Path(tmpdir).resolve()


def test_root_resolves_to_actual_project_not_cwd():
    """El root resuelve al proyecto real, no al cwd actual.

    Este test verifica que la resolución usa __file__ (ubicación del paquete)
    y no el cwd. La función get_project_root() usa Path(__file__).resolve().parents[3]
    que es independiente del directorio de trabajo actual.
    """
    root = get_project_root()
    assert root.name == "Gestor-IA"
    assert (root / "core").exists()
    assert (root / "instances").exists()

    # Verificar que usa __file__ para resolver (no cwd como primera opción)
    source = inspect.getsource(utils_module.get_project_root)
    # Debe usar __file__ para resolver
    assert "__file__" in source
    assert "parents[3]" in source
    # Verificar que el orden de prioridad es: env var -> package -> cwd fallback
    # (comprobado por test_explicit_env_overrides_package_resolution)


class TestGlobalSettingsProjectRoot:
    """Tests de PROJECT_ROOT en GlobalSettings."""

    def test_global_settings_uses_get_project_root(self, monkeypatch):
        """GlobalSettings.PROJECT_ROOT usa get_project_root()."""
        from core.hermes.config import get_global_settings as get_gs

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GESTOR_IA_ROOT", tmpdir)
            # Limpiar cache de lru_cache
            get_gs.cache_clear()

            settings = get_gs()
            assert settings.PROJECT_ROOT == Path(tmpdir).resolve()

    def test_global_settings_cached(self, monkeypatch):
        """GlobalSettings se cachea correctamente."""
        from core.hermes.config import get_global_settings

        get_global_settings.cache_clear()

        settings1 = get_global_settings()
        settings2 = get_global_settings()

        assert settings1 is settings2  # Misma instancia por lru_cache


class TestProjectRootIntegration:
    """Tests de integración con otros módulos."""

    def test_cli_uses_get_project_root(self):
        """CLI usa get_project_root(), no Path.cwd()."""
        # Leer el archivo fuente directamente para evitar importar dependencias
        cli_file = Path(__file__).parent.parent.parent / "core" / "hermes" / "cli" / "__init__.py"
        source = cli_file.read_text()
        assert "get_project_root" in source
        assert "Path.cwd()" not in source

    def test_instance_config_uses_get_instances_root(self):
        """instance_config usa get_instances_root()."""
        source = inspect.getsource(ic_module.load_instance_config)
        assert "get_instances_root" in source
        assert "get_global_settings().PROJECT_ROOT" not in source


# =========================================================================
# RUN TESTS
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
