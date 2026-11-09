#!/usr/bin/env python3
"""Check that the README quick start is what the tool really prints.

    python3 examples/check_readme.py        # needs `instinct-delivery` on PATH

It plays examples/demo.sh and requires every line of the README's quick-start
transcript to appear, in order, in the demo's real output (stdout and stderr
together). A transcript line that ends in " ..." is an explicit elision: it must
match the start of an output line, and the rest of that line may differ.

The transcript is every ```console block in the "## Quick start" section. A
```sh block there, if one is added, must appear verbatim in examples/demo.sh.
The "Your first rule" block is exercised by tests/test_readme_first_rule.py, not here.

Exit codes: 0 the README matches, 1 it does not (each problem is printed),
2 it could not be checked (a file is missing, the demo would not run, or the
README has no transcript). Exit 2 is never a pass.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ELISION = " ..."
ROOT = Path(__file__).resolve().parents[1]


def clean_env(env) -> dict:
    """The environment the demo runs in: the caller's, without any INSTINCT_* setting (a rules
    or state directory of the caller would change what the transcript shows)."""
    return {k: v for k, v in env.items() if not k.startswith("INSTINCT_")}


def quick_start_blocks(readme: str) -> tuple[list[str], list[str]]:
    """(transcript lines, setup lines) from the Quick start section."""
    m = re.search(r"^## Quick start\s*$(.*?)(?=^## )", readme, re.M | re.S)
    if not m:
        raise ValueError("README has no '## Quick start' section")
    transcript: list[str] = []
    setup: list[str] = []
    for kind, body in re.findall(r"^```(\w*)\n(.*?)^```", m.group(1), re.M | re.S):
        if kind == "console":
            transcript += body.splitlines()
        elif kind == "sh":
            setup += body.splitlines()
    if not transcript:
        raise ValueError("Quick start has no ```console transcript")
    return transcript, setup


def problems(transcript: list[str], setup: list[str], demo_script: str,
             output: list[str]) -> list[str]:
    out: list[str] = []
    for line in setup:
        if line.strip() and line not in demo_script:
            out.append(f"setup line is not in demo.sh: {line}")
    pos = 0
    for line in transcript:
        if line.endswith(ELISION):
            prefix = line[: -len(ELISION)]
            hit = lambda real: real.startswith(prefix)   # noqa: E731
        else:
            hit = lambda real, line=line: real == line   # noqa: E731
        while pos < len(output) and not hit(output[pos]):
            pos += 1
        if pos == len(output):
            out.append(f"not in the demo's output, in order: {line}")
            pos = 0          # keep looking for the later lines on their own
        else:
            pos += 1
    return out


def main() -> int:
    try:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        demo_script = (ROOT / "examples" / "demo.sh").read_text(encoding="utf-8")
        transcript, setup = quick_start_blocks(readme)
        run = subprocess.run(["sh", str(ROOT / "examples" / "demo.sh")],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=120, env=clean_env(os.environ))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"check_readme: cannot check ({exc})", file=sys.stderr)
        return 2
    if run.returncode != 0:
        print(f"check_readme: demo.sh exited {run.returncode}, so there is no "
              f"output to compare\n{run.stdout}", file=sys.stderr)
        return 2
    bad = problems(transcript, setup, demo_script, run.stdout.splitlines())
    for p in bad:
        print(f"check_readme: {p}", file=sys.stderr)
    if bad:
        return 1
    print(f"check_readme: all {len(transcript)} transcript lines match the demo output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
