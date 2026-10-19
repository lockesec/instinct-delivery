"""State and log files: session ids map injectively to files and cannot escape the
state directory, planted links and special files are never followed or read,
modes are private, stale temp files are pruned, and a row is never dropped from
the log."""
import datetime as dt
import json
import os
import stat
import time

from conftest import log_rows, write_rule

from instinct_delivery import state as st
from instinct_delivery.engine import Settings, run


def test_a_planted_link_at_the_state_tmp_name_is_not_followed(tmp_path):
    ddir = tmp_path / "state" / "delivery"
    ddir.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    target = st._session_file(ddir, "s1")
    os.symlink(victim, ddir / f".{target.name}.{os.getpid()}.tmp")
    assert st.save_state(ddir, "s1", {"r.yaml": {"n": 1}}) is True
    assert victim.read_text() == "precious"
    assert st.load_state(ddir, "s1") == {"r.yaml": {"n": 1}}


def test_a_state_file_that_is_a_link_is_replaced_not_written_through(tmp_path):
    ddir = tmp_path / "delivery"
    ddir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    os.symlink(victim, st._session_file(ddir, "s1"))
    assert st.save_state(ddir, "s1", {"r.yaml": {"n": 2}}) is True
    assert victim.read_text() == "precious"
    assert not st._session_file(ddir, "s1").is_symlink()


def test_a_log_that_is_a_link_is_not_appended_to(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n")
    log = tmp_path / "state" / "activations.jsonl"
    log.parent.mkdir()
    os.symlink(victim, log)
    assert st.append_rows([{"ts": "t", "rule": "r"}], log) is False
    assert victim.read_text() == "precious\n"


def test_the_real_hook_does_not_append_to_a_linked_log(settings, rules, tmp_path):
    from instinct_delivery import hook
    import io
    write_rule(rules, "a", match_tools=["Edit"])
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n")
    settings.state_dir.mkdir(parents=True)
    os.symlink(victim, settings.state_dir / "activations.jsonl")
    code = hook.main(io.StringIO(json.dumps({"tool_name": "Edit", "session_id": "s",
                                             "tool_input": {"file_path": "x"}})),
                     io.StringIO(), io.StringIO(), settings=settings)
    assert code == 0 and victim.read_text() == "precious\n"


def test_a_log_that_is_not_a_regular_file_is_refused_without_blocking(tmp_path):
    log = tmp_path / "activations.jsonl"
    os.mkfifo(log)
    assert st.append_rows([{"ts": "t"}], log) is False


def test_a_normal_log_still_appends(tmp_path):
    log = tmp_path / "a" / "activations.jsonl"
    assert st.append_rows([{"x": 1}], log) is True and st.append_rows([{"x": 2}], log) is True
    assert [json.loads(x) for x in log.read_text().splitlines()] == [{"x": 1}, {"x": 2}]
    assert stat.S_IMODE(log.stat().st_mode) & 0o600 == 0o600


def test_a_relative_state_dir_is_not_honoured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("INSTINCT_STATE_DIR", "some/where")
    assert st.state_dir() == tmp_path / "xdg" / "instinct-delivery"
    err = capsys.readouterr().err
    assert "INSTINCT_STATE_DIR" in err and "absolute" in err


def test_an_absolute_and_a_tilde_state_dir_are_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTINCT_STATE_DIR", str(tmp_path / "abs"))
    assert st.state_dir() == tmp_path / "abs"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("INSTINCT_STATE_DIR", "~/viahome")
    assert st.state_dir() == tmp_path / "viahome"


def test_a_relative_xdg_state_home_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTINCT_STATE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", "rel")
    assert st.state_dir() == tmp_path / ".local" / "state" / "instinct-delivery"


def test_generated_stem_does_not_collide_with_its_literal_spelling(tmp_path):
    a = st._session_file(tmp_path, "a/b")
    b = st._session_file(tmp_path, a.stem)
    assert a != b


T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)


EDIT = {"file_path": "src/m.py", "new_string": "x"}


def _run(s, sid="s1", now=T0):
    return run("Edit", EDIT, sid, settings=s, now=now)


def test_a_full_delivery_in_one_does_not_suppress_the_other(settings, rules):
    write_rule(rules, "a", match_tools=["Edit"], action="advice")
    encoded = st._session_file(settings.state_dir / "delivery", "a/b").stem
    assert _run(settings, sid="a/b").rows[0]["delivery"] == "full"
    other = _run(settings, sid=encoded, now=T0 + dt.timedelta(minutes=1))
    assert other.rows[0]["delivery"] == "full"


def test_control_safe_ids_stay_literal_and_unsafe_ids_stay_distinct(tmp_path):
    assert st._session_file(tmp_path, "s1").name == "s1.json"
    names = {st._session_file(tmp_path, i).name for i in ("a/b", "a\\b", "a b", "a_b", "a~b")}
    assert len(names) == 5


def test_a_fifo_at_the_state_file_does_not_hang(tmp_path):
    ddir = tmp_path / "d"
    ddir.mkdir()
    os.mkfifo(st._session_file(ddir, "s1"))
    assert st.load_state(ddir, "s1") == {}


