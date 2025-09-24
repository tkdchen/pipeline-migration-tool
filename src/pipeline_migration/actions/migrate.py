import argparse
import json
import logging
import os.path
import operator
import re
import subprocess as sp
import tempfile

from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from itertools import takewhile
from operator import itemgetter
from pathlib import Path
from typing import Final, Any

from jsonschema.exceptions import ValidationError
from jsonschema.validators import Draft202012Validator
from packaging.version import Version, parse as parse_version

from pipeline_migration.pipeline import PipelineFileOperation
from pipeline_migration.quay import QuayTagInfo, list_active_repo_tags
from pipeline_migration.registry import Container, Registry, ImageIndex
from pipeline_migration.types import FilePath
from pipeline_migration.utils import is_true, file_checksum, load_yaml, dump_yaml, YAMLStyle

ANNOTATION_HAS_MIGRATION: Final[str] = "dev.konflux-ci.task.has-migration"
ANNOTATION_IS_MIGRATION: Final[str] = "dev.konflux-ci.task.is-migration"
ANNOTATION_PREVIOUS_MIGRATION_BUNDLE: Final[str] = "dev.konflux-ci.task.previous-migration-bundle"

ANNOTATION_TRUTH_VALUE: Final = "true"

# Example:  0.1-18a61693389c6c912df587f31bc3b4cc53eb0d5b
TASK_TAG_REGEXP: Final = r"^[0-9.]+-[0-9a-f]+$"
DIGEST_REGEXP: Final = r"sha256:[0-9a-f]+"

logger = logging.getLogger("migrate")

SCHEMA_UPGRADE: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12",
    "title": "Schema for Renovate upgrade data",
    "type": "object",
    "properties": {
        "depName": {"type": "string", "minLength": 1},
        "currentValue": {"type": "string", "minLength": 1},
        "currentDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "newValue": {"type": "string", "minLength": 1},
        "newDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "depTypes": {"type": "array", "items": {"type": "string"}},
        "packageFile": {"type": "string", "minLength": 1},
        "parentDir": {"type": "string", "minLength": 1},
    },
    "additionalProperties": True,
    "required": [
        "currentDigest",
        "currentValue",
        "depName",
        "depTypes",
        "newDigest",
        "newValue",
        "packageFile",
        "parentDir",
    ],
}


@dataclass
class TaskBundleMigration:
    # A complete image reference with both tag and digest
    task_bundle: str
    # Content of the script
    migration_script: str


@dataclass
class TaskBundleUpgrade:
    dep_name: str  # Renovate template field: depName
    current_value: str  # Renovate template field: currentValue. It is the image tag.
    current_digest: str  # Renovate template field: currentDigest
    new_value: str  # Renovate template field: newValue. It is the image tag.
    new_digest: str  # Renovate template field: newDigest

    migrations: list[TaskBundleMigration] = field(default_factory=list)

    @property
    def current_bundle(self) -> str:
        return f"{self.dep_name}:{self.current_value}@{self.current_digest}"

    @property
    def new_bundle(self) -> str:
        return f"{self.dep_name}:{self.new_value}@{self.new_digest}"


@dataclass
class PackageFile:
    file_path: str  # Renovate template field packageFile
    parent_dir: str  # Renovate template field parentDir

    task_bundle_upgrades: list[TaskBundleUpgrade] = field(default_factory=list)


class InvalidRenovateUpgradesData(ValueError):
    """Raise this error if any required data is missing in the given Renovate upgrades"""


class MigrationResolveError(Exception):
    def __init__(self, msg, bundle_upgrade: TaskBundleUpgrade, raw_exception: Exception) -> None:
        super().__init__(msg)
        self.bundle_upgrade = bundle_upgrade
        self.raw_exception = raw_exception


class MigrationApplyError(Exception):
    def __init__(
        self,
        msg: str,
        pipeline_file: str,
        bundle_upgrade: TaskBundleUpgrade,
        migration: TaskBundleMigration,
        raw_exception: Exception,
    ) -> None:
        super().__init__(msg)
        self.pipeline_file = pipeline_file
        self.bundle_upgrade = bundle_upgrade
        self.migration = migration
        self.raw_exception = raw_exception


def only_tags_pinned_by_version_revision(tags_info: Iterable[dict]) -> Generator[dict, Any, None]:
    regex = re.compile(TASK_TAG_REGEXP)
    for tag_info in tags_info:
        if regex.match(tag_info["name"]):
            yield tag_info


