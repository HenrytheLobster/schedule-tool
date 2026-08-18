#!/usr/bin/env python3
"""Synthetic checks for the three queue_runner reliability fixes.

Does not run the scheduler, the real queue, or any newsletter task.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config_loader import load_yaml
from queue_runner import _as_text, run_task
from scheduler import CONFIG_PATH, build_tasks


def _logger() -> logging.Logger:
    logger = logging.getLogger("verify_timeout_fixes")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _task(script: Path, timeout_seconds: int, task_id: str) -> dict:
    return {
        "id": task_id,
        "slot_id": "verify",
        "scheduled_for": "verify",
        "newsletter": "verify",
        "task_type": "collect",
        "script_path": str(script),
        "timeout_seconds": timeout_seconds,
    }


def check_as_text() -> None:
    assert _as_text(None) == ""
    assert _as_text("already-str") == "already-str"
    assert _as_text(b"hello") == "hello"
    assert _as_text(b"  padded  \n") == "  padded  \n"
    decoded = _as_text(b"ok \xff\xfe\x80")
    assert isinstance(decoded, str)
    assert decoded.startswith("ok ")
    json.dumps({"stdout": decoded})


def check_timeout_bytes_and_queue_continues(tmp: Path, logger: logging.Logger) -> None:
    slow = tmp / "slow_with_output.py"
    slow.write_text(
        "import sys, time\n"
        "print('partial-stdout', flush=True)\n"
        "print('partial-stderr', file=sys.stderr, flush=True)\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    ok = tmp / "ok.py"
    ok.write_text("print('second-task-ok', flush=True)\n", encoding="utf-8")

    first = run_task(_task(slow, 1, "verify_timeout"), logger)
    assert first["status"] == "timeout", first
    assert isinstance(first["stdout"], str), type(first["stdout"])
    assert isinstance(first["stderr"], str), type(first["stderr"])
    assert "partial-stdout" in first["stdout"]
    assert "partial-stderr" in first["stderr"]
    json.dumps(first)

    second = run_task(_task(ok, 10, "verify_after_timeout"), logger)
    assert second["status"] == "success", second
    assert second["returncode"] == 0
    assert "second-task-ok" in second["stdout"]
    json.dumps({"results": [first, second]})


def check_success_path_unchanged(tmp: Path, logger: logging.Logger) -> None:
    script = tmp / "success.py"
    script.write_text("print('hello-success', flush=True)\n", encoding="utf-8")
    result = run_task(_task(script, 10, "verify_success"), logger)
    assert result["status"] == "success", result
    assert result["returncode"] == 0
    assert result["stdout"] == "hello-success"
    assert result["stderr"] == ""
    json.dumps(result)


def check_process_group_killed(tmp: Path, logger: logging.Logger) -> None:
    marker = tmp / "pg"
    marker.mkdir()
    grand_pid_path = marker / "grand.pid"
    grandchild = tmp / "grandchild.py"
    grandchild.write_text(
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(grand_pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    script = tmp / "spawn_orphan.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"Path({str(marker / 'child.pid')!r}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = run_task(_task(script, 1, "verify_pgkill"), logger)
    elapsed = time.monotonic() - started
    assert result["status"] == "timeout", result
    assert elapsed < 20, f"timeout path took too long: {elapsed:.1f}s"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not grand_pid_path.is_file():
        time.sleep(0.05)
    assert grand_pid_path.is_file(), "grandchild never wrote its pid"
    grand_pid = int(grand_pid_path.read_text(encoding="utf-8"))
    time.sleep(0.2)
    assert not _alive(grand_pid), f"grandchild {grand_pid} still alive after timeout"


def check_task_timeout_config() -> None:
    config = load_yaml(CONFIG_PATH)
    assert config["ollama"]["timeout"] == 3000, config["ollama"]
    logger = logging.getLogger("verify_timeout_fixes.config")
    logger.addHandler(logging.NullHandler())
    now = datetime(2026, 8, 19, 5, 0, tzinfo=ZoneInfo("America/New_York"))
    tasks, slot_ids = build_tasks(config, now, logger)
    assert slot_ids == ["wednesday-review"], slot_ids
    assert tasks, "expected Wednesday tasks"
    types = {task["task_type"] for task in tasks}
    assert {"collect", "write", "seo"} <= types, types
    assert all(task["timeout_seconds"] == 3000 for task in tasks), {
        task["id"]: task["timeout_seconds"] for task in tasks
    }


def main() -> int:
    logger = _logger()
    failures = []
    with tempfile.TemporaryDirectory(prefix="schedule-tool-verify-") as raw_tmp:
        tmp = Path(raw_tmp)
        checks = [
            ("as_text", check_as_text),
            ("timeout_json_and_continue", lambda: check_timeout_bytes_and_queue_continues(tmp, logger)),
            ("success_path", lambda: check_success_path_unchanged(tmp, logger)),
            ("process_group_kill", lambda: check_process_group_killed(tmp, logger)),
            ("task_timeout_config", check_task_timeout_config),
        ]
        for name, fn in checks:
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures.append(name)
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("All verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
