import argparse
from typing import Final

from pipeline_migration.actions.modify.task import register_cli as register_mod_task_cli
from pipeline_migration.actions.modify.generic import register_cli as register_mod_generic_cli

SUBCMD_DESCRIPTION: Final = """\
Allows to modify existing resources in Konflux pipelines/pipeline runs.
"""


def register_cli(subparser) -> None:
    modify_parser = subparser.add_parser(
        "modify",
        help="Modify the specified resource",
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modify_parser.add_argument(
        "-f",
        "--file-or-dir",
        dest="file_or_dir",
        action="append",
        default=[],
        help="Specify locations from where finding out pipelines. "
        "A pipeline can be included in a PipelineRun or a single Pipeline definition. "
        "%(prog)s searches pipelines from given locations by rules, if files are specified, "
        "search just pipelines from them. If directories are specified, search YAML files from the "
        "first level of each one. If neither is specified, the location defaults to ./.tekton/ "
        "directory.",
    )
    subparser_modify = modify_parser.add_subparsers(
        title="subcommands to manage given resources", required=True
    )
    register_mod_task_cli(subparser_modify)
    register_mod_generic_cli(subparser_modify)
