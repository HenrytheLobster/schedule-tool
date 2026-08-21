# Newsletter Scheduler

This directory is the single local scheduling control plane for the Alexandria, Newport, and Wasatch newsletters.

It runs one task at a time on Mike's machine, uses one launchd plist as the scheduler entrypoint, and keeps a local ledger of every task result.

## What it does

- Owns the timing for collect, curate, write, and SEO across all three newsletters.
- Runs tasks strictly sequentially so the local Ollama runtime is never shared by overlapping jobs.
- Uses scheduler-owned wrapper scripts under `tasks/` as the execution boundary.
- Writes a queue JSON before execution, a results JSON after execution, and an append-only JSONL ledger for task history.
- Sends email alerts when a task fails, times out, or a scheduled slot is blocked by an overlapping runner.

## Current recurring schedule

All times are in `America/New_York`.

- Monday at `5:00 AM`: Alexandria collect, Wasatch collect, Newport collect, Alexandria curate, Wasatch curate, Newport curate, Alexandria SEO, Wasatch SEO, Newport SEO
- Tuesday at `6:00 AM`: Alexandria SEO, Wasatch SEO
- Wednesday at `5:00 AM`: Alexandria collect, Wasatch collect, Newport collect, Alexandria curate, Wasatch curate, Newport curate, Alexandria write, Wasatch write, Newport write, Alexandria SEO, Wasatch SEO
- Thursday at `6:00 AM`: Alexandria SEO, Wasatch SEO
- Friday at `6:00 AM`: Alexandria SEO, Wasatch SEO
- Saturday at `6:00 AM`: Alexandria SEO, Wasatch SEO
- Sunday at `6:00 AM`: Alexandria SEO, Wasatch SEO

This is the schedule that will apply after the Saturday, August 15, 2026 update in this repo.

## Directory layout

```text
/Volumes/SSD/Projects/schedule-tool/
├── config/
│   └── newsletters.yaml
├── launch-agents/
│   └── com.naylor.newsletters.plist
├── logs/
├── tasks/
│   ├── common.py
│   ├── run_alexandria_collect.py
│   ├── run_alexandria_curate.py
│   ├── run_alexandria_write.py
│   ├── run_alexandria_seo.py
│   ├── run_newport_collect.py
│   ├── run_newport_curate.py
│   ├── run_newport_write.py
│   ├── run_newport_seo.py
│   ├── run_wasatch_collect.py
│   ├── run_wasatch_curate.py
│   ├── run_wasatch_write.py
│   └── run_wasatch_seo.py
├── config_loader.py
├── daily_run.sh
├── queue_runner.py
├── scheduler.py
└── run_ledger.jsonl
```

## Config

`config/newsletters.yaml` is the source of truth for:

- newsletter display names
- wrapper script paths
- repo paths
- output paths
- explicit central schedule slots
- Ollama host, model, and timeout
- email alert settings
- log, lock, and ledger paths

The schedule is modeled as named slots. Each slot lists:

- local weekdays
- local hour and minute
- the exact ordered task list for that slot

## Execution model

`launchd` invokes `daily_run.sh` at the configured slot times.

`daily_run.sh` does two things:

1. runs `scheduler.py` to build a queue for the current local slot
2. runs `queue_runner.py` against that queue

`queue_runner.py` enforces a global lock. If another scheduler invocation is already running, the new run exits, sends an email alert, and does not start any tasks.

## Wrapper scripts

The wrappers in `tasks/` are the stable interface the scheduler calls.

Every wrapper invokes the consolidated platform through one virtualenv:

```text
/Volumes/SSD/Projects/newsletter-platform/.venv/bin/python -m newsletter_engine.cli --market <id> <job>
```

`<id>` is `alexandria`, `newport`, or `wasatch`. `<job>` is `collect`, `write`, or `seo`. There is no `job` subcommand. Market identity is an argument on the wrapper, not inferred from cwd.

The wrappers set these runtime defaults:

- `LLM_PROVIDER=ollama`
- `CURATION_PROVIDER=ollama`
- `EXTRACTION_PROVIDER=ollama`
- `WRITE_PROVIDER=ollama`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=gemma4:26b`
- `OLLAMA_TIMEOUT_SECONDS=1800`

## Files written by each run

- `queue_YYYYMMDD_HHMMSS_microseconds.json`
- `results_YYYYMMDD_HHMMSS_microseconds.json`
- `logs/scheduler_YYYYMMDD_HHMMSS_microseconds.log`
- `logs/queue_runner_YYYYMMDD_HHMMSS_microseconds.log`
- `run_ledger.jsonl`

The ledger is append-only. Each line records one task result with:

- queue file
- slot ids
- newsletter
- task type
- status
- timestamps
- duration
- stdout
- stderr

## Manual usage

Build a queue for the current local slot:

```bash
cd /Volumes/SSD/Projects/schedule-tool
python3 scheduler.py
```

Build a queue for a specific future or past slot:

```bash
cd /Volumes/SSD/Projects/schedule-tool
python3 scheduler.py --at 2026-08-19T05:00
```

Run a queue manually:

```bash
cd /Volumes/SSD/Projects/schedule-tool
python3 queue_runner.py queue_*.json
```

Run the full wrapper:

```bash
cd /Volumes/SSD/Projects/schedule-tool
./daily_run.sh
```

## launchd install

Install the plist:

```bash
cp /Volumes/SSD/Projects/schedule-tool/launch-agents/com.naylor.newsletters.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.naylor.newsletters.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.naylor.newsletters.plist
```

launchd output paths:

- `/Volumes/SSD/Projects/schedule-tool/logs/launchd.out`
- `/Volumes/SSD/Projects/schedule-tool/logs/launchd.err`

## Alerting

Email alerts use the SMTP settings from the configured env file in `config/newsletters.yaml`.

By default this is:

- `/Volumes/SSD/Projects/newsletter-platform/markets/alexandria/.env`

Required keys:

- `NOTIFY_EMAIL_TO`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`

## Notes

- This tool is the only scheduler source of truth. Do not add separate newsletter cron jobs, repo-local launchd jobs, or GitHub Actions schedules for these same tasks.
- The queue is intentionally sequential, not parallel.
- If a task fails, later tasks in the same slot still run.
