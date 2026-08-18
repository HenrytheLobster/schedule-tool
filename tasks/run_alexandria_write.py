#!/usr/bin/env python3
from __future__ import annotations

from common import run_repo_module


REPO_DIR = "/Volumes/SSD/Projects/alexandria-newsletter"


def main() -> int:
    return run_repo_module(REPO_DIR, "newsletter_engine.cli", "job", "write")


if __name__ == "__main__":
    raise SystemExit(main())
