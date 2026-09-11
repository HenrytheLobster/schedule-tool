#!/usr/bin/env python3
"""Free the local LLM's memory, then bring ComfyUI up and wait until it can
actually render.

Image generation moved off the Windows box (192.168.86.29) on 2026-09-06.
That box had to be awake for every run, and its GPU reset every day or two
(nvlddmkm 153), which killed the CUDA context while ComfyUI kept answering
HTTP -- so `/system_stats` threw, the 3s probe read it as unreachable, and
the newsletter hero image failed 4 times out of 4 without ever alerting.

Both models cannot be resident at once. gemma4:26b is ~26 GB and FLUX plus
the T5 encoder is ~12 GB, on a 24 GB machine. They do not overlap in the
schedule -- `curate` is the only ollama consumer, and `draft_issue` is
claude_cli -- but ollama's keep_alive holds the weights for ~5 minutes after
its last call, and on 2026-09-02 curate's final call was 04:29:26 with write
starting at 04:29:33. Seven seconds. So this unloads ollama explicitly rather
than trusting the gap.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")

COMFY_DIR = "/Volumes/SSD/ComfyUI"
COMFY_PYTHON = f"{COMFY_DIR}/venv/bin/python"
COMFY_MAIN = f"{COMFY_DIR}/main.py"
ENDPOINT = "http://127.0.0.1:8188"
LOG_PATH = "/tmp/comfyui-scheduled.log"
OLLAMA_MODEL = "gemma4:26b"

# Cold start loads no weights, so binding is quick; the models are read on the
# first prompt. 180s is slack for a busy disk, not an expected wait.
READY_TIMEOUT_SECONDS = 180
POLL_SECONDS = 3

# The four files build_flux_workflow() names. A ComfyUI that is up but cannot
# see these renders nothing, and we would rather fail here -- loudly, before
# the newsletter is written -- than have every image report generation_failed.
REQUIRED = {
    "UnetLoaderGGUF": ("unet_name", "flux1-dev-Q4_K_S.gguf"),
    "VAELoader": ("vae_name", "ae.safetensors"),
}
REQUIRED_CLIP = ("clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors")


def _get(path: str, timeout: int = 10):
    with urllib.request.urlopen(f"{ENDPOINT}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _already_up() -> bool:
    try:
        _get("/system_stats", timeout=5)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _ollama_bin() -> str | None:
    """Absolute path, because a scheduled run gets a bare PATH.

    launchd hands these tasks /usr/bin:/bin:/usr/sbin:/sbin with no
    /usr/local/bin, so a bare "ollama" is simply not found -- the same class
    of silent degradation that sent a write job to the wrong model on
    2026-08-28. Better to know we could not unload than to assume we did.
    """
    for candidate in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"):
        if shutil.which(candidate):
            return candidate
    return shutil.which("ollama")


def _unload_ollama() -> None:
    """Drop gemma4:26b now instead of waiting out keep_alive."""
    binary = _ollama_bin()
    if binary is None:
        print("WARNING: ollama binary not found; cannot unload gemma4:26b")
        return
    result = subprocess.run(
        [binary, "stop", OLLAMA_MODEL],
        capture_output=True,
        text=True,
        check=False,
    )
    # A model that was not loaded is the good case, and `ollama stop` says so
    # on stderr with a non-zero exit. Not an error worth failing the slot for.
    #
    # The spinner writes ANSI cursor codes even when stdout is a pipe, which
    # turns the scheduler log into escape soup. Strip them.
    detail = _ANSI.sub("", (result.stderr or result.stdout)).strip()
    print(f"ollama stop {OLLAMA_MODEL}: rc={result.returncode} {detail}".strip())


def _start_comfy() -> None:
    with open(LOG_PATH, "ab", buffering=0) as log:
        subprocess.Popen(
            [COMFY_PYTHON, COMFY_MAIN, "--listen", "127.0.0.1", "--port", "8188"],
            cwd=COMFY_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _models_visible() -> tuple[bool, str]:
    for node, (field, wanted) in REQUIRED.items():
        try:
            info = _get(f"/object_info/{node}")
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            return False, f"{node} unavailable: {exc}"
        try:
            available = info[node]["input"]["required"][field][0]
        except (KeyError, IndexError, TypeError):
            return False, f"{node} has no {field} list"
        if wanted not in available:
            return False, f"{node} cannot see {wanted}"

    try:
        clip = _get("/object_info/DualCLIPLoader")
        names = clip["DualCLIPLoader"]["input"]["required"]["clip_name1"][0]
    except Exception as exc:  # noqa: BLE001
        return False, f"DualCLIPLoader unavailable: {exc}"
    missing = [n for n in REQUIRED_CLIP if n not in names]
    if missing:
        return False, f"DualCLIPLoader cannot see {', '.join(missing)}"

    return True, "all four FLUX models visible"


def main() -> int:
    _unload_ollama()

    if _already_up():
        print("ComfyUI already listening; reusing it")
    else:
        _start_comfy()
        print(f"ComfyUI starting, log at {LOG_PATH}")

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last = "never responded"
    while time.monotonic() < deadline:
        if _already_up():
            ok, last = _models_visible()
            if ok:
                print(f"ComfyUI ready at {ENDPOINT}: {last}")
                return 0
        time.sleep(POLL_SECONDS)

    print(
        f"ComfyUI not ready after {READY_TIMEOUT_SECONDS}s: {last}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
