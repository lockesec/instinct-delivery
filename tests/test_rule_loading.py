"""Rule loading: a rule file is loaded whole or rejected with its cause, one bad file
costs exactly itself, hostile or oversized input is refused fast and small, and
`doctor`/`check` name an unreadable rules directory instead of reporting a zero."""
import io
import json
import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest
from conftest import write_rule

from instinct_delivery import cli, hook, rules as rules_mod
from instinct_delivery.engine import Settings, run
from instinct_delivery.rules import load_rules


def test_doctor_on_a_missing_rules_dir_names_the_absolute_path_and_the_fix(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor"]) == 2
    out = capsys.readouterr().out
    assert str(tmp_path / ".claude" / "instincts") in out
    assert "CLAUDE_PROJECT_DIR" in out and "INSTINCT_RULES_DIR" in out


def test_loader_rejects_a_huge_integer_confidence_and_keeps_valid_rules(rules):
    write_rule(rules, "good", match_tools=["Edit"], action="good advice")
    write_rule(rules, "bad", match_tools=["Edit"], confidence=10**400)
    rs = load_rules(rules)
    assert [r.file for r in rs.fire] == ["good.yaml"]
    assert [r.file for r in rs.rejected] == ["bad.yaml"]


EDIT = {"file_path": "src/m.py", "new_string": "x"}


def test_hook_still_delivers_the_valid_rule(settings, rules):
    write_rule(rules, "good", match_tools=["Edit"], action="good advice")
    write_rule(rules, "bad", match_tools=["Edit"], confidence=10**400)
    out, err = io.StringIO(), io.StringIO()
    payload = json.dumps({"tool_name": "Edit", "tool_input": EDIT, "session_id": "z"})
    assert hook.main(stdin=io.StringIO(payload), stdout=out, stderr=err, settings=settings) == 0
    assert "good advice" in out.getvalue()
    assert "engine failed" not in err.getvalue()


def test_doctor_names_the_rejected_rule(settings, rules, capsys):
    write_rule(rules, "good", match_tools=["Edit"], action="good advice")
    write_rule(rules, "bad", match_tools=["Edit"], confidence=10**400)
    cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(settings.state_dir)])
    assert "REJECTED bad.yaml" in capsys.readouterr().out


def test_control_ordinary_and_large_finite_confidence_load(rules):
    write_rule(rules, "a", match_tools=["Edit"], confidence=0.9)
    write_rule(rules, "b", match_tools=["Edit"], confidence=10**300)
    rs = load_rules(rules)
    assert len(rs.fire) == 2 and not rs.rejected


def _alias_bomb():
    lines = ["trigger: t", "action: a", "match_tools: [Edit]", "z0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 8):
        lines.append(f"z{i}: &a{i} [" + ", ".join([f"*a{i-1}"] * 10) + "]")
    lines.append("domain: *a7")
    return "\n".join(lines) + "\n"


BAD_FILES = {
    "deepre.yaml": lambda: json.dumps({"trigger": "t", "action": "a", "match_tools": ["Edit"],
                                       "match_content": ["(" * 3000 + "a" + ")" * 3000]}),
    "deepyaml.yaml": lambda: ("trigger: t\naction: a\nmatch_tools: [Edit]\ndomain: "
                              + "[" * 5000 + "]" * 5000 + "\n"),
    "bomb.yaml": _alias_bomb,
}


def _raw(rules, name, text):
    rules.mkdir(parents=True, exist_ok=True)
    (rules / name).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("bad", sorted(BAD_FILES))
def test_one_bad_rule_file_is_rejected_and_the_good_rule_still_loads(rules, bad):
    write_rule(rules, "good", match_tools=["Edit"])
    _raw(rules, bad, BAD_FILES[bad]())
    rs = load_rules(rules)
    assert [r.file for r in rs.fire] == ["good.yaml"]
    assert [r.file for r in rs.rejected] == [bad]
    assert rs.rejected[0].reason and len(rs.rejected[0].reason) < 400


@pytest.mark.parametrize("bad", sorted(BAD_FILES))
def test_the_hook_still_delivers_the_good_rule(settings, rules, bad, capsys):
    write_rule(rules, "good", match_tools=["Edit"], action="GOODADVICE")
    _raw(rules, bad, BAD_FILES[bad]())
    res = run("Edit", {"file_path": "a.py", "new_string": "x"}, "s1", settings=settings)
    assert "GOODADVICE" in res.text


@pytest.mark.parametrize("bad", sorted(BAD_FILES))
def test_doctor_reports_each_and_exits_1_without_a_traceback(rules, bad, capsys, monkeypatch, tmp_path):
    write_rule(rules, "good", match_tools=["Edit"])
    _raw(rules, bad, BAD_FILES[bad]())
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(tmp_path / "st")])
    out = capsys.readouterr().out
    assert code == 1 and f"REJECTED {bad}" in out and "Traceback" not in out


