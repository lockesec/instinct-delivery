"""The delivery policy: suppression, re-arm, cap, budget, shadow, isolation."""
import datetime as dt
import json

import pytest
from conftest import log_rows, write_rule

from instinct_delivery import engine
from instinct_delivery.engine import Settings, run

T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
EDIT = {"file_path": "src/m.py", "new_string": "x"}


def _run(s, sid="s1", now=T0, tool="Edit", ti=None):
    return run(tool, EDIT if ti is None else ti, sid, settings=s, now=now)


def _kinds(res):
    return [r["delivery"] for r in res.rows]


# ---------------------------------------------------------------- negative controls

def test_no_match_emits_nothing_and_logs_nothing(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], match_paths=["docs/"])
    res = _run(settings)
    assert res.text == "" and res.rows == [] and log_rows(settings) == []


def test_an_excluded_path_is_silent(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], match_exclude=["src/"])
    assert _run(settings).text == ""


def test_a_retired_rule_never_fires_and_never_shadows(settings, rules):
    write_rule(rules, "r", match_tools=["Edit"], status="retired")
    assert _run(settings).rows == []


# ---------------------------------------------------------------- suppression

def test_first_fire_full_repeat_brief_with_count(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action="line one\nline two")
    r1, r2 = _run(settings), _run(settings, now=T0 + dt.timedelta(minutes=1))
    assert _kinds(r1) == ["full"] and "INSTINCT ACTIVATED [a" in r1.text
    assert _kinds(r2) == ["brief"] and "(x2 this session" in r2.text and "line two" not in r2.text


