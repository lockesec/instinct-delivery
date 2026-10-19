"""Rendering and logging of rules that lack optional fields (`domain`,
`confidence`): no placeholder question marks, and the log row says null."""
import json

from conftest import write_rule

from instinct_delivery import measure, state as st
from instinct_delivery.engine import Settings, full_lines, run
from instinct_delivery.rules import load_rules


def test_a_rule_without_domain_or_confidence_has_no_question_marks(rules):
    write_rule(rules, "bare", match_tools=["Edit"], action="do it")
    (rule,) = load_rules(rules).fire
    text = "\n".join(full_lines(rule))
    assert "?" not in text
    assert "INSTINCT bare: do it" in text and "INSTINCT ACTIVATED [bare]" in text
    write_rule(rules, "dom", match_tools=["Edit"], action="x", domain="ops")
    (rule,) = [r for r in load_rules(rules).fire if r.slug == "dom"]
    assert "ACTIVATED [dom, ops]" in "\n".join(full_lines(rule))


def test_persistence_loss_still_recognises_a_bare_rule_delivery(tmp_path):
    tdir = tmp_path / "t"
    tdir.mkdir()
    shown = {"type": "attachment", "content": "INSTINCT ACTIVATED [bare]: x"}
    (tdir / "s1.jsonl").write_text(json.dumps(shown) + "\n")
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"ts": st.now_iso(), "rule": "bare.yaml", "delivery": "full",
                               "session_id": "s1", "bytes": 10}) + "\n")
    r = measure.persistence_loss(log, tdir, 7)
    r = r[1] if isinstance(r, tuple) else r
    assert r["lost"] == 0, r


def test_a_rule_without_domain_or_confidence_logs_null(tmp_path, rules):
    write_rule(rules, "bare", match_tools=["Edit"])
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state")
    res = run("Edit", {"file_path": "a.py", "new_string": "x"}, "s1", settings=s)
    row = res.rows[0]
    assert row["domain"] is None and row["confidence"] is None


def test_a_rule_with_them_logs_them(tmp_path, rules):
    write_rule(rules, "full", match_tools=["Edit"], domain="code", confidence=0.7)
    s = Settings(rules_dir=rules, state_dir=tmp_path / "state")
    row = run("Edit", {"file_path": "a.py", "new_string": "x"}, "s1", settings=s).rows[0]
    assert row["domain"] == "code" and row["confidence"] == 0.7


def test_a_full_delivery_says_its_first_line_once(rules):
    write_rule(rules, "multi", match_tools=["Edit"], domain="d",
               action="First line of advice,\nand the rest of it.")
    write_rule(rules, "single", match_tools=["Edit"], action="Just one line.")
    write_rule(rules, "long", match_tools=["Edit"], action="x" * 120 + "\nmore")
    by = {r.slug: "\n".join(full_lines(r)) for r in load_rules(rules).fire}
    assert by["multi"].count("First line of advice,") == 1
    assert by["multi"].splitlines() == ["⚠️ INSTINCT [d] multi: First line of advice,",
                                        "INSTINCT ACTIVATED [multi, d]: and the rest of it."]
    assert by["single"].count("Just one line.") == 1
    assert by["single"].splitlines()[1] == "INSTINCT ACTIVATED [single]"
    # a first line cut to fit the head is given whole in the body
    assert "x" * 120 in by["long"]


import pytest  # noqa: E402

LINE_BREAKS = ["\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


def test_the_line_break_set_is_every_splitlines_boundary():
    found = {chr(c) for c in range(0x110000) if len(("a" + chr(c) + "b").splitlines()) == 2}
    assert found <= set(LINE_BREAKS), found - set(LINE_BREAKS)


@pytest.mark.parametrize("brk", LINE_BREAKS, ids=lambda b: "U+%04X" % ord(b[0]) + ("+LF" if len(b) > 1 else ""))
@pytest.mark.parametrize("first", ["First", "F" * 100])
def test_a_full_delivery_drops_no_text_at_any_line_break(rules, brk, first):
    write_rule(rules, "brk", match_tools=["Edit"], action=f"{first}{brk}hidden tail\nSecond")
    (rule,) = load_rules(rules).fire
    head, block = full_lines(rule)
    text = head + "\n" + block
    assert text.count("hidden tail") == 1 and text.count("Second") == 1
    assert "hidden tail" in block.split(": ", 1)[1]
