"""Path-level JSON payload contributor audit.

Read-only helper for proving which nested paths make hot/report payloads large.
"""

from __future__ import annotations

import json
from typing import Any


def json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def largest_json_paths(value: Any, *, root: str = "payload", limit: int = 10, min_bytes: int = 1024) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        size = json_size(node)
        if size >= min_bytes:
            rows.append({"path": path, "bytes": size})
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node[:50]):
                walk(child, f"{path}[{index}]")
            if len(node) > 50:
                rows.append({"path": f"{path}[50:]", "bytes": json_size(node[50:])})

    walk(value, root)
    return sorted(rows, key=lambda item: item["bytes"], reverse=True)[: max(1, int(limit or 10))]