def expand_versions(from_: str, to: str) -> list[str]:
    """Expand versions

    Example:

        from 0.2 to 0.2 => ["0.2"]
        from 0.2 to 0.3 => ["0.2", "0.3"]
        from 0.2 to 0.5 => ["0.2", "0.3", "0.4", "0.5"]

    This expansion only works with the version management based on the minor version.
    """
    from_version = parse_version(from_)
    to_version = parse_version(to)
    if from_version > to_version:
        raise ValueError(f"From version {from_} is greater than the to version {to}.")
    return [f"0.{minor}" for minor in range(int(from_version.minor), int(to_version.minor) + 1)]


def list_bundle_tags(bundle_upgrade: TaskBundleUpgrade) -> list[dict]:
    versions = expand_versions(bundle_upgrade.current_value, bundle_upgrade.new_value)
    tags: list[dict] = []
    c = Container(bundle_upgrade.dep_name)
    for version in versions:
        iter_tags = list_active_repo_tags(c, tag_name_pattern=f"{version}-")
        try:
            first_tag = next(iter_tags)
        except StopIteration:
            logger.info("No tag is queried from registry for version %s", version)
            continue
        tags.append(first_tag)
        tags.extend(iter_tags)
    return sorted(tags, key=operator.itemgetter("start_ts"), reverse=True)


def drop_out_of_order_versions(
    tags_info: Iterable[dict], bundle_upgrade: TaskBundleUpgrade
) -> tuple[list[dict], dict | None, dict | None, bool]:
    """Drop version tags that are out of order.

    Once a new version is bumped for a task, there should be no reason to attach
    migrations to the older version of the task. That means we can ignore "out of order"
    versions.

    For example, if we have these tags (ordered from newest to oldest by creation date):

        ["0.3-b", "0.2-b", "0.3-a", "0.1-b", "0.2-a", "0.1-a"]

    Then we only want to look at these:

        ["0.3-b", "0.3-a", "0.2-a", "0.1-a"]

    Because 0.2-b and 0.1-b are out of order - when they were created, a newer version
    tag already existed.

    :param tags_info: tags information responded by Quay.io listRepoTags endpoint.
        Each tag mapping must have a tag name pinned by version and revision, for
        example, ``0.2-<commit hash>``.
    :param bundle_upgrade: a ``TaskBundleUpgrade`` instance assisting on getting more information
        from the tags.
    :type bundle_upgrade: TaskBundleUpgrade
    :return: a 4-elements tuple. The first one is a list of tags cleaned up by dropping the
        out-of-order bundles. If input ``tags_info`` is empty, the result will be empty too. The
        second one references the current tag. The third one references the new tag. The last one
        indicates whether the current tag is out-of-order.
    """
    tags_that_follow_correct_version_order = []
    highest_version_so_far: Version | None = None
    is_out_of_order = False

    current_tag_info = None
    new_tag_info = None
    current_digest = bundle_upgrade.current_digest
    new_digest = bundle_upgrade.new_digest

    def _parse_version(tag_name: str) -> Version:
        return parse_version(tag_name.split("-")[0])

    for tag in reversed(list(tags_info)):
        if current_tag_info is None and tag["manifest_digest"] == current_digest:
            current_tag_info = tag
            if highest_version_so_far and _parse_version(tag["name"]) < highest_version_so_far:
                is_out_of_order = True
        elif new_tag_info is None and tag["manifest_digest"] == new_digest:
            new_tag_info = tag
        version = _parse_version(tag["name"])
        if highest_version_so_far is None or version >= highest_version_so_far:
            tags_that_follow_correct_version_order.append(tag)
            highest_version_so_far = version

    sort_key = operator.itemgetter("start_ts")
    tags_that_follow_correct_version_order.sort(key=sort_key, reverse=True)
    return tags_that_follow_correct_version_order, current_tag_info, new_tag_info, is_out_of_order


