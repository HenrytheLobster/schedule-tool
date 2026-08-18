# Newsletter Pipeline Review — Prioritized Findings
**Date:** 2026-08-17 · **Scope:** schedule-tool, alexandria-newsletter (NOVA), newport-newsletter, wasatch-newsletter (stufftodoinutah)
**Focus:** reliability/crashes + date/timezone/scheduling. Ranked by likelihood × impact. Each finding has file refs and a suggested fix, ready to hand to Codex/Sonnet.

**The headline:** the engines mostly work when run by hand. What's failing is the *orchestration layer* — the scheduler doesn't survive the Mac mini's real-world conditions (sleep, network-not-yet-up, slow local LLM), and every failure mode ends in a **silent "success"** or an alert that can't send. Findings P0-1 through P0-5 explain why nothing has ever run end-to-end without you.

---

## P0 — Things that stop the pipeline entirely

### P0-1. ~~Scheduled runs aren't firing~~ — RESOLVED Sunday 8/16 ~21:07 (launchd layer now proven end-to-end)

**Root cause, confirmed by live debugging:** two macOS gates, no code bug. (1) launchd refused to spawn the job with `EX_CONFIG` because the program script, WorkingDirectory, and log paths all lived on the external `/Volumes/SSD` — fixed by a boot-volume wrapper (`~/Library/Scripts/newsletters_daily_run_wrapper.sh`, source in `schedule-tool/launch-agents/`) and a v2 plist (`com.naylor.newsletters.v2.plist`) whose launchd-touched paths are all on the boot volume; the wrapper also waits up to 5 min for the SSD to mount (reboot protection). (2) Once spawning, zsh got `can't open input file` on the SSD — TCC removable-volume denial — fixed by granting **Full Disk Access to `/bin/zsh`**. Verified: kickstart at 21:07 ran scheduler → "No slots matched" → empty queue → runner → results, all logged.

