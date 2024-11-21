from os import PathLike
from pathlib import Path
from typing import TypedDict, NotRequired

FilePath = PathLike | str | Path

AnnotationsT = dict[str, str]


class DescriptorT(TypedDict):
    mediaType: str
    digest: str
    size: int
    annotations: NotRequired[AnnotationsT]
    artifactType: NotRequired[str]


class ImageIndexT(TypedDict):
    schemaVersion: int
    mediaType: str
    manifests: list[DescriptorT]
    annotations: AnnotationsT


class ManifestT(TypedDict):
    schemaVersion: int
    mediaType: str
    config: DescriptorT
    layers: list[DescriptorT]
    annotations: AnnotationsT
