import logging

from collections.abc import Generator
from pathlib import Path
from typing import Any, Final

from pipeline_migration.types import FilePath
from pipeline_migration.utils import YAMLStyle, load_yaml

logger = logging.getLogger("pipeline")

TEKTON_KIND_PIPELINE: Final = "Pipeline"
TEKTON_KIND_PIPELINE_RUN: Final = "PipelineRun"


class NotAPipelineFile(Exception):
    """Raise if a file does not include a pipeline definition"""


class PipelineFileOperation:
    """Base class for handling Pipeline or PipelineRun YAML files"""

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        raise NotImplementedError

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        raise NotImplementedError

    def handle(self, file_path: str) -> None:
        yaml_style = YAMLStyle.detect(file_path)
        doc = load_yaml(file_path, yaml_style)
        if not isinstance(doc, dict):
            raise NotAPipelineFile(f"Given file {file_path} is not a YAML mapping.")
        kind = doc.get("kind")
        if kind == TEKTON_KIND_PIPELINE:
            self.handle_pipeline_file(file_path, doc, yaml_style)
        elif kind == TEKTON_KIND_PIPELINE_RUN:
            spec = doc.get("spec") or {}
            if "pipelineSpec" in spec:
                # pipeline definition is inline the PipelineRun
                self.handle_pipeline_run_file(file_path, doc, yaml_style)
            elif "pipelineRef" in spec:
                # Pipeline definition can be referenced here, via either git-resolver or a name
                # field pointing to YAML file under the .tekton/.
                # In this case, Renovate should not handle the given file as a package file since
                # there is no task bundle references.
                raise NotAPipelineFile("PipelineRun definition seems not embedded.")
            else:
                raise NotAPipelineFile(
                    "PipelineRun .spec field includes neither .pipelineSpec nor .pipelineRef field."
                )
        else:
            raise NotAPipelineFile(
                f"Given file {file_path} does not have known kind Pipeline or PipelineRun."
            )


def iterate_files_or_dirs(files_or_dirs: list[str]) -> Generator[Path]:
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
