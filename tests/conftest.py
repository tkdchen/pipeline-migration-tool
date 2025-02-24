import pytest
from copy import deepcopy
from typing import Final

import responses

from pipeline_migration.types import DescriptorT, ManifestT
from pipeline_migration.registry import (
    Container,
    MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
    MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR_GZ,
    MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
)
from pipeline_migration.migrate import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_IS_MIGRATION,
    ANNOTATION_PREVIOUS_MIGRATION_BUNDLE,
    ANNOTATION_TRUTH_VALUE,
)
from tests.utils import generate_digest


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


@pytest.fixture
def migration_content() -> bytes:
    return b"echo hello world"


@pytest.fixture
def mock_fetch_migration(oci_referrer_descriptor, image_manifest, migration_content):
    """Mock HTTP requests for method fetch_migration_file"""

    def _mock(c: Container, migration_content: bytes = migration_content) -> None:
        # mock there is a referrer with specific artifactType and annotation
        oci_referrer_descriptor["annotations"] = {ANNOTATION_IS_MIGRATION: ANNOTATION_TRUTH_VALUE}
        image_index = {
            "schemaVersion": 2,
            "manifests": [oci_referrer_descriptor],
            "annotations": {},
        }
        responses.get(
            f"https://{c.referrers_url}?artifactType=text/x-shellscript", json=image_index
        )

        layer_digest: Final = generate_digest()

        # mock getting referrer image manifest
        referrer_manifest = deepcopy(image_manifest)
        referrer_manifest["layers"][0]["digest"] = layer_digest
        c.digest = oci_referrer_descriptor["digest"]
        responses.get(f"https://{c.manifest_url()}", json=referrer_manifest)

        # mock getting referrer's layer blob, i.e. the content
        responses.get(f"https://{c.get_blob_url(layer_digest)}", body=migration_content)

    return _mock


@pytest.fixture
def mock_get_manifest(image_manifest):
    """Mock get_manifest for image represented by a Container object"""

    def _mock(c: Container, has_migration=False, previous_migration_bundle: str | None = None):
        manifest_json = deepcopy(image_manifest)
        annotations = manifest_json["annotations"]
        if has_migration:
            annotations[ANNOTATION_HAS_MIGRATION] = ANNOTATION_TRUTH_VALUE
        if previous_migration_bundle is not None:
            annotations[ANNOTATION_PREVIOUS_MIGRATION_BUNDLE] = previous_migration_bundle
        responses.add(responses.GET, f"https://{c.manifest_url()}", json=manifest_json)

    return _mock
