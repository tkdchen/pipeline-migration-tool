import copy
import pytest
import responses

from tests.utils import generate_digest
from pipeline_migration.registry import (
    Container,
    Descriptor,
    ImageIndex,
    MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
    MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
    Registry,
)
from pipeline_migration.types import DescriptorT, ImageIndexT


@pytest.mark.parametrize("tag", ["", "0.1"])
def test_container_uri_with_tag(tag):
    image = "reg.io/ns/app"
    c = Container("reg.io/ns/app")
    c.tag = tag
    c.digest = generate_digest()
    if tag:
        assert c.uri_with_tag == f"{image}:{c.tag}@{c.digest}"
    else:
        assert c.uri_with_tag == c.uri


REFERRER_DESCRIPTOR: DescriptorT = {
    "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
    "digest": "sha256:1234567",
    "size": 100,
    "artifactType": "text/plain",
    "annotations": {},
}


IMAGE_INDEX: ImageIndexT = {
    "schemaVersion": 2,
    "mediaType": MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
    "manifests": [],
    "annotations": {},
}


class TestDescriptor:

    def test_get_digest(self):
        descriptor = REFERRER_DESCRIPTOR.copy()
        d = Descriptor(data=descriptor)
        assert d.digest == descriptor["digest"]

    def test_get_annotations(self):
        descriptor = REFERRER_DESCRIPTOR.copy()
        descriptor["annotations"]["key"] = "value"
        d = Descriptor(data=descriptor)
        assert d.annotations == descriptor["annotations"]


def test_image_index_get_manifest() -> None:
    index_json: ImageIndexT = {
        "schemaVersion": 2,
        "mediaType": MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
        "manifests": [REFERRER_DESCRIPTOR.copy()],
        "annotations": {},
    }
    manifests = ImageIndex(data=index_json).manifests
    assert manifests == [Descriptor(data=index_json["manifests"][0])]


class TestListReferrers:

    def test_fail_missing_digest(self):
        c = Container("reg.io/ns/app:0.1")
        with pytest.raises(ValueError, match="Missing digest"):
            Registry().list_referrers(c)

    def _make_list_request(self, count):
        digest = generate_digest()
        c = Container(f"reg.io/ns/app@{digest}")
        referrers = [
            REFERRER_DESCRIPTOR.copy(),
            REFERRER_DESCRIPTOR.copy(),
            REFERRER_DESCRIPTOR.copy(),
        ]
        expected_image_index = copy.deepcopy(IMAGE_INDEX)
        expected_image_index["manifests"].extend(referrers)
        mock_resp = responses.get(f"https://{c.referrers_url}", json=expected_image_index)
        for _ in range(count):
            image_index = Registry().list_referrers(c)
            assert image_index["manifests"] == referrers
        return mock_resp

    @responses.activate
    def test_list_referrers(self):
        mock_resp = self._make_list_request(2)
        assert mock_resp.call_count == 2

    @responses.activate
    def test_list_referrers_by_artifact_type(self):
        digest = generate_digest()
        c = Container(f"reg.io/ns/app@{digest}")
        expected_image_index = copy.deepcopy(IMAGE_INDEX)
        expected_image_index["manifests"].append(REFERRER_DESCRIPTOR.copy())
        responses.get(
            f"https://{c.referrers_url}?artifactType=text/plain", json=expected_image_index
        )
        image_index = Registry().list_referrers(c, "text/plain")
        assert image_index["manifests"] == [REFERRER_DESCRIPTOR.copy()]

    @responses.activate
    def test_ensure_error_response_is_handled(self, monkeypatch, caplog):
        monkeypatch.setattr("time.sleep", lambda n: n)  # make oras retry not sleep
        digest = generate_digest()
        c = Container(f"reg.io/ns/app@{digest}")
        errors_json = {"errors": [{"message": "something is wrong"}]}
        responses.get(f"https://{c.referrers_url}", json=errors_json, status=500)
        with pytest.raises(ValueError, match="Issue with .+ Internal Server Error"):
            Registry().list_referrers(c)
        assert "something is wrong" in caplog.text


@responses.activate
def test_get_manifest(image_manifest):
    image_digest = generate_digest()
    c = Container(f"reg.io/ns/app@{image_digest}")
    responses.get(f"https://{c.manifest_url()}", json=image_manifest)
    assert Registry().get_manifest(c) == image_manifest


@responses.activate
def test_get_artifact():
    expected_content = "echo hello world"
    image_digest = generate_digest()
    c = Container("reg.io/ns/app")
    responses.get(f"https://{c.get_blob_url(image_digest)}", body=expected_content.encode("utf-8"))
    content = Registry().get_artifact(c, image_digest)
    assert content == expected_content