# TODO: cache this as well?
def determine_task_bundle_upgrades_range(
    task_bundle_upgrade: TaskBundleUpgrade,
) -> list[QuayTagInfo]:
    """Determine upgrade range for a given bundle upgrade

    The upgrade range is a collection of tags pointing from the new bundle to the previous one of
    current bundle. This method handles several senariors against the tag scheme:

    * This method aims to work well with the tag scheme pushed by build-definitions CI pipeline.
      The expected tag form is ``<version>-<commit hash>``.
    * Ideally, the bundles should be built linearly version by version. However, out-of-order
      bundles started to present in bundle repositories, for example, old version task is built
      because of deprecation.
    * Transitioning to decentralized build-definitions. Some tasks have been decentralized and
      already have new tag scheme in their image repositories. Part of the repositories have single
      tag scheme, whereas others mixes two.

      The pure new tag scheme looks like (from newest to oldest):

        3.0
        sha256-123456
        sha256-345678

      Similarly, the mixed tag schemes looks like:

        3.0
        sha256-123456
        sha256-345678
        0.2
        0.2-revision_1
        0.2-revision_2

    As of writing this docstring, upgrade range is still determined based on the original tag scheme
    made by build-definitions CI pipeline, and this method tries best to not fail when possibly
    encounter the new tag scheme. For detailed information of the result range, refer to the below
    description.

    IMPORTANT: current implementation is not intended as a solution for addressing the decentralized
    task bundles.

    :param task_bundle_upgrade: a ``TaskBundleUpgrade`` instance providing upgrade information for
        the determination.
    :type task_bundle_upgrade: TaskBundleUpgrade
    :return: a list of ``QuayTagInfo`` instances representing the upgrade range. The current bundle
        is not included in the result range. Empty list is returned if either tag pointing to the
        current bundle or the one point to the new bundle is not retrieved from registry. Once it
        happens, it could either mean the input upgrade data is invalid or the new tag scheme is
        encountered.
    :rtype: list[QuayTagInfo]
    """
    result = drop_out_of_order_versions(
        only_tags_pinned_by_version_revision(list_bundle_tags(task_bundle_upgrade)),
        task_bundle_upgrade,
    )
    tags_info, current_tag_info, new_tag_info, is_out_of_order = result

    current_bundle_ref: Final = task_bundle_upgrade.current_bundle
    new_bundle_ref: Final = task_bundle_upgrade.new_bundle

    if current_tag_info is None:
        logger.warning("Registry does not have current bundle %s", current_bundle_ref)
        return []

    if new_tag_info is None:
        logger.warning("Registry does not have new bundle %s", new_bundle_ref)
        return []

    current_pos = new_pos = -1
    current_digest = task_bundle_upgrade.current_digest
    new_digest = task_bundle_upgrade.new_digest
    for i, tag in enumerate(tags_info):
        this_digest = tag["manifest_digest"]
        if this_digest == new_digest:
            new_pos = i
        elif this_digest == current_digest:
            current_pos = i

    the_range: Iterable[dict]

    if is_out_of_order:
        # This current bundle has been filtered out previously
        logger.info(
            "Current bundle %s is newer than new bundle %s", current_bundle_ref, new_bundle_ref
        )
        current_version = current_tag_info["name"].split("-")[0]
        the_range = takewhile(lambda item: item["name"].split("-")[0] != current_version, tags_info)
    else:
        the_range = tags_info[new_pos:current_pos]
    return [QuayTagInfo.from_tag_info(item) for item in the_range]


