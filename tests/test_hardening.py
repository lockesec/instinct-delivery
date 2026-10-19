"""Regression pins. Each test names the failure it guards against; every one was
red on the code before its fix.

The common thread: a number or a state change must not claim something that
did not happen. Advice counts as delivered only once it has been written, a
malformed input is reported rather than read as a zero, and a limit that is
promised holds on every path."""
import datetime as dt
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import log_rows, write_rule

from instinct_delivery import cli, engine, hook, measure
from instinct_delivery import state as st
from instinct_delivery.bashwrites import write_targets
from instinct_delivery.engine import Settings, run
from instinct_delivery.match import match
from instinct_delivery.rules import load_rules

T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
EDIT = {"file_path": "src/m.py", "new_string": "x"}
SRC = Path(__file__).resolve().parents[1] / "src"


def _run(s, sid="s1", now=T0, tool="Edit", ti=None):
    return run(tool, EDIT if ti is None else ti, sid, settings=s, now=now)


def _kinds(res):
    return [r["delivery"] for r in res.rows]


def _payload(sid="s1", tool="Edit", ti=None):
    return json.dumps({"tool_name": tool, "tool_input": EDIT if ti is None else ti,
                       "session_id": sid})


# ---------------------------------------------------------------- emitted is the commit point

def test_a_non_utf8_stdout_still_delivers_and_exits_0(tmp_path, rules):
    """A stdout whose encoding cannot carry the glyphs (a cp1252 pipe, as on a
    Windows console) must not crash the hook after the delivery was recorded."""
    write_rule(rules, "a", match_tools=["Edit"], action="do the thing ✓")
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=str(SRC),
               INSTINCT_RULES_DIR=str(rules), INSTINCT_STATE_DIR=str(tmp_path / "st"))
    p = subprocess.run([sys.executable, "-m", "instinct_delivery", "hook"],
                       input=_payload().encode(), capture_output=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    ctx = json.loads(p.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert "INSTINCT ACTIVATED [a" in ctx and "✓" in ctx


class _BrokenStdout(io.StringIO):
    def write(self, s):
        raise UnicodeEncodeError("charmap", s, 0, 1, "cannot encode")


def test_a_failed_write_records_no_delivery_and_no_state(settings, rules):
    """Negative control for the commit point: if the envelope never reached
    stdout, the log must not say `full` and the next fire must not be `brief`."""
    write_rule(rules, "a", match_tools=["Edit"])
    err = io.StringIO()
    code = hook.main(io.StringIO(_payload()), _BrokenStdout(), err, settings=settings)
    assert code == 0 and "could not write" in err.getvalue()
    assert log_rows(settings) == []
    assert not (settings.state_dir / "delivery" / "s1.json").exists()
    out = io.StringIO()
    hook.main(io.StringIO(_payload()), out, io.StringIO(), settings=settings)
    assert "INSTINCT ACTIVATED [a" in out.getvalue()      # still full, never brief


def test_a_successful_write_commits_rows_and_state(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"])
    out = io.StringIO()
    hook.main(io.StringIO(_payload()), out, io.StringIO(), settings=settings)
    assert [r["delivery"] for r in log_rows(settings)] == ["full"]
    assert (settings.state_dir / "delivery" / "s1.json").exists()


@pytest.mark.parametrize("stdin", ["[" * 100000, "{\"a\":" * 100000], ids=["list", "object"])
def test_deeply_nested_input_is_exit_0_and_silent(settings, rules, stdin):
    write_rule(rules, "a", match_tools=["Edit"])
    out, err = io.StringIO(), io.StringIO()
    assert hook.main(io.StringIO(stdin), out, err, settings=settings) == 0
    assert out.getvalue() == ""


# ---------------------------------------------------------------- rule field types

@pytest.mark.parametrize("raw", ["2026-01-01", "!!set {a: null}", "!!binary aGVsbG8=", ".nan",
                                 ".inf", "true", "[0.5]"])
def test_a_confidence_that_is_not_a_finite_number_is_rejected(rules, raw):
    (rules / "bad.yaml").write_text(f"trigger: t\naction: x\nmatch_tools: [Edit]\nconfidence: {raw}\n")
    rs = load_rules(rules)
    assert [r.file for r in rs.rejected] == ["bad.yaml"] and "confidence" in rs.rejected[0].reason


@pytest.mark.parametrize("raw,expect", [("0.9", 0.9), ("1", 1), ("'0.7'", 0.7)])
def test_a_numeric_confidence_loads(rules, raw, expect):
    (rules / "ok.yaml").write_text(f"trigger: t\naction: x\nmatch_tools: [Edit]\nconfidence: {raw}\n")
    [r] = load_rules(rules).fire
    assert r.confidence == expect


def test_a_rejected_confidence_never_silences_its_neighbour(settings, rules, capsys):
    write_rule(rules, "a-good", match_tools=["Edit"], confidence=0.9)
    (rules / "b-date.yaml").write_text("trigger: t\naction: x\nmatch_tools: [Edit]\nconfidence: 2026-01-01\n")
    out = io.StringIO()
    hook.main(io.StringIO(_payload()), out, io.StringIO(), settings=settings)
    assert "INSTINCT ACTIVATED [a-good," in out.getvalue()
    assert cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(settings.state_dir)]) == 1
    assert "REJECTED b-date.yaml" in capsys.readouterr().out


def test_an_unserializable_row_is_reported_not_raised(tmp_path):
    p = tmp_path / "log.jsonl"
    ok = st.append_rows([{"a": 1}, {"b": dt.date(2026, 1, 1)}, {"c": float("nan")}, {"d": 2}], p)
    assert ok is False
    assert [json.loads(x) for x in p.read_text().splitlines()] == [{"a": 1}, {"d": 2}]


# ---------------------------------------------------------------- budget

def test_a_long_domain_cannot_overflow_the_default_budget(settings, rules):
    write_rule(rules, "big-domain", match_tools=["Edit"], domain="d" * 9000, action="y" * 20000)
    res = _run(settings)
    assert len(res.text.encode()) <= settings.budget
    assert _kinds(res) == ["truncated"]          # bounded head: still deliverable, not withheld


@pytest.mark.parametrize("budget", [1, 50, 100, 241, 300])
def test_a_small_budget_holds(settings, rules, budget):
    write_rule(rules, "plain", match_tools=["Edit"], action="advice")
    write_rule(rules, "other", match_tools=["Edit"], action="z" * 500)
    settings.budget = budget
    for i in range(3):
        res = _run(settings, now=T0 + dt.timedelta(minutes=i))
        assert len(res.text.encode()) <= budget, (budget, res.text)


def test_the_budget_holds_over_a_sweep_of_fields_and_budgets(tmp_path):
    """Every rendered field is varied, since an unbounded head or tail is how
    the budget broke before; the assertion covers the complete emitted text."""
    import random
    rnd = random.Random(7)
    alphabet = "aé漢😀\\\"\n"
    over = []
    for case in range(120):
        rd = tmp_path / f"c{case}"
        for i in range(rnd.randint(1, 8)):
            n = rnd.choice([0, 10, 300, 3000, 9000])
            text = "".join(rnd.choice(alphabet) for _ in range(n)) or "x"
            field = rnd.choice(["domain", "action", "name", "none"])
            write_rule(rd, (text[:60].replace("/", "_").replace("\n", "_").replace("\\", "_")
                            .replace('"', "_") + f"-{i}") if field == "name" else f"r{i}",
                       match_tools=["Edit"], confidence=rnd.random(),
                       domain=text if field == "domain" else "d",
                       action=text if field == "action" else "advice")
        budget = rnd.choice([1, 100, 241, 300, 500, 1000, 2000, 8000])
        s = Settings(rules_dir=rd, state_dir=tmp_path / f"st{case}", budget=budget)
        for k in range(4):
            res = run("Edit", EDIT, "s", settings=s, now=T0 + dt.timedelta(minutes=k))
            emitted = len(hook.envelope_context_bytes(res.text))
            if emitted > budget:
                over.append((case, budget, emitted))
    assert over == []


def test_the_budget_counts_the_json_escaped_bytes(settings, rules):
    """The envelope escapes quotes, backslashes and newlines, so raw text under
    the budget can still be a larger hook output. Count what is sent."""
    write_rule(rules, "slashy", match_tools=["Edit"], action=('\\"' * 50 + "\n") * 200)
    res = _run(settings)
    assert len(hook.envelope_context_bytes(res.text)) <= settings.budget


def test_the_withheld_notice_is_in_the_telemetry(settings, rules):
    """Every emitted byte is attributed to a row, the closing notice included."""
    for i in range(12):
        write_rule(rules, f"r{i:02d}", match_tools=["Edit"], action="x" * 900, confidence=0.5)
    settings.budget = 4000
    res = _run(settings)
    assert "withheld" in res.lines[-1]
    assert sum(r["bytes"] for r in res.rows) == len(res.text.encode("utf-8"))
    [notice] = [r for r in res.rows if r["delivery"] == "notice"]
    assert notice["rule"] is None and notice["bytes"] == len(res.lines[-1].encode()) + 1


def test_an_oversized_rule_behind_a_stronger_one_is_not_starved(settings, rules):
    """A rule too big for the budget used to be truncated only when it fired
    alone, so a co-firing stronger rule withheld it on every call."""
    write_rule(rules, "a", match_tools=["Edit"], confidence=0.9, action="short")
    write_rule(rules, "b", match_tools=["Edit"], confidence=0.1, action="b" * 9000)
    for sid in (None, "s1"):
        res = _run(settings, sid=sid)
        assert dict((r["rule"], r["delivery"]) for r in res.rows)["b.yaml"] == "truncated", sid


def test_a_withheld_rule_goes_first_on_its_next_fire(settings, rules):
    """With a session, a rule withheld because a stronger rule took the room
    is delivered on its next fire, even when that stronger rule re-arms too."""
    write_rule(rules, "a", match_tools=["Edit"], confidence=0.9, action="a" * 2500)
    write_rule(rules, "b", match_tools=["Edit"], confidence=0.1, action="b" * 2500)
    settings.budget = 4000
    first = _run(settings)
    assert dict((r["rule"], r["delivery"]) for r in first.rows)["b.yaml"] == "budget"
    second = _run(settings, now=T0 + dt.timedelta(minutes=200))      # both re-armed
    assert dict((r["rule"], r["delivery"]) for r in second.rows)["b.yaml"] == "full"


def test_without_a_session_a_crowded_rule_is_cut_to_fit_not_withheld_forever(settings, rules):
    for n in ("a", "b", "c"):
        write_rule(rules, n, match_tools=["Edit"], action=n * 3000, confidence=0.5)
    settings.repeat = False
    for i in range(3):
        res = _run(settings, now=T0 + dt.timedelta(minutes=i))
        assert dict((r["rule"], r["delivery"]) for r in res.rows)["c.yaml"] in ("full", "truncated")


def test_the_closing_line_does_not_promise_the_next_fire(settings, rules):
    for i in range(12):
        write_rule(rules, f"r{i:02d}", match_tools=["Edit"], action="x" * 900)
    settings.budget = 4000
    assert "next fire" not in _run(settings).lines[-1]


# ---------------------------------------------------------------- delivery state

@pytest.mark.parametrize("rec", [{"n": "broken"}, {"n": None, "nw": "x"}, {"n": [1]},
                                 {"n": 1, "nw": {"a": 1}, "ts": 5}, {"n": True}])
def test_a_corrupt_counter_fails_toward_delivery(settings, rules, rec):
    write_rule(rules, "x", match_tools=["Edit"])
    ddir = settings.state_dir / "delivery"
    ddir.mkdir(parents=True)
    (ddir / "s1.json").write_text(json.dumps({"x.yaml": rec}))
    res = _run(settings)
    assert _kinds(res) == ["full"] and not res.errors


def test_a_future_timestamp_rearms_instead_of_muting(settings, rules):
    write_rule(rules, "x", match_tools=["Edit"])
    ddir = settings.state_dir / "delivery"
    ddir.mkdir(parents=True)
    future = (T0 + dt.timedelta(days=150)).isoformat()
    (ddir / "s1.json").write_text(json.dumps({"x.yaml": {"n": 3, "nw": 0, "ts": future}}))
    assert _kinds(_run(settings)) == ["full"]


@pytest.mark.parametrize("a,b", [("a/b", "a?b"), ("x" * 90 + "1", "x" * 90 + "2"), ("a b", "a_b")])
def test_distinct_session_ids_never_share_state(settings, rules, a, b):
    write_rule(rules, "x", match_tools=["Edit"])
    _run(settings, sid=a)
    assert _kinds(_run(settings, sid=b)) == ["full"]


def test_a_plain_session_id_keeps_its_file_name(settings, rules):
    write_rule(rules, "x", match_tools=["Edit"])
    sid = "0b8f3c2e-1a2b-4c3d-9e8f-0123456789ab"
    _run(settings, sid=sid)
    assert (settings.state_dir / "delivery" / f"{sid}.json").exists()


# ---------------------------------------------------------------- catastrophic patterns

@pytest.mark.parametrize("pat", [r"^(a+)+$", r"(a*)*b", r"(\w+\s?)+$", r"((ab)*)+c",
                                 r"(x+x+)+y", r"(.*a){12}"])
def test_a_catastrophic_pattern_is_rejected_at_load(rules, pat):
    write_rule(rules, "redos", match_tools=["Write"], match_content=[pat])
    rs = load_rules(rules)
    assert [r.file for r in rs.rejected] == ["redos.yaml"], pat
    assert "backtrack" in rs.rejected[0].reason


def test_doctor_fails_on_a_catastrophic_pattern(rules, tmp_path, capsys):
    write_rule(rules, "ok", match_tools=["Write"])
    write_rule(rules, "redos", match_tools=["Write"], match_content=[r"^(a+)+$"])
    assert cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(tmp_path / "s")]) == 1
    assert "REJECTED redos.yaml" in capsys.readouterr().out


