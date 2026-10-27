"""Reports on odd input: absent, empty, malformed, future or unparsable log rows and
out-of-range options. Where the input is missing the report says NOT EVALUABLE
with the cause, in every output format, and never prints a zero."""
import datetime as dt
import json

import pytest

from instinct_delivery import cli, measure, state as st


def _log(tmp_path, rows):
    p = tmp_path / "log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_cost_sessionless_rows_are_not_rearm(tmp_path):
    rows = [{"ts": f"2020-01-01T00:0{i}:00+00:00", "rule": "r", "delivery": "full", "bytes": 100,
             "session_id": None} for i in range(3)]
    rows += [{"ts": f"2020-01-01T01:0{i}:00+00:00", "rule": "r", "delivery": "full", "bytes": 10,
              "session_id": "s"} for i in range(2)]
    r = measure.cost(_log(tmp_path, rows), 100000)
    t = r["total"]
    assert t["rearm_full_bytes"] == 10 and t["first_full_bytes"] == 10
    assert t["sessionless_full_bytes"] == 300 and t["full_bytes"] == 320
    assert t["rearm_share"] == 0.5


def test_cost_only_sessionless_rows_has_an_undefined_rearm_share(tmp_path):
    rows = [{"ts": "2020-01-01T00:00:00+00:00", "rule": "r", "delivery": "full", "bytes": 5}]
    t = measure.cost(_log(tmp_path, rows), 100000)["total"]
    assert t["rearm_share"] is None


def test_cost_rejects_negative_days(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["cost", "--log", str(_log(tmp_path, [])), "--days", "-1"])
    assert e.value.code == 64


def test_labels_stats_on_only_unreadable_lines_is_not_evaluable(tmp_path, capsys):
    store = tmp_path / "labels.jsonl"
    store.write_text("not json\n{broken\n")
    assert cli.main(["labels", "stats", "--store", str(store)]) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "2 unreadable line(s)" in out


def test_repeat_rate_with_no_assignable_session_names_the_cause(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    rows = [{"rule": "r", "ts": "garbage", "delivery": "full", "bytes": 10, "tool": "Edit"},
            {"rule": "r", "ts": "garbage", "delivery": "full", "bytes": 10, "tool": "Edit"}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    code = cli.main(["repeat-rate", "--log", str(log)])
    out = capsys.readouterr().out
    assert code == 2 and "ZeroDivision" not in out
    assert "no row could be assigned to a session" in out


def _cofire_log(tmp_path, n):
    log = tmp_path / "log.jsonl"
    rows = []
    for i in range(n):
        for r in ("a", "b"):
            rows.append({"rule": r, "ts": f"2026-01-01T12:00:{i:02d}+00:00", "session_id": "s",
                         "delivery": "full", "bytes": 10, "tool": "Edit"})
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return log


def test_cofire_on_fewer_calls_than_min_support_is_not_evaluable(tmp_path, capsys):
    log = _cofire_log(tmp_path, 3)
    assert cli.main(["cofire", "--log", str(log)]) == 2       # default --min-support 10
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "3 call(s)" in out and "min-support 10" in out


def test_cofire_with_enough_calls_still_reports(tmp_path, capsys):
    log = _cofire_log(tmp_path, 12)
    assert cli.main(["cofire", "--log", str(log)]) == 1
    assert "a + b" in capsys.readouterr().out


def _delivery_row(rule, delivery, ts=None, nbytes=0):
    from datetime import datetime, timezone
    return {"ts": ts or datetime.now(timezone.utc).isoformat(), "rule": rule, "domain": "d",
            "confidence": 0.5, "tool": "Edit", "session_id": "s1", "file_path": None,
            "matched": None, "delivery": delivery, "bytes": nbytes}


def test_cost_on_slow_and_shadow_rows_only_is_not_evaluable(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps(_delivery_row("a.yaml", k)) + "\n"
                           for k in ("slow", "shadow", "shadow", "unevaluated")))
    assert cli.main(["cost", "--log", str(log)]) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "0 of 4" in out and "2 shadow" in out
    with pytest.raises(measure.NotEvaluable):
        measure.cost(log)


def test_cost_with_one_delivery_row_still_reports(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps(_delivery_row("a.yaml", "shadow")) + "\n"
                   + json.dumps(_delivery_row("a.yaml", "full", nbytes=120)) + "\n")
    assert cli.main(["cost", "--log", str(log)]) == 0
    assert "full 120 B" in capsys.readouterr().out


@pytest.mark.parametrize("v", ["-5", "0"])
def test_fire_rate_byte_cap_must_be_positive(v, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["fire-rate", "--byte-cap", v])
    assert e.value.code == 64 and "--byte-cap" in capsys.readouterr().err


def _log_of_rows_or_text(tmp_path, rows):
    p = tmp_path / "l.jsonl"
    p.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in rows))
    return p


