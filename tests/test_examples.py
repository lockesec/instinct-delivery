"""The example scripts run as documented, without inheriting the caller's
instinct environment."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_example_scripts_do_not_inherit_the_instinct_environment(tmp_path):
    mod = _load(ROOT / "examples" / "check_readme.py", "check_readme_x")
    env = mod.clean_env({"INSTINCT_RULES_DIR": "/x", "INSTINCT_STATE_DIR": "/y",
                         "INSTINCT_REPEAT": "full", "PATH": "/bin", "HOME": "/h"})
    assert env == {"PATH": "/bin", "HOME": "/h"}


def test_demo_sh_runs_clean_with_a_foreign_rules_dir_in_the_environment(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    bin_dir = Path(os.environ.get("VIRTUAL_ENV", ROOT / ".venv")) / "bin"
    if not (bin_dir / "instinct-delivery").exists():
        pytest.skip("instinct-delivery is not installed in this interpreter's venv")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", INSTINCT_RULES_DIR=str(foreign),
               INSTINCT_STATE_DIR=str(tmp_path / "poison"), TMPDIR=str(tmp_path))
    run = subprocess.run(["sh", str(ROOT / "examples" / "demo.sh")], env=env, capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not (tmp_path / "poison").exists()
