"""The measurements, each on a synthetic log or transcript set whose answer is
known in advance. Absent or empty input is NOT EVALUABLE, never a zero."""
import datetime as dt
import json

import pytest
from conftest import write_rule

from instinct_delivery import cli, measure

NOW = dt.datetime.now(dt.timezone.utc)


def _ts(minutes=0, seconds=0):
    return (NOW - dt.timedelta(hours=1) + dt.timedelta(minutes=minutes, seconds=seconds)).isoformat()


def _row(rule, delivery="full", sid="s1", ts=None, nbytes=100, **kw):
    r = {"ts": ts or _ts(), "rule": rule, "domain": "d", "confidence": 0.5, "tool": "Edit",
         "session_id": sid, "file_path": None, "matched": None, "delivery": delivery,
         "bytes": nbytes}
    r.update(kw)
    return r


def _log(tmp_path, rows):
    p = tmp_path / "log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


@pytest.mark.parametrize("verb", ["repeat-rate", "cost", "fire-rate", "cofire"])
def test_every_log_report_is_not_evaluable_on_an_absent_or_empty_log(tmp_path, verb, capsys):
    assert cli.main([verb, "--log", str(tmp_path / "none.jsonl")]) == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert cli.main([verb, "--log", str(empty)]) == 2
    assert "NOT EVALUABLE" in capsys.readouterr().out


def test_repeat_rate_counts_same_rule_again_in_the_same_session(tmp_path):
    rows = ([_row("a.yaml", ts=_ts(i)) for i in range(4)]          # 1 novel + 3 repeat
            + [_row("b.yaml", ts=_ts(5))]                            # novel
            + [_row("a.yaml", sid="s2", ts=_ts(6))]                  # novel in s2
            + [_row("c.yaml", delivery="shadow", ts=_ts(7))])        # not a match kind
    r = measure.repeat_rate(_log(tmp_path, rows))
    assert (r["novel"], r["repeat"], r["repeat_pct"], r["sessions"]) == (3, 3, 50.0, 2)


def test_repeat_rate_sessionizes_rows_without_a_session_by_gap(tmp_path):
    rows = [_row("a.yaml", sid=None, ts=_ts(0)), _row("a.yaml", sid=None, ts=_ts(10)),
            _row("a.yaml", sid=None, ts=_ts(50))]                    # gap > 30 min: new session
    r = measure.repeat_rate(_log(tmp_path, rows))
    assert (r["novel"], r["repeat"]) == (2, 1)


def test_cost_splits_first_full_from_rearm_full(tmp_path):
    rows = [_row("a.yaml", nbytes=500, ts=_ts(0)), _row("a.yaml", "brief", nbytes=90, ts=_ts(1)),
            _row("a.yaml", nbytes=500, ts=_ts(40)), _row("b.yaml", "budget", nbytes=0, ts=_ts(2))]
    t = measure.cost(_log(tmp_path, rows))["total"]
    assert (t["first_full_bytes"], t["rearm_full_bytes"], t["brief_bytes"]) == (500, 500, 90)
    assert t["rearm_share"] == 0.5 and t["budget"] == 1


def test_fire_rate_regressed_held_cleared(tmp_path):
    rows = [_row("big.yaml", nbytes=6000, ts=_ts(i)) for i in range(2)] + \
           [_row("old.yaml", nbytes=12000)] + [_row("small.yaml", nbytes=100)]
    log = _log(tmp_path, rows)
    code, r = measure.fire_rate(log)
    assert code == 1 and set(r["regressed"]) == {"big.yaml", "old.yaml"}
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"rules": ["big.yaml", "old.yaml", "gone.yaml"]}))
    code, r = measure.fire_rate(log, baseline=base)
    assert code == 0 and r["cleared"] == ["gone.yaml"] and r["over"]["big.yaml"]["remedy"] == "SHORTEN"
    base.write_text("{")
    with pytest.raises(measure.NotEvaluable):
        measure.fire_rate(log, baseline=base)


def test_cofire_groups_by_the_exact_call_and_needs_support(tmp_path):
    rows = []
    for i in range(12):                       # a and b always together
        rows += [_row("a.yaml", ts=_ts(i)), _row("b.yaml", ts=_ts(i))]
    rows += [_row("c.yaml", ts=_ts(0, 30))]    # same second-ish, different call
    rows += [_row("a.yaml", "shadow", ts=_ts(20))]
    code, r = measure.cofire(_log(tmp_path, rows))
    assert code == 1 and r["pairs"] == [{"a": "a.yaml", "b": "b.yaml", "cofires": 12, "jaccard": 1.0}]
    code, r = measure.cofire(_log(tmp_path, rows), min_support=13)
    assert code == 0 and r["thin_pairs_suppressed"] == 1


