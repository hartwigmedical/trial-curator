#!/usr/bin/env python3
"""Convert the OncoTree CSV resource to a nested YAML tree."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = REPO_ROOT / "data/eligibility_path/resources/oncotree/oncotree.csv"
DEFAULT_OUTPUT = REPO_ROOT / "data/eligibility_path/resources/oncotree/oncotree.yaml"
LEVEL_COLUMNS = [f"level_{idx}" for idx in range(1, 8)]
CODE_RE = re.compile(r"\(([^()]+)\)\s*$")


class IndentedSafeDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def parse_name_code(value: str) -> tuple[str, str]:
    match = CODE_RE.search(value)
    if match is None:
        raise ValueError(f"OncoTree level value is missing trailing '(CODE)': {value!r}")

    code = match.group(1).strip()
    name = value[: match.start()].strip()
    if not code or not name:
        raise ValueError(f"OncoTree level value has an empty name or code: {value!r}")
    return name, code


def make_node(name: str, code: str, level: int) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "level": level,
        "children": [],
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {path}")
        missing = [column for column in LEVEL_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV is missing expected columns: {missing}")
        return [{str(key).strip(): value for key, value in row.items() if key is not None} for row in reader]


def build_tree(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    nodes_by_code: dict[str, dict[str, Any]] = {}
    parent_by_code: dict[str, str | None] = {}

    for row_number, row in enumerate(rows, start=2):
        path: list[dict[str, Any]] = []

        for level, column in enumerate(LEVEL_COLUMNS, start=1):
            value = (row.get(column) or "").strip()
            if not value:
                continue

            name, code = parse_name_code(value)
            node = nodes_by_code.get(code)
            if node is None:
                node = make_node(name=name, code=code, level=level)
                nodes_by_code[code] = node
            elif node["name"] != name or node["level"] != level:
                raise ValueError(
                    f"Inconsistent node at CSV row {row_number}: "
                    f"{value!r} conflicts with {node['name']!r} at level {node['level']}"
                )
            path.append(node)

        if not path:
            continue

        for index, node in enumerate(path):
            parent = path[index - 1] if index else None
            parent_code = parent["code"] if parent is not None else None
            known_parent_code = parent_by_code.get(node["code"])

            if node["code"] not in parent_by_code:
                parent_by_code[node["code"]] = parent_code
                if parent is None:
                    roots.append(node)
                elif node not in parent["children"]:
                    parent["children"].append(node)
            elif known_parent_code != parent_code:
                raise ValueError(
                    f"Inconsistent parent for {node['name']!r} at CSV row {row_number}: "
                    f"{known_parent_code!r} vs {parent_code!r}"
                )

    return roots


def omit_empty_children(node: dict[str, Any]) -> dict[str, Any]:
    output = {
        "code": node["code"],
        "name": node["name"],
        "level": node["level"],
    }
    children = [omit_empty_children(child) for child in node["children"]]
    if children:
        output["children"] = children
    return output


def write_yaml(tree: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output_tree = [omit_empty_children(node) for node in tree]
    yaml_text = yaml.dump(
        output_tree,
        Dumper=IndentedSafeDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(yaml_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert data/eligibility_path/resources/oncotree/oncotree.csv to nested YAML."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"CSV input path. Defaults to {DEFAULT_INPUT}.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"YAML output path. Defaults to {DEFAULT_OUTPUT}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    rows = read_csv_rows(input_path)
    tree = build_tree(rows)
    write_yaml(tree, output_path)

    result: dict[str, Any] = {
        "input": str(input_path),
        "output": str(output_path),
        "rows": len(rows),
        "root_nodes": len(tree),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
