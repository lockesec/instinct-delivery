"""`install` registration: the hook command is absolute and independent of PATH
and of the working directory, our own command is recognised by exact equality and
nothing else, our entry stays separate from other tools' entries, older forms are
upgraded in place, and a second run changes nothing."""
import json
import os
import shlex
import signal
import subprocess
import sys

import pytest
from conftest import SRC, write_rule

from instinct_delivery import cli


def _registered(proj):
    d = json.loads((proj / ".claude" / "settings.json").read_text())
    return [h["command"] for e in d["hooks"]["PreToolUse"] for h in e["hooks"]]


posix_only = pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="POSIX only")


@posix_only
def test_the_registered_command_ignores_modules_in_the_working_directory(tmp_path):
    proj = tmp_path / "p"
    assert cli.main(["install", "--project", str(proj)]) == 0
    (cmd,) = _registered(proj)
    rules = tmp_path / "rules"
    write_rule(rules, "r", match_tools=["Edit"], action="the advice")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    marker = tmp_path / "SHADOW-RAN"
    for mod in ("yaml", "json", "signal", "re", "os", "shlex"):
        (hostile / f"{mod}.py").write_text(
            f"open({str(marker)!r}, 'a').write({mod!r})\nraise SystemExit(7)\n")
    env = dict(os.environ, PYTHONPATH=str(SRC), INSTINCT_RULES_DIR=str(rules),
               INSTINCT_STATE_DIR=str(tmp_path / "state"))
    payload = json.dumps({"session_id": "s", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}})
    p = subprocess.run(["sh", "-c", cmd], input=payload, capture_output=True, text=True,
                       cwd=hostile, env=env, timeout=60)
    assert not marker.exists(), f"project code ran in the hook: {marker.read_text()}"
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert "the advice" in out["hookSpecificOutput"]["additionalContext"]


def test_the_registered_command_is_absolute_and_not_path_dependent(tmp_path):
    proj = tmp_path / "p"
    cli.main(["install", "--project", str(proj)])
    (cmd,) = _registered(proj)
    argv = shlex.split(cmd)
    assert os.path.isabs(argv[0]) and argv[0] == sys.executable
    assert cli._is_ours(cmd)


@pytest.mark.parametrize("cmd", [
    "instinct-delivery hook",
    "/usr/bin/python3 -m instinct_delivery hook",
    "'/opt/my venv/bin/python3' -m instinct_delivery hook",
    "/opt/venv/bin/instinct-delivery hook",
    "'/opt/my env/bin/instinct-delivery' hook",
])
def test_is_ours_recognises_every_form_we_have_registered(cmd):
    assert cli._is_ours(cmd)


@pytest.mark.parametrize("cmd", ["echo hook", "my-linter hook", "python3 -m other hook", None, 3, ""])
def test_is_ours_leaves_other_hooks_alone(cmd):
    assert not cli._is_ours(cmd)


def _settings_with(proj, command):
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command", "command": command}]}]}}))


@pytest.mark.parametrize("old", [
    "instinct-delivery hook",
    "/usr/bin/python3 -m instinct_delivery hook",
    "/opt/venv/bin/instinct-delivery hook",
])
def test_install_upgrades_an_older_registration_in_place(tmp_path, capsys, old):
    proj = tmp_path / "p"
    _settings_with(proj, old)
    assert cli.main(["install", "--project", str(proj)]) == 0
    assert _registered(proj) == [cli.HOOK_COMMAND]
    assert "already registered" not in capsys.readouterr().out
    assert cli.main(["install", "--project", str(proj)]) == 0
    assert "already registered" in capsys.readouterr().out
    assert _registered(proj) == [cli.HOOK_COMMAND]


