"""The per-call matching time budget: a slow pattern or Bash-write parse is bounded,
skipped and named as the cause, other rules still fire, rules the budget never
reached are not blamed as slow, the alarm state is restored, and the hook stays
exit 0."""
import io
import json
import signal
import time

import pytest
from conftest import log_rows, write_rule

from instinct_delivery import cli, hook, match, measure, state as st
from instinct_delivery.bashwrites import write_targets
from instinct_delivery.engine import Settings, run
from instinct_delivery.match import Budget, PARSE_ALLOWANCE, is_parse_slow


def _spent_setup(rules, tmp_path):
    for n in "1234":      # each may take a quarter of the budget: four spend all of it
        write_rule(rules, f"a-evil{n}", match_tools=["Bash"], match_content=["(a|aa)*$"], action="never")
    write_rule(rules, "b-fast", match_tools=["Bash"], match_commands=["aaa"], action="fast")
    return Settings(rules_dir=rules, state_dir=tmp_path / "state", match_budget_ms=200)


posix_only = pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="POSIX only")


@posix_only
def test_budget_exhaustion_is_logged_as_unevaluated_not_slow(rules, tmp_path, capsys):
    s = _spent_setup(rules, tmp_path)
    run("Bash", {"command": "a" * 40 + "b" + " aaa"}, "sess", settings=s)
    by_rule = {r["rule"]: r["delivery"] for r in log_rows(s)}
    assert [by_rule[f"a-evil{n}.yaml"] for n in "1234"] == ["slow"] * 4
    assert by_rule.get("b-fast.yaml") == "unevaluated"
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(s.state_dir)])
    out = capsys.readouterr().out
    assert "SLOW a-evil1.yaml" in out and "SLOW b-fast.yaml" not in out
    assert "b-fast.yaml" in out and "not evaluated" in out
    assert code == 1


def test_unevaluated_is_a_known_kind_and_not_a_match():
    from instinct_delivery import measure
    assert "unevaluated" in st.DELIVERIES and "unevaluated" not in measure.MATCH_KINDS


@posix_only
def test_bash_write_parsing_of_a_huge_unclosed_quote_is_bounded(rules, tmp_path):
    write_rule(rules, "w", match_tools=["Write"], match_content=["zzz"], action="w")
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state", match_budget_ms=400)
    cmd = "echo '" + "a" * 1_200_000
    t = time.monotonic()
    res = run("Bash", {"command": cmd}, "sess", settings=s)
    assert time.monotonic() - t < 2.0
    assert res.unevaluated and "bash-write" in res.unevaluated[0][1]
    assert {r["delivery"] for r in log_rows(s)} == {"unevaluated"}


@posix_only
def test_an_enormous_match_budget_does_not_break_matching(rules, tmp_path, monkeypatch):
    monkeypatch.setenv("INSTINCT_MATCH_BUDGET_MS", "99999999999999999999")
    write_rule(rules, "r", match_tools=["Edit"], action="the advice")
    s = Settings.from_env(rules_dir=rules, state_dir=tmp_path / "state")
    assert 0 < s.match_budget_ms < 10 ** 9
    assert "the advice" in run("Edit", {"file_path": "a"}, "sess", settings=s).text
    with match.Budget(10 ** 30):
        pass


@posix_only
def test_a_stray_alarm_outside_a_guard_is_ignored():
    with match.Budget(1000) as b:
        assert b.armed
        signal.raise_signal(signal.SIGALRM)          # delivered with no rule in flight
        with b.guard("x.yaml"):
            pass
        signal.raise_signal(signal.SIGALRM)
    assert b.slow == []


@posix_only
def test_a_timeout_that_escapes_a_guard_is_caught_at_the_call_boundary(monkeypatch):
    class R:
        file, tools, exclude, exclude_names, paths, content, commands = "r.yaml", ("Edit",), (), (), (), (), ()
    from contextlib import contextmanager
    @contextmanager
    def late(rule_file):
        yield
        raise match.MatchTimeout()               # the alarm lands as the guard is left
    with match.Budget(1000) as b:
        monkeypatch.setattr(b, "guard", late)
        assert match.match_call([R()], match.describe("Edit", {}), b) == []


# long enough that even a linear parse outlasts a 200 ms budget
PARSE_SLOW_CMD = "echo a > f; " * 200000


def engine_msg(pair):
    from instinct_delivery.engine import unevaluated_message
    return unevaluated_message(*pair)


