from typing import Any
from ruamel.yaml import YAML

from pipeline_migration.types import FilePath


def create_yaml_obj():
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 8192
    return yaml


def load_yaml(yaml_file: FilePath) -> Any:
    with open(yaml_file, "r", encoding="utf-8") as f:
        return create_yaml_obj().load(f)


def dump_yaml(yaml_file: FilePath, data: Any):
    with open(yaml_file, "w", encoding="utf-8") as f:
        create_yaml_obj().dump(data, f)
