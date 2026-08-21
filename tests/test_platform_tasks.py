#!/usr/bin/env python3
"""Command-vector and exit-code checks for the platform cutover.

Does not run the platform CLI or any newsletter job. subprocess.run is mocked.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"
if str(TASKS_DIR) not in sys.path:
    sys.path.insert(0, str(TASKS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import PLATFORM_DIR, run_repo_module  # noqa: E402
from config_loader import load_yaml  # noqa: E402
from scheduler import CONFIG_PATH, build_tasks  # noqa: E402


PLATFORM_PYTHON = f"{PLATFORM_DIR}/.venv/bin/python"
CLI_MODULE = "newsletter_engine.cli"

TASKS = (
    ("run_alexandria_collect", "alexandria", "collect"),
    ("run_alexandria_curate", "alexandria", "curate"),
    ("run_alexandria_write", "alexandria", "write"),
    ("run_alexandria_seo", "alexandria", "seo"),
    ("run_newport_collect", "newport", "collect"),
    ("run_newport_curate", "newport", "curate"),
    ("run_newport_write", "newport", "write"),
    ("run_newport_seo", "newport", "seo"),
    ("run_wasatch_collect", "wasatch", "collect"),
    ("run_wasatch_curate", "wasatch", "curate"),
    ("run_wasatch_write", "wasatch", "write"),
    ("run_wasatch_seo", "wasatch", "seo"),
)


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _expected_command(market: str, job: str) -> list[str]:
    return [PLATFORM_PYTHON, "-m", CLI_MODULE, "--market", market, job]


class PlatformTaskVectorTests(unittest.TestCase):
    def test_each_task_targets_platform_and_right_market(self) -> None:
        for module_name, market, job in TASKS:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                with patch("common.subprocess.run", return_value=_Completed(0)) as run:
                    rc = module.main()
                self.assertEqual(rc, 0)
                run.assert_called_once()
                command = run.call_args.args[0]
                kwargs = run.call_args.kwargs
                self.assertEqual(command, _expected_command(market, job))
                self.assertEqual(kwargs["cwd"], PLATFORM_DIR)
                self.assertIs(kwargs["check"], False)
                self.assertNotIn("job", command)
                self.assertFalse(any("seo_daily.py" in str(part) for part in command))
                self.assertFalse(
                    any("newport_newsletter" in str(part) for part in command)
                )

    def test_exit_codes_propagate_including_usage_and_lock_skip(self) -> None:
        for returncode in (0, 1, 2, 3):
            for module_name, market, job in TASKS:
                with self.subTest(module=module_name, returncode=returncode):
                    module = importlib.import_module(module_name)
                    with patch(
                        "common.subprocess.run", return_value=_Completed(returncode)
                    ) as run:
                        rc = module.main()
                    self.assertEqual(rc, returncode)
                    self.assertEqual(
                        run.call_args.args[0], _expected_command(market, job)
                    )

    def test_run_repo_module_passthrough_does_not_rewrite_codes(self) -> None:
        for returncode in (0, 1, 2, 3):
            with self.subTest(returncode=returncode):
                with patch(
                    "common.subprocess.run", return_value=_Completed(returncode)
                ) as run:
                    rc = run_repo_module(
                        PLATFORM_DIR,
                        CLI_MODULE,
                        "--market",
                        "alexandria",
                        "collect",
                    )
                self.assertEqual(rc, returncode)
                command = run.call_args.args[0]
                self.assertEqual(
                    command, _expected_command("alexandria", "collect")
                )
                self.assertEqual(run.call_args.kwargs["cwd"], PLATFORM_DIR)
                self.assertIs(run.call_args.kwargs["check"], False)

    def test_config_points_every_market_at_the_platform(self) -> None:
        config = load_yaml(CONFIG_PATH)
        newsletters = config["newsletters"]
        self.assertEqual(set(newsletters), {"alexandria", "newport", "wasatch"})
        for slug, details in newsletters.items():
            with self.subTest(slug=slug):
                self.assertEqual(details["repo_path"], PLATFORM_DIR)
                self.assertTrue(
                    str(details["output_dir"]).startswith(
                        f"{PLATFORM_DIR}/markets/{slug}/"
                    )
                )
                for key in (
                    "collect_script",
                    "curate_script",
                    "write_script",
                    "seo_script",
                ):
                    script = Path(details[key])
                    self.assertTrue(script.is_file(), f"missing {key}: {script}")
        self.assertEqual(
            config["alerts"]["env_file"],
            f"{PLATFORM_DIR}/markets/alexandria/.env",
        )

    def test_wednesday_queue_records_platform_repo_path(self) -> None:
        import logging
        from datetime import datetime
        from zoneinfo import ZoneInfo

        logger = logging.getLogger("test_platform_tasks.queue")
        logger.addHandler(logging.NullHandler())
        now = datetime(2026, 8, 19, 5, 0, tzinfo=ZoneInfo("America/New_York"))
        tasks, slot_ids = build_tasks(load_yaml(CONFIG_PATH), now, logger)
        self.assertEqual(slot_ids, ["wednesday-review"])
        self.assertEqual(len(tasks), 11)
        for task in tasks:
            self.assertEqual(task["repo_path"], PLATFORM_DIR)
            self.assertIn(task["task_type"], {"collect", "curate", "write", "seo"})

    def test_curate_is_queued_after_collect_and_before_write(self) -> None:
        import logging
        from datetime import datetime
        from zoneinfo import ZoneInfo

        logger = logging.getLogger("test_platform_tasks.queue_order")
        logger.addHandler(logging.NullHandler())
        config = load_yaml(CONFIG_PATH)
        tz = ZoneInfo("America/New_York")
        slots = (
            datetime(2026, 8, 17, 5, 0, tzinfo=tz),  # monday-morning
            datetime(2026, 8, 19, 5, 0, tzinfo=tz),  # wednesday-review
        )
        for now in slots:
            with self.subTest(when=now.isoformat()):
                tasks, _slot_ids = build_tasks(config, now, logger)
                by_market: dict[str, list[str]] = {}
                for task in tasks:
                    by_market.setdefault(task["newsletter"], []).append(
                        task["task_type"]
                    )
                for slug in ("alexandria", "newport", "wasatch"):
                    types = by_market[slug]
                    self.assertIn("collect", types)
                    self.assertIn("curate", types)
                    collect_at = types.index("collect")
                    curate_at = types.index("curate")
                    self.assertGreater(
                        curate_at,
                        collect_at,
                        f"{slug}: curate must follow collect ({types})",
                    )
                    if "write" in types:
                        write_at = types.index("write")
                        self.assertGreater(
                            write_at,
                            curate_at,
                            f"{slug}: write must follow curate ({types})",
                        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