@posix_only
def test_slow_bash_write_parse_is_named_as_the_cause(rules, tmp_path, capsys):
    write_rule(rules, "w", match_tools=["Write"], match_content=["zzz"], action="w")
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state", match_budget_ms=200)
    res = run("Bash", {"command": PARSE_SLOW_CMD}, "sess", settings=s)
    assert res.unevaluated and res.unevaluated[0][0] is None
    msg = engine_msg(res.unevaluated[0])
    assert "already spent" not in msg and "was not itself found slow" not in msg
    assert "Bash-write parsing" in msg and "ran out of time" in msg
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(s.state_dir)])
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "Bash-write parsing" in x)
    assert "already spent" not in line and "not itself found slow" not in line
    assert "ran out of time" in line and code == 1


@posix_only
def test_spent_budget_still_blames_the_budget_not_the_rule(rules, tmp_path, capsys):
    s = _spent_setup(rules, tmp_path)
    run("Bash", {"command": "a" * 40 + "b" + " aaa"}, "sess", settings=s)
    cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(s.state_dir)])
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "NOT EVALUATED b-fast.yaml" in x)
    assert "already spent" in line and "not itself found slow" in line


def test_old_log_reason_is_still_read_as_a_parse_timeout(rules, tmp_path, capsys):
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state")
    write_rule(rules, "w", match_tools=["Write"], action="w")
    from instinct_delivery import state as st2
    row = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "rule": None, "tool": "Bash", "delivery": "unevaluated", "bytes": 0,
           "reason": "bash-write parsing exceeded the match time budget"}
    st2.log_path(s.state_dir).parent.mkdir(parents=True, exist_ok=True)
    st2.log_path(s.state_dir).write_text(json.dumps(row) + "\n")
    cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(s.state_dir)])
    out = capsys.readouterr().out
    assert "ran out of time" in out and "already spent" not in out


EVIL = "a" * 40 + "b"


def _slow_setup(rules, tmp_path, budget_ms=300):
    write_rule(rules, "evil", match_tools=["Bash"], match_content=["(a|aa)*$"], action="never")
    write_rule(rules, "fine", match_tools=["Bash"], match_commands=["aaa"], action="still here")
    return Settings(rules_dir=rules, state_dir=tmp_path / "state", match_budget_ms=budget_ms)


@posix_only
def test_a_slow_pattern_is_bounded_skipped_and_named(rules, tmp_path):
    s = _slow_setup(rules, tmp_path)
    t = time.monotonic()
    res = run("Bash", {"command": EVIL}, "sess", settings=s)
    assert time.monotonic() - t < 2.0
    assert [f for f, _ in res.slow] == ["evil.yaml"]
    rows = [r for r in log_rows(s) if r["delivery"] == "slow"]
    assert [r["rule"] for r in rows] == ["evil.yaml"] and rows[0]["bytes"] == 0


@posix_only
def test_other_rules_still_fire_when_one_is_slow(rules, tmp_path):
    s = _slow_setup(rules, tmp_path)
    res = run("Bash", {"command": EVIL}, "sess", settings=s)
    assert "still here" in res.text and [f for f, _ in res.slow] == ["evil.yaml"]


@posix_only
def test_the_hook_stays_exit_0_and_says_so_on_stderr(rules, tmp_path):
    s = _slow_setup(rules, tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "session_id": "s",
                                             "tool_input": {"command": EVIL}})), out, err, settings=s)
    assert code == 0 and "evil.yaml" in err.getvalue() and "skipped" in err.getvalue()


@posix_only
def test_the_alarm_state_is_restored_and_a_foreign_alarm_is_left_alone(rules, tmp_path):
    s = _slow_setup(rules, tmp_path)
    run("Bash", {"command": "x"}, "s", settings=s)
    assert signal.getsignal(signal.SIGALRM) == signal.SIG_DFL
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    mine = lambda *a: None  # noqa: E731
    old = signal.signal(signal.SIGALRM, mine)
    try:
        run("Bash", {"command": "x"}, "s", settings=s)
        assert signal.getsignal(signal.SIGALRM) is mine          # not replaced
    finally:
        signal.signal(signal.SIGALRM, old)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        run("Bash", {"command": "x"}, "s", settings=s)
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 30      # not cancelled
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


