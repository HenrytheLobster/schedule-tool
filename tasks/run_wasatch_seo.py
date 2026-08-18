#!/usr/bin/env python3
from __future__ import annotations

from common import run_repo_script


REPO_DIR = "/Volumes/SSD/Projects/wasatch-newsletter"


def main() -> int:
    return run_repo_script(REPO_DIR, "scripts/seo_daily.py")


if __name__ == "__main__":
    raise SystemExit(main())
