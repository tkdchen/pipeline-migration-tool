import random
import string


def generate_digest() -> str:
    random_choices = (random.choice(string.hexdigits) for _ in range(64))
    return "sha256:" + "".join(random_choices).lower()
