import json
import logging
import os.path
import re
import subprocess as sp
import tempfile
from collections.abc import Iterable
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import Any

from jsonschema.exceptions import ValidationError
from jsonschema.validators import Draft202012Validator

from pipeline_migration.actions.migrate.constants import (
    ANNOTATION_IS_MIGRATION,
    MIGRATION_IMAGE_TAG_LIKE_PATTERN,
    SCHEMA_UPGRADE,
    logger,
)
from pipeline_migration.actions.migrate.exceptions import (
    IncorrectMigrationAttachment,
    InvalidRenovateUpgradesData,
    MigrationApplyError,
    MigrationResolveError,
)
from pipeline_migration.actions.migrate.models import PackageFile, TaskBundleUpgrade
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.actions.migrate.resolvers.migration_images import MigrationImageTag

from pipeline_migration.quay import list_active_repo_tags
from pipeline_migration.registry import Container, ImageIndex, Registry
from pipeline_migration.pipeline import PipelineFileOperation
from pipeline_migration.types import FilePath
from pipeline_migration.utils import file_checksum, is_true, load_yaml, dump_yaml, YAMLStyle


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

        pipeline_spec = {"kind": "Pipeline", "spec": original_pipeline_doc["spec"]["pipelineSpec"]}
        dump_yaml(temp_pipeline_file, pipeline_spec, style=yaml_style)
        origin_checksum = file_checksum(temp_pipeline_file)

        self._apply_migration(temp_pipeline_file)

        if file_checksum(temp_pipeline_file) != origin_checksum:
            modified_pipeline = load_yaml(temp_pipeline_file, style=yaml_style)
            original_pipeline_doc["spec"]["pipelineSpec"] = modified_pipeline["spec"]
            dump_yaml(file_path, original_pipeline_doc, style=yaml_style)


class TransitionToModifyCommandOperation(MigrationFileOperation):

    def __init__(self, task_bundle_upgrades: list[TaskBundleUpgrade]):
        super().__init__(task_bundle_upgrades)
        self.logger = logging.getLogger("migrate.transition-to-modify")
        self._transition_is_done = self._all_migrations_utilize_modify_cmd()
        if self._transition_is_done:
            self.logger.info("All migration scripts are using pmt-modify command.")
        else:
            self.logger.info(
                "Not all migration scripts are using pmt-modify command. "
                "Using legacy way to handle pipeline file for applying migrations."
            )

    def _all_migrations_utilize_modify_cmd(self) -> bool:
        return all(
            migration.is_pmt_modify_used
            for bundle_upgrade in self._task_bundle_upgrades
            for migration in bundle_upgrade.migrations
        )

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        if self._transition_is_done:
            self._apply_migration(file_path)
        else:
            super().handle_pipeline_file(file_path, loaded_doc, style)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        if self._transition_is_done:
            self._apply_migration(file_path)
        else:
            super().handle_pipeline_run_file(file_path, loaded_doc, style)


class TaskBundleUpgradesManager:

    def __init__(self, upgrades: list[dict[str, Any]], resolver_class: type["Resolver"]) -> None:
        # Deduplicated task bundle upgrades. Key is the full bundle image with tag and digest.
        self._task_bundle_upgrades: dict[str, TaskBundleUpgrade] = {}
        # Grouped task bundle upgrades by package file
        self._package_files: list[PackageFile] = []
        self._resolver = resolver_class()
        self._collect(upgrades)

    @property
    def package_files(self) -> list[PackageFile]:
        return self._package_files

    @staticmethod
    def collect_upgrades(upgrades: list[dict[str, Any]]) -> Iterable[PackageFile]:
        """Collect task bundle upgrades grouped by package file"""
        sorted_upgrades = sorted(upgrades, key=itemgetter("packageFile"))
        grouped_upgrades = groupby(sorted_upgrades, key=itemgetter("packageFile"))
        for package_file, grouped_items in grouped_upgrades:
            package_file = PackageFile(file_path=package_file, parent_dir="")
            for upgrade in grouped_items:
                package_file.parent_dir = upgrade["parentDir"]
                bundle_upgrade = TaskBundleUpgrade(
                    dep_name=upgrade["depName"],
                    current_value=upgrade["currentValue"],
                    current_digest=upgrade["currentDigest"],
                    new_value=upgrade["newValue"],
                    new_digest=upgrade["newDigest"],
                )
                package_file.task_bundle_upgrades.append(bundle_upgrade)
            yield package_file

    def _collect(self, upgrades: list[dict[str, Any]]) -> None:
        for package_file in self.collect_upgrades(upgrades):
            self._package_files.append(package_file)
            for bundle_upgrade in package_file.task_bundle_upgrades:
                if bundle_upgrade.current_bundle not in self._task_bundle_upgrades:
                    self._task_bundle_upgrades[bundle_upgrade.current_bundle] = bundle_upgrade

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
                op = TransitionToModifyCommandOperation(bundle_upgrades)
                op.handle(package_file.file_path)
            except Exception as e:
                errors.append(e)
        if errors:
            raise ExceptionGroup("Migration apply errors", errors)


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

        if "tekton-bundle" not in upgrade.get("depTypes", []):
            logger.debug("Dependency %s is not handled by tekton-bundle manager.", dep_name)
            continue

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

        cleaned_upgrades.append(upgrade)

    return cleaned_upgrades


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


def has_migration_image(image_repo: str) -> bool:
    """Guess if an image repository has migration images

    During the transition to decentralized task repositories, not all tasks are built by the tekton
    bundle builder pipeline and released via release pipeline. This method guesses whether a
    repository has at least one migration image tag.

    Tags are queried by a pattern that has fixed prefix ``migration-``. Although Quay does SQL LIKE
    to match tag names, it should be good enough to get existing migration image tags if there is.

    :param image_repo: bundle repository.
    :type image_repo: str
    :return: True if migration image tags are retrieved from the given repository. Otherwise, False
        is returned.
    """
    c = Container(image_repo)
    tags_count = 10
    tags_iter = list_active_repo_tags(
        c, tag_name_pattern=MIGRATION_IMAGE_TAG_LIKE_PATTERN, per_page=tags_count
    )
    results: list[bool] = []
    for i in range(tags_count):
        try:
            tag_name = next(tags_iter)["name"]
            results.append(MigrationImageTag.parse(tag_name) is not None)
        except StopIteration:
            break
    return any(results)


def update_bundles_in_pipelines(upgrades: list[dict[str, Any]]) -> None:
    package_files = TaskBundleUpgradesManager.collect_upgrades(upgrades)
    for package_file in package_files:
        pipeline_file = Path(package_file.file_path)
        content = pipeline_file.read_text()
        for upgrade in package_file.task_bundle_upgrades:
            current_bundle = upgrade.current_bundle
            new_bundle = upgrade.new_bundle
            regex = rf"(\n +value: ){current_bundle}"
            content = re.sub(regex, rf"\1{new_bundle}", content)
        pipeline_file.write_text(content)
