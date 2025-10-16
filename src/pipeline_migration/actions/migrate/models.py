from dataclasses import dataclass, field


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
