import argparse
import logging
from typing import Any

from pipeline_migration.pipeline import PipelineFileOperation, iterate_files_or_dirs
from pipeline_migration.types import FilePath
from pipeline_migration.utils import YAMLStyle, BlockSequenceIndentation, dump_yaml

logger = logging.getLogger("formatter")


def register_cli(subparser) -> None:
    format_parser: argparse.ArgumentParser = subparser.add_parser(
        "format", help="Format given build pipelines."
    )
    format_parser.add_argument(
        "file_or_dir",
        nargs="*",
        help="Specify locations from where finding out pipelines to add task. "
        "A pipeline can be included in a PipelineRun or a single Pipeline definition. "
        "%(prog)s searches pipelines from given locations by rules, if files are specified, "
        "search just pipelines from them. If directories are specified, search YAML files from the "
        "first level of each one. If neither is specified, the location defaults to ./.tekton/ "
        "directory.",
    )
    format_parser.set_defaults(action=action)


def action(args) -> None:
    formatter = FormatterFileOperation()
    for file_path in iterate_files_or_dirs(args.file_or_dir):
        logger.info("format %s", file_path)
        formatter.handle(str(file_path))


class FormatterFileOperation(PipelineFileOperation):

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        self._format(file_path, loaded_doc, style)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        self._format(file_path, loaded_doc, style)

    def _format(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        style.indentation = BlockSequenceIndentation()
        style.indentation.indent(0)
        dump_yaml(file_path, loaded_doc, style)
