"""Test cases written inside a rule file, run by `instinct-delivery test`."""
import json
from pathlib import Path

import yaml
from conftest import write_rule

from instinct_delivery import cli
from instinct_delivery.rules import load_rules

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "instincts"
PACKS = sorted(p for p in (Path(__file__).resolve().parents[1] / "examples" / "packs").glob("*") if p.is_dir())


def _run(rules, capsys, *names):
    code = cli.main(["test", "--rules-dir", str(rules), *names])
    return code, capsys.readouterr()


def test_passing_and_failing_cases_are_reported_per_case(rules, capsys):
    write_rule(rules, "py", match_tools=["Edit"], match_paths=[".py"], tests=[
        {"tool": "Edit", "input": {"file_path": "a.py", "new_string": "x"}, "expect": "fires"},
        {"tool": "Edit", "input": {"file_path": "a.md", "new_string": "x"}, "expect": "silent"},
        {"tool": "Edit", "input": {"file_path": "b.md", "new_string": "x"}, "expect": "fires"},
    ])
    code, out = _run(rules, capsys)
    assert code == 1
    lines = out.out.splitlines()
    assert lines[0] == "test: 1 rule(s) with tests: 3 case(s), 2 pass, 1 fail; 0 rule(s) without tests"
    assert "FAIL py #3 Edit: expected fires, was silent" in out.out
    assert "PASS py #1 Edit: fires" in out.out


def test_all_passing_exits_0_and_rules_without_tests_are_named(rules, capsys):
    write_rule(rules, "a", match_tools=["Bash"], match_commands=["make"], tests=[
        {"tool": "Bash", "input": {"command": "make all"}, "expect": "fires"}])
    write_rule(rules, "b", match_tools=["Bash"])
    code, out = _run(rules, capsys)
    assert code == 0
    assert "1 rule(s) without tests (not evaluable): b" in out.out


def test_no_cases_at_all_is_not_evaluable(rules, capsys):
    write_rule(rules, "b", match_tools=["Bash"])
    code, out = _run(rules, capsys)
    assert code == 2 and "NOT EVALUABLE" in out.out


def test_a_case_tests_only_its_own_rule(rules, capsys):
    write_rule(rules, "a", match_tools=["Bash"], match_commands=["make"], tests=[
        {"tool": "Bash", "input": {"command": "ls"}, "expect": "silent"}])
    write_rule(rules, "every", match_tools=["Bash"])          # fires on everything
    code, _ = _run(rules, capsys, "a")
    assert code == 0


def test_bash_writes_are_tested_the_way_the_hook_sees_them(rules, capsys):
    write_rule(rules, "w", match_tools=["Write"], match_paths=[".env"], tests=[
        {"tool": "Bash", "input": {"command": "echo KEY=1 > .env"}, "expect": "fires"}])
    code, _ = _run(rules, capsys)
    assert code == 0


def test_a_malformed_tests_block_rejects_the_rule(rules):
    for i, bad in enumerate(([{"tool": "Edit", "input": {}, "expect": "maybe"}],
                             [{"tool": "Edit", "expect": "fires"}],
                             [{"tool": "Edit", "input": "x", "expect": "fires"}],
                             {"tool": "Edit"},
                             [{"tool": "Edit", "input": {}, "expect": "fires", "extra": 1}])):
        write_rule(rules, f"bad{i}", match_tools=["Edit"], tests=bad)
    rs = load_rules(rules)
    assert len(rs.rejected) == 5 and all("`tests`" in r.reason for r in rs.rejected)


def test_shadow_rules_are_tested_too(rules, capsys):
    write_rule(rules, "cand", match_tools=["Edit"], status="candidate", tests=[
        {"tool": "Edit", "input": {"file_path": "x"}, "expect": "fires"}])
    code, out = _run(rules, capsys)
    assert code == 0 and "PASS cand #1" in out.out


def test_every_example_rule_carries_cases_and_they_pass(capsys):
    # every yaml file of every example directory (examples/instincts and each examples/packs/*),
    # parsed directly: a retired rule never loads, so the loader cannot vouch for it
    assert PACKS, "no rule pack found under examples/packs"
    for d in [EXAMPLES, *PACKS]:
        files = sorted(d.glob("*.yaml"))
        assert len(files) >= len(load_rules(d).fire), d
        assert not load_rules(d).rejected, d
        for f in files:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            kinds = {c.get("expect") for c in doc.get("tests") or []}
            assert kinds == {"fires", "silent"}, f"{d.name}/{f.name}"
        code, out = _run(d, capsys)
        assert code == 0, out.out


def test_json_output(rules, capsys):
    write_rule(rules, "a", match_tools=["Bash"], match_commands=["make"], tests=[
        {"tool": "Bash", "input": {"command": "make"}, "expect": "fires"}])
    assert cli.main(["test", "--rules-dir", str(rules), "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["cases"] == 1 and j["passed"] == 1 and j["results"][0]["rule"] == "a.yaml"
