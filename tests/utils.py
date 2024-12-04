import random
import string


def select_random_chars(n=64):
    return (random.choice(string.hexdigits) for _ in range(n))


def generate_digest() -> str:
    random_choices = select_random_chars()
    return "sha256:" + "".join(random_choices).lower()


def generate_git_sha() -> str:
    return "".join(select_random_chars(40)).lower()