**Maintenance notes for Codex:** the active plist is now `~/Library/LaunchAgents/com.naylor.newsletters.plist` (v2 content); the old plist in `launch-agents/` is superseded — update the README's install section. If a macOS update ever resets the FDA grant for zsh, the symptom will be `can't open input file: ./daily_run.sh` in `~/Library/Logs/newsletters-launchd.err`. If Python children ever hit their own TCC denial (not observed), add the venv python to FDA the same way. launchd stderr **appends** — old failure lines linger in `newsletters-launchd.err`; check timestamps in the SSD-side logs instead.

**Still worth fixing (resilience, downgraded from blocker):** the exact-minute slot match below. The Mac never sleeps, so day-to-day fires land on the minute — but any reboot, update, or logged-out window at fire time still converts a run into a silent 0-task "success."

### P0-1 (original analysis). Exact-minute matching makes any late fire a silent no-op
**Files:** `schedule-tool/scheduler.py:60`, `schedule-tool/launch-agents/com.naylor.newsletters.plist`, `schedule-tool/daily_run.sh`

`matching_slots()` requires an **exact hour AND minute match**:
```python
if int(slot.get("hour", -1)) != now.hour or int(slot.get("minute", -1)) != now.minute:
```
launchd's `StartCalendarInterval` coalesces missed events: if the Mac is asleep at 5:00, the job runs *at wake* — 5:23, 7:41, whenever. At that point minute ≠ 0, **zero slots match, a 0-task queue is written, and the runner exits 0.** No alert, no retry. The whole morning silently vanishes.

**CONFIRMED Sunday night via `launchctl print gui/501/com.naylor.newsletters`:** the agent IS loaded with all 7 calendar triggers, but `runs = 1, last exit code = 78: EX_CONFIG` — launchd attempted the Sunday 6:00 fire and **failed at spawn**, before stdout redirection or the script ever ran. Everything the job touches (script, WorkingDirectory, log paths) lives on the external `/Volumes/SSD`; EX_CONFIG at spawn strongly suggests TCC removable-volume consent was never granted to the launchd context (no one at the keyboard to click Allow), or the volume was inaccessible at fire time. Diagnose with `launchctl kickstart gui/501/com.naylor.newsletters` while logged in (grant any permission dialog that appears), and `log show --last 1d --predicate 'process == "launchd"' | grep -i naylor` for launchd's exact complaint. Fallback: System Settings → Privacy & Security → Full Disk Access → add `/bin/zsh`. The wake-tolerance fix below is still worth doing, but it's resilience (reboots, updates) rather than the primary blocker — the Mac is configured to never sleep.

Original evidence trail (as of Sunday evening 8/16 — the big Monday 5:00 slot hasn't had its chance yet):
- `run_ledger.jsonl` contains exactly **2 entries ever**, both from a manual run Sat 8/15 17:39.
- No `logs/launchd.out` / `launchd.err` exist in schedule-tool/logs at all — suggesting the agent may not even be loaded (plist lives in the repo's `launch-agents/`, and `RunAtLoad` is false).
- The **Sunday 6:00 sunday-seo slot** is the one scheduled opportunity since the plist was written (Sat ~9:00), and it left *zero* trace — no scheduler log, no queue file, not even a "No slots matched" entry. That distinguishes the failure modes: if launchd had fired late after a wake, the scheduler would still have run and logged a mismatch. Total silence means the agent isn't loaded, or the Mac slept through 6:00 and hasn't triggered the coalesced fire. Corroborating: alexandria's stale `seo_publish.lock` (from Sat 14:27) would have been auto-cleared by any Sunday alexandria.seo run — it's still there.
- **Monday 5:00 (6 tasks) is the first real test.** Even if the agent is loaded, the exact-minute match above means a sleeping Mac converts that run into a silent no-op.

**Fix (three parts):**
1. Verify/install the agent: `launchctl print gui/$UID/com.naylor.newsletters` — if missing, `cp` the plist to `~/Library/LaunchAgents/` and `launchctl bootstrap gui/$UID ...`.
2. Make matching wake-tolerant: match a slot if `now` is the right weekday and `slot_time <= now <= slot_time + grace` (e.g., 3–6 h), then dedupe against the ledger (skip if a queue for `slot_id + date` already ran). This also gives you free catch-up after reboots.
3. Keep the Mac awake/waking: `sudo pmset repeat wakeorpoweron MTWRFSU 04:55:00` and wrap `daily_run.sh` in `caffeinate -i` so it can't sleep mid-run (Wednesday's slot is 8 tasks × up to 30 min).

### P0-2. Timeout handling crashes the queue runner and abandons the rest of the queue (observed 8/15)
**File:** `schedule-tool/queue_runner.py:194-195` (and the ledger write at 139)

On `subprocess.TimeoutExpired`, `exc.stdout`/`exc.stderr` come back as **bytes** (even with `text=True` — confirmed in your log on Python 3.14). The result dict then hits `json.dumps` → `TypeError: Object of type bytes is not JSON serializable` → **the whole runner aborts inside the task loop.** Remaining tasks never run, no results file, and `maybe_alert` for the timed-out task never fires.

This exact crash is in `logs/queue_runner_20260815_142717_918791.log` (alexandria SEO timed out at 1800 s, then the runner died).

**Fix:** decode defensively — `stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")` — and wrap the per-task body (`run_task` + `append_ledger_entry` + `maybe_alert`) in its own try/except so one bad task can't kill the batch.

### P0-3. Timeouts kill the wrapper but orphan the real job — which then blocks later runs
**Files:** `schedule-tool/queue_runner.py:153` (subprocess.run), `schedule-tool/tasks/common.py` (grandchild spawn), `alexandria-newsletter/scripts/seo_daily.py:55-66,126-127`

The runner executes `python3 tasks/run_X.py` (child), which spawns `<repo>/.venv/bin/python -m ...` (grandchild). `subprocess.run(timeout=...)` kills **only the child**. The grandchild keeps running: still hammering Ollama (competing with the *next* queued task — a slow-motion pileup), still holding the repo lock, and possibly pushing to git minutes later.

Observed consequence: `alexandria-newsletter/logs/seo_publish.lock` was created by the 8/15 14:27 run that "timed out" and **still exists two days later**. The 17:39 retry logged `success` in **0.06 s** — it hit the lock, printed "Another run holds…", and `seo_daily.py` **returned 0**. The ledger's only alexandria "success" was a no-op.

