"""A record must not claim more than happened: advice cut to fit a budget is a
partial delivery, never a full one, and does not suppress the next full delivery."""
import datetime as dt
import json

from conftest import write_rule

from instinct_delivery.engine import run


LONG = "first line\n" + "x" * 12000 + "\nIMPORTANT FINAL INSTRUCTION"


T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)


EDIT = {"file_path": "src/m.py", "new_string": "x"}


def _run(s, sid="s1", now=T0):
    return run("Edit", EDIT, sid, settings=s, now=now)


def test_truncated_then_budget_raised_delivers_the_full_advice(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action=LONG)
    first = _run(settings)
    assert first.rows[0]["delivery"] == "truncated"
    assert "IMPORTANT FINAL INSTRUCTION" not in first.text
    settings.budget = 20000
    second = _run(settings, now=T0 + dt.timedelta(minutes=1))
    assert second.rows[0]["delivery"] == "full"
    assert "IMPORTANT FINAL INSTRUCTION" in second.text
    assert "delivered earlier" not in second.text


def test_truncated_never_claims_full_text_was_delivered(settings, rules):
    """Budget unchanged: the repeat may not be a 'full text delivered earlier' brief."""
    write_rule(rules, "a", match_tools=["Edit"], action=LONG)
    _run(settings)
    again = _run(settings, now=T0 + dt.timedelta(minutes=1))
    assert again.rows[0]["delivery"] != "brief"
    assert "delivered earlier" not in again.text


def test_truncation_leaves_no_full_delivery_stamp(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action=LONG)
    _run(settings)
    rec = json.loads((settings.state_dir / "delivery" / "s1.json").read_text())["a.yaml"]
    assert "ts" not in rec


def test_control_full_delivery_still_suppresses(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action="short advice")
    assert _run(settings).rows[0]["delivery"] == "full"
    second = _run(settings, now=T0 + dt.timedelta(minutes=1))
    assert second.rows[0]["delivery"] == "brief"
