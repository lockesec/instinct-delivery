"""`after`: a rule that fires only when an earlier call of the same session matched a
precondition. Per-session counters only; no call content is ever stored."""
import json

import pytest
from conftest import log_rows, write_rule

from instinct_delivery import cli, state as st
from instinct_delivery.engine import Settings, run
from instinct_delivery.rules import load_rules

SQL = "mcp__db__execute_sql"
READ_SECRET = {"query": "select * from integration_tokens;"}
READ_PLAIN = {"query": "select id from tickets;"}
WRITE = {"query": "insert into messages (body) values ('x');"}


def _toxic(rules, within=None, **kw):
    after = {"tools": [SQL], "content": [r"\bfrom\s+\w*token"]}
    if within is not None:
        after["within"] = within
    write_rule(rules, "toxic", match_tools=[SQL], match_content=[r"\binsert\s+into\b"], after=after, **kw)


def _call(s, ti, sid="s1", tool=SQL):
    return run(tool, ti, sid, settings=s)


def _kinds(res):
    return [r["delivery"] for r in res.rows]


# ---------------------------------------------------------------- loading

def test_after_is_built_like_follow_with_a_default_window_of_20(rules):
    _toxic(rules)
    rule = load_rules(rules).fire[0]
    pred, within = rule.after
    assert within == 20 and pred.tools == [SQL] and pred.content == [r"\bfrom\s+\w*token"]
    assert pred.content_re[0].search("select * FROM integration_tokens")


@pytest.mark.parametrize("bad,why", [
    ({"content": ["x"]}, "tools"),
    ({"tools": [SQL], "within": 0}, "within"),
    ({"tools": [SQL], "within": 201}, "within"),
    ({"tools": [SQL], "within": True}, "within"),
    ({"tools": [SQL], "within": "5"}, "within"),
    ({"tools": [SQL], "bogus": 1}, "unknown key"),
    ({"tools": [SQL], "content": ["(unclosed"]}, "does not compile"),
    ({"tools": [SQL], "content": ["(a+)+$"]}, "backtrack"),
    ({"tools": [SQL], "content": ["a​b"]}, "invisible"),
    ([SQL], "mapping"),
    ("x", "mapping"),
])
def test_a_malformed_after_rejects_the_whole_rule_with_its_reason(rules, bad, why):
    write_rule(rules, "bad", match_tools=[SQL], after=bad)
    rs = load_rules(rules)
    assert rs.fire == [] and len(rs.rejected) == 1
    assert "after" in rs.rejected[0].reason and why in rs.rejected[0].reason


def test_within_accepts_the_bounds(rules):
    write_rule(rules, "lo", match_tools=[SQL], after={"tools": [SQL], "within": 1})
    write_rule(rules, "hi", match_tools=[SQL], after={"tools": [SQL], "within": 200})
    assert [r.after[1] for r in load_rules(rules).fire] == [200, 1]


# ---------------------------------------------------------------- the engine

def test_fires_only_after_the_precondition_and_not_before(settings, rules):
    _toxic(rules)
    assert _kinds(_call(settings, WRITE)) == []                      # quiet: no earlier read
    assert _kinds(_call(settings, READ_SECRET)) == []                # the precondition alone is silent
    assert _kinds(_call(settings, WRITE)) == ["full"]


def test_one_call_matching_both_does_not_satisfy_itself(settings, rules):
    write_rule(rules, "self", match_tools=[SQL], match_content=["token"],
               after={"tools": [SQL], "content": ["token"]})
    assert _kinds(_call(settings, {"query": "insert token"})) == []
    assert _kinds(_call(settings, {"query": "insert token"})) == ["full"]   # the first call is earlier now


def test_within_counts_every_call_of_the_session(settings, rules):
    _toxic(rules, within=3)
    _call(settings, READ_SECRET)                                      # call 1
    _call(settings, READ_PLAIN, tool="Read")                          # call 2 (a tool no rule names)
    _call(settings, READ_PLAIN)                                       # call 3
    assert _kinds(_call(settings, WRITE)) == ["full"]                 # call 4: distance 3
    s2 = Settings(rules_dir=rules, state_dir=settings.state_dir)
    _call(s2, READ_SECRET, sid="t")
    for _ in range(3):
        _call(s2, READ_PLAIN, sid="t")
    assert _kinds(_call(s2, WRITE, sid="t")) == []                    # distance 4: out of the window