class MigrationFileOperation(PipelineFileOperation):

    def __init__(self, task_bundle_upgrades: list[TaskBundleUpgrade]):
        self._task_bundle_upgrades = task_bundle_upgrades

    def _apply_migration(self, file_path: FilePath) -> None:
        """Apply migrations to a given pipeline file

        All migrations are attempted against the given pipeline file even if some error occured.

        :param file_path: file path to a pipeline file.
        :type file_path: FilePath
        :raises: ExceptionGroup[MigrationApplyError]. All errors captured during the process are
            raised as a group at once. Every raw exception is wrapped inside MigrationApplyError.
        """
        fd, migration_file = tempfile.mkstemp(suffix="-migration-file")
        prev_size = 0
        errors: list[Exception] = []

        for bundle_upgrade in self._task_bundle_upgrades:
            for migration in bundle_upgrade.migrations:
                try:
                    logger.info(
                        "Apply migration of task bundle %s in package file %s",
                        migration.task_bundle,
                        file_path,
                    )

                    os.lseek(fd, 0, 0)
                    content = migration.migration_script.encode("utf-8")
                    if len(content) < prev_size:
                        os.truncate(fd, len(content))
                    prev_size = os.write(fd, content)

                    cmd = ["bash", migration_file, file_path]
                    logger.debug("Run: %r", cmd)
                    proc = sp.run(cmd, stderr=sp.STDOUT, stdout=sp.PIPE)
                    logger.debug("%r", proc.stdout)
                    proc.check_returncode()
                except Exception as e:
                    err_msg = f"Failed to apply migration: {str(e)}"
                    logger.error(err_msg)
                    errors.append(
                        MigrationApplyError(err_msg, str(file_path), bundle_upgrade, migration, e)
                    )

        try:
            os.close(fd)
            os.unlink(migration_file)
        except Exception as e:
            logger.warning(
                "Unable to close and delete temporary migration script file %s: %s",
                migration_file,
                e,
            )

        if errors:
            raise ExceptionGroup("Apply migrations errors", errors)

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        yaml_style = style
        origin_checksum = file_checksum(file_path)
        self._apply_migration(file_path)
        if file_checksum(file_path) != origin_checksum:
            # By design, migration scripts invoke yq to apply changes to pipeline YAML and
            # the result YAML includes indented block sequences.
            # This load-dump round-trip ensures the original YAML formatting is preserved
            # as much as possible.
            pl_yaml = load_yaml(file_path, style=yaml_style)
            dump_yaml(file_path, pl_yaml, style=yaml_style)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        yaml_style = style
        original_pipeline_doc = loaded_doc

        fd, temp_pipeline_file = tempfile.mkstemp(suffix="-pipeline")
        os.close(fd)

        pipeline_spec = {"spec": original_pipeline_doc["spec"]["pipelineSpec"]}
        dump_yaml(temp_pipeline_file, pipeline_spec, style=yaml_style)
        origin_checksum = file_checksum(temp_pipeline_file)

        self._apply_migration(temp_pipeline_file)

        if file_checksum(temp_pipeline_file) != origin_checksum:
            modified_pipeline = load_yaml(temp_pipeline_file, style=yaml_style)
            original_pipeline_doc["spec"]["pipelineSpec"] = modified_pipeline["spec"]
            dump_yaml(file_path, original_pipeline_doc, style=yaml_style)


class TaskBundleUpgradesManager:

    def __init__(self, upgrades: list[dict[str, Any]], resolver_class: type["Resolver"]) -> None:
        # Deduplicated task bundle upgrades. Key is the full bundle image with tag and digest.
        self._task_bundle_upgrades: dict[str, TaskBundleUpgrade] = {}

        # Grouped task bundle upgrades by package file. Key is the package file path.
        # One package file may have the more than one task bundle upgrades, that reference the
        # objects in the ``_task_bundle_upgrades``.
        self._package_file_updates: dict[str, PackageFile] = {}

        self._resolver = resolver_class()

        self._collect(upgrades)

    @property
    def package_files(self) -> list[PackageFile]:
        return list(self._package_file_updates.values())

    def _collect(self, upgrades: list[dict[str, Any]]) -> None:
        for upgrade in upgrades:
            task_bundle_upgrade = TaskBundleUpgrade(
                dep_name=upgrade["depName"],
                current_value=upgrade["currentValue"],
                current_digest=upgrade["currentDigest"],
                new_value=upgrade["newValue"],
                new_digest=upgrade["newDigest"],
            )
            package_file = PackageFile(
                file_path=upgrade["packageFile"],
                parent_dir=upgrade["parentDir"],
            )

            tb_update = self._task_bundle_upgrades.get(task_bundle_upgrade.current_bundle)
            if tb_update is None:
                self._task_bundle_upgrades[task_bundle_upgrade.current_bundle] = task_bundle_upgrade
                tb_update = task_bundle_upgrade

            pf = self._package_file_updates.get(package_file.file_path)
            if pf is None:
                self._package_file_updates[package_file.file_path] = package_file
                pf = package_file
            pf.task_bundle_upgrades.append(tb_update)

    def resolve_migrations(self) -> None:
        """Resolve migrations for given task bundle upgrades"""
        self._resolver.resolve(list(self._task_bundle_upgrades.values()))

    def apply_migrations(self, skip_bundles: list[str]) -> None:
        """Apply migrations to package files

        Before calling this method, migrations must be resolved in advance.

        :param skip_bundles: Do not handle these given bundles, each of them is the bundle image
            repository. Refer to Renovate template field ``depName``. Empty list means no bundle is
            skipped.
        :type skip_bundles: list[str] or None
        :raises: ExceptionGroup
        """
        errors: list[Exception] = []
        for package_file in self.package_files:
            try:
                if not os.path.exists(package_file.file_path):
                    raise ValueError(f"Pipeline file does not exist: {package_file.file_path}")
                bundle_upgrades = [
                    u for u in package_file.task_bundle_upgrades if u.dep_name not in skip_bundles
                ]
                op = MigrationFileOperation(bundle_upgrades)
                op.handle(package_file.file_path)
            except Exception as e:
                errors.append(e)
        if errors:
            raise ExceptionGroup("Migration apply errors", errors)


