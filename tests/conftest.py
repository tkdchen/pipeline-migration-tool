import pytest

from pipeline_migration.cache import FileBasedCache
from pipeline_migration.types import DescriptorT, ManifestT
from pipeline_migration.registry import (
    MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
    MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR_GZ,
    MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
)


@pytest.fixture()
def file_based_cache(tmp_path) -> FileBasedCache:
    """Configure cache and return an instance of it"""
    FileBasedCache.configure(cache_dir=str(tmp_path))
    return FileBasedCache()


@pytest.fixture
def disable_file_based_cache(monkeypatch, tmp_path) -> FileBasedCache:
    monkeypatch.setitem(FileBasedCache.config, "cache_dir", str(tmp_path))
    monkeypatch.setitem(FileBasedCache.config, "disabled", "True")
    return FileBasedCache()


@pytest.fixture
def image_manifest() -> ManifestT:
    """Example image manifest that tests can customize for themselves"""
    return {
        "schemaVersion": 2,
        "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
        "config": {
            "mediaType": MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
            "digest": "sha256:070f25377bd2436ae765bfcc36cd47e9e153cd479d1c0fa147929dd2e1fe21f8",
            "size": 100,
        },
        "layers": [
            {
                "mediaType": MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR_GZ,
                "digest": "sha256:498ce84ac04c70f2bce9630eec216a33f8ab0f345702a830826548f773e351ec",
                "size": 200,
            },
        ],
        "annotations": {},
    }


@pytest.fixture
def oci_image_descriptor() -> DescriptorT:
    return {
        "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
        "digest": "sha256:498ce84ac04c70f2bce9630eec216a33f8ab0f345702a830826548f773e351ef",
        "size": 100,
        "annotations": {},
    }


@pytest.fixture
def oci_referrer_descriptor(oci_image_descriptor) -> DescriptorT:
    oci_image_descriptor["artifactType"] = "text/plain"
    return oci_image_descriptor
