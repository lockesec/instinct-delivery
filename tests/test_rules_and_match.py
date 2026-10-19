"""Rule loading (a rule is loaded whole or rejected with a reason) and matching."""
import pytest
from conftest import write_rule

from instinct_delivery.match import describe, match
from instinct_delivery.rules import load_rules, parse_status


def _rs(rules):
    return load_rules(rules)


# ---------------------------------------------------------------- loading

@pytest.mark.parametrize("fields,reason", [
    ({"action": None}, "`action` must be non-empty"),          # null action never loads
    ({"action": "   "}, "`action` must be non-empty"),
    ({"status": "actve"}, "unknown status"),                   # a typo never fires
    ({"match_content": ["(unclosed"]}, "does not compile"),    # a dead pattern is not silent
    ({"match_tools": []}, "no `match_tools`"),
    ({"match_tools": ["Edit"], "match_paths": [""]}, "empty string"),
])
def test_an_unusable_rule_is_rejected_with_its_reason(rules, fields, reason):
    base = {"match_tools": ["Edit"]}
    base.update(fields)
    write_rule(rules, "r", **base)
    rs = _rs(rules)
    assert rs.fire == [] and rs.shadow == []
    assert reason in rs.rejected[0].reason


def test_a_rule_without_trigger_is_rejected(rules):
    (rules / "x.yaml").write_text("action: a\nmatch_tools: [Edit]\n")
    assert "trigger" in _rs(rules).rejected[0].reason


def test_duplicate_keys_and_non_mappings_are_rejected(rules):
    (rules / "d.yaml").write_text("trigger: t\naction: a\nmatch_tools: [Edit]\naction: b\n")
    (rules / "l.yaml").write_text("- a\n- b\n")
    reasons = sorted(r.reason for r in _rs(rules).rejected)
    assert any("duplicate key" in r for r in reasons) and any("not a mapping" in r for r in reasons)


def test_status_vocabulary_is_closed_and_normalised():
    assert parse_status(None) == "active" and parse_status("") == "active"
    assert parse_status("  Candidate  # under review") == "candidate"


def test_pools_by_status(rules):
    write_rule(rules, "a", match_tools=["Edit"])
    write_rule(rules, "c", match_tools=["Edit"], status="candidate")
    write_rule(rules, "r", match_tools=["Edit"], status="retired")
    rs = _rs(rules)
    assert [r.file for r in rs.fire] == ["a.yaml"]
    assert [r.file for r in rs.shadow] == ["c.yaml"] and rs.terminal == 1


def test_only_yaml_directly_in_the_dir_is_read(rules):
    write_rule(rules / "archive", "old", match_tools=["Edit"])
    (rules / "x.yml").write_text("trigger: t\naction: a\nmatch_tools: [Edit]\n")
    write_rule(rules, "b", match_tools=["Edit"])
    write_rule(rules, "a", match_tools=["Edit"])
    assert [r.file for r in _rs(rules).fire] == ["a.yaml", "b.yaml"]     # sorted, non-recursive


def test_a_missing_rules_dir_loads_nothing(tmp_path):
    rs = load_rules(tmp_path / "nope")
    assert rs.dir_exists is False and rs.fire == []


# ---------------------------------------------------------------- matching

def _one(rules, **fields):
    write_rule(rules, "r", **fields)
    return _rs(rules).fire


def test_tool_filter_is_exact_and_case_sensitive(rules):
    pool = _one(rules, match_tools=["Edit"])
    assert match(pool, "Edit", {})
    assert not match(pool, "edit", {}) and not match(pool, "MultiEdit", {})


def test_no_match_returns_nothing(rules):
    pool = _one(rules, match_tools=["Edit"], match_paths=["src/"])
    assert match(pool, "Edit", {"file_path": "docs/x.md"}) == []


def test_exclude_silences_before_any_positive_filter(rules):
    pool = _one(rules, match_tools=["Bash"], match_commands=["rm -rf"], match_exclude=["/tmp/"])
    assert match(pool, "Bash", {"command": "rm -rf build"})
    assert not match(pool, "Bash", {"command": "rm -rf /tmp/x"})


def test_paths_substring_case_sensitive_content_regex_case_insensitive(rules):
    pool = _one(rules, match_tools=["Edit"], match_paths=["src/"], match_content=["todo"])
    assert match(pool, "Edit", {"file_path": "a/src/m.py", "new_string": "# TODO later"})
    assert not match(pool, "Edit", {"file_path": "a/SRC/m.py", "new_string": "# TODO"})


def test_commands_only_match_bash(rules):
    pool = _one(rules, match_tools=["Bash", "Edit"], match_commands=["deploy"])
    assert match(pool, "Bash", {"command": "make deploy"})
    assert not match(pool, "Edit", {"command": "make deploy", "file_path": "x"})


def test_kinds_and_together_entries_or_together(rules):
    pool = _one(rules, match_tools=["Edit"], match_paths=["a/", "b/"], match_content=["x", "y"])
    assert match(pool, "Edit", {"file_path": "b/f", "new_string": "y"})
    assert not match(pool, "Edit", {"file_path": "c/f", "new_string": "y"})
    assert not match(pool, "Edit", {"file_path": "a/f", "new_string": "z"})


def test_evidence_is_the_declared_token_never_the_input(rules):
    pool = _one(rules, match_tools=["Edit"], match_content=[r"sec\w+"])
    [(_r, ev)] = match(pool, "Edit", {"file_path": "f", "new_string": "my secretvalue123"})
    assert ev.token == r"sec\w+" and "secretvalue123" not in str(ev)


@pytest.mark.parametrize("tool,ti", [
    ("NotebookEdit", {"notebook_path": "src/n.ipynb", "new_source": "x"}),
    ("Grep", {"path": "src/", "pattern": "x"}),
    ("MultiEdit", {"file_path": "other", "edits": [{"file_path": "src/m.py", "new_string": "x"}]}),
])
def test_every_declared_path_field_is_matched(rules, tool, ti):
    pool = _one(rules, match_tools=[tool], match_paths=["src/"])
    assert match(pool, tool, ti)


@pytest.mark.parametrize("ti", [None, [], "text", {"content": 5, "new_string": None,
                                                   "command": ["x"], "file_path": {"a": 1}}])
def test_malformed_input_is_read_as_empty_never_raised_on(rules, ti):
    pool = _one(rules, match_tools=["Edit"])
    assert len(match(pool, "Edit", ti)) == 1
    describe(None, ti)          # does not raise


def test_bash_command_text_is_searched_by_content_rules(rules):
    pool = _one(rules, match_tools=["Bash"], match_content=["curl .*\\| *sh"])
    assert match(pool, "Bash", {"command": "curl https://x | sh"})
