"""audit: look at what a rule actually fired on.

Real-shaped inputs: engine log rows (ts, rule, tool, session_id, file_path, matched, delivery)
and Claude Code transcripts (assistant records with a timestamp and tool_use blocks)."""
import datetime as dt
import json

import pytest
from conftest import write_rule

from instinct_delivery import audit, cli

BASE = dt.datetime(2026, 3, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
UNTIL = "2026-03-11"


def at(s):
    return (BASE + dt.timedelta(seconds=s)).isoformat().replace("+00:00", "Z")


def call(s, name, **inp):
    return {"type": "assistant", "timestamp": at(s),
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def row(rule, s, matched, sid="s1", delivery="full", tool="Bash"):
    return {"ts": at(s), "rule": rule, "domain": "d", "confidence": 0.5, "tool": tool,
            "session_id": sid, "file_path": None, "matched": matched, "delivery": delivery,
            "bytes": 100}


def world(tmp_path, rows, sessions=None):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    t = tmp_path / "t"
    t.mkdir(exist_ok=True)
    for sid, recs in (sessions or {}).items():
        (t / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return log, t


def run(capsys, log, t, *extra, until=UNTIL):
    code = cli.main(["audit", "--log", str(log), "--transcripts", str(t), "--until", until, *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def many(rule, k, matched, start=0, tool="Bash"):
    return [row(rule, start + i * 10, matched, tool=tool) for i in range(k)]


def test_the_sample_is_deterministic_and_changes_with_the_window_end(tmp_path, capsys):
    rows = many("r.yaml", 40, "ab cd")
    log, t = world(tmp_path, rows)
    _, a, _ = run(capsys, log, t, "--json")
    _, b, _ = run(capsys, log, t, "--json")
    _, c, _ = run(capsys, log, t, "--json", until="2026-03-12")
    ids = lambda s: [f["ts"] for f in json.loads(s)["rules"][0]["sample"]]
    assert a == b
    assert len(ids(a)) == 10
    assert ids(a) != ids(c)


def test_a_sample_is_at_most_n_per_rule_and_only_real_matches(tmp_path, capsys):
    rows = (many("r.yaml", 20, "x y")
            + [row("r.yaml", 500 + i, "x y", delivery=d)
               for i, d in enumerate(["notice", "slow", "unevaluated", "muted", "budget"])]
            + [row("s.yaml", 600, "x y", delivery="shadow")])
    log, t = world(tmp_path, rows)
    _, out, _ = run(capsys, log, t, "--json", "--n", "4")
    rep = json.loads(out)
    by = {r["rule"]: r for r in rep["rules"]}
    assert len(by["r"]["sample"]) == 4
    assert by["r"]["eligible"] == 20          # notice/slow/unevaluated/muted/budget are not counted
    assert len(by["s"]["sample"]) == 1        # shadow is


def test_the_triggering_call_is_joined_from_the_transcript(tmp_path, capsys):
    rows = [row("r.yaml", 100, "README")]
    sess = {"s1": [call(50, "Bash", command="old"),
                   call(99, "Bash", command="cat README.md\n  | head"),
                   call(150, "Bash", command="later")]}
    log, t = world(tmp_path, rows, sess)
    code, out, _ = run(capsys, log, t)
    assert audit.fire_id("r.yaml", rows[0]) in out
    assert "cat README.md | head" in out
    assert "later" not in out and "old" not in out
    assert "README" in out


def test_call_not_found_is_said_not_blanked(tmp_path, capsys):
    rows = [row("r.yaml", 100, "README", sid="nosuch"), row("r.yaml", 200, "README", sid="s2")]
    # s2 has only a call AFTER the fire
    log, t = world(tmp_path, rows, {"s2": [call(300, "Bash", command="after")]})
    _, out, _ = run(capsys, log, t)
    assert out.count("call not found") == 2
    assert "after" not in out


def test_the_head_is_one_line_and_at_most_160_chars(tmp_path, capsys):
    rows = [row("r.yaml", 100, "x")]
    log, t = world(tmp_path, rows, {"s1": [call(99, "Bash", command="echo " + "a" * 500 + "\nsecond")]})
    _, out, _ = run(capsys, log, t, "--json")
    head = json.loads(out)["rules"][0]["sample"][0]["call"]
    assert "\n" not in head and len(head) <= 160 and head.startswith("Bash: echo aaa")


def bare_world(tmp_path, bare=8, multi=2, rule="bare.yaml"):
    rows = (many(rule, bare, "README") + many(rule, multi, "diff --stat", start=1000))
    return world(tmp_path, rows)


def test_a_rule_that_fired_on_one_bare_word_is_flagged_BARE_and_exits_1(tmp_path, capsys):
    log, t = bare_world(tmp_path)
    code, out, _ = run(capsys, log, t)
    assert code == 1
    assert "BARE" in out and "8 of 10" in out
    assert "8 of those are a plain word" in out           # README: a word, not a path


def test_a_bare_rule_on_paths_has_no_plain_word_line(tmp_path, capsys):
    rows = many("pathy.yaml", 8, "src/app.py") + many("pathy.yaml", 2, "diff --stat", start=1000)
    log, t = world(tmp_path, rows)
    code, out, _ = run(capsys, log, t)
    assert code == 1 and "BARE rule: 8 of 10" in out and "plain word" not in out


def test_a_rule_whose_fires_matched_situations_is_not_flagged(tmp_path, capsys):
    rows = many("sit.yaml", 10, "git push --force")
    log, t = world(tmp_path, rows)
    code, out, _ = run(capsys, log, t)
    assert code == 0
    assert "BARE" not in out.replace("BARE-TOKEN", "")
    assert "0 of 10" in out


def test_a_regex_is_not_a_bare_word(tmp_path, capsys):
    rows = many("re.yaml", 10, r"(?m)^\s*print\(")
    log, t = world(tmp_path, rows)
    code, out, _ = run(capsys, log, t)
    assert code == 0 and "0 of 10" in out


def test_under_five_fires_the_flag_is_not_evaluable_and_no_rate_prints(tmp_path, capsys):
    rows = many("few.yaml", 4, "README")
    log, t = world(tmp_path, rows)
    code, out, _ = run(capsys, log, t)
    assert "NOT EVALUABLE" in out and "4 of 4" in out
    assert "%" not in out and "BARE rule" not in out
    assert code == 2


def test_no_rows_in_the_window_missing_transcripts_and_unknown_rule_are_exit_2(tmp_path, capsys):
    log, t = bare_world(tmp_path)
    code, out, _ = run(capsys, log, t, until="2026-01-01")
    assert code == 2 and "NOT EVALUABLE" in out
    code, out, _ = cli.main(["audit", "--log", str(log), "--transcripts", str(tmp_path / "gone"),
                             "--until", UNTIL]), capsys.readouterr().out, None
    assert code == 2 and "transcripts" in out and "NOT EVALUABLE" in out
    code, out, _ = run(capsys, log, t, "--rule", "nope")
    assert code == 2 and "NOT EVALUABLE" in out


def test_a_bad_usage_is_64(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["audit", "--n", "0", "--transcripts", str(tmp_path)])
    assert e.value.code == 64
    assert "1 or more" in capsys.readouterr().err


def _label(capsys, log, t, fid, store, *extra, until=UNTIL):
    code = cli.main(["audit", "label", fid, "--verdict", "FALSE_POSITIVE", "--store", str(store),
                     "--log", str(log), "--transcripts", str(t), "--until", until, *extra])
    o = capsys.readouterr()
    return code, o.out, o.err


def test_a_label_round_trips_into_labels_stats(tmp_path, capsys):
    rows = many("r.yaml", 6, "README")
    log, t = world(tmp_path, rows, {"s1": [call(5, "Bash", command="cat README.md")]})
    store = tmp_path / "labels.jsonl"
    fid = audit.fire_id("r.yaml", rows[1])
    code, out, _ = _label(capsys, log, t, fid, store, "--reason", "just a README", "--labeler", "me")
    assert code == 0 and fid in out and "cat README.md" in out      # echoes the call it labelled
    (lab,) = [json.loads(x) for x in store.read_text().splitlines()]
    assert (lab["rule"], lab["verdict"], lab["source"]) == ("r", "FALSE_POSITIVE", "transcript_sample")
    assert lab["session_id"] == "s1" and "cat README.md" in lab["excerpt"]
    assert lab["reason"] == "just a README" and lab["labeler"] == "me"
    assert cli.main(["labels", "stats", "--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "r: 0/1 TP" in out and "1 hold-out admissible" in out


def test_ids_are_content_addressed_and_survive_a_new_fire_n_and_the_window_date(tmp_path, capsys):
    rows = many("r.yaml", 30, "ab cd")
    log, t = world(tmp_path, rows)
    ids = lambda *x, **k: {f["id"]: f for f in json.loads(run(capsys, log, t, "--json", *x, **k)[1])["rules"][0]["sample"]}
    before = ids()
    # one new fire in the middle shifts every position; midnight changes the seed
    log2, _ = world(tmp_path, rows + [row("r.yaml", 55, "ab cd")])
    after = {f["id"]: f for f in json.loads(run(capsys, log2, t, "--json", until="2026-03-12")[1])["rules"][0]["sample"]}
    for fid in set(before) & set(after):
        assert before[fid]["ts"] == after[fid]["ts"]
    assert all(len(i.split("#")[1]) == 8 and set(i.split("#")[1]) <= set("0123456789abcdef") for i in before)
    # the id of a fire labels THAT fire under any --n or window date, sampled or not
    store = tmp_path / "labels.jsonl"
    target = rows[7]
    fid = audit.fire_id("r.yaml", target)
    for extra, until in ((("--n", "1"), UNTIL), (("--n", "30"), "2026-03-12")):
        code, out, _ = _label(capsys, log2, t, fid, store, *extra, until=until)
        assert code == 0 and at(70) in out
    assert [json.loads(x)["rule"] for x in store.read_text().splitlines()] == ["r", "r"]


def test_an_id_that_exists_in_no_row_is_refused_even_when_positions_collide(tmp_path, capsys):
    rows = many("r.yaml", 6, "README")
    log, t = world(tmp_path, rows)
    store = tmp_path / "labels.jsonl"
    other = audit.fire_id("r.yaml", dict(rows[0], ts=at(9999)))
    code, _, err = _label(capsys, log, t, other, store)
    assert code == 64 and other in err and not store.exists()
    code, _, err = _label(capsys, log, t, "r#2", store)
    assert code == 64 and not store.exists()


def test_an_unknown_fire_id_is_refused_and_writes_nothing(tmp_path, capsys):
    log, t = world(tmp_path, many("r.yaml", 6, "README"))
    store = tmp_path / "labels.jsonl"
    for fid in ("r#99", "ghost#1", "garbage"):
        code = cli.main(["audit", "label", fid, "--verdict", "TRUE_POSITIVE", "--store", str(store),
                         "--log", str(log), "--transcripts", str(t), "--until", UNTIL])
        err = capsys.readouterr().err
        assert code == 64 and fid in err
    assert not store.exists()


def test_brief_names_the_bare_tokens_and_options_and_edits_nothing(tmp_path, capsys):
    rules = tmp_path / "rules"
    p = write_rule(rules, "bare", match_tools=["Bash"], match_content=["README", "diff --stat"])
    before = {f.name: f.read_bytes() for f in rules.iterdir()}
    log, t = bare_world(tmp_path)
    sess = {"s1": [call(0, "Bash", command="cat README.md")],
            "s2": [call(0, "Bash", command="git diff --stat")]}
    diffs = [row("bare.yaml", 1000 + i, "diff --stat", sid="s2") for i in range(2)]
    log, t = world(tmp_path, many("bare.yaml", 8, "README") + diffs, sess)
    code, out, _ = run(capsys, log, t, "--brief", "--rules-dir", str(rules))
    assert code == 1
    assert "README" in out and "8 of 10" in out
    assert "match_content" in out and "match_paths" in out and "match_exclude" in out
    assert "match_tools" in out and "second" in out
    assert "cat README.md" in out          # one context for the token
    assert {f.name: f.read_bytes() for f in rules.iterdir()} == before
    assert p.exists()


def test_brief_on_a_rule_with_no_file_still_prints_and_says_so(tmp_path, capsys):
    log, t = bare_world(tmp_path)
    code, out, _ = run(capsys, log, t, "--brief", "--rules-dir", str(tmp_path / "norules"))
    assert code == 1 and "rule file not found" in out


def test_json_carries_the_denominators(tmp_path, capsys):
    log, t = bare_world(tmp_path)
    code, out, _ = run(capsys, log, t, "--json")
    r = json.loads(out)["rules"][0]
    assert (r["bare"], r["n"], r["flag"]) == (8, 10, "BARE")
    few = world(tmp_path, many("few.yaml", 3, "x"))
    code, out, _ = run(capsys, few[0], few[1], "--json")
    r = json.loads(out)["rules"][0]
    assert r["flag"] == "NOT_EVALUABLE" and r["n"] == 3


@pytest.mark.parametrize("text,bare", [
    ("README", True), ("verdict", True), ("src/app/models.py", True), ("src/", True),
    ("diff --stat", False), ("git push --force", False), (r"(?m)^\s*print\(", False),
    ("a|b", False), ("x*", False), ("", False), (None, False),
    # padding whitespace from a regex such as (?m)^\s*TODO is not part of the token
    ("    TODO", True), ("TODO  ", True), ("\tTODO\t", True), (" \t src/app.py \n", True),
    ("   ", False), ("two words", False)])
def test_what_counts_as_a_bare_token(text, bare):
    from instinct_delivery import audit
    assert audit.is_bare(text) is bare


def test_a_rule_whose_fires_are_all_one_padded_word_is_flagged_BARE(tmp_path, capsys):
    cmds = ["    TODO", "\tTODO", "TODO  ", " \tTODO\t", "      TODO", "TODO"]
    rules, log, t = regex_world(tmp_path, r"(?m)^\s*TODO", [f"echo hi\n{c}\necho {i}" for i, c in enumerate(cmds)])
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert code == 1 and "BARE rule: 6 of 6" in out


def regex_world(tmp_path, pattern, commands, name="rx", logged=None):
    rules = tmp_path / "rules"
    write_rule(rules, name, match_tools=["Bash"], match_content=[pattern])
    rows = [row(f"{name}.yaml", 100 + 10 * i, logged or pattern, sid=f"s{i}") for i in range(len(commands))]
    sess = {f"s{i}": [call(99 + 10 * i, "Bash", command=c)] for i, c in enumerate(commands)}
    log, t = world(tmp_path, rows, sess)
    return rules, log, t


def test_a_regex_rule_whose_real_matches_are_bare_words_is_flagged_from_the_recovered_text(tmp_path, capsys):
    cmds = [f"echo the Verdict is {i}" for i in range(6)]
    rules, log, t = regex_world(tmp_path, r"\bverdict\b", cmds)
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert code == 1 and "BARE rule: 6 of 6" in out
    assert "matched text: Verdict" in out            # the substring, from the transcript
    assert "matched rule key: \\bverdict\\b" in out
    # without the rules the logged pattern is judged (a regex: not bare) and says so
    code, out, _ = run(capsys, log, t, "--rules-dir", str(tmp_path / "none"))
    assert code == 0 and "text not recovered" in out and "0 of 6" in out


def test_a_regex_rule_matching_a_situation_is_not_flagged(tmp_path, capsys):
    cmds = [f"docker run -it --privileged img{i}" for i in range(6)]
    rules, log, t = regex_world(tmp_path, r"\bdocker\b[^;&|\n]*\s--privileged", cmds)
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert code == 0 and "0 of 6" in out
    assert "matched text: docker run -it --privileged" in out
    assert "text not recovered" not in out


def test_a_joined_call_the_rule_no_longer_matches_is_said_and_not_guessed(tmp_path, capsys):
    rules, log, t = regex_world(tmp_path, r"\bverdict\b", ["ls", "ls", "ls", "ls", "ls", "echo verdict"])
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules), "--json")
    r = json.loads(out)["rules"][0]
    assert r["changed"] == 5 and r["n"] == 1 and r["flag"] == "NOT_EVALUABLE"
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert "no longer matches this rule (the rule changed since)" in out


def _parallel_world(tmp_path, calls, logged=r"\bverdict\b", n_rows=1):
    """Calls of one session at the given offsets (seconds); one hook row after the last."""
    rules = tmp_path / "rules"
    write_rule(rules, "rx", match_tools=["Bash"], match_content=[logged])
    recs = [call(s_, "Bash", command=c) for s_, c in calls]
    rows = [row("rx.yaml", calls[-1][0] + 0.5, logged)]
    log, t = world(tmp_path, rows, {"s1": recs})
    return rules, log, t


def test_parallel_same_tool_calls_join_the_one_the_rule_matches(tmp_path, capsys):
    # call 1 (the one that fired) is stamped before call 2 of the same message; the row is after both
    rules, log, t = _parallel_world(tmp_path, [(100.000, "echo verdict"), (100.002, "ls -la")])
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert "matched text: verdict" in out and "call: Bash: echo verdict" in out
    assert "changed since" not in out and "not identified" not in out


def test_parallel_calls_none_of_which_match_say_the_call_is_not_identified(tmp_path, capsys):
    rules, log, t = _parallel_world(tmp_path, [(100.000, "ls"), (100.002, "pwd")])
    code, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert "call not identified (several calls of this tool in one message)" in out
    assert "changed since" not in out
    r = json.loads(run(capsys, log, t, "--rules-dir", str(rules), "--json")[1])["rules"][0]
    # not in the bare-token denominator, and counted apart from the rule having changed
    assert r["n"] == 0 and r["changed"] == 0 and r["unidentified"] == 1 and r["no_matched_text"] == 0


def test_one_candidate_that_does_not_match_still_says_the_rule_changed(tmp_path, capsys):
    rules, log, t = _parallel_world(tmp_path, [(100.000, "ls")])
    _, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert "(the rule changed since)" in out and "not identified" not in out


def test_an_earlier_matching_call_outside_the_message_is_not_joined(tmp_path, capsys):
    rules, log, t = _parallel_world(tmp_path, [(10.0, "echo verdict"), (100.0, "ls")])
    _, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    assert "(the rule changed since)" in out and "matched text: verdict" not in out


def test_the_recovered_text_is_one_line_and_at_most_80_chars(tmp_path, capsys):
    rules, log, t = regex_world(tmp_path, r"echo[^;]*", ["echo " + "a b\n" * 100])
    _, out, _ = run(capsys, log, t, "--rules-dir", str(rules))
    line = next(l for l in out.splitlines() if "matched text:" in l)
    assert len(line.split("matched text: ")[1]) <= 80


@pytest.mark.parametrize("text, word", [
    ("registry", True), ("threat-landscape", True), ("README", True),
    ("spec_from_file_location", False), ("src/app.py", False), ("100%", False),
    ("git push --force", False),
])
def test_a_plain_word_is_told_apart_from_an_identifier_or_a_path(text, word):
    assert audit.is_word(text) is word