class IncorrectMigrationAttachment(Exception):
    pass


def fetch_migration_file(image: str, digest: str) -> str | None:
    """Fetch migration file for a task bundle

    :param image: image name of a task bundle without tag or image.
    :type image: str
    :param digest: digest of the task bundle.
    :type digest: str
    :return: the migration file content. If migration file can't be found for the given task
        bundle, None is returned.
    """
    c = Container(image)
    if c.digest:
        raise ValueError("Image should not include digest.")
    c.digest = digest
    registry = Registry()

    # query and fetch migration file via referrers API
    image_index = ImageIndex(data=registry.list_referrers(c, "text/x-shellscript"))
    descriptors = [
        descriptor
        for descriptor in image_index.manifests
        if is_true(descriptor.annotations.get(ANNOTATION_IS_MIGRATION, "false"))
    ]
    if len(descriptors) > 1:
        msg = (
            f"{len(descriptors)} referrers containing migration script are listed. "
            "However, there should be one per task bundle."
        )
        logger.warning(msg)
        raise IncorrectMigrationAttachment(msg)
    if descriptors:
        c.digest = descriptors[0].digest
        manifest = registry.get_manifest(c)
        descriptor = manifest["layers"][0]
        return registry.get_artifact(c, descriptor["digest"])
    return None


def migrate(upgrades: list[dict[str, Any]], migration_resolver: type["Resolver"]) -> None:
    """The core method doing the migrations

    :param upgrades: upgrades data, that follows the schema of Renovate template field ``upgrades``.
    :type upgrades: list[dict[str, any]]
    """
    manager = TaskBundleUpgradesManager(upgrades, migration_resolver)
    errors: list[ExceptionGroup] = []

    try:
        manager.resolve_migrations()
    except ExceptionGroup as eg:
        errors.append(eg)

    skip_bundles: list[str] = []
    if errors and (sg := errors[0].subgroup(MigrationResolveError)) is not None:
        skip_bundles = [exc.bundle_upgrade.dep_name for exc in sg.exceptions]  # type: ignore

    logger.warning("Failed to resolve migrations for bundles: %r", skip_bundles)
    logger.warning("Do not attempt handling migrations for them.")

    try:
        manager.apply_migrations(skip_bundles=skip_bundles)
    except ExceptionGroup as eg:
        errors.append(eg)

    if errors:
        raise ExceptionGroup("migrate errors", errors)


