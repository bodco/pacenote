"""Scratch files (run logs, patches, merge leftovers) never get tracked in the repo."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRATCH = (".tmp", ".patch", ".orig", ".rej")


def test_no_scratch_files_are_tracked():
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    here = Path(__file__).resolve().parent
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                         capture_output=True, text=True)
    if top.returncode != 0:
        pytest.skip("not inside a git checkout")
    files = subprocess.run(["git", "ls-files"], cwd=top.stdout.strip(),
                           capture_output=True, text=True, check=True).stdout.splitlines()
    assert [name for name in files if name.endswith(SCRATCH)] == []