**Fix:**
1. In `run_task`, launch with `start_new_session=True` and on timeout `os.killpg(proc.pid, SIGKILL)` (requires switching to `Popen`/`communicate`).
2. In each repo's `seo_daily.py`: return **non-zero** (or send an alert) on lock-blocked exit — a skipped publish should never look like success. Same pattern exists in wasatch's `scripts/seo_daily.py`.
3. Simplify: have `tasks/common.py` exec the venv python **directly as the runner's child** (it already does — the extra wrapper layer is `run_X.py` itself; consider having the queue read a module + repo from the queue JSON and spawn one process, not two).

### P0-4. The 5:00 a.m. network race makes every job fail at once — and kills the alerts too
**Evidence:** all three `logs/collect.log` on 8/14 14:42, `write.log` 15:51 — every host, including `oauth2.googleapis.com` and your SMTP host, failed DNS (`[Errno 8] nodename nor servname provided`).

When the Mac wakes for a scheduled run, Wi-Fi/DNS can lag by 10–60 s. Collect fires immediately → every collector fails → "Collect job returned zero rows" → `alert_failure` also can't resolve SMTP → **RuntimeError: Alert delivery failed**. Total loss, zero notification. This will recur on every wake-triggered run.

**Fix:** add a preflight to `daily_run.sh` (or top of `queue_runner.main`): loop up to ~5 min until a DNS lookup + HTTPS HEAD to a reliable host succeeds; only then run the queue. Separately, make alert delivery resilient: on SMTP failure, append the alert to a local `pending_alerts.jsonl` and retry at the start of the next run.

### P0-5. Task timeout (1800 s) is mathematically guaranteed to be too small — and equals the per-LLM-call timeout
**Files:** `schedule-tool/config/newsletters.yaml:68` (`timeout: 1800`), `schedule-tool/tasks/common.py` (`OLLAMA_TIMEOUT_SECONDS: "1800"`), wasatch log `queue_runner_20260815_173945.log` (model load = **48.7 s** for one call)

A single Ollama call is allowed 1800 s — the same as the *entire task*. And real runs show gemma4:26b takes ~50 s just to load, ~60 s per durability batch; alexandria's SEO run against 14,776 sheet rows blew the 30-min budget doing ~13 durability batches (see P1-1, which multiplies this).

