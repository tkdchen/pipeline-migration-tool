import argparse
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Final
from typing import Iterable

from oras.container import Container
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from pipeline_migration.actions.add_task import KonfluxBuildDefinitions
from pipeline_migration.actions.migrate.main import clean_upgrades
from pipeline_migration.actions.migrate.main import migrate
from pipeline_migration.actions.migrate.main import update_bundles_in_pipelines
from pipeline_migration.actions.migrate.constants import logger
from pipeline_migration.actions.migrate.constants import REGEX_BUNDLE_REF_VALUE
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.actions.migrate.resolvers.simple import SimpleIterationResolver
from pipeline_migration.actions.migrate.resolvers.transition_proxy import (
    DecentralizationTransitionResolverProxy,
)
from pipeline_migration.types import RenovateUpgradeT


SUBCMD_DESCRIPTION: Final = """\
Migrate command applies task migrations to build pipelines. It aims to run in
two major scenariors. One is to run as a Renovate post-upgrade command. It is
configured in the Renovate server side. Upgrades are generated via Renovate
templating system and from the `upgrades' template field. Another scenarior is
to do migrations for bundles manually in users side. However, technically,
migrate command itself does not distinguish local and server-side environment.

The following are examples of several scenariors:

* Pass upgrades generated from Renovate template field `upgrades'. All the
  upgrades must be in type tekton-bundle:

    pmt migrate -u '[{"depName": "...", ...}, {"depName": "...", ...}, ...]'

* Pass the generated upgrades via a data file. This is useful for a large
  number of upgrades particularly:

    pmt migrate -f \"$RENOVATE_POST_UPGRADE_COMMAND_DATA_FILE\"

  This command must be configured in the Renovate server-side. During the
  runtime, Renovate sets a file path to the environment variable.

  For details of data file usage, refer to Renovate configuration option
  `dataFileTemplate'.

* Run migrate manually.:

    pmt migrate --new-bundle quay.io/org/image:0.1:@sha256:123456

  In this form, migrate command searches pipelines from directory .tekton/,
  then searches current bundle of quay.io/org/image from those pipelines. As a
  result, migrations are applied if there is and the current bundle is replaced
  with the specified new one inside the searched pipeline files.

  Alternatively, specify an explicit pipeline file:

    pmt migrate --new-bundle quay.io/org/image:0.1:@sha256:123456 --pipeline-file .tekton/pull.yaml
"""


def arg_type_upgrades_file(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"Upgrades file {value} does not exist.")
    return p


def arg_type_pipeline_file(value: str) -> str:
    p = arg_type_upgrades_file(value)
    cur_dir = Path(".").absolute()
    if p.absolute().is_relative_to(cur_dir):
        return str(p)
    raise argparse.ArgumentTypeError(
        f"Pipeline file is not relative to current working directory: {cur_dir}"
    )


