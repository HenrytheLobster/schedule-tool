#!/bin/zsh
# Boot-volume entrypoint for com.naylor.newsletters.
#
# macOS launchd can refuse to spawn (posix_spawn EPERM -> exit 78 EX_CONFIG)
# when a job's program arguments / working directory / log paths live on an
# external volume. This wrapper keeps every launchd touchpoint on the boot
# volume and hops over to the SSD once the process is actually running.
# It also waits for the SSD to mount, which protects the 5:00 run after a
# reboot when volumes can come up after login.
set -euo pipefail

for i in {1..30}; do
  if [[ -d /Volumes/SSD/Projects/schedule-tool ]]; then
    break
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') waiting for /Volumes/SSD to mount (attempt $i/30)"
  sleep 10
done

if [[ ! -d /Volumes/SSD/Projects/schedule-tool ]]; then
  echo "FATAL: /Volumes/SSD/Projects/schedule-tool never appeared; aborting."
  exit 1
fi

cd /Volumes/SSD/Projects/schedule-tool
exec /bin/zsh ./daily_run.sh
