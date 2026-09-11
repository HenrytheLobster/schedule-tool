#!/usr/bin/env python3
"""Shut ComfyUI down once the slot's image work is finished.

FLUX plus the T5 encoder is ~12 GB on a 24 GB machine. Leaving it resident
would put it in competition with gemma4:26b (~26 GB) at the next slot's
curate step, which is the collision AGENTS.md calls the binding constraint.

Exits 0 even when there was nothing to stop. A slot must not be reported as
failed because the thing we wanted gone was already gone.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request

ENDPOINT = "http://127.0.0.1:8188"
PATTERN = "ComfyUI/main.py"
GRACE_SECONDS = 20
POLL_SECONDS = 2


def _listening() -> bool:
    try:
        with urllib.request.urlopen(f"{ENDPOINT}/system_stats", timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _pids() -> list[str]:
    result = subprocess.run(
        ["pgrep", "-f", PATTERN], capture_output=True, text=True, check=False
    )
    return [line for line in result.stdout.split() if line.strip()]


def main() -> int:
    pids = _pids()
    if not pids:
        print("ComfyUI was not running")
        return 0

    subprocess.run(["pkill", "-f", PATTERN], check=False)
    print(f"sent TERM to {len(pids)} ComfyUI process(es): {', '.join(pids)}")

    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _pids() and not _listening():
            print("ComfyUI stopped; ~12 GB released")
            return 0
        time.sleep(POLL_SECONDS)

    # A sampler mid-step ignores TERM until the current iteration returns, and
    # a step is ~35s on this machine. Escalate rather than leave the weights
    # resident into the next slot.
    stubborn = _pids()
    if stubborn:
        subprocess.run(["pkill", "-9", "-f", PATTERN], check=False)
        print(f"escalated to KILL for {', '.join(stubborn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