class Resolver(ABC):
    """Base class for resolving migrations"""

    @abstractmethod
    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migraitons for a bundle upgrade

        :param bundle_upgrade: instance of TaskBundleUpgrade.
        :param upgrades_range: a list of Quay tag mappings from which to discover migrations.
        :type upgrades_range: list[QuayTagInfo]
        :return: a generator yielding migrations represented by a TaskBundleMigration instances.
        """
        raise NotImplementedError("Must be implemented in a subclass.")

    def _resolve_task(self, bundle_upgrade: TaskBundleUpgrade) -> None:
        """Task to resolve migrations for a specific bundle upgrade

        :param bundle_upgrade: an instance of TaskBundleUpgrade.
        :type bundle_upgrade: TaskBundleUpgrade
        """
        upgrades_range = determine_task_bundle_upgrades_range(bundle_upgrade)
        for tb_migration in self._resolve_migrations(bundle_upgrade, upgrades_range):
            bundle_upgrade.migrations.append(tb_migration)
        # Quay.io lists tags from the newest to the oldest one.
        # Migrations must be applied in the reverse order.
        bundle_upgrade.migrations.reverse()

    def resolve(self, tb_upgrades: list[TaskBundleUpgrade]) -> None:
        """Resolve migrations for given task bundles upgrades

        Depending on the implementation of ``_resolve_migrations`` in subclasses, migrations are
        resolved from remote, i.e. Quay.io, and put into the ``TaskBundleUpgrade.migrations`` in
        place. This method ensures the migrations are in order from oldest to newest.

        :raises: ExceptionGroup[MigrationResolveError]. Any error happening during resolving
            migration for a specific upgrade is captured. Then, all such errors are grouped
            into an ``ExceptionGroup`` instance.
        """
        errors: list[Exception] = []

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self._resolve_task, tb_upgrade): tb_upgrade
                for tb_upgrade in tb_upgrades
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    bundle_upgrade = futures[future]
                    err_msg = (
                        "Error occurs when resolving migration for upgrade "
                        f"from {bundle_upgrade.current_bundle} to {bundle_upgrade.new_bundle}"
                    )
                    logger.error(err_msg)
                    errors.append(MigrationResolveError(f"{err_msg}: {str(e)}", bundle_upgrade, e))

        if errors:
            raise ExceptionGroup("Migration resolve errors", errors)


class SimpleIterationResolver(Resolver):
    """Legacy resolution by checking individual task bundle within an upgrade"""

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migration of individual task bundle one-by-one through the upgrade range"""
        dep_name = bundle_upgrade.dep_name
        for tag_info in upgrades_range:
            c = Container(f"{dep_name}:{tag_info.name}@{tag_info.manifest_digest}")
            uri_with_tag = c.uri_with_tag

            manifest_json = Registry().get_manifest(c)
            if not is_true(
                manifest_json.get("annotations", {}).get(ANNOTATION_HAS_MIGRATION, "false")
            ):
                continue

            script_content = fetch_migration_file(dep_name, tag_info.manifest_digest)
            if script_content:
                logger.info("Task bundle %s has migration.", uri_with_tag)
                yield TaskBundleMigration(task_bundle=uri_with_tag, migration_script=script_content)
            else:
                logger.info("Task bundle %s does not have migration.", uri_with_tag)


