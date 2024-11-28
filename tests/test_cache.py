import os
import pytest
import tempfile

from pipeline_migration.cache import (
    ENV_CACHE_DIR,
    CACHE_DIR_PREFIX,
    FileBasedCache,
    get_cache,
    set_cache_dir,
)
from tests.utils import generate_digest


class TestFileBasedCacheGet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_nothing_is_cached(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        assert not list(setup_cache_dir.iterdir())
        v = c.get(self.key)
        assert v is None

    def test_get_cached_data(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        c.set(self.key, self.content)
        v = c.get(self.key)
        assert v == self.content

    def test_get_cached_data_if_cached_more_than_once(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        c.set(self.key, self.content)
        c.set(self.key, self.content)
        v = c.get(self.key)
        assert v == self.content

    def test_raise_error_if_empty_key(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        with pytest.raises(ValueError, match="Key is empty"):
            c.get("")


class TestFileBasedCacheSet:

    def setup_method(self, method):
        self.key = f"some-content-{generate_digest()}"
        self.content = "echo hello world"

    def test_set_value(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        c.set(self.key, self.content)
        files = list(setup_cache_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == self.content

    def test_set_same_value_twice(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        c.set(self.key, self.content)
        c.set(self.key, self.content)
        files = list(setup_cache_dir.iterdir())
        assert len(files) == 2
        assert all([f.name.startswith(f"{self.key}-") for f in files])
        contents = [f.read_text() for f in files]
        assert contents == [self.content, self.content]

    def test_raise_error_if_empty_key(self, setup_cache_dir):
        c = FileBasedCache(setup_cache_dir)
        with pytest.raises(ValueError, match="Key is empty."):
            c.set("", "some content")


class TestSetCacheDir:

    @pytest.mark.parametrize("create", [True, False])
    def test_use_env(self, create, setup_cache_dir):
        if not create:
            setup_cache_dir.rmdir()
            with pytest.raises(ValueError, match="Cache directory .+ does not exist."):
                set_cache_dir("/path/to/cache")
        else:
            set_cache_dir("/path/to/cache")
            assert os.environ[ENV_CACHE_DIR] == str(setup_cache_dir)

    @pytest.mark.parametrize("create", [True, False])
    def test_set_given_dir(self, create, monkeypatch, tmp_path):
        monkeypatch.delenv(ENV_CACHE_DIR)
        cache_dir = tmp_path / "test-cache_dir"
        if create:
            cache_dir.mkdir()
            set_cache_dir(str(cache_dir))
            assert os.environ[ENV_CACHE_DIR] == str(cache_dir)
        else:
            with pytest.raises(ValueError, match="Cache directory .+ does not exist."):
                set_cache_dir(str(cache_dir))

    @pytest.mark.parametrize("dir_path", ["", None])
    def test_fallback(self, dir_path, monkeypatch):
        monkeypatch.delenv(ENV_CACHE_DIR)
        set_cache_dir(dir_path)
        cache_dir = os.environ[ENV_CACHE_DIR].rstrip("/")
        assert os.path.basename(cache_dir).startswith(CACHE_DIR_PREFIX)
        assert os.path.exists(cache_dir)


class TestGetCache:

    def test_get_cache(self):
        """Cache directory is set globally by the fixture"""
        cache = get_cache()
        assert cache

    def test_missing_directory_path(self, monkeypatch):
        monkeypatch.delenv(ENV_CACHE_DIR)
        with pytest.raises(ValueError, match="Missing environment variable .+"):
            get_cache()

    def test_given_directory_does_not_exist(self, monkeypatch):
        cache_dir = tempfile.mkdtemp()
        os.rmdir(cache_dir)
        monkeypatch.setenv(ENV_CACHE_DIR, cache_dir)
        with pytest.raises(ValueError, match="Cache directory .+ does not exist."):
            get_cache()
