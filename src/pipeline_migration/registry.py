from typing import Final
import urllib.parse

from dataclasses import dataclass

from oras.provider import Registry as OrasRegistry
from oras.container import Container as OrasContainer
from oras.decorator import ensure_container
from oras.types import container_type
from requests.models import Response as Response

from pipeline_migration.types import AnnotationsT, ImageIndexT, DescriptorT

MEDIA_TYPE_OCI_EMTPY_V1: Final = "application/vnd.oci.empty.v1+json"
MEDIA_TYPE_OCI_IMAGE_CONFIG_V1: Final = "application/vnd.oci.image.config.v1+json"
MEDIA_TYPE_OCI_IMAGE_INDEX_V1: Final = "application/vnd.oci.image.index.v1+json"
MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1: Final = "application/vnd.oci.image.manifest.v1+json"
MEDIA_TYPE_OCI_IMAGE_LAYER_V1_TAR_GZ: Final = "application/vnd.oci.image.layer.v1.tar+gzip"


@dataclass
class Descriptor:
    data: DescriptorT

    @property
    def digest(self) -> str:
        return self.data["digest"]

    @property
    def annotations(self) -> AnnotationsT:
        return self.data.get("annotations", {})


@dataclass
class ImageIndex:
    data: ImageIndexT

    @property
    def manifests(self) -> list[Descriptor]:
        return [Descriptor(data=item) for item in self.data["manifests"]]


class Container(OrasContainer):

    @property
    def referrers_url(self) -> str:
        return f"{self.registry}/v2/{self.api_prefix}/referrers/{self.digest}"

    @property
    def uri_with_tag(self) -> str:
        """Include the tag in the uri

        :return: include the tag in the uri. If tag is not set, the return value is same as
            ``self.uri``.
        """
        uri = self.uri
        if self.tag:
            uri = uri.replace("@", f":{self.tag}@")
        return uri


class Registry(OrasRegistry):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @ensure_container
    def get_blob(self, *args, **kwargs) -> Response:
        response = super().get_blob(*args, **kwargs)
        self._check_200_response(response)
        return response

    @ensure_container
    def get_artifact(self, container: container_type, digest: str) -> str:
        resp = self.get_blob(container, digest)
        return resp.content.decode("utf-8")

    @ensure_container
    def list_referrers(self, c: Container, artifact_type: str | None = None) -> ImageIndexT:
        """List referrers of given image

        :param c: a Container object representing an image.
        :type c: Container
        :param artifact_type: query the referrers by artifact type.
        :type artifact_type: str or None
        :return: the raw JSON responded by the registry. That is an image
            index, where manifests field are the images referring the given one.
        """
        if not c.digest:
            raise ValueError("Missing digest in image.")
        referrers_api = f"{self.prefix}://{c.referrers_url}"
        query_args = ""
        if artifact_type:
            query_args = urllib.parse.urlencode([("artifactType", artifact_type)])
        referrers_api = f"{referrers_api}?{query_args}"
        resp = self.do_request(referrers_api)
        self._check_200_response(resp)
        return resp.json()