def test_install_collapses_duplicate_registrations_of_its_own(tmp_path):
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command", "command": "instinct-delivery hook"},
                                    {"type": "command", "command": "other-tool run"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/instinct-delivery hook"}]}]}}))
    assert cli.main(["install", "--project", str(proj)]) == 0
    cmds = _registered(proj)
    assert cmds.count(cli.HOOK_COMMAND) == 1 and "other-tool run" in cmds
    assert len([c for c in cmds if cli._is_ours(c)]) == 1


def test_is_ours_still_recognises_the_earlier_release_command():
    old = ("import os,sys;sys.path[:]=[p for p in sys.path if os.path.isabs(p)];"
           "from instinct_delivery.cli import main;sys.exit(main([\"hook\"]))")
    assert cli._is_ours(f"/usr/bin/python3 -c {shlex.quote(old)}")


@pytest.mark.parametrize("cmd", [
    "python3 -c 'print(1)'",
    "python3 -c 'import yaml' extra",
    "python3 -c 'from other.cli import main;main([\"hook\"])'",
])
def test_is_ours_leaves_other_dash_c_commands_alone(cmd):
    assert not cli._is_ours(cmd)


def _entries(proj):
    return json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


def _write(proj, doc):
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    p = proj / ".claude" / "settings.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_install_upgrades_an_earlier_release_command_in_place(tmp_path):
    old = ("import os,sys;sys.path[:]=[p for p in sys.path if os.path.isabs(p)];"
           "from instinct_delivery.cli import main;sys.exit(main([\"hook\"]))")
    _write(tmp_path, {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": f"/usr/bin/python3 -c {shlex.quote(old)}"}]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    [e] = _entries(tmp_path)
    assert [h["command"] for h in e["hooks"]] == [cli.HOOK_COMMAND]


def test_install_registers_the_running_interpreter_not_a_bare_name(tmp_path):
    proj = tmp_path / "p"
    assert cli.main(["install", "--project", str(proj)]) == 0
    (cmd,) = _registered(proj)
    argv = shlex.split(cmd)
    assert argv[0] == sys.executable and os.path.isabs(argv[0])
    assert argv[1:] == ["-c", cli.HOOK_CODE]       # hostile-cwd run: test_the_registered_command_ignores_modules_in_the_working_directory


def test_install_is_idempotent_and_recognises_the_old_bare_registration(tmp_path, capsys):
    proj = tmp_path / "p"
    cli.main(["install", "--project", str(proj)])
    cli.main(["install", "--project", str(proj)])
    assert len(_registered(proj)) == 1
    old = tmp_path / "q"
    (old / ".claude").mkdir(parents=True)
    (old / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command", "command": "instinct-delivery hook"}]}]}}))
    assert cli.main(["install", "--project", str(old)]) == 0
    assert "upgraded" in capsys.readouterr().out
    assert _registered(old) == [cli.HOOK_COMMAND]      # the bare form is upgraded in place


OLD = "instinct-delivery hook"


def _hook(cmd):
    return {"type": "command", "command": cmd}


def _pre(proj):
    return json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


@pytest.mark.parametrize("matchers", [(".*", ".*"), ("Edit", ".*"), (".*", "Edit"), ("Edit", "Bash")])
def test_duplicate_dedicated_entries_leave_one_current_command(tmp_path, matchers):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": m, "hooks": [_hook(OLD)]} for m in matchers]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    assert len(es) == 1 and es[0]["matcher"] == ".*"
    assert es[0]["hooks"] == [_hook(cli.HOOK_COMMAND)]
    assert not any(h["command"] == OLD for e in es for h in e["hooks"])


def _ours(es):
    return [e for e in es if any(cli._is_ours(h["command"]) for h in e["hooks"])]


def test_foreign_entries_survive_a_merge_in_place_and_order(tmp_path):
    foreign = {"matcher": "Bash", "hooks": [_hook("/usr/bin/guard")]}
    mixed = {"matcher": "Read", "hooks": [_hook(OLD), _hook("/usr/bin/other")]}
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [_hook(OLD)]}, foreign, mixed,
        {"matcher": ".*", "hooks": [_hook(OLD)]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    assert len(_ours(es)) == 1 and _ours(es)[0]["hooks"] == [_hook(cli.HOOK_COMMAND)]
    assert [e for e in es if not _ours([e])] == [
        foreign, {"matcher": "Read", "hooks": [_hook("/usr/bin/other")]}]


OURS = "instinct-delivery hook"


def _our_hooks(e):
    return [h for h in e["hooks"] if cli._is_ours(h["command"])]


def test_a_mixed_entry_keeps_its_matcher_and_foreign_hooks(tmp_path, capsys):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [_hook(OURS), _hook("/usr/bin/other-guard")]},
        {"matcher": "Edit", "hooks": [_hook(OURS)]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    foreign = [e for e in es if any(h["command"] == "/usr/bin/other-guard" for h in e["hooks"])]
    assert len(foreign) == 1 and foreign[0]["matcher"] == "Bash"
    assert foreign[0]["hooks"] == [_hook("/usr/bin/other-guard")]
    ded = [e for e in es if _our_hooks(e)]
    assert len(ded) == 1 and ded[0]["matcher"] == "Edit"     # the one dedicated entry, as the user left it
    assert ded[0]["hooks"] == [_hook(cli.HOOK_COMMAND)]
    out = capsys.readouterr().out
    assert "removed our hook from 1 mixed entry" in out and "left 1 other hook untouched" in out


def test_only_mixed_entries_get_one_new_dedicated_entry(tmp_path):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [_hook(OURS), _hook("other")]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    assert es[0] == {"matcher": "Bash", "hooks": [_hook("other")]}
    assert es[1] == {"matcher": ".*", "hooks": [_hook(cli.HOOK_COMMAND)]}
    assert len(es) == 2


def test_two_dedicated_entries_collapse_to_one_broad_entry(tmp_path):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [_hook(OURS)]},
        {"matcher": "Edit", "hooks": [_hook("/x/instinct-delivery hook")]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    [e] = _pre(tmp_path)
    assert e["matcher"] == ".*" and e["hooks"] == [_hook(cli.HOOK_COMMAND)]


def test_a_lone_dedicated_entry_keeps_a_hand_set_matcher(tmp_path):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "(Edit|Write)", "hooks": [_hook(OURS)]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    [e] = _pre(tmp_path)
    assert e["matcher"] == "(Edit|Write)" and e["hooks"] == [_hook(cli.HOOK_COMMAND)]


def test_regex_matchers_are_never_rewritten(tmp_path):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "Edit|Write", "hooks": [_hook(OURS)]},
        {"matcher": "mcp__.*", "hooks": [_hook("other"), _hook(OURS)]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    assert {e["matcher"] for e in es} == {"Edit|Write", "mcp__.*"}
    assert not hasattr(cli, "_merged_matcher")


def test_install_is_idempotent_after_a_redesign(tmp_path, capsys):
    _write(tmp_path, {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [_hook(OURS), _hook("other")]}]}})
    cli.main(["install", "--project", str(tmp_path)])
    first = _pre(tmp_path)
    capsys.readouterr()
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    assert "already registered" in capsys.readouterr().out
    assert _pre(tmp_path) == first


FOREIGN_C = ["python3", "-c", "import my_audit; my_audit.run()  # see instinct-delivery-hook docs"]


def test_a_foreign_dash_c_command_naming_the_marker_is_not_ours(tmp_path):
    cmd = shlex.join(FOREIGN_C)
    assert not cli._is_ours(cmd)
    _write(tmp_path, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [_hook(cmd)]}]}})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    es = _pre(tmp_path)
    assert es[0] == {"matcher": "Bash", "hooks": [_hook(cmd)]}
    assert len(es) == 2