class LinkedMigrationsResolver(Resolver):
    """Resolve linked migrations via bundle image annotation"""

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migrations by links represented by annotation"""
        dep_name = bundle_upgrade.dep_name

        if not upgrades_range:
            logger.info("Upgrade range is empty for %s. Skip resolving migrations.", dep_name)
            return

        manifest_digests = [tag.manifest_digest for tag in upgrades_range]
        i = 0
        while True:
            tag_info = upgrades_range[i]
            c = Container(f"{dep_name}:{tag_info.name}@{tag_info.manifest_digest}")
            uri_with_tag = c.uri_with_tag

            manifest_json = Registry().get_manifest(c)
            has_migration = manifest_json.get("annotations", {}).get(
                ANNOTATION_HAS_MIGRATION, "false"
            )

            if is_true(has_migration):
                script_content = fetch_migration_file(dep_name, tag_info.manifest_digest)
                if script_content:
                    logger.info("Task bundle %s has migration.", uri_with_tag)
                    yield TaskBundleMigration(
                        task_bundle=uri_with_tag, migration_script=script_content
                    )
                else:
                    logger.info("Task bundle %s does not have migration.", uri_with_tag)

            digest = manifest_json.get("annotations", {}).get(
                ANNOTATION_PREVIOUS_MIGRATION_BUNDLE, ""
            )
            if digest:
                try:
                    i = manifest_digests.index(digest)
                except ValueError:
                    logger.info(
                        "Migration search stops at %s. It points to a previous migration bundle %s "
                        "that is before the current upgrade.",
                        c.uri_with_tag,
                        digest,
                    )
                    break
            else:
                logger.info("Migration search stops at %s", c.uri_with_tag)
                break


@dataclass
class MigrationImageTag:
    prefix: str
    version: str
    file_checksum: str
    timestamp: str

    @classmethod
    def parse(cls, tag: str) -> "MigrationImageTag | None":
        match = MIGRATION_IMAGE_TAG_REGEX.match(tag)
        if not match:
            return None
        groups = match.groupdict()
        return MigrationImageTag(
            prefix=groups["prefix"],
            version=groups["version"],
            file_checksum=groups["checksum"],
            timestamp=groups["timestamp"],
        )


MIGRATION_IMAGE_TAG_REGEX: Final = re.compile(
    "^"
    r"(?P<prefix>migration)-"
    r"(?P<version>\d+\.\d+(\.\d+)?)-"
    r"(?P<checksum>[0-9a-f]{64})-"
    r"(?P<timestamp>\d+)"
    "$"
)

MIGRATION_IMAGE_TAG_LIKE_PATTERN: Final = r"migration-%.%-%-%"


class MigrationImagesResolver(Resolver):

    def _resolve_migrations(
        self,
        bundle_upgrade: TaskBundleUpgrade,
        upgrades_range: list[QuayTagInfo],
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migrations for a bundle upgrade

        Migrations are pushed to the registry as OCI images tagged in a specific form:

            migration-<actual task version>-<file sha256sum>-<timestamp>

        * `migration-`: is a fixed prefix.
        * `actual task version`: is the task version set in label ``app.kubernetes.io/version``.
          They are treated as semantic versions.
        * `file sha256sum`: is the checksum calculated in SHA256 algorithm.
        * `timestamp`: is the time when a migration is pushed.

        Every bundle has to be tagged with the actual task version. When Renovate sends an update
        pull request, ``(current_value, new_value]`` will represent a straightforward upgrade range,
        for example (0.2.1, 0.3.4], that are used to filter migrations.

        :param bundle_upgrade: Refer to :meth:`Resolver._resolve_migrations`.
        :param upgrades_range: Useless for this resolver. Any value passed-in is ignored.
        :return: a generator yielding found migration represented by a TaskBundleMigration instance.
            Yielded migrations are ensured to be in the correct order from oldest to newest version.
        :raises ValueError: It is not allowed to modify an existing migration. The tekton bundle
            build pipeline ensures such modification does not happen. But, in case a modified
            migration is present in the image repository accidentally, resolver will stop proceeding
            immediately.
        :raises ValueError: If a migration image includes more than one layers (files), ValueError
            will be raised.
        """
        old_version = parse_version(bundle_upgrade.current_value)
        new_version = parse_version(bundle_upgrade.new_value)
        if old_version == new_version:
            # By tagging bundles with actual task version, e.g. 0.2, 0.2.1, 0.3.2, before bumping
            # the actual task version, bundles within current version point to the migration that
            # has been applied previously.
            # So, in this in-version bundle update, there is no new migration to apply.
            return
        image_repo = bundle_upgrade.dep_name
        migrations: list[tuple[str, TaskBundleMigration]] = []
        c = Container(image_repo)
        tags = list_active_repo_tags(c, tag_name_pattern=MIGRATION_IMAGE_TAG_LIKE_PATTERN)
        version_checksum_pairs: dict[str, str] = {}
        for tag in tags:
            tag_name = tag["name"]
            migration_image_tag = MigrationImageTag.parse(tag_name)

            if migration_image_tag is None:
                logger.debug(
                    "Tag %s is not a migration image tag. Continue handling next one.", tag
                )
                continue

            actual_task_version = migration_image_tag.version
            seen_version = actual_task_version in version_checksum_pairs
            if seen_version:
                seen_file_checksum = version_checksum_pairs[actual_task_version]
                if migration_image_tag.file_checksum == seen_file_checksum:
                    continue
                else:
                    raise ValueError(
                        f"Migration of task version {actual_task_version} is modified."
                    )
            else:
                version_checksum_pairs[actual_task_version] = migration_image_tag.file_checksum

            if old_version < parse_version(actual_task_version) <= new_version:
                migration_script = self._fetch_migration_script(f"{image_repo}:{tag_name}")
                migrations.append(
                    (
                        actual_task_version,
                        TaskBundleMigration(
                            task_bundle=f"{image_repo}:{actual_task_version}",
                            migration_script=migration_script,
                        ),
                    )
                )

        migrations.sort(key=itemgetter(0))
        for _, tb_migration in migrations:
            yield tb_migration

    def _fetch_migration_script(self, image: str) -> str:
        """Fetch migration from registry

        :param image: migration image reference.
        :type image: str
        :return: the migration script content.
        """
        with tempfile.TemporaryDirectory(suffix="-migration") as tmp_dir:
            files = Registry().pull(image, outdir=tmp_dir)
            if len(files) > 1:
                files_str = ", ".join(os.path.basename(file_path) for file_path in files)
                raise ValueError(f"Migration image {image} has multiple files: {files_str}.")
            with open(files[0], "r") as f:
                return f.read()

    def _resolve_task(self, bundle_upgrade: TaskBundleUpgrade) -> None:
        for tb_migration in self._resolve_migrations(bundle_upgrade, []):
            print("-->", tb_migration)
            bundle_upgrade.migrations.append(tb_migration)