def test_the_rearm_window_restores_full_text(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    _run(settings)
    assert _kinds(_run(settings, now=T0 + dt.timedelta(minutes=91))) == ["full"]
    settings.rearm_min = 0
    assert _kinds(_run(settings, now=T0 + dt.timedelta(days=3))) == ["brief"]


def test_repeat_off_is_byte_identical_across_fires(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    settings.repeat = False
    first = _run(settings).text
    assert "INSTINCT ACTIVATED" in first
    assert first == _run(settings).text
    assert not (settings.state_dir / "delivery").exists()


def test_no_session_id_means_no_suppression_and_no_state(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    assert _kinds(_run(settings, sid=None)) == _kinds(_run(settings, sid=None)) == ["full"]
    assert log_rows(settings)[0]["session_id"] is None
    assert not (settings.state_dir / "delivery").exists()


def test_sessions_do_not_share_state(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    _run(settings, sid="s1")
    assert _kinds(_run(settings, sid="s2")) == ["full"]


def test_brief_cap_mutes_then_rearm_reopens(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    settings.brief_cap = 2
    kinds = [_kinds(_run(settings, now=T0 + dt.timedelta(minutes=i)))[0] for i in range(5)]
    assert kinds == ["full", "brief", "brief", "muted", "muted"]
    assert _kinds(_run(settings, now=T0 + dt.timedelta(minutes=200))) == ["full"]


@pytest.mark.parametrize("bad", ["", "{", "[1]", '{"a.yaml": {"ts": "not a time", "n": 1}}'])
def test_damaged_state_fails_toward_full_delivery(settings, rules, bad):
    write_rule(rules, "a", match_tools=["Edit"])
    d = settings.state_dir / "delivery"
    d.mkdir(parents=True)
    (d / "s1.json").write_text(bad)
    assert _kinds(_run(settings)) == ["full"]


def test_an_unwritable_state_dir_never_blocks_delivery(tmp_path, rules):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = Settings(rules_dir=rules, state_dir=blocker / "sub")
    write_rule(rules, "a", match_tools=["Edit"])
    assert "INSTINCT ACTIVATED" in _run(s).text


def test_a_session_id_cannot_escape_the_state_dir(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    _run(settings, sid="../../escape")
    files = list((settings.state_dir / "delivery").iterdir())
    assert len(files) == 1 and files[0].parent == settings.state_dir / "delivery"


@pytest.mark.parametrize("var,val,attr,expect", [
    ("INSTINCT_REARM_MIN", "soon", "rearm_min", 90),
    ("INSTINCT_REARM_MIN", "-5", "rearm_min", 90),
    ("INSTINCT_PAYLOAD_BUDGET", "lots", "budget", 8000),
    ("INSTINCT_PAYLOAD_BUDGET", "off", "budget", 0),
    ("INSTINCT_BRIEF_CAP", "x", "brief_cap", 6),
])
def test_a_typo_in_a_knob_keeps_the_default_never_zero(monkeypatch, var, val, attr, expect):
    monkeypatch.setenv(var, val)
    assert getattr(Settings.from_env(), attr) == expect


def test_every_documented_off_value_disables_suppression(monkeypatch):
    for v in ("full", "0", "off", "no", "false"):
        monkeypatch.setenv("INSTINCT_REPEAT", v)
        assert Settings.from_env().repeat is False


# ---------------------------------------------------------------- budget

def _many(rules, n, size, conf=None):
    for i in range(n):
        write_rule(rules, f"r{i:02d}", match_tools=["Edit"], action="x" * size,
                   confidence=(conf[i] if conf else 0.5))


def test_the_budget_holds_every_emitted_byte(settings, rules):
    _many(rules, 12, 900)
    settings.budget = 4000
    res = _run(settings)
    assert len(res.text.encode()) <= 4000
    assert "withheld" in res.lines[-1] and "budget" in _kinds(res)


def test_under_budget_the_strongest_rule_is_kept(settings, rules):
    _many(rules, 3, 2500, conf=[0.2, 0.9, 0.5])
    settings.budget = 4000
    res = _run(settings)
    kept = [r["rule"] for r in res.rows if r["delivery"] == "full"]
    assert kept == ["r01.yaml"]


def test_a_withheld_rule_is_delivered_in_full_on_its_next_fire(settings, rules):
    _many(rules, 2, 2500, conf=[0.9, 0.1])
    settings.budget = 4000
    first = _run(settings)
    assert dict((r["rule"], r["delivery"]) for r in first.rows)["r01.yaml"] == "budget"
    (rules / "r00.yaml").unlink()
    second = _run(settings, now=T0 + dt.timedelta(minutes=1))
    assert [r["delivery"] for r in second.rows] == ["full"]      # not "brief": it was never delivered


def test_a_rule_bigger_than_the_whole_budget_is_truncated_not_starved(settings, rules):
    write_rule(rules, "huge", match_tools=["Edit"], action="y" * 20000)
    settings.budget = 4000
    res = _run(settings)
    assert _kinds(res) == ["truncated"] and "TRUNCATED" in res.text
    assert len(res.text.encode()) <= 4000


def test_budget_zero_is_byte_identical_to_unbudgeted(settings, rules, monkeypatch):
    _many(rules, 12, 900)
    settings.budget = 0
    settings.repeat = False
    a = _run(settings).text
    b = _run(settings).text
    assert a == b and "withheld" not in a and len(a.encode()) > 10000


# ---------------------------------------------------------------- shadow

def test_shadow_is_byte_identical_and_never_touches_state(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    write_rule(rules, "cand", match_tools=["Edit"], status="candidate")
    on = _run(settings, sid="s1")
    settings.shadow = False
    off = _run(settings, sid="s2")
    assert on.text.strip(), "nothing emitted: the parity check would be vacuous"
    assert on.text == off.text
    state = json.loads((settings.state_dir / "delivery" / "s1.json").read_text())
    assert "cand.yaml" not in state
    shadow_rows = [r for r in log_rows(settings) if r["delivery"] == "shadow"]
    assert shadow_rows == [dict(shadow_rows[0], shadow=True, bytes=0)] and shadow_rows[0]["rule"] == "cand.yaml"


# ---------------------------------------------------------------- isolation and log

def test_one_failing_rule_never_silences_another(settings, rules, monkeypatch, capsys):
    write_rule(rules, "bad", match_tools=["Edit"], confidence=0.9)
    write_rule(rules, "good", match_tools=["Edit"], confidence=0.1)
    real = engine.full_lines

    def flaky(rule):
        if rule.file == "bad.yaml":
            raise RuntimeError("boom")
        return real(rule)
    monkeypatch.setattr(engine, "full_lines", flaky)
    res = _run(settings)
    assert "INSTINCT ACTIVATED [good" in res.text and res.errors[0][0] == "bad.yaml"
    assert "bad.yaml skipped" in capsys.readouterr().err
    assert json.loads((settings.state_dir / "delivery" / "s1.json").read_text())["good.yaml"]["n"] == 1


def test_rows_of_one_call_share_one_timestamp_and_carry_the_schema(settings, rules):
    from instinct_delivery.state import ROW_FIELDS
    write_rule(rules, "a", match_tools=["Edit"])
    write_rule(rules, "b", match_tools=["Edit"])
    rows = _run(settings).rows
    assert len({r["ts"] for r in rows}) == 1
    assert all(tuple(r)[:len(ROW_FIELDS)] == ROW_FIELDS for r in rows)


def test_the_budget_holds_at_every_boundary(tmp_path):
    """A sweep, not one lucky case: rule sizes and budgets chosen so a
    miscounted newline or an unreserved closing line would overshoot."""
    from conftest import write_rule as wr
    over = []
    for budget in range(900, 4200, 53):
        for size in (150, 333, 610):
            rd = tmp_path / f"r{budget}-{size}"
            for i in range(9):
                wr(rd, f"r{i}", match_tools=["Edit"], action="z" * (size + i), confidence=0.5)
            s = Settings(rules_dir=rd, state_dir=tmp_path / "st", budget=budget, repeat=False)
            n = len(run("Edit", EDIT, None, settings=s, now=T0).text.encode())
            if n > budget:
                over.append((budget, size, n))
    assert over == []


def test_the_budget_holds_when_the_withheld_line_is_full(tmp_path):
    """Many withheld rules with long names fill the closing line to its
    reserve, so any undercount elsewhere shows as an overshoot."""
    from conftest import write_rule as wr
    over = []
    for budget in range(700, 2600, 29):
        rd = tmp_path / f"w{budget}"
        for i in range(40):
            wr(rd, f"a-deliberately-long-rule-name-{i:03d}", match_tools=["Edit"],
               action=("q" * 40 + "\n") * 3, confidence=0.5)
        s = Settings(rules_dir=rd, state_dir=tmp_path / "st", budget=budget, repeat=False)
        n = len(run("Edit", EDIT, None, settings=s, now=T0).text.encode())
        if n > budget:
            over.append((budget, n))
    assert over == []


def test_the_byte_counter_equals_the_bytes_emitted(settings, rules):
    """The budget's unit and the emitted text must be one measure: every row's
    bytes sum to exactly the delivered text (newlines included)."""
    for i in range(5):
        write_rule(rules, f"r{i}", match_tools=["Edit"], action=f"line a {i}\nline b ✓")
    settings.repeat = False
    res = _run(settings)
    assert res.rows and sum(r["bytes"] for r in res.rows) == len(res.text.encode("utf-8"))
    assert engine._nbytes(res.lines) == len(res.text.encode("utf-8"))
