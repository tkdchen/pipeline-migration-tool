from pipeline_migration.cache import FileBasedCache


def test_get(tmp_path) -> None:
    fbc = FileBasedCache(tmp_path)
    assert fbc is not None
