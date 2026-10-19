"""The command line and the hook under hostile environments: consoles that cannot
encode, non-UTF-8 arguments, a full or closed stdout, unexpandable directories,
and usage errors. Exit codes mean what the docs say and no path raises a
traceback."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write_rule

from instinct_delivery import cli, hook, state as st


def _run_hook(tmp_path, rules, stdout):
    write_rule(rules, "a", match_tools=["Edit"])
    env = dict(os.environ, INSTINCT_RULES_DIR=str(rules), INSTINCT_STATE_DIR=str(tmp_path / "st"),
               PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
    payload = json.dumps({"tool_name": "Edit", "session_id": "s", "tool_input": {"file_path": "x"}})
    return subprocess.Popen([sys.executable, "-m", "instinct_delivery", "hook"], stdin=subprocess.PIPE,
                            stdout=stdout, stderr=subprocess.PIPE, env=env), payload


@pytest.mark.skipif(not os.path.exists("/dev/full"), reason="no /dev/full")
def test_a_full_stdout_still_exits_0(tmp_path, rules):
    with open("/dev/full", "wb") as full:
        p, payload = _run_hook(tmp_path, rules, full)
        _, err = p.communicate(payload.encode(), timeout=30)
    assert p.returncode == 0, err
    assert b"nothing was recorded as delivered" in err


def test_a_closed_pipe_still_exits_0(tmp_path, rules):
    p, payload = _run_hook(tmp_path, rules, subprocess.PIPE)
    p.stdout.close()
    _, err = p.communicate(payload.encode(), timeout=30)
    assert p.returncode == 0, err


def test_an_unexpandable_rules_dir_is_exit_2_with_a_message(capsys, tmp_path):
    for verb in ("doctor", "breadth"):
        argv = [verb, "--state-dir", str(tmp_path / "s")]
        if verb == "breadth":
            argv += ["--transcripts", str(tmp_path)]
        os.environ["INSTINCT_RULES_DIR"] = "~nosuchuser_zz/x"
        try:
            assert cli.main(argv) == 2
        finally:
            del os.environ["INSTINCT_RULES_DIR"]
        out = capsys.readouterr().out
        assert "NOT EVALUABLE" in out and "INSTINCT_RULES_DIR" in out and "Traceback" not in out


def test_an_unexpandable_state_dir_is_exit_2_for_commands(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("INSTINCT_STATE_DIR", "~nosuchuser_zz/x")
    assert cli.main(["cost"]) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "INSTINCT_STATE_DIR" in out


def test_the_hook_stays_exit_0_on_an_unexpandable_dir(monkeypatch, tmp_path):
    import io
    monkeypatch.setenv("INSTINCT_RULES_DIR", "~nosuchuser_zz/x")
    monkeypatch.setenv("INSTINCT_STATE_DIR", "~nosuchuser_zz/y")
    out, err = io.StringIO(), io.StringIO()
    code = hook.main(stdin=io.StringIO(json.dumps({"tool_name": "Edit", "tool_input": {}})),
                     stdout=out, stderr=err)
    assert code == 0 and out.getvalue() == ""


ASCII = {"PYTHONIOENCODING": "ascii"}


C_LOCALE = {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}


ROOT = Path(__file__).resolve().parents[1]


SRC = ROOT / "src"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("INSTINCT_", "PYTHON", "LC_", "LANG"))}
    env.update(PYTHONPATH=str(SRC), INSTINCT_STATE_DIR=str(tmp_path / "state"),
               HOME=str(tmp_path / "home"), **extra)
    return env


def _cli(tmp_path, args, **env):
    return subprocess.run([sys.executable, "-m", "instinct_delivery", *args], env=_env(tmp_path, **env),
                          capture_output=True, cwd=tmp_path, timeout=60)


@pytest.mark.parametrize("enc", [ASCII, C_LOCALE], ids=["ascii", "C-locale"])
def test_a_report_on_a_missing_log_does_not_crash_on_a_dash(tmp_path, enc):
    r = _cli(tmp_path, ["repeat-rate", "--log", str(tmp_path / "none.jsonl")], **enc)
    assert r.returncode == 2, r
    assert b"Traceback" not in r.stderr and b"NOT EVALUABLE" in r.stdout


def _rule_dir(tmp_path):
    d = tmp_path / "rules"
    write_rule(d, "np", match_tools=["Edit"], match_content=["print"], domain="d", confidence=0.5)
    return d


@pytest.mark.parametrize("enc", [ASCII, C_LOCALE], ids=["ascii", "C-locale"])
def test_doctor_and_check_survive_a_console_that_cannot_encode(tmp_path, enc):
    rd = _rule_dir(tmp_path)
    r = _cli(tmp_path, ["doctor", "--rules-dir", str(rd)], **enc)
    assert r.returncode == 0 and b"Traceback" not in r.stderr and b"1 firing" in r.stdout
    r = _cli(tmp_path, ["doctor", "--rules-dir", str(tmp_path / "gone")], **enc)
    assert r.returncode == 2 and b"Traceback" not in r.stderr
    r = _cli(tmp_path, ["check", "--rules-dir", str(rd), "Edit",
                        '{"file_path":"a.py","new_string":"print(1)"}'], **enc)
    assert r.returncode == 0 and b"Traceback" not in r.stderr
    assert b"INSTINCT" in r.stdout and b"np" in r.stdout            # delivered, with the marks escaped
    assert r.stdout.isascii()


def test_a_utf8_console_still_gets_the_real_characters(tmp_path):
    rd = _rule_dir(tmp_path)
    r = _cli(tmp_path, ["check", "--rules-dir", str(rd), "Edit",
                        '{"file_path":"a.py","new_string":"print(1)"}'], PYTHONIOENCODING="utf-8")
    assert "⚠️".encode() in r.stdout


def test_a_non_utf8_byte_in_an_argument_does_not_crash_doctor(tmp_path):
    bad = os.fsencode(str(tmp_path)) + b"/\xff"
    r = subprocess.run([sys.executable, "-m", "instinct_delivery", "doctor", "--rules-dir", bad],
                       env=_env(tmp_path, PYTHONIOENCODING="utf-8"), capture_output=True, timeout=60)
    assert r.returncode == 2 and b"Traceback" not in r.stderr
    assert b"NOT EVALUABLE" in r.stdout and b"\\udcff" in r.stdout


def test_install_into_a_non_utf8_directory_exits_0_after_writing(tmp_path):
    proj = os.fsencode(str(tmp_path)) + b"/p\xff"
    os.mkdir(proj)
    r = subprocess.run([sys.executable, "-m", "instinct_delivery", "install", "--project", proj],
                       env=_env(tmp_path, PYTHONIOENCODING="utf-8"), capture_output=True, timeout=60)
    assert (Path(os.fsdecode(proj)) / ".claude" / "settings.json").exists()
    assert r.returncode == 0 and b"Traceback" not in r.stderr, r
    assert b"registered" in r.stdout


def test_a_report_whose_print_fails_is_exit_2_never_1(tmp_path, monkeypatch, capsys):
    log = tmp_path / "state" / "activations.jsonl"
    log.parent.mkdir()
    log.write_text(json.dumps({"ts": st.now_iso(), "rule": "a.yaml", "session_id": "s", "delivery": "full",
                               "bytes": 5}) + "\n")

    def boom(*a, **k):
        raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")
    monkeypatch.setattr(cli, "_print", boom)
    assert cli.main(["repeat-rate", "--log", str(log)]) == 2
    assert "NOT EVALUABLE" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["check", "--json", "Edit", "{}"], ["doctor", "--json"]])
def test_json_is_rejected_where_it_does_nothing(args, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 64
    assert "--json" in capsys.readouterr().err


def test_json_is_still_honoured_where_it_works(tmp_path, capsys):
    log = tmp_path / "l.jsonl"
    log.write_text(json.dumps({"ts": st.now_iso(), "rule": "a.yaml", "session_id": "s",
                               "delivery": "full", "bytes": 5}) + "\n")
    assert cli.main(["repeat-rate", "--log", str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1


def test_state_dir_on_a_dry_run_check_is_a_usage_error(tmp_path, capsys):
    rd = _rule_dir(tmp_path)
    assert cli.main(["check", "--rules-dir", str(rd), "--state-dir", str(tmp_path / "sd"), "Edit", "{}"]) == 64
    assert "--state-dir" in capsys.readouterr().err
    assert not (tmp_path / "sd").exists()


def test_state_dir_with_record_writes_there_and_record_excludes_scratch(tmp_path, capsys):
    rd = _rule_dir(tmp_path)
    sd = tmp_path / "sd"
    assert cli.main(["check", "--rules-dir", str(rd), "--state-dir", str(sd), "--record", "--session", "s",
                     "Edit", '{"file_path":"a.py","new_string":"print(1)"}']) == 0
    assert (sd / "activations.jsonl").exists()
    assert cli.main(["check", "--rules-dir", str(rd), "--record", "--scratch-state", str(tmp_path / "x"),
                     "Edit", "{}"]) == 64


def test_check_names_an_input_that_is_not_an_object(tmp_path, rules, capsys):
    write_rule(rules, "r", match_tools=["Edit"])
    code = cli.main(["check", "Edit", "[1,2]", "--rules-dir", str(rules)])
    cap = capsys.readouterr()
    assert code == 0 and "not a JSON object" in cap.err and "list" in cap.err


def test_check_names_a_tool_no_rule_mentions(tmp_path, rules, capsys):
    write_rule(rules, "r", match_tools=["Edit"])
    code = cli.main(["check", "Bsh", "{}", "--rules-dir", str(rules)])
    cap = capsys.readouterr()
    assert code == 0 and "Bsh" in cap.err and "no loaded rule" in cap.err and "Edit" in cap.err
    assert cap.out == ""


def test_quiet_direction_a_scoped_tool_and_an_object_input_say_nothing_extra(tmp_path, rules, capsys):
    write_rule(rules, "r", match_tools=["Edit"], match_content=["zzz"])
    assert cli.main(["check", "Edit", '{"new_string":"a"}', "--rules-dir", str(rules)]) == 0
    assert capsys.readouterr().err == ""
    # Bash can reach a rule scoped to Write through a write it makes
    write_rule(rules, "w", match_tools=["Write"], match_content=["zzz"])
    assert cli.main(["check", "Bash", '{"command":"ls"}', "--rules-dir", str(rules)]) == 0
    assert capsys.readouterr().err == ""
