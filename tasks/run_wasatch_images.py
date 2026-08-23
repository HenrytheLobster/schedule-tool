#!/usr/bin/env python3
from __future__ import annotations

from common import PLATFORM_DIR, run_repo_module


MARKET = "wasatch"


def main() -> int:
    return run_repo_module(
        PLATFORM_DIR, "newsletter_engine.cli", "--market", MARKET, "images"
    )


if __name__ == "__main__":
    raise SystemExit(main())
