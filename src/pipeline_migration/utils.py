import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import subprocess

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
    "git_add",
]

logger = logging.getLogger("utils")


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

    def indent(self, level: int) -> None:
        """Set indentation for block sequence consistently through whole YAML doc"""
        self.indentations = {level: 1}


@dataclass
class _NodePath:
    node: CommentedBase
    field: str = ""


def is_flow_style_seq(node: CommentedSeq) -> bool:
    return len(node) == 0 or node.fa.flow_style()


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
            elif isinstance(node, CommentedSeq) and not is_flow_style_seq(node):
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


def create_yaml_obj(style: YAMLStyle | None = None) -> YAML:
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


def load_yaml(yaml_file: FilePath, style: YAMLStyle | None = None) -> Any:
    with open(yaml_file, "r", encoding="utf-8") as f:
        return create_yaml_obj(style).load(f)


def dump_yaml(yaml_file: FilePath, data: Any, style: YAMLStyle | None = None) -> None:
    with open(yaml_file, "w", encoding="utf-8") as f:
        create_yaml_obj(style).dump(data, f)


def file_checksum(file_path: FilePath) -> str:
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_add(file_path: FilePath) -> None:
    """Git add given file

    The git-add command may fail due to any reason, e.g. git command is not available in the system,
    in which case just logging a message and terminate quietly.

    :param file_path: an absolute path to a file.
    :type file_path: FilePath
    :raises ValueError: if given file path is not an absolute path.
    """
    fp = Path(file_path)
    if not fp.is_absolute():
        raise ValueError(f"File path {file_path} is not an absolute path.")
    cmd = ["git", "add", fp.name]
    try:
        subprocess.run(cmd, cwd=fp.parent, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.warning("%s is not added to git index: %s", file_path, e.stderr)
