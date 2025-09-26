from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import requests

from pipeline_migration.registry import Container


@dataclass
class QuayTagInfo:
    name: str
    manifest_digest: str
    start_ts: int

    @classmethod
    def from_tag_info(cls, tag_info: dict) -> "QuayTagInfo":
        return cls(
            name=tag_info["name"],
            manifest_digest=tag_info["manifest_digest"],
            start_ts=tag_info["start_ts"],
        )


def list_active_repo_tags(
    c: Container, tag_name: str = "", tag_name_pattern: str = "", limit: int = 0
) -> Generator[dict, Any, None]:
    """List repository tags

    Make GET HTTP request to Quay API ``listRepoTags``.

    :param c: container object.
    :type c: Container
    """
    page = 1
    while True:
        params = {"page": str(page), "onlyActiveTags": "true"}
        if tag_name:
            params["specificTag"] = tag_name
        if tag_name_pattern:
            params["filter_tag_name"] = f"like:{tag_name_pattern}"
        if limit > 0:
            params["limit"] = str(limit)
        api_url = f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/"
        resp = requests.get(api_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        for tag in data["tags"]:
            yield tag
        if not data.get("has_additional"):
            break
        page = int(data["page"]) + 1


def get_active_tag(c: Container, name: str) -> dict | None:
    try:
        return next(list_active_repo_tags(c, tag_name=name))
    except StopIteration:
        return None
