import pytest
from copy import deepcopy
from textwrap import dedent
from typing import Final
from pathlib import Path
import tempfile

import responses
from responses import matchers

from pipeline_migration.actions.migrate.constants import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_IS_MIGRATION,
    ANNOTATION_PREVIOUS_MIGRATION_BUNDLE,
    ANNOTATION_TRUTH_VALUE,
    MIGRATION_IMAGE_TAG_LIKE_PATTERN,
)
from pipeline_migration.actions.migrate.resolvers.migration_images import MigrationImageTag
from pipeline_migration.types import DescriptorT, ManifestT
from pipeline_migration.registry import (
    MEDIA_TYPE_OCI_EMTPY_V1,
    Container,
    MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
    MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR,
    MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR_GZ,
    MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
)

from tests.utils import generate_digest, RepoPath


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
def migration_image_manifest() -> ManifestT:
    return {
        "schemaVersion": 2,
        "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
        "config": {
            "mediaType": MEDIA_TYPE_OCI_EMTPY_V1,
            "digest": "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8b",
            "size": 2,
            "data": "e30=",
        },
        "layers": [
            {
                "mediaType": MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR,
                "digest": "sha256:498ce84ac04c70f2bce9630eec216a33f8ab0f345702a830826548f773e351ec",
                "size": 113,
            },
        ],
        "annotations": {
            "dev.konflux-ci.task.is-migration": "true",
        },
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


@pytest.fixture
def mock_get_manifest_for_migration(migration_image_manifest):

    def _mock(c: Container, filename: str, add_additional_layer=False):
        assert "/" not in filename, "File name should not include path components"
        manifest_json = deepcopy(migration_image_manifest)
        layers = manifest_json["layers"]
        annotations = layers[0].setdefault("annotations", {})
        annotations["org.opencontainers.image.title"] = filename
        layers[0]["digest"] = generate_digest()
        if add_additional_layer:
            layers.append(
                {
                    "mediaType": MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR,
                    "digest": generate_digest(),
                    "size": 404,
                },
            )
        responses.add(responses.GET, f"https://{c.manifest_url()}", json=manifest_json)
        return manifest_json

    return _mock


@pytest.fixture
def pipeline_yaml():
    return dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: pl
        spec:
          params:
          - name: git-url
          - name: revision
            default: "main"
          tasks:
          - name: clone
            taskRef:
              resolver: bundles
              params:
              - name: name
                value: git-clone-oci-ta
              - name: bundle
                value: bundle_ref
              - name: kind
                value: task
          - name: test
            taskRef:
              resolver: bundles
              params:
              - name: name
                value: test
              - name: bundle
                value: bundle_ref
              - name: kind
                value: task
          - name: build
            taskRef:
              resolver: bundles
              params:
              - name: name
                value: buildah-oci-ta
              - name: bundle
                value: bundle_ref
              - name: kind
                value: task
        """
    )


@pytest.fixture(params=["no_indents", "2_spaces_indents"])
def pipeline_yaml_with_various_indent_styles(request, pipeline_yaml):
    match request.param:
        case "no_indents":
            return pipeline_yaml
        case "2_spaces_indents":
            return dedent(
                """\
                apiVersion: tekton.dev/v1
                kind: Pipeline
                metadata:
                  name: pl
                spec:
                  tasks:
                    - name: clone
                      image: debian:latest
                      script: |
                        git clone https://git.host/project
                """
            )
        case _:
            raise ValueError(f"Unexpected param {request.param}")


@pytest.fixture
def pipeline_run_yaml() -> str:
    return dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: PipelineRun
        metadata:
          name: docker-build
        spec:
          params:
          - name: git-url
            value: '{{source_url}}'
          - name: revision
            value: '{{revision}}'
          pipelineSpec:
            params:
            - name: git-url
            - name: revision
              default: "main"
            tasks:
            - name: clone
              taskRef:
                resolver: bundles
                params:
                - name: name
                  value: git-clone-oci-ta
                - name: bundle
                  value: bundle_ref
                - name: kind
                  value: task
        """
    )


@pytest.fixture(params=["pipeline", "pipeline_run"])
def pipeline_and_run_yaml(request, pipeline_yaml, pipeline_run_yaml) -> str:
    match request.param:
        case "pipeline":
            return pipeline_yaml
        case "pipeline_run":
            return pipeline_run_yaml
        case _:
            raise ValueError(f"Unexpected param {request.param}")


@pytest.fixture
def component_a_repo(tmp_path, pipeline_run_yaml) -> RepoPath:
    component_a_tekton = tmp_path / "component_a" / ".tekton"
    component_a_tekton.mkdir(parents=True)
    yaml_file = component_a_tekton / "pr.yaml"
    yaml_file.write_text(pipeline_run_yaml)
    yaml_file = component_a_tekton / "push.yaml"
    yaml_file.write_text(pipeline_run_yaml)
    return RepoPath(component_a_tekton.parent)


@pytest.fixture
def component_b_repo(tmp_path, pipeline_yaml) -> RepoPath:
    component_b_tekton = tmp_path / "component_b" / ".tekton"
    component_b_tekton.mkdir(parents=True)
    yaml_file = component_b_tekton / "build-pipeline.yaml"
    yaml_file.write_text(pipeline_yaml)
    return RepoPath(component_b_tekton.parent)


@pytest.fixture
def create_yaml_file(tmp_path):
    def _create(yaml_content) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=tmp_path, mode="w", delete=False, suffix=".yaml", encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_file = Path(f.name)
        return tmp_file

    return _create


@pytest.fixture
def mock_migration_images(mock_get_manifest_for_migration):
    """Mock for MigrationImagesResolver to discover and fetch migration images"""

    def _mock(image_repo: str, tags: list[dict]):
        c = Container(image_repo)
        api_url = f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/"
        responses.get(
            api_url,
            json={"tags": tags, "page": 1, "has_additional": False},
            match=[
                matchers.query_param_matcher(
                    {
                        "page": "1",
                        "onlyActiveTags": "true",
                        "filter_tag_name": "like:" + MIGRATION_IMAGE_TAG_LIKE_PATTERN,
                    },
                )
            ],
        )

        # Mock for Registry.pull()
        for tag in tags:
            tag_name = tag["name"]
            c = Container(f"{image_repo}:{tag_name}")
            migration_image_tag = MigrationImageTag.parse(tag_name)
            if migration_image_tag is not None:
                version = migration_image_tag.version
                manifest_json = mock_get_manifest_for_migration(c, f"{version}.sh")
                # Mock get_blob
                blob_digest = manifest_json["layers"][0]["digest"]
                responses.get(f"https://{c.get_blob_url(blob_digest)}", body=f"echo {version}")

    return _mock
