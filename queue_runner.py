#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from alerts import send_failure_alert


SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"


def setup_logger(timestamp: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"queue_runner_{timestamp}.log"

    logger = logging.getLogger("queue_runner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def load_queue(queue_path: Path) -> dict:
    return json.loads(queue_path.read_text(encoding="utf-8"))


def maybe_alert(config: dict, task: dict, result: dict, logger: logging.Logger) -> None:
    alerts = config.get("alerts", {})
    if not alerts.get("enabled", True):
        return

    env_file = alerts.get("env_file")
    subject = f"Newsletter scheduler failure: {task['display_name']} {task['task_type']}"
    body = (
        "Task failed in /Volumes/SSD/Projects/schedule-tool\n\n"
        f"Newsletter: {task['display_name']}\n"
        f"Slug: {task['newsletter']}\n"
        f"Task type: {task['task_type']}\n"
        f"Slot: {task.get('slot_id', '<unknown>')}\n"
        f"Status: {result['status']}\n"
        f"Return code: {result['returncode']}\n"
        f"Started at: {result['started_at']}\n"
        f"Finished at: {result['finished_at']}\n"
        f"Duration seconds: {result['duration_seconds']}\n"
        f"Script: {task['script_path']}\n\n"
        f"STDOUT:\n{result['stdout'] or '<empty>'}\n\n"
        f"STDERR:\n{result['stderr'] or '<empty>'}\n"
    )
    try:
        send_failure_alert(subject, body, env_file=env_file)
        logger.info("Failure alert sent for task %s", task["id"])
    except Exception as exc:  # pragma: no cover
        logger.exception("Could not send failure alert for task %s: %s", task["id"], exc)


def acquire_lock(queue: dict, logger: logging.Logger) -> tuple[int | None, str | None]:
    runner_config = queue.get("config", {}).get("runner", {})
    lock_path = Path(str(runner_config.get("lock_file", SCRIPT_DIR / "scheduler.lock")))
    stale_seconds = int(runner_config.get("lock_stale_seconds", 6 * 3600))
    payload = {
        "pid": os.getpid(),
        "queue_file": queue.get("queue_file"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "slot_ids": queue.get("slot_ids", []),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        created_at = existing.get("created_at")
        if created_at:
            age = time.time() - datetime.fromisoformat(created_at).timestamp()
        else:
            age = time.time() - lock_path.stat().st_mtime

        if age >= stale_seconds:
            logger.warning(
                "Removing stale scheduler lock at %s (age %.0fs)",
                lock_path,
                age,
            )
            lock_path.unlink(missing_ok=True)
        else:
            logger.error("Scheduler overlap prevented by existing lock %s", lock_path)
            return None, str(lock_path)

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        logger.error("Scheduler overlap prevented by lock race at %s", lock_path)
        return None, str(lock_path)

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return fd, str(lock_path)


def release_lock(lock_path: str | None, logger: logging.Logger) -> None:
    if not lock_path:
        return
    try:
        Path(lock_path).unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not remove lock %s: %s", lock_path, exc)


def append_ledger_entry(queue: dict, result: dict, logger: logging.Logger) -> None:
    runner_config = queue.get("config", {}).get("runner", {})
    ledger_path = Path(str(runner_config.get("ledger_file", SCRIPT_DIR / "run_ledger.jsonl")))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "queue_file": queue.get("queue_file"),
        "slot_ids": queue.get("slot_ids", []),
        "result": result,
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    logger.info("Ledger appended: %s", ledger_path)


def run_task(task: dict, logger: logging.Logger) -> dict:
    script_path = Path(task["script_path"])
    timeout_seconds = int(task.get("timeout_seconds", 300))
    command = ["python3", str(script_path)]

    logger.info("Starting task %s: %s", task["id"], " ".join(command))
    started = datetime.now()
    monotonic_started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(script_path.parent),
            check=False,
        )
        duration = round(time.monotonic() - monotonic_started, 2)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        if stdout:
            logger.info("Task %s stdout:\n%s", task["id"], stdout)
        if stderr:
            logger.warning("Task %s stderr:\n%s", task["id"], stderr)

        status = "success" if completed.returncode == 0 else "failed"
        logger.info(
            "Finished task %s with status=%s returncode=%s duration=%ss",
            task["id"],
            status,
            completed.returncode,
            duration,
        )
        return {
            "id": task["id"],
            "slot_id": task.get("slot_id"),
            "scheduled_for": task.get("scheduled_for"),
            "newsletter": task["newsletter"],
            "task_type": task["task_type"],
            "status": status,
            "returncode": completed.returncode,
            "started_at": started.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_seconds": duration,
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired as exc:
        duration = round(time.monotonic() - monotonic_started, 2)
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        logger.error("Task %s timed out after %ss", task["id"], timeout_seconds)
        if stdout:
            logger.info("Task %s partial stdout:\n%s", task["id"], stdout)
        if stderr:
            logger.warning("Task %s partial stderr:\n%s", task["id"], stderr)
        return {
            "id": task["id"],
            "slot_id": task.get("slot_id"),
            "scheduled_for": task.get("scheduled_for"),
            "newsletter": task["newsletter"],
            "task_type": task["task_type"],
            "status": "timeout",
            "returncode": None,
            "started_at": started.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_seconds": duration,
            "stdout": stdout,
            "stderr": stderr,
        }


def alert_overlap(queue: dict, queue_path: Path, lock_path: str, logger: logging.Logger) -> None:
    alerts = queue.get("config", {}).get("alerts", {})
    if not alerts.get("enabled", True):
        return
    env_file = alerts.get("env_file")
    try:
        send_failure_alert(
            "Newsletter scheduler overlap prevented",
            (
                "A scheduled run in /Volumes/SSD/Projects/schedule-tool did not start "
                "because another scheduler invocation already held the global lock.\n\n"
                f"Queue file: {queue_path}\n"
                f"Slot ids: {queue.get('slot_ids', [])}\n"
                f"Lock file: {lock_path}\n"
            ),
            env_file=env_file,
        )
        logger.info("Overlap alert sent.")
    except Exception as exc:  # pragma: no cover
        logger.exception("Could not send overlap alert: %s", exc)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: python3 {Path(__file__).name} <queue.json>")
        return 1

    queue_path = Path(argv[1]).expanduser().resolve()
    if not queue_path.is_file():
        print(f"Queue file not found: {queue_path}")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    logger = setup_logger(timestamp)

    lock_path: str | None = None
    try:
        queue = load_queue(queue_path)
        queue["queue_file"] = str(queue_path)
        tasks = queue.get("tasks", [])
        config = queue.get("config", {})
        logger.info(
            "Loaded queue %s with %s task(s) for slot(s) %s",
            queue_path,
            len(tasks),
            queue.get("slot_ids", []),
        )

        _fd, lock_path = acquire_lock(queue, logger)
        if not lock_path or _fd is None:
            alert_overlap(queue, queue_path, lock_path or "<unknown>", logger)
            return 1

        results = []
        for task in tasks:
            result = run_task(task, logger)
            results.append(result)
            append_ledger_entry(queue, result, logger)
            if result["status"] != "success":
                maybe_alert(config, task, result, logger)

        results_path = SCRIPT_DIR / f"results_{timestamp}.json"
        payload = {
            "queue_file": str(queue_path),
            "slot_ids": queue.get("slot_ids", []),
            "started_at": queue.get("created_at"),
            "completed_at": datetime.now().isoformat(),
            "task_count": len(results),
            "results": results,
        }
        results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Results written to %s", results_path)

        failed = [item for item in results if item["status"] != "success"]
        return 0 if not failed else 1
    except Exception as exc:  # pragma: no cover - defensive logging entrypoint
        logger.exception("Queue runner failed: %s", exc)
        try:
            send_failure_alert(
                "Newsletter scheduler queue runner failed",
                (
                    "Queue runner crashed in /Volumes/SSD/Projects/schedule-tool\n\n"
                    f"Queue file: {queue_path}\n"
                    f"Error: {exc}\n"
                ),
                env_file=(locals().get("config", {}).get("alerts", {}) or {}).get("env_file"),
            )
        except Exception:
            logger.exception("Could not send queue runner failure alert.")
        return 1
    finally:
        release_lock(lock_path, logger)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
