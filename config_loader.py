#!/usr/bin/env python3
"""Minimal YAML and env loaders for the scheduler config.

This intentionally supports the subset used by config/newsletters.yaml:
- nested mappings via indentation
- inline lists like [0, 1, 2]
- quoted and unquoted scalars
"""

from __future__ import annotations

import ast
import os
from pathlib import Path


def _parse_scalar(raw: str):
    value = raw.strip()
    if value == "":
        return ""

    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None

    if value.startswith(("'", '"', "[", "{", "(", "-")) or value[:1].isdigit():
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            pass

    return value


def load_yaml(path: str | Path) -> dict:
    source = Path(path)
    lines = source.read_text(encoding="utf-8").splitlines()
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError(
                f"Unsupported indentation in {source} at line {line_number}: {raw_line!r}"
            )

        content = raw_line.lstrip(" ")
        if ":" not in content:
            raise ValueError(
                f"Expected 'key: value' syntax in {source} at line {line_number}: {raw_line!r}"
            )

        key, value_part = content.split(":", 1)
        key = key.strip()
        value_part = value_part.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        if value_part == "":
            child: dict = {}
            current[key] = child
            stack.append((indent, child))
            continue

        current[key] = _parse_scalar(value_part)

    return root


def load_env_file(path: str | Path) -> dict[str, str]:
    source = Path(path)
    values: dict[str, str] = {}
    if not source.is_file():
        return values

    for raw_line in source.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def merged_env(env_file: str | Path | None = None) -> dict[str, str]:
    values = dict(os.environ)
    if env_file:
        file_values = load_env_file(env_file)
        for key, value in file_values.items():
            values.setdefault(key, value)
    return values
