"""The PreToolUse adapter and the command line."""
import io
import json

import pytest
from conftest import log_rows, write_rule

from instinct_delivery import cli, hook


def _hook(stdin_text, settings):
    out, err = io.StringIO(), io.StringIO()
    code = hook.main(io.StringIO(stdin_text), out, err, settings=settings)
    return code, out.getvalue(), err.getvalue()


def test_a_match_emits_exactly_the_context_envelope(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action="do the thing")
    code, out, _ = _hook(json.dumps({"tool_name": "Edit", "session_id": "s",
                                     "tool_input": {"file_path": "x"}}), settings)
    env = json.loads(out)
    assert code == 0
    assert env["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "INSTINCT ACTIVATED [a" in env["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("stdin", ["", "   ", "{not json", "[1,2]", '"text"', "null"])
def test_bad_input_is_exit_0_and_silent(settings, rules, stdin):
    write_rule(rules, "a", match_tools=["Edit"])
    assert _hook(stdin, settings) == (0, "", "")


def test_no_match_writes_no_stdout_at_all(settings, rules):
    write_rule(rules, "a", match_tools=["Bash"])
    code, out, _ = _hook(json.dumps({"tool_name": "Edit", "tool_input": {}}), settings)
    assert (code, out) == (0, "")


def test_an_engine_failure_is_exit_0_with_nothing_half_emitted(settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(hook, "run", boom)
    code, out, err = _hook(json.dumps({"tool_name": "Edit", "tool_input": {}}), settings)
    assert (code, out) == (0, "") and "engine down" in err


def test_the_session_comes_from_the_payload_only(settings, rules, monkeypatch):
    write_rule(rules, "a", match_tools=["Edit"])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-env")
    _hook(json.dumps({"tool_name": "Edit", "tool_input": {}}), settings)
    assert log_rows(settings)[0]["session_id"] is None


# ---------------------------------------------------------------- CLI

def test_doctor_names_every_rejection_and_the_over_budget_rule(rules, capsys):
    write_rule(rules, "ok", match_tools=["Edit"])
    write_rule(rules, "bad", match_tools=["Edit"], status="actve")
    write_rule(rules, "big", match_tools=["Edit"], action="w" * 20000)
    assert cli.main(["doctor", "--rules-dir", str(rules)]) == 1
    out = capsys.readouterr().out
    assert "REJECTED bad.yaml: unknown status" in out and "OVER BUDGET big.yaml" in out


def test_doctor_on_nothing_loaded_is_not_evaluable(rules, tmp_path, capsys):
    assert cli.main(["doctor", "--rules-dir", str(tmp_path / "none")]) == 2
    assert cli.main(["doctor", "--rules-dir", str(rules)]) == 2
    assert "NOT EVALUABLE" in capsys.readouterr().out


def test_a_dry_run_check_touches_no_real_state(rules, tmp_path, capsys):
    write_rule(rules, "a", match_tools=["Edit"])
    for _ in range(2):
        assert cli.main(["check", "Edit", "{}", "--rules-dir", str(rules), "--session", "s"]) == 0
    assert capsys.readouterr().out.count("INSTINCT ACTIVATED") == 2     # both "first" fires
    assert not (tmp_path / "state").exists()


def test_install_registers_once_and_refuses_a_broken_settings_file(tmp_path, capsys):
    proj = tmp_path / "p"
    assert cli.main(["install", "--project", str(proj)]) == 0
    assert cli.main(["install", "--project", str(proj)]) == 0
    data = json.loads((proj / ".claude" / "settings.json").read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert (proj / ".claude" / "instincts").is_dir()
    bad = tmp_path / "q" / ".claude"
    bad.mkdir(parents=True)
    (bad / "settings.json").write_text("{nope")
    assert cli.main(["install", "--project", str(tmp_path / "q")]) == 2
    assert (bad / "settings.json").read_text() == "{nope"


def test_install_keeps_existing_settings(tmp_path):
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}))
    cli.main(["install", "--project", str(proj)])
    data = json.loads((proj / ".claude" / "settings.json").read_text())
    assert data["model"] == "x" and len(data["hooks"]["PreToolUse"]) == 2


def test_usage_errors_exit_64(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["no-such-verb"])
    assert e.value.code == 64
