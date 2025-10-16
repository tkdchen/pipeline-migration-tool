from dataclasses import dataclass
import os
import tempfile
from collections.abc import Generator
from operator import itemgetter
from typing import Any

from packaging.version import parse as parse_version, Version

from pipeline_migration.actions.migrate.constants import (
    MIGRATION_IMAGE_TAG_LIKE_PATTERN,
    MIGRATION_IMAGE_TAG_REGEX,
    logger,
)
from pipeline_migration.actions.migrate.models import (
    TaskBundleMigration,
    TaskBundleUpgrade,
)
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.quay import QuayTagInfo, list_active_repo_tags
from pipeline_migration.registry import Container, Registry


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
        migrations: list[tuple[Version, TaskBundleMigration]] = []
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
                        parse_version(actual_task_version),
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
            bundle_upgrade.migrations.append(tb_migration)
