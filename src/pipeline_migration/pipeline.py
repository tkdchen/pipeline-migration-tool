import os
import logging
import tempfile

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from pipeline_migration.types import FilePath
from pipeline_migration.utils import dump_yaml, file_checksum, load_yaml, YAMLStyle

logger = logging.getLogger("pipeline")

TEKTON_KIND_PIPELINE: Final = "Pipeline"
TEKTON_KIND_PIPELINE_RUN: Final = "PipelineRun"


class NotAPipelineFile(Exception):
    """Raise if a file does not include a pipeline definition"""


@contextmanager
def resolve_pipeline(pipeline_file: FilePath) -> Generator[FilePath, Any, None]:
    """Yield resolved pipeline file

    :param pipeline_file:
    :type pipeline_file: str
    :return: a generator yielding a file containing the pipeline definition.
    :raises NotAPipelineFile: if a YAML file is not a Pipeline definition or a
        PipelineRun with embedded pipelineSpec.
    """
    yaml_style = YAMLStyle.detect(pipeline_file)
    origin_pipeline = load_yaml(pipeline_file, style=yaml_style)

    if not isinstance(origin_pipeline, dict):
        raise NotAPipelineFile(f"Given file {pipeline_file} is not a YAML mapping.")

    kind = origin_pipeline.get("kind")
    if kind == TEKTON_KIND_PIPELINE:
        origin_checksum = file_checksum(pipeline_file)
        yield pipeline_file
        if file_checksum(pipeline_file) != origin_checksum:
            pl_yaml = load_yaml(pipeline_file, style=yaml_style)
            dump_yaml(pipeline_file, pl_yaml, style=yaml_style)
    elif kind == TEKTON_KIND_PIPELINE_RUN:
        spec = origin_pipeline.get("spec") or {}
        if "pipelineSpec" in spec:
            # pipeline definition is inline the PipelineRun
            fd, temp_pipeline_file = tempfile.mkstemp(suffix="-pipeline")
            os.close(fd)
            pipeline = {"spec": spec["pipelineSpec"]}
            dump_yaml(temp_pipeline_file, pipeline, style=yaml_style)
            origin_checksum = file_checksum(temp_pipeline_file)
            yield temp_pipeline_file
            if file_checksum(temp_pipeline_file) != origin_checksum:
                modified_pipeline = load_yaml(temp_pipeline_file, style=yaml_style)
                spec["pipelineSpec"] = modified_pipeline["spec"]
                dump_yaml(pipeline_file, origin_pipeline, style=yaml_style)
        elif "pipelineRef" in spec:
            # Pipeline definition can be referenced here, via either git-resolver or a name field
            # pointing to YAML file under the .tekton/.
            # In this case, Renovate should not handle the given file as a package file since
            # there is no task bundle references.
            raise NotAPipelineFile("PipelineRun definition seems not embedded.")
        else:
            raise NotAPipelineFile(
                "PipelineRun .spec field includes neither .pipelineSpec nor .pipelineRef field."
            )
    else:
        raise NotAPipelineFile(
            f"Given file {pipeline_file} does not have knownn kind Pipeline or PipelineRun."
        )


def search_pipeline_files(files_or_dirs: Iterable[str]) -> Generator[tuple[str, str]]:

    def _iterate_files_or_dirs() -> Generator[Path]:
        for item in files_or_dirs:
            if not item:
                continue
            entry_path = Path(item).absolute()
            if entry_path.is_symlink():
                logger.warning(
                    "Skip symlink %s. Please specify the destination file or directory instead.",
                    item,
                )
            elif entry_path.is_dir():
                for entry in entry_path.iterdir():
                    if entry.is_symlink():
                        continue
                    if entry.is_file() and entry.name.endswith(".yaml"):
                        yield entry
            elif entry_path.is_file():
                yield entry_path

    for file_path in _iterate_files_or_dirs():
        try:
            with resolve_pipeline(file_path) as pipeline_file:
                yield str(file_path), str(pipeline_file)
        except NotAPipelineFile as e:
            logger.warning("%s is not an expected pipeline file due to: %s", file_path.name, e)
        except Exception as e:
            logger.warning("%s seems not a YAML file due to: %s", file_path.name, e)
