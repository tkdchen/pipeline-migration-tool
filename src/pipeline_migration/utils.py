import hashlib
from dataclasses import dataclass, field
from typing import Any

from pipeline_migration.types import FilePath

from ruamel.yaml import YAML, CommentedMap, CommentedSeq
from ruamel.yaml.comments import CommentedBase


__all__ = [
    "BlockSequenceIndentation",
    "create_yaml_obj",
    "dump_yaml",
    "is_true",
    "load_yaml",
    "YAMLStyle",
]


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


@dataclass
class BlockSequenceIndentation:
    indentations: dict[int, int] = field(default_factory=dict)

    @property
    def is_consistent(self) -> bool:
        return len(self.levels) == 1

    @property
    def levels(self) -> list[int]:
        return list(self.indentations.keys())


@dataclass
class _NodePath:
    node: CommentedBase
    field: str = ""


@dataclass
class YAMLStyle:
    indentation: BlockSequenceIndentation

    preserve_quotes: bool = True
    width: int = 8192

    @classmethod
    def _detect_block_sequence_indentation(cls, node: CommentedBase) -> BlockSequenceIndentation:

        parent_nodes: list[_NodePath] = []
        block_seq_indentations = BlockSequenceIndentation()
        indentations = block_seq_indentations.indentations

        def _walk(node: CommentedBase) -> None:
            if isinstance(node, CommentedMap):
                parent_nodes.append(_NodePath(node=node))
                for key, value in node.items():
                    parent_nodes[-1].field = key
                    _walk(value)
                parent_nodes.pop()
            elif isinstance(node, CommentedSeq):
                levels = node.lc.col - parent_nodes[-1].node.lc.col
                if levels in indentations:
                    indentations[levels] += 1
                else:
                    indentations[levels] = 1
                for item in node:
                    _walk(item)

        _walk(node)

        return block_seq_indentations

    @classmethod
    def detect(cls, file_path: FilePath) -> "YAMLStyle":
        doc = load_yaml(file_path)
        indentation = cls._detect_block_sequence_indentation(doc)
        return cls(indentation=indentation)


def create_yaml_obj(style: YAMLStyle | None = None):
    yaml = YAML()
    if style is None:
        return yaml

    yaml.preserve_quotes = style.preserve_quotes
    yaml.width = style.width

    offset = 0
    if style.indentation.is_consistent:
        offset = style.indentation.levels[0]
    sequence = offset + 2
    yaml.indent(sequence=sequence, offset=offset)

    return yaml


def load_yaml(yaml_file: FilePath) -> Any:
    with open(yaml_file, "r", encoding="utf-8") as f:
        return create_yaml_obj().load(f)


def dump_yaml(yaml_file: FilePath, data: Any, style: YAMLStyle | None = None) -> None:
    with open(yaml_file, "w", encoding="utf-8") as f:
        create_yaml_obj(style).dump(data, f)


def file_checksum(file_path: FilePath) -> str:
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
