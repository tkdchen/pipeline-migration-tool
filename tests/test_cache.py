import pytest

from pipeline_migration.cache import FileBasedCache
from tests.utils import generate_digest


class TestFileBasedCacheGet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_nothing_is_cached(self, file_based_cache):
        assert not list(file_based_cache.path.iterdir())
        v = file_based_cache.get(self.key)
        assert v is None

    def test_get_cached_data(self, file_based_cache):
        file_based_cache.set(self.key, self.content)
        v = file_based_cache.get(self.key)
        assert v == self.content

    def test_get_cached_data_if_cached_more_than_once(self, file_based_cache):
        file_based_cache.set(self.key, self.content)
        file_based_cache.set(self.key, self.content)
        v = file_based_cache.get(self.key)
        assert v == self.content

    def test_raise_error_if_empty_key(self, file_based_cache):
        with pytest.raises(ValueError, match="Key is empty"):
            file_based_cache.get("")


class TestFileBasedCacheSet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_set_value(self, file_based_cache):
        file_based_cache.set(self.key, self.content)
        files = list(file_based_cache.path.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == self.content

    def test_set_same_value_twice(self, file_based_cache):
        file_based_cache.set(self.key, self.content)
        file_based_cache.set(self.key, self.content)
        files = list(file_based_cache.path.iterdir())
        assert len(files) == 2
        assert all([f.name.startswith(f"{self.key}-") for f in files])
        contents = [f.read_text() for f in files]
        assert contents == [self.content, self.content]

    def test_raise_error_if_empty_key(self, file_based_cache):
        with pytest.raises(ValueError, match="Key is empty."):
            file_based_cache.set("", "some content")


class TestCacheDirValidation:

    def test_not_configured(self, monkeypatch):
        monkeypatch.setitem(FileBasedCache.config, "cache_dir", "")
        with pytest.raises(ValueError, match="not set"):
            FileBasedCache()

    def test_is_not_absolute(self, monkeypatch):
        monkeypatch.setitem(FileBasedCache.config, "cache_dir", "tmp/cache_dir")
        with pytest.raises(ValueError, match="is not an absolute path"):
            FileBasedCache()

    @pytest.mark.parametrize("create_file", [True, False])
    def test_is_not_dir(self, create_file, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        if create_file:
            cache_dir.touch()
        monkeypatch.setitem(FileBasedCache.config, "cache_dir", cache_dir)
        with pytest.raises(IOError, match="is not a directory"):
            FileBasedCache()