def test_a_later_match_of_the_precondition_renews_the_window(settings, rules):
    _toxic(rules, within=2)
    _call(settings, READ_SECRET)
    _call(settings, READ_PLAIN)
    _call(settings, READ_SECRET)
    _call(settings, READ_PLAIN)
    assert _kinds(_call(settings, WRITE)) == ["full"]


def test_sessions_do_not_share_a_precondition(settings, rules):
    _toxic(rules)
    _call(settings, READ_SECRET, sid="a")
    assert _kinds(_call(settings, WRITE, sid="b")) == []
    assert _kinds(_call(settings, WRITE, sid="a")) == ["full"]


def test_no_session_id_cannot_be_evaluated_and_says_why(settings, rules):
    _toxic(rules)
    res = run(SQL, WRITE, None, settings=settings)
    assert res.text == "" and _kinds(res) == ["unevaluated"]
    row = res.rows[0]
    assert row["rule"] == "toxic.yaml" and row["reason"] == "after: no session id to remember earlier calls"
    assert res.unevaluated == [("toxic.yaml", "after: no session id to remember earlier calls")]
    assert log_rows(settings)[0]["delivery"] == "unevaluated"


def test_no_session_id_and_no_own_match_logs_nothing(settings, rules):
    _toxic(rules)
    res = run(SQL, READ_PLAIN, None, settings=settings)
    assert res.rows == [] and res.unevaluated == []


def test_state_holds_counters_only_never_content(settings, rules):
    _toxic(rules)
    _call(settings, READ_SECRET)
    _call(settings, READ_PLAIN)
    raw = (st.delivery_dir(settings.state_dir) / "s1.json").read_text()
    assert "integration_tokens" not in raw and "select" not in raw
    state = json.loads(raw)
    seq = state["~seq"]
    assert seq["n"] == 2 and seq["last"] == {"toxic.yaml": 1}
    assert all(isinstance(v, int) for v in seq["last"].values())


def test_state_is_written_on_calls_that_match_nothing(settings, rules):
    _toxic(rules)
    _call(settings, READ_PLAIN, tool="Read")
    assert json.loads((st.delivery_dir(settings.state_dir) / "s1.json").read_text())["~seq"]["n"] == 1


def test_no_after_rule_means_no_state_is_read_or_written_on_a_quiet_call(settings, rules):
    write_rule(rules, "plain", match_tools=[SQL], match_content=["insert"])
    _call(settings, READ_PLAIN)
    assert not st.delivery_dir(settings.state_dir).exists()


def test_repeat_off_still_remembers_the_precondition(settings, rules):
    _toxic(rules)
    settings.repeat = False
    _call(settings, READ_SECRET)
    assert _kinds(_call(settings, WRITE)) == ["full"]
    assert _kinds(_call(settings, WRITE)) == ["full"]


def test_delivery_suppression_still_works_beside_the_sequence_state(settings, rules):
    _toxic(rules)
    _call(settings, READ_SECRET)
    assert _kinds(_call(settings, WRITE)) == ["full"]
    assert _kinds(_call(settings, WRITE)) == ["brief"]


def test_a_shadow_after_rule_is_logged_as_shadow_only_once_satisfied(settings, rules):
    _toxic(rules, status="candidate")
    assert _kinds(_call(settings, WRITE)) == []
    _call(settings, READ_SECRET)
    res = _call(settings, WRITE)
    assert _kinds(res) == ["shadow"] and res.text == ""


def test_damaged_sequence_state_reads_as_no_earlier_call(settings, rules):
    _toxic(rules)
    d = st.delivery_dir(settings.state_dir)
    st.save_state(d, "s1", {"~seq": {"n": "x", "last": {"toxic.yaml": "y"}}})
    assert _kinds(_call(settings, WRITE)) == []
    _call(settings, READ_SECRET)
    assert _kinds(_call(settings, WRITE)) == ["full"]


def test_a_future_stamp_in_state_does_not_satisfy_the_rule(settings, rules):
    _toxic(rules)
    st.save_state(st.delivery_dir(settings.state_dir), "s1", {"~seq": {"n": 3, "last": {"toxic.yaml": 99}}})
    assert _kinds(_call(settings, WRITE)) == []


