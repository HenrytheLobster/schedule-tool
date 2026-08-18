#!/usr/bin/env python3
from __future__ import annotations

from common import run_repo_module


REPO_DIR = "/Volumes/SSD/Projects/newport-newsletter"


def main() -> int:
    return run_repo_module(REPO_DIR, "newport_newsletter.cli", "job", "seo")


if __name__ == "__main__":
    raise SystemExit(main())