**Fix:** per-task-type timeouts in the YAML (collect: 3600, write: 2700, seo: 3600 — tune from the ledger's `duration_seconds` once runs exist), and set `OLLAMA_TIMEOUT_SECONDS` to something like 300 so one hung call fails fast instead of eating the task. Note the queue's `ollama:` block from the YAML is **written into the queue JSON but never used** — `tasks/common.py` hardcodes its own values (see P2-4), so today edits to the YAML model/timeout silently do nothing.

---

## P1 — Stage-breaking bugs and silent quality loss

### P1-1. Ollama structured output is broken for every prompt except curate.md → SEO durability fails 100% of the time
**Files:** `alexandria .../llm.py:269`, `wasatch .../llm.py:387`, `.../seo/durability.py` (both repos)

```python
request_body["format"] = CURATION_RESPONSE_SCHEMA if prompt_name == "curate.md" else "json"
```
`format: "json"` nudges gemma toward a JSON **object**; durability needs an **array** of `{id, durable, kind, reason}`. Result, in every recent log: `Durability classification failed for N candidates (Structured extraction response must be a JSON array.)` — dozens of times per run, ~40–60 s each. Every candidate is dropped ("fails closed"), so **roundup pages get "0 viable of 30"** — and because failures aren't cached, the same doomed calls burn ~25+ minutes again on every single run (this is what pushed alexandria past the 1800 s timeout).

**Fix:** give each JSON prompt a proper array schema (mirror `CURATION_RESPONSE_SCHEMA` — Ollama accepts a JSON-schema object in `format`), e.g. a `DURABILITY_RESPONSE_SCHEMA` with the verdict fields. Also worth caching *failures* for ~24 h in `durability_verdicts.json` so a bad model day costs one attempt, not thirteen.

Related trap: the Gemini path's `_gemini_json` (alexandria `llm.py`) hardcodes `EVENT_RESPONSE_SCHEMA` (title/date_text/url/summary) for **every** prompt — so durability is schema-broken under Gemini too. If you ever flip providers back, this bites again. Thread the schema through per-prompt.

### P1-2. No `num_ctx` on any Ollama request → silent prompt truncation → hallucinated/invalid JSON
**Files:** `alexandria .../llm.py` (`_ollama_generate` — no `options`), same in wasatch and newport llm.py

Ollama defaults to a small context window (typically 4096 tokens) unless `options.num_ctx` is set. Your payloads routinely exceed it: draft_issue carries the full planner JSON; curation batches 20 events with summaries; durability batches 25. Truncated input is the classic cause of exactly what your curate logs show: *"Curator returned a row outside its input batch"*, *"returned duplicate row"* — the model literally can't see the rows it was given.

**Fix:** add `"options": {"num_ctx": 16384}` (gemma4:26b supports it; watch RAM on the mini) to all three repos' Ollama request bodies, and log `prompt_eval_count` from the response so truncation is visible. Consider `keep_alive` alignment too — wasatch sets it, alexandria/newport don't, so the 26B model reloads (≈49 s) between calls if gaps exceed 5 min.

### P1-3. Alexandria curation writes have been failing since 7/31 — Sheets "exceeds grid limits" on every batch
**Files:** `alexandria .../sheets.py:311-342` (`_overwrite_tab`), log: `logs/curate.log` 7/31 23:46+

`_overwrite_tab` computes `grid_rows` from cached tab properties, writes rows, then clears `A{last+1}:{end}{grid_rows}`. When the merge produces **exactly as many rows as the grid has** (or the grid shrank since properties were fetched), the clear range starts past the last row → HTTP 400 `Range exceeds grid limits. Max rows: 1238` → the batch is counted "failed after retries". Log shows batches 1,3,4,5,6 all failing this way — curated output has been partially/fully stalling since.

**Fix:** skip the clear when `last_data_row + 1 > grid_rows` (re-fetch rowCount after `_ensure_tab_row_capacity`), and catch 400-with-"exceeds grid limits" as benign. Check whether newport/wasatch `sheets.py` share the pattern (newport's is an older 8.6 KB variant — verify while in there).

### P1-4. One hallucinated row throws away the whole curation batch
**File:** `alexandria .../jobs/curate.py:173-187` (`_validate_curated_batch` raises), same idea in wasatch

`Curator returned a row outside its input batch` / `duplicate row` → **RuntimeError → all ~20 rows in that batch discarded**, three logged occurrences in July. Combined with P1-2 (truncation makes hallucination likely), you're structurally losing good events.

**Fix:** drop the offending row and keep the rest; only fail the batch if >N% of rows are invalid. Keep the "no progress at all" hard failure.

### P1-5. Social posting doesn't exist in production — anywhere
**Files:** `newport .../facebook_poster.py`, `reddit_poster.py`, `newport/.env` (keys absent), `newport .../config.py:110`, `wasatch/CODEX-FACEBOOK-DAILY-BRIEF.md` (plan only)

Reality vs. the four-stage design you described:
- **Newport** is the only repo with Facebook/Reddit code, and it's gated by `FACEBOOK_POST_ENABLED` / `REDDIT_POST_ENABLED` — **neither is set in `.env`** (nor are `BUFFER_API_KEY`, `REDDIT_*`), so `post_facebook_issue` returns `skipped: "FACEBOOK_POST_ENABLED is off"` on every write.
- Design mismatch: it posts **the issue link once per issue**, not "an event every day."
- **Alexandria and wasatch** have no social code at all.
- **schedule-tool** has no `social` task type — `newsletters.yaml` only knows collect/write/seo.

**Fix (scope decision for you, then hand off):** either (a) accept "issue link when the issue ships" and just set the newport env keys, or (b) build the daily-event poster as a fourth task type (`social_script`) in schedule-tool + a `post_daily_event` job per repo (wasatch's CODEX brief is basically the spec). Flagging so the missing stage doesn't get discovered in November.

### P1-6. Published newsletter issues never reach the live site for alexandria/wasatch
**Files:** `alexandria .../jobs/write.py` `run()` (no deploy step), `alexandria/scripts/seo_daily.py` `COMMIT_PATHS = ("content", "site/guides", "site/sitemap.xml", "data/durability_verdicts.json")`, `alexandria/scripts/deploy_site.sh` (manual wrangler)

`write` publishes the issue HTML into `site/issues/` and updates the archive index — but nothing commits or deploys `site/issues/`: seo_daily deliberately stages only its own paths, and the wrangler deploy is a manual one-shot. So the site's "Current Issue"/archive quietly go stale. Newport solved this (its `deploy.py` commits `site/issues` and pushes, gated by `DEPLOY_ENABLED`, which IS set in newport's .env) — alexandria and wasatch never got the port.

**Fix:** port newport's `deploy_site(published)` git-commit-and-push step into alexandria's and wasatch's write jobs (or add `site/issues` + `site/index.html` to a deploy step the write job triggers).

### P1-7. Kit "publishing" stops at a draft, and the only notification is an email that often can't send
**Files:** all three `sender.py` (`"public": False`, no schedule call), write jobs' `send_email_alert("... draft ready", ...)`

By design the write job creates a **draft** broadcast for your review — fine — but the only signal a draft exists is an SMTP email, and P0-4 shows alert delivery is your least reliable component. If the notification fails, a perfectly good issue sits unnoticed in Kit. Also note alexandria's `sender.py` doesn't validate that the response contains an id (newport's `_unwrap_broadcast_response` does — another divergence).

**Fix:** persist a local "draft created: broadcast_id, subject, preview path" marker (and surface it in the ledger via task stdout), port newport's response validation to the other two, and fold alert-retry from P0-4.

---

## P2 — Drift, hygiene, and smaller traps

**P2-1. Three-way fork drift is the meta-problem.** `diff -rq` shows nearly **every shared module differs** across the three repos (llm.py, sheets.py, write.py, seo/*, collectors/*). Concrete casualties found in this review: the link-validation crash wasatch hit on 8/02 (`Draft contains links that are not present in the issue planner: goodnewsnetwork.org/...` — fatal write failure) was fixed *differently* in alexandria (`strip_unexpected_links`) and wasatch (`_allowed_draft_urls`), and newport never got Kit response validation while the others never got deploy. Every bug in this document must be fixed **three times**. Worth an explicit decision: keep forking (and maintain a fix-porting checklist per `MULTI-MARKET-PLAN.md`), or extract the genuinely shared core into one package with per-market config.

**P2-2. Stale queue/results/log accumulation with a retention config nobody reads.** `newsletters.yaml` has `logging.retention_days: 30` but nothing implements it; queue_*.json / results_*.json pile up in the schedule-tool root, and `daily_run.sh` picks the queue via `ls -t | head -1` — a manual/test queue generated between scheduler and runner would be picked instead. Fix: have scheduler print the queue path and daily_run use *that* (it already prints it — capture `LATEST_QUEUE="$(python3 scheduler.py | tail -1)"`), plus a cleanup pass honoring retention_days.

**P2-3. Old per-repo launch agents may still be loaded.** Each repo ships `scripts/install_launchd.py` installing `com.novathisweek.{collect,write,seo-publish}`, `com.stufftodoinutah.*`, `com.newportnewsletter.*` — from the pre-schedule-tool era. If any are still bootstrapped, jobs run twice via two different env setups. Verify: `launchctl list | grep -E 'novathisweek|stufftodoinutah|newportnewsletter|naylor'` and bootout the per-repo ones.

**P2-4. Config lives in two places and one of them is dead.** `newsletters.yaml`'s `ollama:` block (host/model/timeout) is embedded into each queue file but never consumed; the real values are hardcoded in `tasks/common.py` `DEFAULT_ENV` (`OLLAMA_MODEL=gemma4:26b`, `OLLAMA_TIMEOUT_SECONDS=1800`, providers forced to ollama). Changing the YAML model does nothing. Pick one source of truth (pass the queue's ollama block into the task env in `run_task`).

**P2-5. Naive datetimes throughout the scheduler.** `queue_runner.py` stamps `started_at`/`finished_at` with naive `datetime.now()` while the ledger uses UTC — harmless today, confusing in forensics, and ruff DTZ flags 25+ spots across repos (`scheduler.py:164`, `queue_runner.py:149,187,210,249,283`, engines' `dates.py:61` `date.today()` which uses *system* TZ rather than the newsletter's). The one that matters for content: anywhere `date.today()` feeds issue windows or SEO "today" (e.g. `publish.py:43`, `seo/build.py`) should use `ZoneInfo(settings.newsletter_timezone)` — wasatch runs 2 h behind ET, and a 5:00 ET run is 3:00 in Denver (fine now, wrong if you ever move slots near midnight).

**P2-6. events_dc date params look reversed.** `alexandria .../collectors/events_dc.py:20-21` sends `field_start_date_value = today + 550 days` and `field_end_date_value = today`. If the API treats these literally, the window is inverted; if it's a quirk that works, add a comment saying so — the 8/14 failure log surfaced the URL and it reads like a bug.

**P2-7. Secrets hygiene.** Ticketmaster API key appears in plaintext failure-log URLs (`logs/collect.log`); `.env` files sit in repos that the SEO job `git push`es from (seo_daily's explicit-paths staging is good protection — keep it that way, and confirm `.env` is in every `.gitignore`, including newport where `deploy.py` also commits). `queue_runner`'s failure alert emails include full task stdout — fine for you, just know creds in stdout would be mailed.

**P2-8. Unbounded caches.** `alexandria/data/cache/raw_inbox.json` is **43 MB** and growing; HTTP cache files have TTL for reads but nothing evicts old entries. Low urgency on a big SSD; worth a periodic prune.

**P2-9. `schedule-tool/config_loader.py` is a hand-rolled YAML subset.** It silently mis-parses anything beyond its subset (e.g., a quoted string containing `: `, block lists, comments after values are handled but nested lists aren't). PyYAML is already in every repo's venv; the custom parser only exists so system python3 can run the scheduler. Either `pip3 install pyyaml --user` and use the real thing, or add a startup validation that round-trips expected keys (a malformed edit currently degrades to "slot never matches" — another silent skip).

**P2-10. Harmless but flagged:** `jobs/collect.py:104` references undefined `RawInboxRow` in a nested annotation (alexandria + wasatch) — inert under `from __future__ import annotations`, but will crash if anything ever calls `get_type_hints` on it; import it properly. A few `f`-strings without placeholders and an unused loop var (`seo_publish.py:139`) — cosmetic.

---

## Verify-on-the-Mac checklist (5 minutes, before any code changes)

1. `launchctl print gui/$UID/com.naylor.newsletters` → is the schedule-tool agent actually loaded?
2. `launchctl list | grep -E 'novathisweek|stufftodoinutah|newportnewsletter'` → any zombie per-repo agents?
3. `rm /Volumes/SSD/Projects/alexandria-newsletter/logs/seo_publish.lock` (it's stale since 8/15 and blocking every alexandria SEO run until the 6 h staleness window).
4. `pmset -g` → check `sleep`/`womp`; add `sudo pmset repeat wakeorpoweron MTWRFSU 04:55:00`.
5. `ollama ls` → confirm `gemma4:26b` is present; `ollama ps` during a run to watch load/evict behavior.
6. Send yourself a test alert: `python3 -c "from alerts import send_failure_alert; send_failure_alert('test','test', env_file='/Volumes/SSD/Projects/alexandria-newsletter/.env')"` from schedule-tool.

## Suggested fix order (matches the ranking)

1. P0-1 wake-tolerant slot matching + agent verification (unblocks everything)
2. P0-2 + P0-3 queue_runner bytes crash, process-group kill, non-zero lock exits
3. P0-4 network preflight + alert retry queue
4. P1-1 + P1-2 Ollama schemas + num_ctx (fixes SEO output *and* most timeout pressure)
5. P0-5 per-task timeouts (re-tune after 4 lands)
6. P1-3 + P1-4 Sheets grid clamp, per-row curation validation
7. P1-6 + P1-7 issue deploy for alexandria/wasatch, Kit draft visibility
8. P1-5 social stage decision, then P2s opportunistically
