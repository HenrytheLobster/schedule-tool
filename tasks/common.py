from __future__ import annotations

import os
import subprocess
from pathlib import Path


DEFAULT_ENV = {
    "PYTHONPATH": "src",
    "LLM_PROVIDER": "ollama",
    "CURATION_PROVIDER": "ollama",
    "EXTRACTION_PROVIDER": "ollama",
    "WRITE_PROVIDER": "ollama",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "gemma4:26b",
    "OLLAMA_TIMEOUT_SECONDS": "1800",
}


def run_repo_module(repo_dir: str, module: str, *args: str) -> int:
    root = Path(repo_dir)
    python_bin = root / ".venv" / "bin" / "python"
    env = os.environ.copy()
    env.update(DEFAULT_ENV)
    command = [str(python_bin), "-m", module, *args]
    completed = subprocess.run(command, cwd=str(root), env=env, check=False)
    return completed.returncode


def run_repo_script(repo_dir: str, script_relpath: str, *args: str) -> int:
    root = Path(repo_dir)
    python_bin = root / ".venv" / "bin" / "python"
    env = os.environ.copy()
    env.update(DEFAULT_ENV)
    command = [str(python_bin), str(root / script_relpath), *args]
    completed = subprocess.run(command, cwd=str(root), env=env, check=False)
    return completed.returncode