def arg_type_bundle_reference(value: str) -> str:
    try:
        KonfluxBuildDefinitions.validate_bundle_ref(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Bundle reference {value} is not valid: {e}")
    return value


def register_cli(subparser) -> None:
    migrate_parser = subparser.add_parser(
        "migrate",
        help="Discover and apply migrations for given task bundles upgrades.",
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = migrate_parser.add_mutually_exclusive_group()
    group.add_argument(
        "-u",
        "--renovate-upgrades",
        metavar="JSON_STR",
        help="A JSON string converted from Renovate template field upgrades.",
    )
    group.add_argument(
        "-f",
        "--upgrades-file",
        metavar="PATH",
        type=arg_type_upgrades_file,
        help="Path to a file containing Renovate upgrades represented as encoded JSON data",
    )
    group.add_argument(
        "-b",
        "--new-bundle",
        metavar="IMAGE",
        dest="new_bundles",
        action="append",
        type=arg_type_bundle_reference,
        help="Migrate to this new task bundle. It must be a valid image reference including both "
        "tag and digest. This option can be specified multiple times to handle multiple bundle "
        "upgrades. This option works with-p/--pipeline-file to apply migrations to specific "
        "pipelines.",
    )
    migrate_parser.add_argument(
        "-l",
        "--use-legacy-resolver",
        action="store_true",
        help="Use legacy resolver to fetch migrations.",
    )
    migrate_parser.add_argument(
        "-p",
        "--pipeline-file",
        metavar="PATH",
        dest="pipeline_files",
        action="append",
        type=arg_type_pipeline_file,
        help="Relative path to Pipeline/PipelineRun YAML file. Used in conjunction with option "
        "-b/--new-bundle only. If omitted, pipeline files will be searched from YAML files "
        "under ./.tekton/ directory that have kind Pipeline or PipelineRun.",
    )
    migrate_parser.set_defaults(action=action)


class DotTekton(Path):

    def list_pipeline_files(self) -> Iterable[Path]:
        for possible_yaml_file in self.glob("*.y[a]ml"):
            doc = None
            with suppress(ScannerError, IOError):
                doc = YAML().load(possible_yaml_file.read_text())
            if doc and isinstance(doc, dict):
                kind = doc.get("kind")
                if kind == "Pipeline" or kind == "PipelineRun":
                    yield possible_yaml_file


def search_pipeline_files() -> list[str]:
    """Search pipeline files from .tekton/

    :param dir_path: A directory path from where to search pipeline files. Pipeline files are Tekton
        Pipeline/PipelineRun YAML files.
    :type dir_path: str
    :return: A list of pipeline file paths.
    :rtype: list[str]
    """
    tekton_dir = DotTekton("./.tekton")
    if not tekton_dir.exists():
        logger.info("Current working directory does not have directory .tekton/.")
        return []
    pipeline_files = list(tekton_dir.list_pipeline_files())
    if not pipeline_files:
        logger.info("No Tekton Pipeline/PipelineRun is found from directory .tekton/.")
        return []
    return list(map(str, pipeline_files))


def generate_upgrades_data(new_bundles: list[str], pipeline_files: list[str]) -> str:
    """Generate Renovate upgrades

    :param new_bundles: a list of new bundle references. Each of them must have both tag and digest.
    :type: list[str]
    :param pipeline_files: a list of pipeline files. Upgrades will be generated for each of these
        pipeline files.
    :type pipeline_files: list[str]
    :return: a string representing the upgrades. That includes fields Renovate template field
        ``upgrades`` has.
    :rtype: str
    """
    upgrades: list[RenovateUpgradeT] = []
    for pipeline_file in pipeline_files:
        with open(pipeline_file, "r") as f:
            content = f.read()
        for bundle_ref in new_bundles:
            new_c = Container(bundle_ref)
            dep_name = f"{new_c.registry}/{new_c.api_prefix}"
            regex = REGEX_BUNDLE_REF_VALUE.replace("dep_name", dep_name)
            if match := re.search(regex, content):
                _, current_bundle_ref, current_tag, _, current_digest = match.groups()
                if current_bundle_ref == bundle_ref:
                    logger.info(
                        "New bundle %s is included in pipeline file already: %s",
                        bundle_ref,
                        pipeline_file,
                    )
                else:
                    upgrades.append(
                        {
                            "depName": dep_name,
                            "currentValue": current_tag,
                            "currentDigest": current_digest,
                            "newValue": new_c.tag,
                            "newDigest": new_c.digest,
                            "depTypes": ["tekton-bundle"],
                            "packageFile": pipeline_file,
                            "parentDir": ".tekton/",
                        }
                    )
            else:
                logger.debug("Bundle %s is not included in pipeline %s", dep_name, pipeline_file)
    return json.dumps(upgrades)


def action(args) -> None:
    resolver_class: type[Resolver]

    if args.use_legacy_resolver:
        resolver_class = SimpleIterationResolver
    else:
        resolver_class = DecentralizationTransitionResolverProxy

    if args.new_bundles:
        pipeline_files = args.pipeline_files or search_pipeline_files()
        if not pipeline_files:
            return
        upgrades_data = generate_upgrades_data(args.new_bundles, pipeline_files)
        logger.debug("Generated upgrades data: %r", upgrades_data)
    elif args.upgrades_file:
        upgrades_data = args.upgrades_file.read_text().strip()
    else:
        upgrades_data = args.renovate_upgrades

    if upgrades_data:
        upgrades = clean_upgrades(upgrades_data)
        if upgrades:
            migrate(upgrades, resolver_class)
            if args.new_bundles:
                update_bundles_in_pipelines(upgrades)
        else:
            logger.warning(
                "Input upgrades does not include Konflux bundles the migration tool aims to handle."
            )
            logger.warning(
                "The upgrades should represent bundles pushed to quay.io/konflux-ci and be "
                "generated by Renovate tekton-bundle manager."
            )
    else:
        logger.info(
            "Empty input upgrades. Either upgrades file or upgrades JSON string must be specified."
        )