@pytest.mark.parametrize("pat", [r"print\(", r"(ab)+", r"\w+\s+\w+", r"(foo|bar)+baz",
                                 r"^\s*import\s+(os|sys)", r"a{2,5}(bc)?", r"(?:x+y)+"])
def test_ordinary_patterns_still_load(rules, pat):
    write_rule(rules, "fine", match_tools=["Write"], match_content=[pat])
    assert len(load_rules(rules).fire) == 1, pat


# ---------------------------------------------------------------- unread rule files

def test_a_yml_file_is_named_by_doctor(rules, tmp_path, capsys):
    write_rule(rules, "a", match_tools=["Edit"])
    (rules / "b.yml").write_text("trigger: t\naction: x\nmatch_tools: [Edit]\n")
    assert cli.main(["doctor", "--rules-dir", str(rules), "--state-dir", str(tmp_path / "s")]) == 1
    out = capsys.readouterr().out
    assert "b.yml" in out and "NOT READ" in out


# ---------------------------------------------------------------- install

@pytest.mark.parametrize("doc", [{"hooks": []}, {"hooks": None}, {"hooks": "x"},
                                 {"hooks": {"PreToolUse": None}}, {"hooks": {"PreToolUse": {}}}])
def test_install_refuses_an_unexpected_shape_without_a_traceback(tmp_path, doc, capsys):
    s = tmp_path / ".claude" / "settings.json"
    s.parent.mkdir()
    s.write_text(json.dumps(doc))
    before = s.read_text()
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert s.read_text() == before and "unexpected shape" in capsys.readouterr().out