def test_an_alias_bomb_is_rejected_fast_and_small(rules):
    write_rule(rules, "good", match_tools=["Edit"])
    _raw(rules, "bomb.yaml", _alias_bomb())
    tracemalloc.start()
    t0 = time.monotonic()
    rs = load_rules(rules)
    took = time.monotonic() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert took < 0.5 and peak < 32 * 1024 * 1024
    assert [r.file for r in rs.rejected] == ["bomb.yaml"]
    assert "alias" in rs.rejected[0].reason.lower()


def test_an_anchor_without_an_alias_is_refused_too(rules):
    _raw(rules, "anch.yaml", "trigger: &t t\naction: a\nmatch_tools: [Edit]\n")
    [rej] = load_rules(rules).rejected
    assert "anchor" in rej.reason.lower() or "alias" in rej.reason.lower()


def test_a_huge_bad_value_is_not_echoed_whole(rules):
    _raw(rules, "big.yaml", "trigger: t\naction: a\nmatch_tools: [Edit]\nmatch_content: ["
                            + "'" + "(" * 200000 + "'" + "]\n")
    [rej] = load_rules(rules).rejected
    assert len(rej.reason) < 500


def test_a_huge_wrong_typed_confidence_is_not_echoed_whole(rules):
    _raw(rules, "conf.yaml", "trigger: t\naction: a\nmatch_tools: [Edit]\nconfidence: '"
                             + "x" * 200000 + "'\n")
    [rej] = load_rules(rules).rejected
    assert len(rej.reason) < 500