def comes_from_konflux(image_repo: str) -> bool:
    if os.environ.get("PMT_LOCAL_TEST"):
        logger.warning(
            "Environment variable PMT_LOCAL_TEST is set. Migration tool works with images "
            "from arbitrary registry organization."
        )
        return True
    return image_repo.startswith("quay.io/konflux-ci/")


def clean_upgrades(input_upgrades: str) -> list[dict[str, Any]]:
    """Clean input Renovate upgrades string

    Only images from konflux-ci image organization are returned. If
    PMT_LOCAL_TEST environment variable is set, this check is skipped and images
    from arbitrary image organizations are returned.

    Only return images handled by Renovate tekton manager.

    :param input_upgrades: a JSON string containing Renovate upgrades data.
    :type input_upgrades: str
    :return: a list of valid upgrade mappings.
    :raises InvalidRenovateUpgradesData: if the input upgrades data is not a
        JSON data and cannot be decoded. If the loaded upgrades data cannot be
        validated by defined schema, also raise this error.
    """
    cleaned_upgrades: list[dict[str, Any]] = []

    try:
        upgrades = json.loads(input_upgrades)
    except json.decoder.JSONDecodeError as e:
        logger.error("Input upgrades is not a valid encoded JSON string: %s", e)
        logger.error(
            "Argument --renovate-upgrades accepts a list of mappings which is a subset of Renovate "
            "template field upgrades. See https://docs.renovatebot.com/templates/"
        )
        raise InvalidRenovateUpgradesData("Input upgrades is not a valid encoded JSON string.")

    if not isinstance(upgrades, list):
        raise InvalidRenovateUpgradesData(
            "Input upgrades is not a list containing Renovate upgrade mappings."
        )

    validator = Draft202012Validator(SCHEMA_UPGRADE)

    for upgrade in upgrades:
        if not upgrade:
            continue  # silently ignore any falsy objects

        dep_name = upgrade.get("depName")

        if not dep_name:
            raise InvalidRenovateUpgradesData("Upgrade does not have value of field depName.")

        if not comes_from_konflux(dep_name):
            logger.info("Dependency %s does not come from Konflux task definitions.", dep_name)
            continue

        try:
            validator.validate(upgrade)
        except ValidationError as e:
            if e.path:  # path could be empty due to missing required properties
                field = e.path[0]
            else:
                field = ""

            logger.error("Input upgrades data does not pass schema validation: %s", e)

            if e.validator == "minLength":
                err_msg = f"Property {field} is empty: {e.message}"
            else:
                err_msg = f"Invalid upgrades data: {e.message}, path '{e.json_path}'"
            raise InvalidRenovateUpgradesData(err_msg)

        if "tekton-bundle" not in upgrade["depTypes"]:
            logger.debug("Dependency %s is not handled by tekton-bundle manager.", dep_name)
            continue

        cleaned_upgrades.append(upgrade)

    return cleaned_upgrades


def arg_type_upgrades_file(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"Upgrades file {value} does not exist.")
    return p


def register_cli(subparser) -> None:
    migrate_parser = subparser.add_parser(
        "migrate", help="Discover and apply migrations for given task bundles upgrades."
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
    migrate_parser.add_argument(
        "-l",
        "--use-legacy-resolver",
        action="store_true",
        help="Use legacy resolver to fetch migrations.",
    )
    migrate_parser.set_defaults(action=action)


def action(args) -> None:
    resolver_class: type[Resolver]

    if args.use_legacy_resolver:
        resolver_class = SimpleIterationResolver
    else:
        resolver_class = LinkedMigrationsResolver

    if args.upgrades_file:
        upgrades_data = args.upgrades_file.read_text().strip()
    else:
        upgrades_data = args.renovate_upgrades

    if upgrades_data:
        upgrades = clean_upgrades(upgrades_data)
        if upgrades:
            migrate(upgrades, resolver_class)
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