def _default_row(**kw):
    r = {"ts": st.now_iso(), "rule": "a.yaml", "session_id": "s", "delivery": "full", "bytes": 5}
    r.update(kw)
    return r


def test_cost_days_0_names_the_zero_window(tmp_path, capsys):
    assert cli.main(["cost", "--log", str(_log_of_rows_or_text(tmp_path, [_default_row()])), "--days", "0"]) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and "--days 0" in out and "zero" in out


def test_rows_with_an_unparsable_timestamp_are_counted_and_named(tmp_path, capsys):
    log = _log_of_rows_or_text(tmp_path, [_default_row(ts="junk"), _default_row(ts=None)])
    assert cli.main(["cost", "--log", str(log)]) == 2
    out = capsys.readouterr().out
    assert "2 row(s)" in out and "timestamp" in out


def test_future_rows_are_excluded_and_said_so(tmp_path, capsys):
    log = _log_of_rows_or_text(tmp_path, [_default_row(), _default_row(ts="2999-01-01T00:00:00+00:00", bytes=1000)])
    assert cli.main(["cost", "--log", str(log), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["total"]["full_bytes"] == 5 and rep["future_rows"] == 1
    assert cli.main(["cost", "--log", str(log)]) == 0
    assert "1 row(s) dated in the future excluded" in capsys.readouterr().out
    only = _log_of_rows_or_text(tmp_path, [_default_row(ts="2999-01-01T00:00:00+00:00")])
    assert cli.main(["cost", "--log", str(only)]) == 2
    assert "future" in capsys.readouterr().out


def test_unhashable_fields_are_malformed_rows_in_every_report(tmp_path, capsys):
    bad = [_default_row(rule=["x"]), _default_row(session_id=["s"]), _default_row(ts=5)]
    log = _log_of_rows_or_text(tmp_path, [_default_row(), _default_row(rule="b.yaml")] + bad)
    assert cli.main(["repeat-rate", "--log", str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["malformed_rows"] == 3
    assert cli.main(["cofire", "--log", str(log), "--min-support", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["malformed_rows"] == 3
    assert cli.main(["fire-rate", "--log", str(log), "--json"]) in (0, 2)
    assert json.loads(capsys.readouterr().out)["malformed_rows"] == 3


def test_a_bad_baseline_names_the_cause_not_a_raw_exception(tmp_path, capsys):
    log = _log_of_rows_or_text(tmp_path, [_default_row()])
    base = tmp_path / "b.json"
    base.write_text("[1]")
    assert cli.main(["fire-rate", "--log", str(log), "--baseline", str(base)]) == 2
    out = capsys.readouterr().out
    assert "baseline" in out and "rules" in out and "TypeError" not in out and "list indices" not in out
    base.write_text("{not json")
    assert cli.main(["fire-rate", "--log", str(log), "--baseline", str(base)]) == 2
    assert "not valid JSON" in capsys.readouterr().out


def _future():
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat()


def _rule_row(rule, ts, nbytes=100, **kw):
    r = {"ts": ts, "rule": rule, "domain": "d", "confidence": 0.5, "tool": "Edit",
         "session_id": "s1", "file_path": None, "matched": None, "delivery": "full",
         "bytes": nbytes}
    r.update(kw)
    return r


def _ts(minutes=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
            + dt.timedelta(minutes=minutes)).isoformat()


EXCLUDED = {
    "malformed": ([_rule_row("a.yaml", _ts(1)), _rule_row("b.yaml", _ts(2), nbytes="12KB")], "malformed"),
    "future": ([_rule_row("a.yaml", _ts(1)), _rule_row("b.yaml", _future())], "future"),
    "unstamped": ([_rule_row("a.yaml", _ts(1)), _rule_row("b.yaml", None)], "timestamp"),
}


def _log_with_trailer(tmp_path, rows, raw=""):
    p = tmp_path / "log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + raw)
    return p


@pytest.mark.parametrize("name", sorted(EXCLUDED))
def test_fire_rate_with_excluded_rows_and_no_offender_says_not_evaluable(name, tmp_path, capsys):
    rows, word = EXCLUDED[name]
    code = cli.main(["fire-rate", "--log", str(_log_with_trailer(tmp_path, rows))])
    out = capsys.readouterr().out
    assert code == 2
    assert out.startswith("NOT EVALUABLE")
    assert word in out and "1 " in out          # the cause, with its count
    assert "1 of 2" in out or "2 row" in out or "rules_seen" in out or "fire-rate" in out


@pytest.mark.parametrize("name", sorted(EXCLUDED))
def test_the_same_in_json(name, tmp_path, capsys):
    rows, word = EXCLUDED[name]
    code = cli.main(["fire-rate", "--log", str(_log_with_trailer(tmp_path, rows)), "--json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 2 and d["status"] == "NOT_EVALUABLE" and word in d["cause"]


def test_quiet_direction_no_excluded_rows_and_no_offender_is_clean(tmp_path, capsys):
    rows = [_rule_row("a.yaml", _ts(1)), _rule_row("b.yaml", _ts(2))]
    assert cli.main(["fire-rate", "--log", str(_log_with_trailer(tmp_path, rows))]) == 0
    assert "NOT EVALUABLE" not in capsys.readouterr().out


def test_a_new_offender_is_still_a_finding_while_rows_are_excluded(tmp_path, capsys):
    rows = [_rule_row("a.yaml", _ts(1), nbytes=99999), _rule_row("b.yaml", _future())]
    assert cli.main(["fire-rate", "--log", str(_log_with_trailer(tmp_path, rows))]) == 1


def test_every_report_that_returns_a_code_never_prints_a_report_for_exit_2(tmp_path, capsys):
    """Any report that exits 2 prints NOT EVALUABLE first (the one consumer-side rule)."""
    rows = [_rule_row("a.yaml", _ts(1)), _rule_row("b.yaml", _ts(2), nbytes="x")]
    log = _log_with_trailer(tmp_path, rows)
    for argv in (["fire-rate"], ["cost"], ["repeat-rate"], ["cofire", "--min-support", "1"]):
        code = cli.main(argv + ["--log", str(log)])
        out = capsys.readouterr().out
        if code == 2:
            assert out.startswith("NOT EVALUABLE"), argv


def test_a_not_evaluable_report_is_json_with_json(tmp_path, capsys):
    for verb in ("repeat-rate", "cost", "fire-rate", "cofire"):
        code = cli.main([verb, "--log", str(tmp_path / "none.jsonl"), "--json"])
        d = json.loads(capsys.readouterr().out)
        assert code == 2 and d["status"] == "NOT_EVALUABLE" and "does not exist" in d["cause"]


def test_text_is_unchanged_without_json(tmp_path, capsys):
    assert cli.main(["cost", "--log", str(tmp_path / "none.jsonl")]) == 2
    assert capsys.readouterr().out.startswith("NOT EVALUABLE — activation log")


def test_a_huge_days_names_the_cause(tmp_path, capsys):
    log = _log_with_trailer(tmp_path, [_rule_row("a.yaml", _ts(1))])
    assert cli.main(["fire-rate", "--log", str(log), "--days", "1000000"]) == 2
    out = capsys.readouterr().out
    assert "--days" in out and "too large" in out and "Overflow" not in out


def test_a_directory_as_the_log_names_the_cause(tmp_path, capsys):
    assert cli.main(["cost", "--log", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "directory" in out and "IsADirectory" not in out and "report failed" not in out


def test_quiet_direction_a_large_but_valid_days_still_reports(tmp_path, capsys):
    log = _log_with_trailer(tmp_path, [_rule_row("a.yaml", _ts(1))])
    assert cli.main(["fire-rate", "--log", str(log), "--days", "3000"]) == 0