def _big_rule(i, size=240_000):
    pats = "".join(f"  - 'foo{n}[a-z]+bar(x|y)\\d{{2}}'\n" for n in range(size // 33))
    return "trigger: t\nmatch_tools: [Edit]\naction: a\nmatch_content:\n" + pats


def test_forty_large_legitimate_files_load_inside_a_time_bound(rules):
    for i in range(40):
        _raw(rules, f"big{i:02d}.yaml", _big_rule(i))
    t = time.monotonic()
    rs = load_rules(rules)
    took = time.monotonic() - t
    assert took < 6, f"loading took {took:.1f}s"
    assert len(rs.fire) < 40 and rs.rejected          # the excess is rejected, not silently dropped


def test_files_past_the_total_byte_cap_are_rejected_with_the_cap_named(rules, monkeypatch):
    monkeypatch.setattr(rules_mod, "MAX_TOTAL_BYTES", 200)
    for i in range(5):
        write_rule(rules, f"r{i}", match_tools=["Edit"])
    rs = load_rules(rules)
    assert 0 < len(rs.fire) < 5
    assert len(rs.fire) + len(rs.rejected) == 5
    for r in rs.rejected:
        assert "total size" in r.reason and "200" in r.reason and "not loaded" in r.reason


def test_files_past_the_count_cap_are_rejected_in_one_named_entry(rules, monkeypatch):
    monkeypatch.setattr(rules_mod, "MAX_RULE_FILES", 3)
    for i in range(8):
        write_rule(rules, f"r{i}", match_tools=["Edit"])
    rs = load_rules(rules)
    assert [r.file for r in rs.fire] == ["r0.yaml", "r1.yaml", "r2.yaml"]
    [r] = rs.rejected
    assert "5 more" in r.file + r.reason and "3" in r.reason and "not loaded" in r.reason


def test_doctor_names_the_cap_and_exits_1(rules, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(rules_mod, "MAX_RULE_FILES", 1)
    write_rule(rules, "a", match_tools=["Edit"])
    write_rule(rules, "b", match_tools=["Edit"])
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert code == 1 and "REJECTED" in out and "not loaded" in out


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_doctor_names_a_permission_error_on_the_rules_dir(tmp_path, capsys):
    d = tmp_path / "rules"
    d.mkdir()
    write_rule(d, "a", match_tools=["Edit"])
    d.chmod(0)
    try:
        code = cli.main(["doctor", "--rules-dir", str(d), "--state-dir", str(tmp_path / "s")])
    finally:
        d.chmod(0o700)
    out = capsys.readouterr().out
    assert code == 2
    assert "NOT EVALUABLE" in out and "Permission denied" in out
    assert "0 rules loaded" not in out and "0 firing" not in out


def test_doctor_says_what_a_non_directory_rules_path_is(tmp_path, capsys):
    f = tmp_path / "rules"
    f.write_text("x")
    assert cli.main(["doctor", "--rules-dir", str(f), "--state-dir", str(tmp_path / "s")]) == 2
    out = capsys.readouterr().out
    assert "not a directory" in out and "regular file" in out and "does not exist" not in out
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert cli.main(["doctor", "--rules-dir", str(fifo), "--state-dir", str(tmp_path / "s")]) == 2
    out = capsys.readouterr().out
    assert "not a directory" in out and "FIFO" in out and "does not exist" not in out


def test_load_rules_never_raises_on_an_unreadable_dir(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    rs = load_rules(f)
    assert rs.dir_problem and not rs.fire


@pytest.mark.parametrize("key", ["domain", "match_paths", "match_content", "match_commands",
                                 "match_exclude"])
def test_a_lone_surrogate_in_any_text_field_rejects_the_file(rules, key):
    _raw(rules, "bad.yaml",
         'trigger: t\naction: a\nmatch_tools: [Edit]\n' + f'{key}: "x\\ud800y"\n')
    write_rule(rules, "good", match_tools=["Edit"])
    rs = load_rules(rules)
    assert [r.file for r in rs.fire] == ["good.yaml"]
    [bad] = rs.rejected
    assert bad.file == "bad.yaml" and key in bad.reason and "Unicode" in bad.reason


def test_check_on_a_missing_rules_dir_is_not_evaluable_with_the_cause(tmp_path, capsys):
    code = cli.main(["check", "--rules-dir", str(tmp_path / "gone"), "Edit", "{}"])
    cap = capsys.readouterr()
    assert code == 2 and cap.out == ""
    assert "NOT EVALUABLE" in cap.err and "does not exist" in cap.err and "gone" in cap.err


def test_check_on_a_file_instead_of_a_directory_says_what_it_is(tmp_path, capsys):
    f = tmp_path / "afile"
    f.write_text("x")
    assert cli.main(["check", "--rules-dir", str(f), "Edit", "{}"]) == 2
    err = capsys.readouterr().err
    assert "NOT EVALUABLE" in err and "not a directory" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_check_on_an_unreadable_rules_dir_names_the_permission(tmp_path, capsys):
    d = tmp_path / "locked"
    d.mkdir()
    d.chmod(0)
    try:
        assert cli.main(["check", "--rules-dir", str(d), "Edit", "{}"]) == 2
        assert "NOT EVALUABLE" in capsys.readouterr().err
    finally:
        d.chmod(0o700)


EDIT_PAYLOAD = {"tool_name": "Edit", "session_id": "s",
        "tool_input": {"file_path": "a.py", "new_string": "print(1)"}}


def _hook_run(tmp_path, rules_dir, payload):
    import io
    from instinct_delivery import hook
    err, out = io.StringIO(), io.StringIO()
    s = Settings(rules_dir=Path(rules_dir), state_dir=tmp_path / "state")
    code = hook.main(stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err, settings=s)
    return code, out.getvalue(), err.getvalue()


def test_the_hook_stays_exit_0_and_names_the_cause_on_stderr(tmp_path):
    code, out, err = _hook_run(tmp_path, tmp_path / "gone", EDIT_PAYLOAD)
    assert code == 0 and out == ""
    lines = [x for x in err.splitlines() if x.strip()]
    assert len(lines) == 1 and "does not exist" in lines[0] and "gone" in lines[0]


def _rule_dir(tmp_path):
    d = tmp_path / "rules"
    write_rule(d, "np", match_tools=["Edit"], match_content=["print"], domain="d", confidence=0.5)
    return d


def test_quiet_direction_a_readable_dir_is_silent_and_check_exits_0(tmp_path, capsys):
    rd = _rule_dir(tmp_path)
    code, out, err = _hook_run(tmp_path, rd, EDIT_PAYLOAD)
    assert code == 0 and "INSTINCT" in out and err == ""
    assert cli.main(["check", "--rules-dir", str(rd), "Edit",
                     '{"file_path":"a.py","new_string":"print(1)"}']) == 0
    assert capsys.readouterr().err == ""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _hook_run(tmp_path, empty, EDIT_PAYLOAD)[2] == ""          # an existing, empty dir is not a fault


@pytest.fixture
def _no_hang():
    """A load that opens a FIFO blocks forever: turn that into a failure after 10 s."""
    import signal

    def boom(*_a):
        raise AssertionError("the load blocked (a FIFO was opened)")
    prev = signal.signal(signal.SIGALRM, boom)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, prev)


def test_a_dangling_symlink_a_fifo_and_a_directory_are_named_not_read(tmp_path, capsys, _no_hang):
    rd = tmp_path / "rules"
    write_rule(rd, "good", match_tools=["Edit"])
    os.symlink(tmp_path / "nowhere.yaml", rd / "dangling.yaml")
    os.mkfifo(rd / "fifo.yaml")
    (rd / "dir.yaml").mkdir()
    os.symlink(rd / "loop.yaml", rd / "loop.yaml")
    rs = load_rules(rd)
    assert [r.file for r in rs.fire] == ["good.yaml"]
    reasons = {r.file: r.reason for r in rs.unread}
    assert set(reasons) == {"dangling.yaml", "fifo.yaml", "dir.yaml", "loop.yaml"}
    assert "dangling" in reasons["dangling.yaml"] and "FIFO" in reasons["fifo.yaml"]
    assert "directory" in reasons["dir.yaml"] and "loop" in reasons["loop.yaml"]
    code = cli.main(["doctor", "--rules-dir", str(rd), "--state-dir", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert code == 1
    for name in ("dangling.yaml", "fifo.yaml", "dir.yaml", "loop.yaml"):
        assert f"NOT READ {name}:" in out


def test_quiet_direction_a_symlink_to_a_real_rule_is_read(tmp_path):
    rd = tmp_path / "rules"
    write_rule(tmp_path / "elsewhere", "real", match_tools=["Edit"])
    rd.mkdir()
    os.symlink(tmp_path / "elsewhere" / "real.yaml", rd / "link.yaml")
    rs = load_rules(rd)
    assert [r.file for r in rs.fire] == ["link.yaml"] and not rs.unread


def test_a_huge_repetition_count_is_rejected_with_its_own_cause(tmp_path):
    rd = tmp_path / "rules"
    write_rule(rd, "big", match_tools=["Edit"], match_content=["a{99999999999}"])
    [r] = load_rules(rd).rejected
    assert "too large" in r.reason and "deeply" not in r.reason


def test_a_posix_class_is_rejected_with_a_named_cause_and_no_warning(tmp_path):
    import warnings
    rd = tmp_path / "rules"
    write_rule(rd, "posix", match_tools=["Edit"], match_content=["[[:alpha:]]+"])
    write_rule(rd, "ok", match_tools=["Edit"], match_content=["[a-z]+"])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        rs = load_rules(rd)
    assert [x.file for x in rs.fire] == ["ok.yaml"]
    [r] = rs.rejected
    assert r.file == "posix.yaml" and "character class" in r.reason and "[[:alpha:]]" in r.reason


ROOT = Path(__file__).resolve().parents[1]


SRC = ROOT / "src"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("INSTINCT_", "PYTHON", "LC_", "LANG"))}
    env.update(PYTHONPATH=str(SRC), INSTINCT_STATE_DIR=str(tmp_path / "state"),
               HOME=str(tmp_path / "home"), **extra)
    return env


def test_the_hook_prints_no_python_warning_for_a_posix_class(tmp_path):
    rd = tmp_path / "rules"
    write_rule(rd, "posix", match_tools=["Edit"], match_content=["[[:alpha:]]+"])
    r = subprocess.run([sys.executable, "-W", "default", "-m", "instinct_delivery", "hook"],
                       env=_env(tmp_path, INSTINCT_RULES_DIR=str(rd)), input=json.dumps(EDIT_PAYLOAD).encode(),
                       capture_output=True, timeout=60)
    assert r.returncode == 0 and b"FutureWarning" not in r.stderr


def test_those_patterns_do_load(tmp_path, rules):
    from instinct_delivery.rules import load_rules
    for i, pat in enumerate(["(a?){30}a{30}", "(a{1,50}){1,50}$"]):
        write_rule(rules, f"p{i}", match_tools=["Edit"], match_content=[pat])
    rs = load_rules(rules)
    assert len(rs.fire) == 2