# ---------------------------------------------------------------- labels

def test_labels_add_reports_a_failed_write(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    store = blocker / "labels.jsonl"          # parent is a file: the write cannot succeed
    code = cli.main(["labels", "add", "--store", str(store), "--rule", "x", "--tool", "Edit",
                     "--excerpt", "e", "--verdict", "TRUE_POSITIVE", "--source", "transcript_sample"])
    captured = capsys.readouterr()
    assert code == 2 and "added" not in captured.out and "could not write" in captured.err


# ---------------------------------------------------------------- bash writes

@pytest.mark.parametrize("cmd,target", [
    ('echo "print(1)" > "src/gen.py"', "src/gen.py"),
    ("echo 'print(1)' > 'a.py'", "a.py"),
    ('printf "%s" "print(1)" >"a.py"', "a.py"),
    ('echo x > "dir with space/a.py"', "dir with space/a.py"),
    ('cat > "b.py" <<EOF\nprint(1)\nEOF', "b.py"),
])
def test_a_quoted_write_target_is_found(cmd, target):
    assert [p for p, _ in write_targets(cmd)] == [target]


@pytest.mark.parametrize("tail", ["", "\ny"])       # "\ny" would terminate a heredoc `<<y`
def test_arithmetic_shift_is_not_a_heredoc(tail):
    got = write_targets('x=$((1<<y)) > log.txt\necho "print(2)" > b.py' + tail)
    assert got == [("b.py", "print(2)")]


def test_a_heredoc_without_its_terminator_runs_to_the_end_as_in_bash():
    got = write_targets('cat > a.py <<EOF\nprint(1)\necho "x" > b.py')
    assert got == [("a.py", 'print(1)\necho "x" > b.py')]


def test_an_exclusion_in_the_command_silences_a_derived_write_hit(rules):
    write_rule(rules, "np", match_tools=["Write", "Bash"], match_content=[r"print\("],
               match_exclude=["tests/"])
    rs = load_rules(rules)
    hits = match(rs.fire, "Bash", {"command": 'cd tests/ && echo "print(1)" > t.py'})
    assert hits == []


# ---------------------------------------------------------------- measurements

def _ts(minutes=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
            + dt.timedelta(minutes=minutes)).isoformat()


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


def _jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _shown(slug):
    return {"type": "attachment", "attachment": {
        "type": "hook_additional_context",
        "content": [f"INSTINCT ACTIVATED [{slug}, d, confidence=0.5]: x"]}}


def test_persistence_loss_reads_subagent_transcripts(tmp_path):
    rows = [_row("x.yaml", sid="S", ts=_ts(1)), _row("y.yaml", sid="S", ts=_ts(2))]
    tdir = tmp_path / "t"
    _jsonl(tdir / "S.jsonl", [_shown("x")])
    _jsonl(tdir / "S" / "subagents" / "agent-1.jsonl", [_shown("y")])
    r = measure.persistence_loss(_log(tmp_path, rows), tdir)
    assert (r["full_logged"], r["lost"], r["transcript_files"]) == (2, 0, 2)


def test_persistence_loss_with_no_attachment_records_is_not_evaluable(tmp_path):
    tdir = tmp_path / "t"
    drifted = {"type": "attachment_v2", "attachment": _shown("x")["attachment"]}
    _jsonl(tdir / "S.jsonl", [drifted])
    with pytest.raises(measure.NotEvaluable, match="no attachment records"):
        measure.persistence_loss(_log(tmp_path, [_row("x.yaml", sid="S")]), tdir)


def test_persistence_loss_does_not_count_the_hooks_own_stdout_log(tmp_path):
    """The transcript also logs the hook process's raw stdout; the full text
    is there even when the model received only a short preview. Only the
    content the model was given counts as delivered."""
    tdir = tmp_path / "t"
    raw = {"type": "attachment", "attachment": {
        "type": "hook_success", "content": "",
        "stdout": json.dumps({"additionalContext": "INSTINCT ACTIVATED [x, d, confidence=0.5]: x"})}}
    preview = {"type": "attachment", "attachment": {
        "type": "hook_additional_context",
        "content": ["<persisted-output>\nOutput too large (11.5KB). Preview (first 2KB):\n"
                    "⚠️ INSTINCT [d] x: first line\n...\n</persisted-output>"]}}
    _jsonl(tdir / "S.jsonl", [raw, preview])
    r = measure.persistence_loss(_log(tmp_path, [_row("x.yaml", sid="S")]), tdir)
    assert (r["lost"], r["persisted_previews"]) == (1, 1)


def test_breadth_does_not_count_bash_writes_for_a_read_only_rule(tmp_path, rules):
    write_rule(rules, "p", match_tools=["Read"], match_content=["print"])
    calls = [{"type": "tool_use", "name": "Read",
              "input": {"file_path": f"f{i}.py", "pattern": "print(1)"}} for i in range(4)]
    calls += [{"type": "tool_use", "name": "Bash", "input": {"command": f"echo x > f{i}.txt"}}
              for i in range(13)]
    _jsonl(tmp_path / "t" / "s.jsonl", [{"message": {"content": calls}}])
    code, r = measure.breadth(tmp_path / "t", rules)
    [d] = r["rules"]
    assert (d["fires"], d["eligible"], code) == (4, 4, 1)


def test_breadth_rejects_a_session_count_below_one(tmp_path, rules):
    (tmp_path / "t").mkdir()
    with pytest.raises(SystemExit) as e:
        cli.main(["breadth", "--transcripts", str(tmp_path / "t"), "--rules-dir", str(rules),
                  "--sessions", "0"])
    assert e.value.code == 64


@pytest.mark.parametrize("verb", ["cost", "fire-rate", "repeat-rate"])
@pytest.mark.parametrize("bad", ["12KB", None, -5, True, [1]])
def test_a_malformed_byte_count_is_reported_never_a_finding_or_a_zero(tmp_path, verb, bad, capsys):
    rows = [_row("a.yaml", nbytes=100, ts=_ts(1)), _row("b.yaml", nbytes=bad, ts=_ts(2))]
    code = cli.main([verb, "--log", str(_log(tmp_path, rows))])
    out = capsys.readouterr().out
    assert code in (0, 2) and code != 1
    assert "1 malformed row(s)" in out or "NOT EVALUABLE" in out


def test_fire_rate_with_malformed_rows_and_no_offender_is_not_evaluable(tmp_path, capsys):
    rows = [_row("a.yaml", nbytes=100, ts=_ts(1)), _row("b.yaml", nbytes="12KB", ts=_ts(2))]
    assert cli.main(["fire-rate", "--log", str(_log(tmp_path, rows))]) == 2


def test_an_unexpected_report_failure_is_exit_2_not_1(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(measure, "cofire", boom)
    assert cli.main(["cofire", "--log", str(_log(tmp_path, [_row("a.yaml")]))]) == 2
    assert "NOT EVALUABLE" in capsys.readouterr().out


def test_hook_envelope_is_one_line_of_json(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action="l1\nl2 ✓")
    out = io.StringIO()
    hook.main(io.StringIO(_payload()), out, io.StringIO(), settings=settings)
    assert out.getvalue().count("\n") == 1 and json.loads(out.getvalue())
    assert engine.MARK in out.getvalue()
