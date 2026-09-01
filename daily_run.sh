#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MEM_SAVE_SCRIPT="${MEM_SAVE_SCRIPT:-/Volumes/SSD/Projects/mem-save/mem-save.sh}"
MEM_SAVE_TIMEOUT_SECONDS="${MEM_SAVE_TIMEOUT_SECONDS:-120}"
MEM_SAVE_MODE="${MEM_SAVE_MODE:---force}"

run_mem_save_prereq() {
  if [[ ! -x "$MEM_SAVE_SCRIPT" ]]; then
    echo "mem-save prerequisite skipped: not executable at $MEM_SAVE_SCRIPT"
    return 0
  fi

  typeset -a mem_save_cmd
  mem_save_cmd=("$MEM_SAVE_SCRIPT")
  [[ -n "$MEM_SAVE_MODE" ]] && mem_save_cmd+=("$MEM_SAVE_MODE")

  /usr/bin/perl -e '
    use strict;
    use warnings;
    my ($timeout, @cmd) = @ARGV;
    my $pid = fork();
    die "fork failed: $!" unless defined $pid;
    if ($pid == 0) {
      setpgrp(0, 0);
      exec @cmd;
      die "exec failed: $!";
    }
    my $timed_out = 0;
    local $SIG{ALRM} = sub {
      $timed_out = 1;
      kill "TERM", -$pid;
      select undef, undef, undef, 0.5;
      kill "KILL", -$pid;
    };
    alarm $timeout;
    waitpid($pid, 0);
    alarm 0;
    exit 124 if $timed_out;
    exit(($? >> 8) & 255);
  ' "$MEM_SAVE_TIMEOUT_SECONDS" "${mem_save_cmd[@]}" || {
    rc=$?
    echo "mem-save prerequisite returned $rc; continuing with scheduler."
  }
}

run_mem_save_prereq

if [[ "${DAILY_RUN_STOP_AFTER_MEM_SAVE:-0}" == "1" ]]; then
  echo "DAILY_RUN_STOP_AFTER_MEM_SAVE=1; scheduler.py would run next."
  exit 0
fi

python3 "$SCRIPT_DIR/scheduler.py"

LATEST_QUEUE="$(ls -t "$SCRIPT_DIR"/queue_*.json 2>/dev/null | head -n 1 || true)"

if [[ -z "${LATEST_QUEUE}" ]]; then
  echo "No queue file found after scheduler run."
  exit 1
fi

python3 "$SCRIPT_DIR/queue_runner.py" "$LATEST_QUEUE"