def test_a_symlinked_state_file_is_not_read(tmp_path):
    ddir = tmp_path / "d"
    ddir.mkdir()
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"r.yaml": {"ts": "x", "n": 1}}))
    os.symlink(real, st._session_file(ddir, "s1"))
    assert st.load_state(ddir, "s1") == {}


def test_a_lone_surrogate_session_id_still_writes_its_rows(settings, rules, capsys):
    write_rule(rules, "good", match_tools=["Edit"])
    res = run("Edit", {"file_path": "a.py", "new_string": "x"}, "s\ud800x", settings=settings,
              commit=False)
    assert res.commit() is True
    assert "could not write" not in capsys.readouterr().err
    [row] = log_rows(settings)
    assert row["session_id"] and "s" in row["session_id"]


def test_stale_temp_files_are_pruned_and_fresh_ones_kept(tmp_path):
    ddir = tmp_path / "d"
    ddir.mkdir()
    old, fresh = ddir / ".s.json.abc.tmp", ddir / ".t.json.def.tmp"
    old.write_text("x")
    fresh.write_text("x")
    t = time.time() - 30 * 86400
    os.utime(old, (t, t))
    st.prune_state(ddir, 7)
    assert not old.exists() and fresh.exists()


def test_the_log_and_state_files_are_created_0600(tmp_path):
    old = os.umask(0)
    try:
        log = tmp_path / "a" / "activations.jsonl"
        assert st.append_rows([{"x": 1}], log)
        ddir = tmp_path / "d"
        assert st.save_state(ddir, "s1", {"r.yaml": {"ts": "x"}})
    finally:
        os.umask(old)
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(st._session_file(ddir, "s1").stat().st_mode) == 0o600


def _raw(rules, name, text):
    rules.mkdir(parents=True, exist_ok=True)
    (rules / name).write_text(text, encoding="utf-8")


def test_a_rule_row_is_never_dropped_from_the_log(rules, tmp_path):
    _raw(rules, "bad.yaml", 'trigger: t\naction: a\nmatch_tools: [Edit]\ndomain: "\\ud800"\n')
    write_rule(rules, "good", match_tools=["Edit"])
    s = Settings(rules_dir=rules, state_dir=tmp_path / "st")
    res = run("Edit", {"file_path": "a.py", "new_string": "x"}, "s1", settings=s)
    assert len(res.rows) == 1 and res.commit()
    log = st.log_path(s.state_dir)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_new_state_and_log_directories_are_0700_whatever_the_umask(tmp_path):
    old = os.umask(0)
    try:
        log = tmp_path / "state" / "activations.jsonl"
        assert st.append_rows([{"x": 1}], log)
        ddir = st.delivery_dir(tmp_path / "state2")
        assert st.save_state(ddir, "s1", {"r.yaml": {"ts": "x"}})
    finally:
        os.umask(old)
    for d in (log.parent, ddir, ddir.parent):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d


def test_an_existing_directory_keeps_its_mode(tmp_path):
    d = tmp_path / "state"
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    assert st.append_rows([{"x": 1}], d / "activations.jsonl")
    assert stat.S_IMODE(d.stat().st_mode) == 0o755


def test_a_lone_surrogate_in_tool_input_still_writes_the_log_row(tmp_path):
    rd = tmp_path / "rules"
    write_rule(rd, "np", match_tools=["Edit"], match_paths=["a"], domain="d", confidence=0.5)
    s = Settings(rules_dir=rd, state_dir=tmp_path / "state")
    res = run("Edit", {"file_path": "a\ud800.py"}, "sess", settings=s)
    assert "INSTINCT" in res.text
    rows = [json.loads(x) for x in (tmp_path / "state" / "activations.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["delivery"] == "full"
    assert rows[0]["file_path"] == "a\\ud800.py"


def test_every_string_in_every_row_kind_is_made_encodable():
    from instinct_delivery import engine
    rows = [engine._slow_row("r\ud800.yaml", "T\ud801", "s\ud802", "ts", "why\ud803"),
            engine._unevaluated_row(None, "T\ud801", "s\ud802", "ts", "why"),
            engine._notice_row("T\ud801", "s\ud802", 3, "ts")]
    for r in rows:
        json.dumps(r, ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert rows[0]["tool"] == "T\\ud801" and rows[0]["rule"] == "r\\ud800.yaml"


def test_a_session_id_is_capped_in_the_row_and_stays_distinct(tmp_path):
    rd = tmp_path / "rules"
    write_rule(rd, "np", match_tools=["Edit"])
    s = Settings(rules_dir=rd, state_dir=tmp_path / "state")
    a, b = "x" * 5000 + "a", "x" * 5000 + "b"
    run("Edit", {}, a, settings=s)
    run("Edit", {}, b, settings=s)
    rows = [json.loads(x) for x in (tmp_path / "state" / "activations.jsonl").read_text().splitlines()]
    ids = [r["session_id"] for r in rows]
    assert all(len(i) <= 128 for i in ids) and ids[0] != ids[1]
    assert [r["delivery"] for r in rows] == ["full", "full"]       # two sessions, two first deliveries
    run("Edit", {}, "short", settings=s)
    assert json.loads((tmp_path / "state" / "activations.jsonl").read_text().splitlines()[-1])["session_id"] == "short"