def test_a_future_stamp_is_dropped_so_it_cannot_fire_once_the_counter_catches_up(settings, rules, capsys):
    """Review wave 2, finding 4: {n:1, last:3} used to be ignored only until n reached 3, then
    the rule fired with no precondition. A stamp past the counter is damage: dropped, said once."""
    _toxic(rules)
    st.save_state(st.delivery_dir(settings.state_dir), "D", {"~seq": {"n": 1, "last": {"toxic.yaml": 3}}})
    kinds = [_kinds(_call(settings, WRITE, sid="D")) for _ in range(4)]
    assert kinds == [[], [], [], []]
    err = capsys.readouterr().err
    assert err.count("damaged") == 1 and "toxic.yaml" in err
    saved = st.load_state(st.delivery_dir(settings.state_dir), "D")["~seq"]
    assert saved["last"] == {} and saved["n"] == 5
    # the quiet direction: a real precondition afterwards still satisfies the rule
    _call(settings, READ_SECRET, sid="D")
    assert _kinds(_call(settings, WRITE, sid="D")) == ["full"]
    assert "damaged" not in capsys.readouterr().err


# ---------------------------------------------------------------- check

def test_check_says_an_after_rule_was_not_evaluated(rules, capsys):
    _toxic(rules)
    code = cli.main(["check", SQL, json.dumps(WRITE), "--rules-dir", str(rules), "--session", "s"])
    err = capsys.readouterr().err
    assert code == 0 and "toxic.yaml" in err and "needs an earlier call" in err and "not evaluated" in err


def test_check_is_quiet_about_after_when_the_rule_did_not_match_on_its_own(rules, capsys):
    _toxic(rules)
    cli.main(["check", SQL, json.dumps(READ_PLAIN), "--rules-dir", str(rules), "--session", "s"])
    assert "earlier call" not in capsys.readouterr().err


def test_doctor_names_an_after_rule_that_could_not_be_evaluated_and_why(settings, rules, capsys):
    _toxic(rules)
    run(SQL, WRITE, None, settings=settings)
    code = cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(settings.state_dir)])
    out = capsys.readouterr().out
    assert code == 1
    assert "NOT EVALUATED toxic.yaml" in out and "`after`" in out and "no session id" in out
    assert "call budget was already spent" not in out          # the wrong cause is not named


def test_the_unevaluated_message_for_after_does_not_blame_the_time_budget():
    from instinct_delivery.engine import unevaluated_message
    m = unevaluated_message("toxic.yaml", "after: no session id to remember earlier calls")
    assert "toxic.yaml" in m and "no session id" in m and "budget" not in m


# ---------------------------------------------------------------- breadth, audit

def _tu(tool, ti):
    return {"type": "tool_use", "name": tool, "input": ti}


def _file(path, calls):
    path.write_text(json.dumps({"message": {"content": [_tu(t, i) for t, i in calls]}}) + "\n")


def test_breadth_replays_each_transcript_in_order_with_a_fresh_sequence_per_file(tmp_path, rules):
    from instinct_delivery import measure
    _toxic(rules, within=2)
    t = tmp_path / "t"
    t.mkdir()
    _file(t / "a.jsonl", [(SQL, WRITE), (SQL, READ_SECRET), (SQL, WRITE), (SQL, READ_PLAIN), (SQL, READ_PLAIN),
                          (SQL, WRITE)])
    _file(t / "b.jsonl", [(SQL, WRITE)])           # the precondition in a.jsonl must not leak here
    code, r = measure.breadth(t, rules, threshold=0.99)
    [d] = r["rules"]
    # eligible: every SQL call (7); the rule fires on a.jsonl call 3 only (call 6 is 4 calls after the read)
    assert (d["eligible"], d["fires"]) == (7, 1) and d["rate"] == round(1 / 7, 3)
    assert d["after"] is True


def test_breadth_of_a_rule_without_after_is_unchanged_by_file_boundaries(tmp_path, rules):
    from instinct_delivery import measure
    write_rule(rules, "plain", match_tools=[SQL], match_content=["insert"])
    t = tmp_path / "t"
    t.mkdir()
    _file(t / "a.jsonl", [(SQL, WRITE), (SQL, READ_PLAIN)])
    _file(t / "b.jsonl", [(SQL, WRITE)])
    _, r = measure.breadth(t, rules, threshold=0.99)
    assert (r["rules"][0]["eligible"], r["rules"][0]["fires"], r["rules"][0]["after"]) == (3, 2, False)


