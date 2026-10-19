"""A Bash write counts as an edit for rule matching and for breadth: an Edit- or
Write-scoped rule fires on a visible shell write, once, and a rule scoped to
neither does not."""
import json

from conftest import write_rule

from instinct_delivery import measure
from instinct_delivery.engine import Settings, run
from instinct_delivery.match import match
from instinct_delivery.rules import load_rules


def test_an_edit_scoped_rule_fires_on_a_sed_in_place_edit(rules):
    write_rule(rules, "e", match_tools=["Edit"], match_paths=[".py"], match_content=["print"])
    pool = load_rules(rules).fire
    [(_r, ev)] = match(pool, "Bash", {"command": "sed -i 's/x/print(1)/' src/m.py"})
    assert ev.via == "bash-write" and ev.path == "src/m.py"
    assert match(pool, "Bash", {"command": "sed -i 's/x/print(1)/' src/m.py"}, bash_writes=False) == []


def test_an_edit_scoped_rule_fires_on_a_redirect_write(rules):
    write_rule(rules, "e", match_tools=["Edit"], match_content=["secret"])
    pool = load_rules(rules).fire
    [(_r, ev)] = match(pool, "Bash", {"command": "echo secret > f.txt"})
    assert ev.via == "bash-write"


def test_a_rule_scoped_to_write_and_edit_is_delivered_once(rules):
    write_rule(rules, "both", match_tools=["Write", "Edit"], match_content=["secret"])
    assert len(match(load_rules(rules).fire, "Bash", {"command": "echo secret > f.txt"})) == 1


def test_a_rule_scoped_to_neither_does_not_fire_on_a_bash_write(rules):
    write_rule(rules, "r", match_tools=["Read"], match_content=["secret"])
    assert match(load_rules(rules).fire, "Bash", {"command": "echo secret > f.txt"}) == []


def _jsonl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def test_breadth_counts_bash_writes_as_eligible_for_an_edit_rule(tmp_path, rules):
    write_rule(rules, "p", match_tools=["Edit"], match_content=["print"])
    calls = [{"type": "tool_use", "name": "Edit",
              "input": {"file_path": f"f{i}.py", "new_string": "print(1)"}} for i in range(4)]
    calls += [{"type": "tool_use", "name": "Bash", "input": {"command": f"echo x > f{i}.txt"}}
              for i in range(12)]
    _jsonl(tmp_path / "t" / "s.jsonl", [{"message": {"content": calls}}])
    code, r = measure.breadth(tmp_path / "t", rules)
    [d] = r["rules"]
    assert (d["fires"], d["eligible"]) == (4, 16)


KEY = "AKIA" + "ABCDEFGHIJKLMNOP"      # a synthetic key-shaped string


def test_the_leaked_secret_now_fires_a_content_rule(rules):
    write_rule(rules, "key", match_tools=["Write"], match_content=["AKIA[A-Z0-9]{16}"])
    s = Settings(rules_dir=rules, state_dir=rules.parent / "state")
    res = run("Bash", {"command": "echo " + KEY + ">creds.cfg"}, "s1", settings=s)
    assert res.text and "key" in res.text
