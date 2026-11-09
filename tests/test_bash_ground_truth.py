"""The reader against real bash: run each command in a throwaway folder and compare the files it
created with the writes the reader names."""
import os
import shutil
import subprocess

import pytest

from instinct_delivery.bashwrites import write_targets

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

SAME = ["echoes>f.py", "env X=1 ls > out.txt", '"FOO=1" echo a > f', 'FOO"x"=1 echo a > f', "ls ${x:-a>b} > f",
        "[[ a ]] [[ b > c ]] echo x", "cat <<EOF | nosuch > o\nbody\nEOF", "ls > f", ": > f", "exec > f",
        "if true; then :; fi > f", "for i in 1; do :; done > f", "cp a f", "mv a f", "ln -s a f",
        "dd if=a of=f status=none", "install a f", 'eval "echo a > f"', "bash -c 'echo a > f'", "sh -c 'echo a > f'",
        "bash -lc 'echo a > f'", "ls 2>&1", "ls >&2", "ls > /dev/null", "grep x a > /dev/null 2>&1", "test 3 > 2",
        "[ 3 > 2 ]", "a=$(ls > f)", "echo 'ls > f'", "nosuchcmd > f", "true && false > f",
        "case x in x) ls > f;; esac", "function g { ls > f; }; g", "$HOME/nosuch",
        "cp -at d a", "cp -vt d a", "cp -td a", "bash -o pipefail -c 'echo a > f'", "bash -O extglob -c 'echo a > f'",
        "bash -c -- 'echo a > f'", "timeout 5 bash -c 'echo a > f'", "timeout 5 ls > f", "install -d x y"]

# Known differences, stated in docs/rules.md: a stderr redirect is not read; a directory destination
# is named as the directory; a syntax error is read as written.
DIFFER = {"ls 2> f": ({"f"}, set()), "cp -t d a": ({"d/a"}, {"d"}), "cp a d/": ({"d/a"}, {"d/"}), "ls 3> f": ({"f"}, set()),
          "echo ((a > b))": (set(), {"b"})}


def _created(cmd, tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "d").mkdir()
    before = {"a", "d"}
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, timeout=20,
                   env={**os.environ, "HOME": str(tmp_path)})
    found = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")}
    return found - before


@pytest.mark.parametrize("cmd", SAME)
def test_the_reader_names_what_bash_creates(cmd, tmp_path):
    made = _created(cmd, tmp_path)
    named = {p for p, _ in write_targets(cmd)}
    if "-t" in cmd or "t d" in cmd:             # a -t destination is named as the directory
        assert {m.split("/")[0] for m in made} == named
    else:
        assert named == made


@pytest.mark.parametrize("cmd", DIFFER)
def test_the_known_differences_are_still_the_only_ones(cmd, tmp_path):
    bash, tool = DIFFER[cmd]
    assert _created(cmd, tmp_path) == bash and {p for p, _ in write_targets(cmd)} == tool
