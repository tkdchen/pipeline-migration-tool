import pytest

from pipeline_migration.cache import ENV_FBC_DIR
from pipeline_migration.types import DescriptorT, ManifestT


@pytest.fixture(autouse=True)
def mock_fbc_dir(monkeypatch, tmp_path):
    fbc_dir = tmp_path / "file-based-cache"
    fbc_dir.mkdir()
    monkeypatch.setenv(ENV_FBC_DIR, str(fbc_dir))
    return fbc_dir


@pytest.fixture
def image_manifest() -> ManifestT:
    """Example image manifest that tests can customize for themselves"""
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:070f25377bd2436ae765bfcc36cd47e9e153cd479d1c0fa147929dd2e1fe21f8",
            "size": 100,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:498ce84ac04c70f2bce9630eec216a33f8ab0f345702a830826548f773e351ec",
                "size": 200,
            },
        ],
        "annotations": {},
    }


@pytest.fixture
def oci_image_descriptor() -> DescriptorT:
    return {
        "mediaType": "image.manifest",
        "digest": "sha256:498ce84ac04c70f2bce9630eec216a33f8ab0f345702a830826548f773e351ef",
        "size": 100,
        "annotations": {},
    }


@pytest.fixture
def oci_referrer_descriptor(oci_image_descriptor) -> DescriptorT:
    oci_image_descriptor["artifactType"] = "text/plain"
    return oci_image_descriptor