def test_audit_judges_an_after_rule_on_its_own_keys_and_says_the_precondition_was_not_rerun(tmp_path, rules, capsys):
    import datetime as dt
    base = dt.datetime(2026, 3, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    stamp = lambda sec: (base + dt.timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")
    _toxic(rules)
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"ts": stamp(5), "rule": "toxic.yaml", "domain": None, "confidence": None,
                               "tool": SQL, "session_id": "s1", "file_path": None,
                               "matched": r"\binsert\s+into\b", "delivery": "full", "bytes": 10}) + "\n")
    t = tmp_path / "t"
    t.mkdir()
    (t / "s1.jsonl").write_text(json.dumps({"type": "assistant", "timestamp": stamp(4), "message": {
        "content": [_tu(SQL, WRITE)]}}) + "\n")                  # the precondition call is NOT in this record
    code = cli.main(["audit", "--log", str(log), "--transcripts", str(t), "--until", "2026-03-11",
                     "--rules-dir", str(rules)])
    out = capsys.readouterr().out
    assert "the rule changed since" not in out
    assert "insert into" in out.lower()
    assert "`after`" in out and "own match keys" in out and "not re-run" in out
    assert code == 2                 # one sampled fire: the BARE flag is NOT EVALUABLE, as always


def test_the_sequence_is_saved_on_a_call_whose_only_hit_is_a_shadow_rule(settings, rules):
    # delivery state is untouched by a shadow hit, so only the sequence makes this call write state
    _toxic(rules)
    write_rule(rules, "watch", match_tools=[SQL], match_content=["integration_tokens"], status="candidate")
    assert _kinds(_call(settings, READ_SECRET)) == ["shadow"]
    assert _kinds(_call(settings, WRITE)) == ["full"]


def test_breadth_does_not_carry_a_precondition_from_one_transcript_into_the_next(tmp_path, rules):
    import os
    from instinct_delivery import measure
    _toxic(rules)                                   # within 20: a leak would show
    t = tmp_path / "t"
    t.mkdir()
    _file(t / "a.jsonl", [(SQL, READ_SECRET), (SQL, WRITE)])
    _file(t / "b.jsonl", [(SQL, WRITE)])
    os.utime(t / "a.jsonl", (1000, 1000))
    os.utime(t / "b.jsonl", (2000, 2000))           # b is replayed after a
    _, r = measure.breadth(t, rules, threshold=0.99)
    assert (r["rules"][0]["eligible"], r["rules"][0]["fires"]) == (3, 1)


def test_the_docs_quote_the_cause_and_the_state_key_the_code_uses():
    from pathlib import Path
    from instinct_delivery import sequence
    root = Path(__file__).resolve().parents[1]
    rules_md = (root / "docs" / "rules.md").read_text(encoding="utf-8")
    reports_md = (root / "docs" / "reports.md").read_text(encoding="utf-8")
    assert sequence.NO_SESSION in rules_md and sequence.NO_SESSION in reports_md
    assert f"`{sequence.KEY}`" in rules_md
    assert "within` from 1 to 200 (default 20)" in rules_md


def test_two_hooks_of_one_session_do_not_lose_the_precondition(rules, tmp_path):
    """Hook A (the precondition) and hook B (an unrelated call) overlap; B must see A's
    state, not overwrite it with the state it loaded before A committed."""
    import threading
    import time
    _toxic(rules)
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state")
    a = run(SQL, READ_SECRET, "s1", settings=s, commit=False)       # holds the session's lock
    box = {}
    t = threading.Thread(target=lambda: box.update(b=run(SQL, READ_PLAIN, "s1", settings=s, commit=False)))
    t.start()
    time.sleep(0.4)                                                  # B is waiting, not reading stale state
    a.commit()
    t.join(10)
    box["b"].commit()
    assert _kinds(_call(s, WRITE)) == ["full"]


def test_a_stuck_lock_costs_a_short_wait_then_the_hook_goes_on(rules, tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LOCK_WAIT_SECONDS", 0.2)
    _toxic(rules)
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state")
    held = run(SQL, READ_SECRET, "s1", settings=s, commit=False)
    res = run(SQL, READ_PLAIN, "s1", settings=s)                     # waits 0.2 s, then proceeds unlocked
    assert res.committed
    held.commit()
