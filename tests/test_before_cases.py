"""`tests` cases with history: `before` calls run first, in order, through the engine in a
fresh scratch session; `expect` applies to the case's own call only."""
import json

import pytest
from conftest import write_rule

from instinct_delivery import cli, state as st
from instinct_delivery.rules import load_rules

SQL = "mcp__db__execute_sql"
AFTER = {"tools": [SQL], "content": [r"\bfrom\s+\w*token"]}
READ = {"tool": SQL, "input": {"query": "select * from integration_tokens;"}}
WRITE = {"tool": SQL, "input": {"query": "insert into m (b) values ('x');"}}


def _rule(rules, tests, **kw):
    write_rule(rules, "toxic", match_tools=[SQL], match_content=[r"\binsert\s+into\b"], after=AFTER,
               tests=tests, **kw)


def _test(rules, capsys, *extra):
    code = cli.main(["test", "--rules-dir", str(rules), *extra])
    return code, capsys.readouterr().out


def test_a_before_list_makes_an_after_rule_fire_and_no_history_keeps_it_silent(rules, capsys):
    _rule(rules, [{**WRITE, "before": [READ], "expect": "fires"},
                  {**WRITE, "expect": "silent"},                           # the quiet direction
                  {**WRITE, "before": [{"tool": SQL, "input": {"query": "select 1"}}], "expect": "silent"}])
    code, out = _test(rules, capsys)
    assert code == 0, out
    assert out.count("PASS toxic") == 3


def test_a_case_that_expects_the_wrong_thing_fails_with_the_history_unchanged(rules, capsys):
    _rule(rules, [{**WRITE, "before": [READ], "expect": "silent"}])
    code, out = _test(rules, capsys)
    assert code == 1 and "FAIL toxic #1" in out


def test_expect_applies_to_the_own_call_only_not_to_the_before_calls(rules, capsys):
    # the before call matches the rule's own filters too (and fires nothing: no history yet);
    # only the final call is judged
    write_rule(rules, "self", match_tools=[SQL], match_content=["insert"],
               after={"tools": [SQL], "content": ["insert"]},
               tests=[{**WRITE, "before": [WRITE], "expect": "fires"},
                      {**WRITE, "expect": "silent"}])
    code, out = _test(rules, capsys)
    assert code == 0, out


def test_each_case_gets_a_fresh_session(rules, capsys):
    _rule(rules, [{**WRITE, "before": [READ], "expect": "fires"},
                  {**WRITE, "expect": "silent"},                           # must not inherit the READ above
                  {**WRITE, "before": [READ], "expect": "fires"}])
    assert _test(rules, capsys)[0] == 0


def test_before_cases_never_touch_the_real_state_dir(rules, capsys, tmp_path):
    _rule(rules, [{**WRITE, "before": [READ], "expect": "fires"}])
    _test(rules, capsys)
    assert not (tmp_path / "state").exists()


def test_a_case_without_before_on_an_after_rule_goes_through_the_engine(rules, capsys):
    # the bare matcher ignores `after` and would call this "fires"; the engine path does not
    _rule(rules, [{**WRITE, "expect": "fires"}])
    code, out = _test(rules, capsys)
    assert code == 1 and "FAIL toxic #1" in out and "was silent" in out


def test_before_is_loaded_whole_or_the_rule_is_rejected(rules):
    bad = [{"expect": "fires", "tool": SQL, "input": {}, "before": "x"},
           {"expect": "fires", "tool": SQL, "input": {}, "before": [{"tool": SQL}]},
           {"expect": "fires", "tool": SQL, "input": {}, "before": [{"tool": "", "input": {}}]},
           {"expect": "fires", "tool": SQL, "input": {}, "before": [{"tool": SQL, "input": {}, "x": 1}]},
           {"expect": "fires", "tool": SQL, "input": {}, "before": ["x"]},
           {"expect": "fires", "tool": SQL, "input": {}, "before": [{"tool": SQL, "input": {}}] * 101}]
    for i, case in enumerate(bad):
        write_rule(rules, f"bad{i}", match_tools=[SQL], tests=[case])
    rs = load_rules(rules)
    assert len(rs.rejected) == 6 and all("before" in r.reason for r in rs.rejected)


def test_an_empty_before_list_is_a_plain_case(rules, capsys):
    write_rule(rules, "p", match_tools=[SQL], tests=[{**WRITE, "before": [], "expect": "fires"}])
    assert _test(rules, capsys)[0] == 0


def test_json_output_names_the_history_length(rules, capsys):
    _rule(rules, [{**WRITE, "before": [READ], "expect": "fires"}])
    code = cli.main(["test", "--rules-dir", str(rules), "--json"])
    j = json.loads(capsys.readouterr().out)
    assert code == 0 and j["results"][0]["before"] == 1 and j["results"][0]["pass"] is True


def test_a_before_call_is_not_judged_even_if_the_rule_fires_on_it(rules, capsys):
    write_rule(rules, "any", match_tools=[SQL], tests=[
        {**WRITE, "before": [WRITE], "expect": "fires"}])
    assert _test(rules, capsys)[0] == 0
