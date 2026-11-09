"""`replay FILE.jsonl`: rows {session, tool_name, tool_input} through the real engine, one
throwaway state dir, one session per `session` value."""
import json

from conftest import write_rule

from instinct_delivery import cli

SQL = "mcp__db__execute_sql"


def _rows(*rows):
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows)


def _call(session, tool, **ti):
    return {"kind": "x", "source": "y", "step": 99, "session": session, "tool_name": tool, "tool_input": ti}


def _setup(rules):
    write_rule(rules, "plain", match_tools=["Bash"], match_commands=["rm -rf"], action="plain advice")
    write_rule(rules, "toxic", match_tools=[SQL], match_content=[r"\binsert\s+into\b"],
               after={"tools": [SQL], "content": [r"\bfrom\s+\w*token"]}, action="toxic advice")


def _replay(tmp_path, rules, capsys, content, *extra):
    f = tmp_path / "r.jsonl"
    f.write_text(content)
    code = cli.main(["replay", str(f), "--rules-dir", str(rules), *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


FILE = _rows(
    {"session": "attack", "event": "user_prompt", "prompt": "hi"},                        # no tool call
    _call("attack", SQL, query="select * from integration_tokens"),
    {"session": "attack", "tool_name": SQL, "tool_input": None, "tool_response": "..."},    # a result
    _call("attack", SQL, query="insert into m (b) values (1)"),
    _call("benign", SQL, query="insert into m (b) values (1)"),
    _call("benign", "Bash", command="rm -rf build"),
    "not json at all",
    "[1, 2]",
)


def test_replay_runs_the_real_engine_per_session_and_reports_each_delivery(tmp_path, rules, capsys):
    _setup(rules)
    code, out, _ = _replay(tmp_path, rules, capsys, FILE, "--json")
    j = json.loads(out)
    assert code == 0
    calls = [(c["session"], c["step"], c["tool"], [(d["rule"], d["delivery"]) for d in c["deliveries"]])
             for c in j["calls"]]
    assert calls == [("attack", 1, SQL, []),
                     ("attack", 3, SQL, [("toxic.yaml", "full")]),
                     ("benign", 4, SQL, []),                                     # quiet: no secret read first
                     ("benign", 5, "Bash", [("plain.yaml", "full")])]
    assert j["replayed"] == 4 and j["sessions"] == 2


def test_replay_counts_what_it_skipped_and_why(tmp_path, rules, capsys):
    _setup(rules)
    _, out, _ = _replay(tmp_path, rules, capsys, FILE, "--json")
    j = json.loads(out)
    assert j["rows"] == 8 and j["skipped"] == {"not_json": 1, "not_an_object": 1, "no_tool_call": 2}
    assert j["replayed"] + sum(j["skipped"].values()) == j["rows"]


def test_replay_summarises_per_session_and_in_total(tmp_path, rules, capsys):
    _setup(rules)
    _, out, _ = _replay(tmp_path, rules, capsys, FILE, "--json")
    j = json.loads(out)
    by = {s["session"]: s for s in j["sessions_summary"]}
    assert by["attack"]["calls"] == 2 and by["attack"]["calls_with_delivery"] == 1
    assert by["attack"]["deliveries"] == {"full": 1} and by["benign"]["calls_with_delivery"] == 1
    assert j["totals"] == {"calls": 4, "calls_with_delivery": 2, "deliveries": {"full": 2}}


def test_replay_text_names_session_step_tool_and_rule(tmp_path, rules, capsys):
    _setup(rules)
    code, out, _ = _replay(tmp_path, rules, capsys, FILE)
    assert code == 0
    assert "attack  row 3  " + SQL + "  toxic:full" in out
    assert "attack  row 1  " + SQL + "  -" in out
    assert "total: 4 call(s) in 2 session(s); 2 call(s) with a delivery" in out
    assert "4 replayed of 8 row(s); skipped 4" in out


def test_a_repeat_in_one_session_is_a_brief_like_the_hook_says(tmp_path, rules, capsys):
    _setup(rules)
    content = _rows(*[_call("s", "Bash", command="rm -rf x") for _ in range(2)])
    _, out, _ = _replay(tmp_path, rules, capsys, content, "--json")
    assert [c["deliveries"][0]["delivery"] for c in json.loads(out)["calls"]] == ["full", "brief"]


def test_replay_writes_nothing_outside_its_throwaway_dir(tmp_path, rules, capsys):
    _setup(rules)
    before = sorted(p.name for p in tmp_path.iterdir())
    _replay(tmp_path, rules, capsys, FILE)
    assert not (tmp_path / "state").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before + ["r.jsonl"])


def test_an_unreadable_file_is_not_evaluable(tmp_path, rules, capsys):
    _setup(rules)
    code = cli.main(["replay", str(tmp_path / "nope.jsonl"), "--rules-dir", str(rules)])
    out = capsys.readouterr().out
    assert code == 2 and "NOT EVALUABLE" in out and "nope.jsonl" in out


def test_a_file_with_no_replayable_row_is_not_evaluable_with_the_counts(tmp_path, rules, capsys):
    _setup(rules)
    code, out, _ = _replay(tmp_path, rules, capsys, _rows({"session": "s", "event": "user_prompt"}, "x"))
    assert code == 2 and "NOT EVALUABLE" in out and "0 of 2" in out


def test_an_empty_file_is_not_evaluable(tmp_path, rules, capsys):
    _setup(rules)
    assert _replay(tmp_path, rules, capsys, "")[0] == 2


def test_a_missing_rules_dir_is_not_evaluable_not_a_silent_replay(tmp_path, capsys):
    f = tmp_path / "r.jsonl"
    f.write_text(FILE)
    code = cli.main(["replay", str(f), "--rules-dir", str(tmp_path / "gone")])
    out = capsys.readouterr().out
    assert code == 2 and "NOT EVALUABLE" in out and "gone" in out


def test_rows_without_a_session_replay_with_no_session_and_say_so(tmp_path, rules, capsys):
    _setup(rules)
    content = _rows({"tool_name": SQL, "tool_input": {"query": "insert into m (b) values (1)"}})
    code, out, _ = _replay(tmp_path, rules, capsys, content, "--json")
    j = json.loads(out)
    assert code == 0
    assert j["calls"][0]["session"] is None
    assert j["calls"][0]["deliveries"] == [{"rule": "toxic.yaml", "delivery": "unevaluated"}]
    text = _replay(tmp_path, rules, capsys, content)[1]
    assert "(no session)" in text


def test_replay_reports_rejected_rules_instead_of_hiding_them(tmp_path, rules, capsys):
    _setup(rules)
    (rules / "broken.yaml").write_text("action: x\n")
    _, out, _ = _replay(tmp_path, rules, capsys, FILE)
    assert "1 rule(s) rejected" in out
