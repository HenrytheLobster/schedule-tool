#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config_loader import load_yaml


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config" / "newsletters.yaml"
LOGS_DIR = SCRIPT_DIR / "logs"


def setup_logger(timestamp: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"scheduler_{timestamp}.log"

    logger = logging.getLogger("scheduler")
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


def resolve_now(config: dict, at: str | None) -> datetime:
    timezone_name = config.get("schedule", {}).get("timezone", "America/New_York")
    tz = ZoneInfo(timezone_name)
    if at:
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return datetime.now(tz)


def matching_slots(config: dict, now: datetime, logger: logging.Logger) -> list[tuple[str, dict]]:
    schedule = config.get("schedule", {})
    slots = schedule.get("slots", {})
    matched: list[tuple[str, dict]] = []
    for slot_id, slot in slots.items():
        weekdays = set(slot.get("weekdays", []))
        if now.weekday() not in weekdays:
            continue
        if int(slot.get("hour", -1)) != now.hour or int(slot.get("minute", -1)) != now.minute:
            continue
        matched.append((slot_id, slot))
    if matched:
        logger.info(
            "Matched slot(s) for %s: %s",
            now.isoformat(),
            ", ".join(slot_id for slot_id, _slot in matched),
        )
    else:
        logger.info("No slots matched for %s", now.isoformat())
    return matched


def build_tasks(config: dict, now: datetime, logger: logging.Logger) -> tuple[list[dict], list[str]]:
    newsletters = config.get("newsletters", {})
    timeout_seconds = int(config.get("ollama", {}).get("timeout", 3000))
    slots = matching_slots(config, now, logger)

    tasks: list[dict] = []
    slot_ids: list[str] = []
    order = 1

    for slot_id, slot in slots:
        slot_ids.append(slot_id)
        for task_ref in slot.get("tasks", []):
            try:
                newsletter_slug, task_type = str(task_ref).split(".", 1)
            except ValueError:
                logger.warning("Skipping malformed task reference: %s", task_ref)
                continue

            details = newsletters.get(newsletter_slug)
            if not isinstance(details, dict):
                logger.warning("Skipping unknown newsletter slug in task reference: %s", task_ref)
                continue

            script_key = f"{task_type}_script"
            script_path = Path(str(details.get(script_key, ""))).expanduser()
            if not script_path.is_file():
                logger.warning(
                    "Skipping %s because %s does not exist: %s",
                    task_ref,
                    script_key,
                    script_path,
                )
                continue

            task = {
                "id": f"{order:02d}_{newsletter_slug}_{task_type}",
                "sequence": order,
                "slot_id": slot_id,
                "scheduled_for": now.isoformat(),
                "newsletter": newsletter_slug,
                "display_name": details.get("display_name", newsletter_slug),
                "task_type": task_type,
                "script_path": str(script_path),
                "repo_path": details.get("repo_path"),
                "output_dir": details.get("output_dir"),
                "timeout_seconds": timeout_seconds,
            }
            tasks.append(task)
            order += 1

    return tasks, slot_ids


def write_queue(
    tasks: list[dict],
    config: dict,
    timestamp: str,
    now: datetime,
    slot_ids: list[str],
    logger: logging.Logger,
) -> Path:
    queue_path = SCRIPT_DIR / f"queue_{timestamp}.json"
    payload = {
        "created_at": now.isoformat(),
        "scheduled_date": now.date().isoformat(),
        "weekday": now.weekday(),
        "timezone": config.get("schedule", {}).get("timezone", "America/New_York"),
        "slot_ids": slot_ids,
        "ollama": config.get("ollama", {}),
        "config": {
            "alerts": config.get("alerts", {}),
            "runner": config.get("runner", {}),
        },
        "task_count": len(tasks),
        "tasks": tasks,
    }
    queue_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Queue file created: %s", queue_path)
    logger.info("Task count: %s", len(tasks))
    return queue_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--at",
        help="Override the local scheduler time in ISO format, for example 2026-08-19T05:00",
    )
    args = parser.parse_args(argv)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    logger = setup_logger(timestamp)

    try:
        logger.info("Loading config from %s", CONFIG_PATH)
        config = load_yaml(CONFIG_PATH)
        now = resolve_now(config, args.at)
        tasks, slot_ids = build_tasks(config, now, logger)
        queue_path = write_queue(tasks, config, timestamp, now, slot_ids, logger)
        print(queue_path)
        return 0
    except Exception as exc:  # pragma: no cover - defensive logging entrypoint
        logger.exception("Scheduler failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
