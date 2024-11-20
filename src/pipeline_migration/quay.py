from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import requests

from pipeline_migration.registry import Container


@dataclass
class QuayTagInfo:
    name: str
    manifest_digest: str


def list_active_repo_tags(c: Container) -> Generator[dict, Any, None]:
    """List repository tags

    Make GET HTTP request to Quay API ``listRepoTags``.

    :param c: container object.
    :type c: Container
    """
    page = 1
    while True:
        params = {"page": str(page), "onlyActiveTags": "true"}
        api_url = f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/"
        resp = requests.get(api_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        for tag in data["tags"]:
            yield tag
        if not data.get("has_additional"):
            break
        page = int(data["page"]) + 1
