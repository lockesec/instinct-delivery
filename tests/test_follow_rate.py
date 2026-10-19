"""follow-rate: after a lesson was delivered, did the agent's next calls do what it asked?

Every case is a real-shaped transcript (assistant records carrying tool_use blocks and
a timestamp) joined to activation-log rows on session id and time."""
import datetime as dt
import json

import pytest
from conftest import write_rule

from instinct_delivery import cli, measure
from instinct_delivery.rules import load_rules

T0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)


def at(s):
    return (T0 + dt.timedelta(seconds=s)).isoformat().replace("+00:00", "Z")


def call(s, name, **inp):
    return {"type": "assistant", "timestamp": at(s),
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def row(rule, s, sid="s1", delivery="full"):
    return {"ts": at(s), "rule": rule, "domain": "d", "confidence": 0.5, "tool": "Edit",
            "session_id": sid, "file_path": None, "matched": None, "delivery": delivery, "bytes": 100}


def write(tmp_path, rows, sessions):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    t = tmp_path / "t"
    t.mkdir()
    for sid, recs in sessions.items():
        (t / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return log, t


@pytest.fixture
def testrule(rules):
    write_rule(rules, "run-tests", match_tools=["Edit"], match_paths=["src/"],
               follow={"tools": ["Bash"], "commands": ["pytest"], "within": 3})
    return rules


def test_a_follow_inside_the_window_counts_and_one_outside_does_not(tmp_path, testrule):
    rows = [row("run-tests.yaml", 10), row("run-tests.yaml", 110)]
    s1 = [call(9, "Edit", file_path="src/a.py"),
          call(11, "Read", file_path="x"), call(12, "Bash", command="pytest -q"),     # followed
          call(109, "Edit", file_path="src/b.py"),
          call(111, "Read", file_path="x"), call(112, "Read", file_path="y"), call(113, "Grep", pattern="z"),
          call(114, "Bash", command="pytest")]                                           # 4th call: too late
    log, t = write(tmp_path, rows, {"s1": s1})
    r = measure.follow_rate(log, t, testrule, min_n=1)
    (d,) = r["rules"]
    assert (d["deliveries"], d["joined"], d["followed"]) == (2, 2, 1)
    assert d["rate"] == 0.5


def test_a_rule_without_a_follow_predicate_is_not_evaluable_not_zero(tmp_path, rules):
    write_rule(rules, "plain", match_tools=["Edit"])
    log, t = write(tmp_path, [row("plain.yaml", 10)], {"s1": [call(11, "Bash", command="ls")]})
    r = measure.follow_rate(log, t, rules, min_n=1)
    (d,) = r["rules"]
    assert d["rate"] is None and d["followed"] is None and "no `follow`" in d["why"]


def test_below_the_minimum_sample_the_counts_print_but_no_rate(tmp_path, testrule):
    log, t = write(tmp_path, [row("run-tests.yaml", 10)],
                   {"s1": [call(11, "Bash", command="pytest")]})
    (d,) = measure.follow_rate(log, t, testrule, min_n=10)["rules"]
    assert (d["joined"], d["followed"], d["rate"]) == (1, 1, None)
    assert "fewer than 10" in d["why"]


def test_shadow_and_withheld_rows_are_not_deliveries(tmp_path, testrule):
    rows = [row("run-tests.yaml", 10, delivery="shadow"), row("run-tests.yaml", 20, delivery="budget")]
    log, t = write(tmp_path, rows, {"s1": [call(21, "Bash", command="pytest")]})
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    assert d["deliveries"] == 0


def test_a_delivery_with_no_later_call_or_no_transcript_is_not_in_the_denominator(tmp_path, testrule):
    rows = [row("run-tests.yaml", 10), row("run-tests.yaml", 10, sid="gone")]
    log, t = write(tmp_path, rows, {"s1": [call(5, "Bash", command="pytest")]})   # only an EARLIER call
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    assert (d["deliveries"], d["joined"], d["no_later_call"], d["no_transcript"]) == (2, 0, 1, 1)
    assert d["rate"] is None


def test_the_base_rate_counts_every_window_in_the_joined_sessions(tmp_path, testrule):
    rows = [row("run-tests.yaml", 10)]
    s1 = [call(9, "Edit", file_path="src/a.py"), call(11, "Bash", command="pytest"),
          call(12, "Read", file_path="a"), call(13, "Read", file_path="b"),
          call(14, "Read", file_path="c"), call(15, "Read", file_path="d")]
    log, t = write(tmp_path, rows, {"s1": s1})
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    # windows of 3 after each of the 6 positions: only the window after position 0 holds pytest
    assert (d["base_windows"], d["base_hits"]) == (5, 1)
    assert d["base_rate"] == 0.2


def test_the_follow_predicate_uses_the_real_matcher_including_bash_writes(tmp_path, rules):
    write_rule(rules, "env", match_tools=["Edit"],
               follow={"tools": ["Write"], "paths": [".env.example"], "within": 2})
    log, t = write(tmp_path, [row("env.yaml", 10)],
                   {"s1": [call(11, "Bash", command="echo 'KEY=' > .env.example")]})
    (d,) = measure.follow_rate(log, t, rules, min_n=1)["rules"]
    assert d["followed"] == 1


def test_a_malformed_follow_block_rejects_the_rule(rules):
    for i, bad in enumerate(({"tools": []}, {"tools": ["Bash"], "within": 0}, {"tools": ["Bash"], "within": 99},
                             {"tools": ["Bash"], "content": ["(a+)+"]}, ["Bash"],
                             {"tools": ["Bash"], "nope": 1})):
        write_rule(rules, f"b{i}", match_tools=["Edit"], follow=bad)
    rs = load_rules(rules)
    assert len(rs.rejected) == 6 and all("`follow`" in r.reason for r in rs.rejected)


def test_cli_prints_counts_beside_rates_and_says_it_is_not_causal(tmp_path, testrule, capsys):
    rows = [row("run-tests.yaml", 10)]
    log, t = write(tmp_path, rows, {"s1": [call(11, "Bash", command="pytest")]})
    code = cli.main(["follow-rate", "--rules-dir", str(testrule), "--log", str(log),
                     "--transcripts", str(t), "--min-n", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "run-tests  1 of 1 joined delivery(ies) followed within 3 call(s) (100%)" in out
    assert "not causal" in out


def test_no_delivery_joins_is_not_evaluable(tmp_path, testrule, capsys):
    log, t = write(tmp_path, [row("run-tests.yaml", 10, sid="other")], {"s1": []})
    code = cli.main(["follow-rate", "--rules-dir", str(testrule), "--log", str(log),
                     "--transcripts", str(t)])
    assert code == 2


def test_no_deliveries_is_not_evaluable_not_a_measured_zero(tmp_path, testrule, capsys):
    log, t = write(tmp_path, [row("other.yaml", 10)], {"s1": [call(11, "Bash", command="pytest")]})
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    assert d["joined"] == 0 and d["followed"] is None and d["rate"] is None
    assert d["deliveries"] == 0 and "no deliveries" in d["why"]
    args = ["follow-rate", "--rules-dir", str(testrule), "--log", str(log),
            "--transcripts", str(t)]
    assert cli.main(args) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "0 of 0" not in out
    cli.main(args + ["--json"])
    assert json.loads(capsys.readouterr().out)["rules"][0]["followed"] is None


def test_unjoined_deliveries_are_not_evaluable_with_the_cause_and_counts(tmp_path, testrule, capsys):
    log, t = write(tmp_path, [row("run-tests.yaml", 10, sid="gone"), row("run-tests.yaml", 20, sid="s1")],
                   {"s1": [call(5, "Bash", command="pytest")]})
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    assert d["followed"] is None and (d["deliveries"], d["no_transcript"], d["no_later_call"]) == (2, 1, 1)
    assert "transcript" in d["why"]
    cli.main(["follow-rate", "--rules-dir", str(testrule), "--log", str(log), "--transcripts", str(t)])
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "1 with no transcript" in out and "1 with no later call" in out


def test_below_min_n_the_base_rate_prints_counts_only(tmp_path, testrule, capsys):
    log, t = write(tmp_path, [row("run-tests.yaml", 10)],
                   {"s1": [call(11, "Bash", command="pytest"), call(12, "Read", file_path="x"),
                           call(13, "Read", file_path="y")] * 20})
    (d,) = measure.follow_rate(log, t, testrule, min_n=10)["rules"]
    assert d["base_windows"] >= 10 and d["joined"] == 1
    assert d["base_rate"] is None and d["base_hits"] > 0
    cli.main(["follow-rate", "--rules-dir", str(testrule), "--log", str(log), "--transcripts", str(t)])
    out = capsys.readouterr().out
    assert "base rate" in out and "%" not in out


@pytest.mark.parametrize("sid", ["../x", "a/b", "..", "/etc/passwd", "a\\..\\b", "x\0y"])
def test_a_session_id_that_escapes_the_transcripts_dir_is_refused(tmp_path, testrule, sid):
    log, t = write(tmp_path, [row("run-tests.yaml", 10, sid=sid)], {})
    (tmp_path / "x.jsonl").write_text(json.dumps(call(11, "Bash", command="pytest")) + "\n")
    (d,) = measure.follow_rate(log, t, testrule, min_n=1)["rules"]
    assert d["joined"] == 0 and d["no_transcript"] == 1 and d["followed"] is None
