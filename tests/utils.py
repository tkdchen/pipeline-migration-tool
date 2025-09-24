import random
import string
import time
from itertools import count
from pathlib import Path


class RepoPath(Path):
    @property
    def tekton_dir(self) -> Path:
        return self / ".tekton"


def select_random_chars(n=64):
    return (random.choice(string.hexdigits) for _ in range(n))


def generate_digest() -> str:
    random_choices = select_random_chars()
    return "sha256:" + "".join(random_choices).lower()


def generate_git_sha() -> str:
    return "".join(select_random_chars(40)).lower()


def generate_sha256sum() -> str:
    random_choices = select_random_chars()
    return "".join(random_choices).lower()


def generate_timestamp():
    counter = count()

    def _inner() -> int:
        return int(time.time()) + next(counter)

    return _inner
