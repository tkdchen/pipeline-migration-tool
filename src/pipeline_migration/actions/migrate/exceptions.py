from pipeline_migration.actions.migrate.models import TaskBundleMigration, TaskBundleUpgrade


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


class IncorrectMigrationAttachment(Exception):
    pass