@pytest.mark.parametrize("cmd", [
    "instinct-delivery hook --x",
    "./instinct-delivery hook",
    "python3 -c 'from instinct_delivery.cli import main;main([\"hook\"])'",
    "python3 -m instinct_delivery hook extra",
    "python3 -c 'import sys;from instinct_delivery.cli import main;sys.exit(main([\"hook\"]));#instinct-delivery-hook'",
])
def test_recognition_is_exact_not_a_substring(cmd):
    assert not cli._is_ours(cmd)


def test_every_released_hook_code_is_recognised():
    assert len(cli.HOOK_CODES) >= 2 and cli.HOOK_CODE in cli.HOOK_CODES
    for code in cli.HOOK_CODES:
        assert cli._is_ours(f"/usr/bin/python3 -c {shlex.quote(code)}")
    assert cli._is_ours(cli.HOOK_COMMAND)
    assert cli._is_ours("instinct-delivery hook")
    assert cli._is_ours("/opt/v/bin/instinct-delivery hook")
    assert cli._is_ours("/usr/bin/python3 -m instinct_delivery hook")


def test_install_does_not_say_upgraded_when_it_only_moved_out_of_a_mixed_entry(tmp_path, capsys):
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    mixed = {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool"},
                                          {"type": "command", "command": "instinct-delivery hook"}]}
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [mixed]}}))
    assert cli.main(["install", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "upgraded" not in out and "removed our hook from 1 mixed entry" in out
    assert cli.main(["install", "--project", str(proj)]) == 0
    assert "already registered" in capsys.readouterr().out


def test_install_still_says_upgraded_for_an_older_dedicated_entry(tmp_path, capsys):
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    ours = {"matcher": ".*", "hooks": [{"type": "command", "command": "instinct-delivery hook"}]}
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [ours]}}))
    assert cli.main(["install", "--project", str(proj)]) == 0
    assert "upgraded" in capsys.readouterr().out
