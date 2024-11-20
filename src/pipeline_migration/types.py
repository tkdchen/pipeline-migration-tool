from os import PathLike
from pathlib import Path
from typing import TypedDict

FilePath = PathLike | str | Path

AnnotationsT = dict[str, str]


class DescriptorT(TypedDict):
    mediaType: str
    digest: str
    size: int
    artifactType: str
    annotations: AnnotationsT


class ImageIndexT(TypedDict):
    schemaVersion: int
    mediaType: str
    manifests: list[DescriptorT]
    annotations: AnnotationsT
