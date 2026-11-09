"""Shared fixtures. Every test runs with its own rules dir and state dir; the
real user state is never touched (INSTINCT_STATE_DIR points into tmp)."""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from instinct_delivery.engine import Settings  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "instincts"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    for k in ("INSTINCT_REPEAT", "INSTINCT_REARM_MIN", "INSTINCT_BRIEF_CAP",
              "INSTINCT_PAYLOAD_BUDGET", "INSTINCT_SHADOW", "INSTINCT_BASH_EDITS",
              "INSTINCT_RULES_DIR", "CLAUDE_PROJECT_DIR", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSTINCT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def write_rule(d: Path, name: str, **fields) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    doc = {"trigger": fields.pop("trigger", "t"), "action": fields.pop("action", f"advice {name}")}
    doc.update(fields)
    p = d / (name if name.endswith(".yaml") else name + ".yaml")
    p.write_text(json.dumps(doc))          # JSON is YAML
    return p


@pytest.fixture
def rules(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    return d


@pytest.fixture
def settings(tmp_path, rules):
    return Settings(rules_dir=rules, state_dir=tmp_path / "state")


def log_rows(settings):
    p = settings.state_dir / "activations.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
