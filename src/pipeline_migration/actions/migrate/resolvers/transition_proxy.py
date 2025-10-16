import logging
from collections.abc import Generator
from typing import Any

from pipeline_migration.actions.migrate.main import has_migration_image
from pipeline_migration.actions.migrate.models import (
    TaskBundleMigration,
    TaskBundleUpgrade,
)
from .linked_migrations import LinkedMigrationsResolver
from .migration_images import MigrationImagesResolver
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.quay import QuayTagInfo


class DecentralizationTransitionResolverProxy(Resolver):

    def __init__(self):
        self.logger = logging.getLogger("migrate.resolver-proxy")
        self._lmr = LinkedMigrationsResolver()
        self._mir = MigrationImagesResolver()

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """This method is useless for proxy"""
        yield TaskBundleMigration("", "")  # yield empty instance to fulfill linters

    def _resolve_task(self, bundle_upgrade: TaskBundleUpgrade) -> None:
        if has_migration_image(bundle_upgrade.dep_name):
            resolve_method = self._mir.resolve_single_upgrade
        else:
            resolve_method = self._lmr.resolve_single_upgrade
        self.logger.debug(
            "Migration image is found from repository %s, then use %s to resolve migrations.",
            bundle_upgrade.dep_name,
            resolve_method.__self__.__class__.__name__,
        )
        resolve_method(bundle_upgrade)
