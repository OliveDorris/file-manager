from __future__ import annotations

import sqlite3
from typing import Any

from repositories.category_repository import (
    create_category,
    get_category,
    get_category_depth,
)


CATEGORY_NOT_EMPTY_MESSAGE = "文件夹中有文件，请清空后再删除文件夹。"
MAX_CATEGORY_DEPTH = 3


def create_category_with_parent(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    parent_id: int | None,
    created_at: str,
) -> int:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("文件夹名称不能为空")
    if len(cleaned_name) > 50:
        raise ValueError("文件夹名称不能超过 50 个字符")

    if parent_id is not None:
        parent = get_category(conn, parent_id)
        if not parent:
            raise ValueError("父文件夹不存在")
        parent_depth = get_category_depth(conn, parent_id)
        if parent_depth is None:
            raise ValueError("父文件夹不存在")
        if parent_depth >= MAX_CATEGORY_DEPTH:
            raise ValueError("最多支持三级文件夹")

    try:
        return create_category(conn, cleaned_name, description.strip(), parent_id, created_at)
    except sqlite3.IntegrityError as exc:
        raise ValueError("文件夹名称已存在") from exc


def validate_category_can_delete(document_count: int, child_count: int = 0) -> None:
    if document_count > 0 or child_count > 0:
        raise ValueError(CATEGORY_NOT_EMPTY_MESSAGE)


def build_category_tree(
    rows: list[sqlite3.Row],
    active_category_id: int | None = None,
) -> list[dict[str, Any]]:
    nodes = {
        int(row["id"]): {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
            "depth": 1,
            "children": [],
            "is_expanded": False,
        }
        for row in rows
    }
    roots: list[dict[str, Any]] = []

    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        if parent:
            parent["children"].append(node)
        else:
            roots.append(node)

    def assign_depth_and_sort(node: dict[str, Any], depth: int) -> None:
        node["depth"] = min(depth, MAX_CATEGORY_DEPTH)
        node["children"].sort(key=lambda item: (item["name"].casefold(), item["id"]))
        for child in node["children"]:
            assign_depth_and_sort(child, depth + 1)

    roots.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    for root in roots:
        assign_depth_and_sort(root, 1)

    current_id = active_category_id
    visited: set[int] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = nodes.get(current_id)
        if not current:
            break
        current["is_expanded"] = True
        current_id = current["parent_id"]

    return roots


def flatten_category_tree(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def visit(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            item = {key: value for key, value in node.items() if key != "children"}
            item["display_name"] = f"{'— ' * (int(node['depth']) - 1)}{node['name']}"
            flattened.append(item)
            visit(node["children"])

    visit(tree)
    return flattened