def _transcript(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_persistence_loss_counts_logged_full_blocks_missing_from_the_transcript(tmp_path):
    rows = [_row("a.yaml", sid="s1", ts=_ts(i)) for i in range(4)] + \
           [_row("a.yaml", sid="s9", ts=_ts(9))]                     # no transcript: unjoined
    tdir = tmp_path / "t"
    tdir.mkdir()
    shown = {"type": "attachment", "content": "INSTINCT ACTIVATED [a, d, confidence=0.5]: x"}
    _transcript(tdir / "s1.jsonl", [shown, shown, {"type": "user", "content": "INSTINCT ACTIVATED [a,"}])
    r = measure.persistence_loss(_log(tmp_path, rows), tdir)
    assert (r["joined"], r["sessions_with_full"], r["full_logged"], r["lost"], r["lost_pct"]) == \
        (1, 2, 4, 2, 50.0)


def test_persistence_loss_with_no_join_is_not_evaluable(tmp_path):
    (tmp_path / "t").mkdir()
    with pytest.raises(measure.NotEvaluable, match="no logged session joins"):
        measure.persistence_loss(_log(tmp_path, [_row("a.yaml")]), tmp_path / "t")


def test_breadth_replays_through_the_real_matcher_and_never_exceeds_one(tmp_path, rules):
    write_rule(rules, "noprint", match_tools=["Write"], match_content=[r"print\("])
    tdir = tmp_path / "t"
    tdir.mkdir()
    calls = [{"type": "tool_use", "name": "Write", "input": {"file_path": "a.py", "content": "print(1)"}},
             {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py", "content": "x = 1"}},
             {"type": "tool_use", "name": "Bash", "input": {"command": "echo 'print(2)' > c.py"}},
             {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]
    _transcript(tdir / "s.jsonl", [{"message": {"content": calls}}])
    code, r = measure.breadth(tdir, rules, threshold=0.9)
    [d] = r["rules"]
    assert (d["eligible"], d["fires"], d["rate"]) == (3, 2, 0.667) and code == 0


def test_labels_need_a_store_a_minimum_sample_and_honest_sources(tmp_path):
    store = tmp_path / "labels.jsonl"
    with pytest.raises(measure.NotEvaluable):
        measure.label_stats(store)
    with pytest.raises(ValueError):
        measure.make_label("a", "Edit", "FALSE_POSITIVE", "rule_evidence", "x")
    rows = [measure.make_label("a", "Edit", "TRUE_POSITIVE" if i % 4 else "FALSE_POSITIVE",
                               "transcript_sample", f"ex {i}") for i in range(19)]
    store.write_text("".join(json.dumps(r) + "\n" for r in rows) + "garbage\n")
    s = measure.label_stats(store)
    assert s["rules"]["a"]["precision"] is None and s["bad_lines"] == 1
    store.write_text(store.read_text() + json.dumps(rows[1]) + "\n")
    assert measure.label_stats(store)["rules"]["a"]["precision"] == 0.75


# ---- a zero denominator is NOT EVALUABLE, never "0 of 0"

@pytest.mark.parametrize("kind", ["slow", "unevaluated", "shadow", "notice"])
def test_fire_rate_with_no_eligible_rows_is_not_evaluable(tmp_path, capsys, kind):
    log = _log(tmp_path, [_row("a.yaml", kind, nbytes=0), _row("b.yaml", kind, nbytes=0, sid="s2")])
    assert cli.main(["fire-rate", "--log", str(log)]) == 2
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out and kind in out and "0 of 0" not in out


def test_fire_rate_with_a_baseline_does_not_report_cleared_on_nothing(tmp_path, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"rules": ["a.yaml"]}))
    log = _log(tmp_path, [_row("a.yaml", "slow", nbytes=0)])
    assert cli.main(["fire-rate", "--log", str(log), "--baseline", str(base)]) == 2
    assert "cleared" not in capsys.readouterr().out


def test_fire_rate_with_eligible_rows_still_reports(tmp_path, capsys):
    log = _log(tmp_path, [_row("a.yaml", nbytes=10), _row("b.yaml", "slow", nbytes=0)])
    assert cli.main(["fire-rate", "--log", str(log)]) == 0
    assert "0 of 1 rule(s)" in capsys.readouterr().out


def test_breadth_where_no_rule_had_an_eligible_call_is_not_evaluable(tmp_path, rules, capsys):
    write_rule(rules, "p", match_tools=["Edit"], match_content=["print"])
    calls = [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}]
    (tmp_path / "t").mkdir()
    _transcript(tmp_path / "t" / "s.jsonl", [{"message": {"content": calls}}])
    with pytest.raises(measure.NotEvaluable, match="eligible"):
        measure.breadth(tmp_path / "t", rules)
    assert cli.main(["breadth", "--transcripts", str(tmp_path / "t"),
                     "--rules-dir", str(rules)]) == 2
    assert "NOT EVALUABLE" in capsys.readouterr().out


def test_cost_without_full_deliveries_says_why_not_none(tmp_path, capsys):
    log = _log(tmp_path, [_row("a.yaml", "brief", nbytes=40)])
    assert cli.main(["cost", "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "None" not in out and "no full deliveries" in out