@posix_only
def test_doctor_names_a_rule_the_log_says_was_slow(rules, tmp_path, capsys):
    s = _slow_setup(rules, tmp_path)
    run("Bash", {"command": EVIL}, "sess", settings=s)
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(s.state_dir)])
    out = capsys.readouterr().out
    assert code == 1 and "SLOW evil.yaml" in out


def test_budget_zero_or_off_disables_it(monkeypatch):
    monkeypatch.setenv("INSTINCT_MATCH_BUDGET_MS", "off")
    assert Settings.from_env().match_budget_ms == 0
    monkeypatch.setenv("INSTINCT_MATCH_BUDGET_MS", "junk")
    assert Settings.from_env().match_budget_ms == 1000


def test_slow_rows_are_a_known_delivery_kind_and_not_a_match():
    assert "slow" in st.DELIVERIES and "slow" not in measure.MATCH_KINDS


def test_breadth_replay_has_a_time_budget_per_call(tmp_path, capsys, monkeypatch):
    import time as _t
    rd = tmp_path / "rules"
    # loads (the structural check does not catch overlapping alternation) and takes ~30 s
    # on 40 `a` and a `b` when unbounded
    write_rule(rd, "slow", match_tools=["Edit"], match_content=["(a|aa)*$"])
    write_rule(rd, "fine", match_tools=["Edit"], match_content=["zzz"])
    tdir = tmp_path / "t"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text("".join(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"new_string": "a" * 40 + "b"}}]}}) + "\n"
        for _ in range(3)))
    t0 = _t.monotonic()
    code, rep = measure.breadth(tdir, rd, budget_ms=200)
    assert _t.monotonic() - t0 < 10
    by = {d["rule"]: d for d in rep["rules"]}
    assert by["slow.yaml"]["skipped"] == 3 and by["slow.yaml"]["rate"] is None
    assert by["fine.yaml"]["skipped"] == 0 and by["fine.yaml"]["rate"] == 0.0
    monkeypatch.setenv("INSTINCT_MATCH_BUDGET_MS", "200")
    assert cli.main(["breadth", "--rules-dir", str(rd), "--transcripts", str(tdir)]) in (0, 1)
    out = capsys.readouterr().out
    assert "slow.yaml" in out and "skipped" in out and "time budget" in out


def test_breadth_bounds_the_bash_write_parse_of_a_call(tmp_path):
    rd = tmp_path / "rules"
    write_rule(rd, "w", match_tools=["Write"], match_content=["zzz"])
    tdir = tmp_path / "t"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo a > f; " + "[[ " * 66000}}]}}) + "\n")
    t0 = time.monotonic()
    code, rep = measure.breadth(tdir, rd, budget_ms=1000)
    assert time.monotonic() - t0 < 10 and code in (0, 1)
    assert rep["rules"][0]["eligible"] == 1


def test_has_writes_runs_under_the_per_call_budget(tmp_path, monkeypatch):
    """The parse is bounded by the Budget even when the parse itself is slow."""
    from instinct_delivery import measure as m
    rd = tmp_path / "rules"
    write_rule(rd, "w", match_tools=["Write"], match_content=["zzz"])
    tdir = tmp_path / "t"
    tdir.mkdir()
    (tdir / "a.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo a > f"}}]}}) + "\n")

    def slow(cmd, *a, **k):
        t_end = time.monotonic() + 30
        while time.monotonic() < t_end:
            pass
        return []
    monkeypatch.setattr(m, "write_targets", slow)
    import instinct_delivery.match as mt
    monkeypatch.setattr(mt, "write_targets", slow)
    t0 = time.monotonic()
    m.breadth(tdir, rd, budget_ms=200)
    assert time.monotonic() - t0 < 10


def test_a_spent_substitution_scan_is_marked_not_silently_dropped():
    spent = write_targets("$(" * 100000 + "echo a > f")
    assert getattr(spent, "out_of_budget", None) is True
    fine = write_targets("echo $(echo a > g) > f")
    assert getattr(fine, "out_of_budget", None) is False
    assert sorted(fine) == [("f", "$(echo a > g)"), ("g", "a")]
    assert write_targets("") == []


def test_match_names_the_spent_scan_as_unevaluated():
    b = Budget(1000)
    match.match([], "Bash", {"command": "$(" * 100000}, budget=b)
    assert any(r is None and is_parse_slow(w) and w == PARSE_ALLOWANCE for r, w in b.unevaluated)
    b2 = Budget(1000)
    match.match([], "Bash", {"command": "echo a > f"}, budget=b2)
    assert b2.unevaluated == []
