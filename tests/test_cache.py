import os
import pytest
import tempfile

from pipeline_migration.cache import ENV_FBC_DIR, FileBasedCache, get_cache, set_cache_dir
from tests.utils import generate_digest


class TestFileBasedCacheGet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_nothing_is_cached(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        assert not list(mock_fbc_dir.iterdir())
        v = c.get(self.key)
        assert v is None

    def test_get_cached_data(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        c.set(self.key, self.content)
        v = c.get(self.key)
        assert v == self.content

    def test_get_cached_data_if_cached_more_than_once(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        c.set(self.key, self.content)
        c.set(self.key, self.content)
        v = c.get(self.key)
        assert v == self.content

    def test_raise_error_if_empty_key(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        with pytest.raises(ValueError, match="Key is empty"):
            c.get("")


class TestFileBasedCacheSet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_set_value(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        c.set(self.key, self.content)
        files = list(mock_fbc_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == self.content

    def test_set_same_value_twice(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        c.set(self.key, self.content)
        c.set(self.key, self.content)
        files = list(mock_fbc_dir.iterdir())
        assert len(files) == 2
        assert all([f.name.startswith(f"{self.key}-") for f in files])
        contents = [f.read_text() for f in files]
        assert contents == [self.content, self.content]

    def test_raise_error_if_empty_key(self, mock_fbc_dir):
        c = FileBasedCache(mock_fbc_dir)
        with pytest.raises(ValueError, match="Key is empty."):
            c.set("", "some content")


@pytest.mark.parametrize("create_dir", [True, False])
def test_set_cache_dir(create_dir, monkeypatch, tmp_path):
    cache_dir = tmp_path / "test-cache-dir"
    if create_dir:
        cache_dir.mkdir()
    if create_dir:
        set_cache_dir(str(cache_dir))
        assert os.environ[ENV_FBC_DIR] == str(cache_dir)
    else:
        with pytest.raises(ValueError, match="does not exist"):
            set_cache_dir(str(cache_dir))


class TestGetCache:

    def test_get_cache(self):
        """Cache directory is set globally by the fixture"""
        cache = get_cache()
        assert cache

    def test_missing_directory_path(self, monkeypatch):
        monkeypatch.delenv(ENV_FBC_DIR)
        with pytest.raises(ValueError, match="Missing environment variable .+"):
            get_cache()

    def test_given_directory_does_not_exist(self, monkeypatch):
        cache_dir = tempfile.mkdtemp()
        os.rmdir(cache_dir)
        monkeypatch.setenv(ENV_FBC_DIR, cache_dir)
        with pytest.raises(ValueError, match="Cache directory .+ does not exist."):
            get_cache()
