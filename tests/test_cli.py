import os

from pipeline_migration.cache import ENV_FBC_DIR
from pipeline_migration.cli import entry_point


class TestSetFBCDir:

    def test_set_from_command_line(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ENV_FBC_DIR)
        monkeypatch.setattr("sys.argv", ["mt", "-u", "{}", "-d", str(tmp_path)])
        monkeypatch.setattr("pipeline_migration.migrate.migrate", lambda: 1)
        entry_point()
        assert os.environ[ENV_FBC_DIR] == str(tmp_path)

    def test_fallback_to_a_temporary_dir(self, monkeypatch):
        monkeypatch.delenv(ENV_FBC_DIR)
        monkeypatch.setattr("sys.argv", ["mt", "-u", "{}"])
        monkeypatch.setattr("pipeline_migration.migrate.migrate", lambda: 1)
        entry_point()
        cache_dir = os.environ[ENV_FBC_DIR]
        assert os.path.isdir(cache_dir)
        assert os.path.basename(cache_dir.rstrip("/")).startswith("cache-dir-")
