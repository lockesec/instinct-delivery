"""Property tests written from the docs (README, docs/rules.md, docs/delivery-policy.md,
docs/limits.md, SECURITY.md). They check the documented limits on both sides, every
documented list value, and how delivery, matching, `after`, the state files and the
install command behave over generated inputs. Expected values are worked out here, not
taken from the package. Every test uses its own rules dir, state dir and HOME."""
import contextlib
import datetime as dt
import io
import itertools
import json
import os
import re
import stat
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from instinct_delivery import cli, hook
from instinct_delivery import state as state_mod
from instinct_delivery.engine import Settings, run
from instinct_delivery.match import match
from instinct_delivery.rules import load_rules

PROP = settings(derandomize=True, database=None, deadline=None, max_examples=40)
FEW = settings(derandomize=True, database=None, deadline=None, max_examples=15)

BASE = dt.datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
ENV_KEYS = ("INSTINCT_REPEAT", "INSTINCT_REARM_MIN", "INSTINCT_BRIEF_CAP",
            "INSTINCT_PAYLOAD_BUDGET", "INSTINCT_SHADOW", "INSTINCT_BASH_EDITS",
            "INSTINCT_RULES_DIR", "INSTINCT_MATCH_BUDGET_MS", "INSTINCT_STATE_KEEP_DAYS",
            "CLAUDE_PROJECT_DIR", "XDG_STATE_HOME")
_COUNTER = itertools.count()


@pytest.fixture(scope="module", autouse=True)
def _isolate(tmp_path_factory):
    """Module-wide guard: HOME and the state dir point into tmp, whatever a test does.
    (Replaces the per-test fixture of the same name, which would make every property
    test depend on a function-scoped fixture.)"""
    base = tmp_path_factory.mktemp("guard")
    mp = pytest.MonkeyPatch()
    mp.setenv("HOME", str(base / "home"))
    mp.setenv("INSTINCT_STATE_DIR", str(base / "state"))
    yield
    mp.undo()


WD = None


@pytest.fixture(scope="module", autouse=True)
def work(tmp_path_factory):
    global WD
    WD = tmp_path_factory.mktemp("props")
    return WD


@contextlib.contextmanager
def box(root: Path):
    """A fresh rules dir, state dir and HOME under `root`, with the env pointed at them."""
    base = root / f"case{next(_COUNTER)}"
    rules, home = base / "rules", base / "home"
    rules.mkdir(parents=True)
    home.mkdir()
    state = base / "state"
    with pytest.MonkeyPatch.context() as mp:
        for k in ENV_KEYS:
            mp.delenv(k, raising=False)
        mp.setenv("HOME", str(home))
        mp.setenv("INSTINCT_STATE_DIR", str(state))
        yield rules, state


def rule(d: Path, name: str, **fields) -> Path:
    doc = {"trigger": "t", "action": f"advice {name}", "match_tools": ["Edit"]}
    doc.update(fields)
    p = d / (name if name.endswith(".yaml") else name + ".yaml")
    try:
        p.write_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    except UnicodeEncodeError:               # a lone surrogate can only be written as an escape
        p.write_text(json.dumps(doc))
    return p


def call(rules, state, tool="Edit", inp=None, sid="s1", minutes=0, agent=None, **kw):
    s = Settings(rules_dir=rules, state_dir=state, **kw)
    inp = {"file_path": "a.py", "new_string": "x"} if inp is None else inp
    return run(tool, inp, session_id=sid, settings=s,
               now=BASE + dt.timedelta(minutes=minutes), agent_id=agent)


def kinds(res):
    return [r["delivery"] for r in res.rows]


def loaded(rules):
    rs = load_rules(rules)
    return rs, [r.file for r in rs.rejected]


# ---------------------------------------------------------------- status values

@pytest.mark.parametrize("status,where", [
    ("active", "fire"), ("validated", "fire"), ("", "fire"),
    ("candidate", "shadow"),
    ("retired", "terminal"), ("retiring", "terminal"), ("rejected", "terminal"),
    ("archived", "terminal"), ("expired", "terminal"),
])
def test_status_values_from_the_docs(work, status, where):
    with box(work) as (rules, _):
        rule(rules, "r", status=status)
        rs = load_rules(rules)
        assert (len(rs.fire), len(rs.shadow), rs.terminal) == {
            "fire": (1, 0, 0), "shadow": (0, 1, 0), "terminal": (0, 0, 1)}[where]
        assert rs.rejected == []


def test_a_misspelled_status_rejects_the_file(work):
    with box(work) as (rules, _):
        rule(rules, "r", status="activ")
        rs = load_rules(rules)
        assert not rs.fire and not rs.shadow and len(rs.rejected) == 1


# ------------------------------------------------------------- rejected and loaded

BAD = [
    ("empty action", {"action": ""}),
    ("misspelled key", {"match_contnet": ["x"]}),
    ("bad regex", {"match_content": ["("]}),
    ("repeat too large", {"match_content": ["a{99999999999}"]}),
    ("nested repeat", {"match_content": ["(a+)+"]}),
    ("nested repeat with optional", {"match_content": [r"(\w+\s?)*"]}),
    ("nested repeat dot", {"match_content": ["(.*a){12}"]}),
    ("posix class", {"match_content": ["[[:alpha:]]"]}),
    ("confidence text", {"confidence": "high"}),
    ("after within 0", {"after": {"tools": ["Read"], "within": 0}}),
    ("after within 201", {"after": {"tools": ["Read"], "within": 201}}),
    ("after no tools", {"after": {"within": 5}}),
    ("after unknown key", {"after": {"tools": ["Read"], "bogus": 1}}),
    ("follow within 0", {"follow": {"tools": ["Read"], "within": 0}}),
    ("follow within 51", {"follow": {"tools": ["Read"], "within": 51}}),
    ("test expect word", {"tests": [{"tool": "Edit", "input": {}, "expect": "maybe"}]}),
    ("lone surrogate", {"action": "bad \ud800 text"}),
    ("unknown status", {"status": "enabled"}),
]


@pytest.mark.parametrize("label,fields", BAD, ids=[b[0] for b in BAD])
def test_a_bad_rule_is_rejected_and_its_neighbour_still_loads(work, label, fields):
    with box(work) as (rules, _):
        rule(rules, "good")
        rule(rules, "bad", **fields)
        rs = load_rules(rules)
        assert [r.file for r in rs.rejected] == ["bad.yaml"], label
        assert [r.file for r in rs.fire] == ["good.yaml"]


def test_a_rule_without_match_tools_is_rejected(work):
    with box(work) as (rules, _):
        (rules / "bad.yaml").write_text(json.dumps({"trigger": "t", "action": "a"}))
        assert [r.file for r in load_rules(rules).rejected] == ["bad.yaml"]


@pytest.mark.parametrize("text", [
    "trigger: t\naction: a\nmatch_tools: [Edit]\naction: b\n",
    "trigger: &x t\naction: *x\nmatch_tools: [Edit]\n",
    "trigger: t\naction: a\nmatch_tools: [Edit]\nconfidence: .nan\n",
], ids=["duplicate key", "alias", "nan confidence"])
def test_yaml_forms_the_docs_refuse(work, text):
    with box(work) as (rules, _):
        (rules / "bad.yaml").write_text(text)
        rs = load_rules(rules)
        assert [r.file for r in rs.rejected] == ["bad.yaml"] and not rs.fire


@pytest.mark.parametrize("fields", [
    {"match_content": [r"(\.\d+)*"]},
    {"match_content": ["(a|aa)*$"]},
    {"match_content": ["(a?){30}a{30}"]},
    {"match_content": ["(a{1,50}){1,50}$"]},
    {"after": {"tools": ["Read"], "within": 1}},
    {"after": {"tools": ["Read"], "within": 200}},
    {"after": {"tools": ["Read"]}},
    {"follow": {"tools": ["Read"], "within": 1}},
    {"follow": {"tools": ["Read"], "within": 50}},
    {"confidence": 0.0},
    {"confidence": 1},
], ids=str)
def test_documented_forms_that_do_load(work, fields):
    with box(work) as (rules, _):
        rule(rules, "r", **fields)
        rs = load_rules(rules)
        assert rs.rejected == [] and len(rs.fire) == 1


def test_a_file_name_with_a_line_break_is_rejected(work):
    with box(work) as (rules, _):
        rule(rules, "a\nb")
        rs = load_rules(rules)
        assert not rs.fire and len(rs.rejected) == 1


# ------------------------------------------------------------- hidden characters

HIDDEN = [
    "\U000e0041", "\U000e007f", "\u202e", "\u2066", "\u200b", "\u200c", "\ufeff", "\u00ad",
    "ㅤ", "⠀", "᠋", "͏", "឴", "\u2065", "\ufff0", "\ue000",
    "\x1b", "︀",
]


@pytest.mark.parametrize("ch", HIDDEN, ids=lambda c: f"U+{ord(c):04X}")
@pytest.mark.parametrize("field", ["action", "trigger", "domain", "match_paths"])
def test_each_documented_hidden_character_rejects_the_rule(work, ch, field):
    with box(work) as (rules, _):
        val = f"ab{ch}cd"
        rule(rules, "r", **{field: [val] if field == "match_paths" else val})
        rs = load_rules(rules)
        assert not rs.fire and len(rs.rejected) == 1


@given(text=st.text("abcdefgh ", min_size=1, max_size=20), pos=st.integers(0, 20),
       ch=st.sampled_from(HIDDEN), work=st.just(None))
@example("ab", 1, "\u200b", None).via("doc: zero-width characters are hidden")
@PROP
def test_a_hidden_character_anywhere_in_the_action_rejects_it(text, pos, ch, work):
    pos = min(pos, len(text))
    with box(WD) as (rules, _):
        rule(rules, "plain", action=text)
        rule(rules, "hid", action=text[:pos] + ch + text[pos:])
        rs = load_rules(rules)
        assert [r.file for r in rs.fire] == ["plain.yaml"]
        assert [r.file for r in rs.rejected] == ["hid.yaml"]


@pytest.mark.parametrize("action,ok", [
    ("ok ️", False),                   # selector after a space
    ("️ok", False),                    # selector at the start
    ("a️️", False),               # a second selector
    ("a️", True),                      # one selector after a visible character
    ("a︎", True),
    ("\U0001f468\u200d\U0001f469\u200d\U0001f467 family", True),   # joiner between emoji
    ("a\u200db", False),                    # joiner between letters
    ("\u200d\U0001f468", False),
])
def test_variation_selector_and_joiner_rules(work, action, ok):
    with box(work) as (rules, _):
        rule(rules, "r", action=action)
        assert bool(load_rules(rules).fire) is ok


# -------------------------------------------------------------------- load limits

def sized(d: Path, name: str, nbytes: int) -> Path:
    doc = {"trigger": "t", "action": "", "match_tools": ["Edit"]}
    pad = nbytes - len(json.dumps(doc).encode())
    doc["action"] = "a" * pad
    p = d / name
    p.write_text(json.dumps(doc))
    assert p.stat().st_size == nbytes
    return p


@pytest.mark.parametrize("n,loads", [(1, 1), (499, 499), (500, 500), (501, 500), (503, 500)])
def test_rule_file_count_cap_is_500(work, n, loads):
    with box(work) as (rules, _):
        for i in range(n):
            rule(rules, f"r{i:04d}")
        rs = load_rules(rules)
        assert len(rs.fire) == loads
        assert (len(rs.rejected) > 0) is (n > 500)


@pytest.mark.parametrize("nbytes,loads", [(256 * 1024 - 1, True), (256 * 1024, True),
                                          (256 * 1024 + 1, False)])
def test_per_file_size_cap_is_256_kib(work, nbytes, loads):
    with box(work) as (rules, _):
        sized(rules, "big.yaml", nbytes)
        rs = load_rules(rules)
        assert bool(rs.fire) is loads and bool(rs.rejected) is (not loads)


@pytest.mark.parametrize("extra,third_loads", [(0, True), (1, False)])
def test_total_size_cap_is_512_kib(work, extra, third_loads):
    with box(work) as (rules, _):
        third = len(json.dumps({"trigger": "t", "action": "c", "match_tools": ["Edit"]}).encode())
        sized(rules, "a.yaml", 256 * 1024)
        sized(rules, "b.yaml", 256 * 1024 - third)
        # a small third file: total is exactly 512 KiB, or one byte over
        doc = {"trigger": "t", "action": "c" * (1 + extra), "match_tools": ["Edit"]}
        (rules / "c.yaml").write_text(json.dumps(doc))
        rs = load_rules(rules)
        assert (sum(1 for r in rs.fire if r.file == "c.yaml") == 1) is third_loads
        assert {r.file for r in rs.fire} >= {"a.yaml", "b.yaml"}
        if not third_loads:
            assert [r.file for r in rs.rejected] == ["c.yaml"]


# --------------------------------------------------------------- settings from env

DEFAULT_ENV = [("rearm_min", 90), ("brief_cap", 6), ("budget", 8000), ("match_budget_ms", 1000),
               ("repeat", True), ("shadow", True), ("bash_writes", True)]


@pytest.mark.parametrize("attr,val", DEFAULT_ENV)
def test_documented_defaults(work, attr, val):
    with box(work) as (rules, state):
        assert getattr(Settings.from_env(rules, state), attr) == val


@pytest.mark.parametrize("var,attr,value,expected", [
    ("INSTINCT_REPEAT", "repeat", "full", False), ("INSTINCT_REPEAT", "repeat", "off", False),
    ("INSTINCT_REPEAT", "repeat", "0", False), ("INSTINCT_REPEAT", "repeat", "no", False),
    ("INSTINCT_SHADOW", "shadow", "off", False),
    ("INSTINCT_BASH_EDITS", "bash_writes", "off", False),
    ("INSTINCT_PAYLOAD_BUDGET", "budget", "0", 0), ("INSTINCT_PAYLOAD_BUDGET", "budget", "off", 0),
    ("INSTINCT_REARM_MIN", "rearm_min", "0", 0), ("INSTINCT_BRIEF_CAP", "brief_cap", "0", 0),
    ("INSTINCT_MATCH_BUDGET_MS", "match_budget_ms", "0", 0),
    ("INSTINCT_MATCH_BUDGET_MS", "match_budget_ms", "off", 0),
    ("INSTINCT_BRIEF_CAP", "brief_cap", "3", 3), ("INSTINCT_REARM_MIN", "rearm_min", "15", 15),
    ("INSTINCT_PAYLOAD_BUDGET", "budget", "1234", 1234),
])
def test_documented_off_and_parity_values(work, var, attr, value, expected):
    with box(work) as (rules, state):
        os.environ[var] = value
        assert getattr(Settings.from_env(rules, state), attr) == expected


@given(junk=st.text("xyz!?_", min_size=1, max_size=6))
@PROP
def test_a_value_that_does_not_parse_keeps_the_default(junk):
    with box(WD) as (rules, state):
        for var in ("INSTINCT_REARM_MIN", "INSTINCT_BRIEF_CAP", "INSTINCT_PAYLOAD_BUDGET",
                    "INSTINCT_MATCH_BUDGET_MS"):
            os.environ[var] = junk
        s = Settings.from_env(rules, state)
        assert (s.rearm_min, s.brief_cap, s.budget, s.match_budget_ms) == (90, 6, 8000, 1000)


# ------------------------------------------------------------ repeats and the cap

@given(cap=st.integers(0, 8), n=st.integers(1, 14))
@example(cap=6, n=7).via("doc: six brief lines are delivered")
@example(cap=6, n=8).via("doc: quiet after six such lines")
@example(cap=0, n=14).via("doc: BRIEF_CAP 0 means no cap")
@example(cap=1, n=3).via("doc: smallest cap")
@PROP
def test_brief_lines_stop_after_the_cap(cap, n):
    with box(WD) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        out = [call(rules, state, minutes=i, brief_cap=cap) for i in range(n)]
        briefs = n - 1 if cap == 0 else min(n - 1, cap)
        assert kinds_seq(out) == ["full"] + ["brief"] * briefs + ["muted"] * (n - 1 - briefs)
        assert [bool(o.text) for o in out] == [True] * (1 + briefs) + [False] * (n - 1 - briefs)
        for k in range(1, 1 + briefs):
            assert f"x{k + 1} this session" in out[k].text
            assert "\n" not in out[k].text.strip()          # one line


def kinds_seq(results):
    return [r["delivery"] for res in results for r in res.rows]


@given(rearm=st.integers(1, 300))
@example(rearm=90).via("doc: INSTINCT_REARM_MIN default 90")
@PROP
def test_rearm_window_both_sides(rearm):
    for delta, want in ((rearm - 1, "brief"), (rearm + 1, "full")):
        with box(WD) as (rules, state):
            rule(rules, "r", action="first line\nsecond line")
            call(rules, state, minutes=0, rearm_min=rearm)
            res = call(rules, state, minutes=delta, rearm_min=rearm)
            assert kinds(res) == [want]


@pytest.mark.parametrize("gap", [1, 300, 3000])
def test_rearm_zero_never_rearms_inside_a_session(work, gap):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        call(rules, state, minutes=0, rearm_min=0)
        assert kinds(call(rules, state, minutes=gap, rearm_min=0)) == ["brief"]


def test_a_rearm_after_the_cap_starts_the_count_again(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        out = [call(rules, state, minutes=i) for i in range(8)]
        assert kinds(out[7]) == ["muted"]
        again = call(rules, state, minutes=7 + 91)
        assert kinds(again) == ["full"]
        assert kinds(call(rules, state, minutes=7 + 92)) == ["brief"]


@given(n=st.integers(1, 6))
@PROP
def test_no_session_id_means_every_fire_is_full(n):
    with box(WD) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        out = [call(rules, state, sid=None, minutes=i) for i in range(n)]
        assert kinds_seq(out) == ["full"] * n
        assert len({o.text for o in out}) == 1 and out[0].text
        assert all(r["session_id"] is None for o in out for r in o.rows)


def test_each_agent_gets_the_full_text_once(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        steps = [("A", "full"), ("B", "full"), ("A", "brief"), (None, "full"), (None, "brief"),
                 ("B", "brief")]
        for i, (agent, want) in enumerate(steps):
            assert kinds(call(rules, state, minutes=i, agent=agent)) == [want], (i, agent)


def test_editing_a_rules_text_makes_it_a_first_fire_again(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        assert kinds(call(rules, state, minutes=0)) == ["full"]
        assert kinds(call(rules, state, minutes=1)) == ["brief"]
        rule(rules, "r", action="first line\nchanged second line")
        assert kinds(call(rules, state, minutes=2)) == ["full"]


def test_repeat_full_makes_every_fire_full(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        out = [call(rules, state, minutes=i, repeat=False) for i in range(4)]
        assert kinds_seq(out) == ["full"] * 4


def test_a_candidate_rule_is_logged_and_never_shown(work):
    with box(work) as (rules, state):
        rule(rules, "r", status="candidate")
        res = call(rules, state)
        assert res.text == "" and kinds(res) == ["shadow"]
        assert call(rules, state, shadow=False).text == ""


# ---------------------------------------------------------- the full delivery split

SEPS = ["\n", "\r", "\x0c", "\u2028", "\u2029"]


@given(seps=st.lists(st.sampled_from(SEPS), min_size=1, max_size=3), word=st.text("abcdefg", max_size=8))
@example(seps=["\n"], word="").via("doc: line feed")
@example(seps=["\r"], word="").via("doc: carriage return")
@example(seps=["\x0c"], word="").via("doc: form feed")
@example(seps=["\u2028"], word="").via("doc: U+2028")
@example(seps=["\u2029"], word="").via("doc: U+2029")
@PROP
def test_full_text_splits_once_at_the_first_line_break(seps, word):
    toks = [f"zq{i}{word}" for i in range(len(seps) + 1)]
    action = toks[0] + "".join(s + t for s, t in zip(seps, toks[1:]))
    with box(WD) as (rules, state):
        rule(rules, "r", action=action)
        text = call(rules, state).text
        mark = text.find("ACTIVATED [")
        assert mark > 0
        assert all(text.count(t) == 1 for t in toks)          # none dropped, none said twice
        assert text.find(toks[0]) < mark
        assert all(text.find(t) > mark for t in toks[1:])


# ------------------------------------------------------------------------ budget

def hook_text(rules, state, name, inp, sid="s1", **kw):
    out = io.StringIO()
    payload = {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp,
               "session_id": sid}
    rc = hook.main(stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=io.StringIO(),
                   settings=Settings(rules_dir=rules, state_dir=state, **kw))
    assert rc == 0
    return context_of(json.loads(out.getvalue())) if out.getvalue() else None


def context_of(doc):
    if isinstance(doc, dict):
        if "additionalContext" in doc:
            return doc["additionalContext"]
        for v in doc.values():
            got = context_of(v)
            if got is not None:
                return got
    return None


@given(budget=st.integers(400, 2500), sizes=st.lists(st.integers(20, 1800), min_size=1, max_size=6))
@example(budget=400, sizes=[1800]).via("doc: a rule larger than the budget is cut to fit")
@PROP
def test_no_call_emits_more_than_the_payload_budget(budget, sizes):
    with box(WD) as (rules, state):
        for i, n in enumerate(sizes):
            rule(rules, f"r{i}", action="head line\n" + ("w" * n))
        text = hook_text(rules, state, "Edit", {"file_path": "a.py", "new_string": "x"}, budget=budget)
        assert text and len(text.encode()) <= budget


def test_default_budget_is_8000_bytes_and_zero_turns_it_off(work):
    with box(work) as (rules, state):
        for i in range(10):
            rule(rules, f"r{i:02d}", action="head line\n" + ("w" * 3000))
        inp = {"file_path": "a.py", "new_string": "x"}
        default = hook_text(rules, state, "Edit", inp)
        assert len(default.encode()) <= 8000
        names = [f"r{i:02d}" for i in range(10)]
        assert all(n in default for n in names)         # a withheld rule is named, not lost
        unlimited = hook_text(rules, state, "Edit", inp, sid="other", budget=0)
        assert len(unlimited.encode()) > 30000


def test_a_rule_withheld_by_the_budget_goes_first_next_time(work):
    with box(work) as (rules, state):
        for i in range(3):
            rule(rules, f"r{i}", action="head line\n" + ("w" * 3500))
        first = call(rules, state, minutes=0)
        withheld = [r["rule"] for r in first.rows if r["delivery"] == "budget"]
        assert withheld
        second = call(rules, state, minutes=1)
        by_rule = {r["rule"]: r["delivery"] for r in second.rows}
        assert by_rule[withheld[0]] == "full"


# ----------------------------------------------------------------------- matching

PATH_AB, TEXT_AB = "pq./", "xyzXYZ "
TOOLS = ["Edit", "Write", "Read"]


@given(
    tools=st.lists(st.sampled_from(TOOLS), min_size=1, max_size=2, unique=True),
    paths=st.lists(st.text(PATH_AB, min_size=1, max_size=3), max_size=2),
    content=st.lists(st.text("xyz", min_size=1, max_size=2), max_size=2),
    exclude=st.lists(st.one_of(st.text(PATH_AB, min_size=1, max_size=2),
                               st.text("XYZ", min_size=1, max_size=2)), max_size=2),
    tool=st.sampled_from(TOOLS),
    path=st.text(PATH_AB, min_size=1, max_size=6),
    text=st.text(TEXT_AB, max_size=8),
)
@example(["Edit"], ["pq"], [], [], "Edit", "pq.", "").via("doc: path substring")
@example(["Edit"], [], ["xy"], [], "Edit", "p", "XY").via("doc: content is case-insensitive")
@example(["Edit"], [], [], ["Z"], "Edit", "p", "xZ").via("doc: exclude silences")
@example(["Edit"], [], [], ["Z"], "Edit", "p", "xz").via("doc: exclude is case-sensitive")
@example(["Edit"], ["pp"], [], ["q"], "Edit", "pp.", "").via("doc: exclude beats the path match")
@example(["Edit", "Write"], ["qq", "pp"], ["z"], [], "Write", "pp", "z").via("doc: lists OR")
@example(["Edit"], ["pp"], ["z"], [], "Edit", "pp", "x").via("doc: kinds AND")
@PROP
def test_matcher_agrees_with_the_documented_rule_semantics(tools, paths, content, exclude, tool,
                                                           path, text):
    fields = {"match_tools": tools}
    if paths:
        fields["match_paths"] = paths
    if content:
        fields["match_content"] = content
    if exclude:
        fields["match_exclude"] = exclude
    expect = (tool in tools
              and (not paths or any(p in path for p in paths))
              and (not content or any(c.lower() in text.lower() for c in content))
              and not any(e in path or e in text for e in exclude))
    with box(WD) as (rules, _):
        rule(rules, "r", **fields)
        hits = match(load_rules(rules).fire, tool, {"file_path": path, "new_string": text})
        assert bool(hits) is expect


@pytest.mark.parametrize("tool", ["edit", "EDIT", "MultiEdit", "NotebookEdit", "Edit "])
def test_tool_names_are_exact_and_case_sensitive(work, tool):
    with box(work) as (rules, _):
        rule(rules, "r")
        assert not match(load_rules(rules).fire, tool, {"file_path": "a.py"})
        assert match(load_rules(rules).fire, "Edit", {"file_path": "a.py"})


@pytest.mark.parametrize("path,fires", [("a.py", True), ("A.PY", False), ("x/a.pyc", True),
                                        ("a.p", False)])
def test_match_paths_is_a_case_sensitive_substring(work, path, fires):
    with box(work) as (rules, _):
        rule(rules, "r", match_paths=[".py"])
        assert bool(match(load_rules(rules).fire, "Edit", {"file_path": path})) is fires


@pytest.mark.parametrize("inp", [
    {"file_path": "a.py"}, {"notebook_path": "a.py"}, {"path": "a.py"},
    {"edits": [{"file_path": "z"}, {"file_path": "a.py"}]},
])
def test_documented_path_fields_are_read(work, inp):
    with box(work) as (rules, _):
        rule(rules, "r", match_paths=["a.py"], match_tools=["T"])
        assert match(load_rules(rules).fire, "T", inp)


@pytest.mark.parametrize("field", ["content", "new_string", "old_string", "new_source", "pattern",
                                   "prompt", "query", "url", "description", "command"])
def test_documented_text_fields_are_read(work, field):
    tool = "Bash" if field == "command" else "T"
    with box(work) as (rules, _):
        rule(rules, "r", match_content=["needle"], match_tools=[tool])
        assert match(load_rules(rules).fire, tool, {field: "a NEEDLE b"})


def test_content_is_read_from_nested_arguments_and_edits(work):
    with box(work) as (rules, _):
        rule(rules, "r", match_content=["needle"], match_tools=["T"])
        fire = load_rules(rules).fire
        assert match(fire, "T", {"deep": {"er": ["x", {"k": "a needle"}]}})
        assert match(fire, "T", {"edits": [{"new_string": "needle"}]})
        assert not match(fire, "T", {"deep": {"er": ["x", {"k": "a hay"}]}})


def test_match_exclude_names_only_looks_at_file_names(work):
    with box(work) as (rules, _):
        rule(rules, "r", match_exclude_names=["skip"], match_content=["needle"])
        fire = load_rules(rules).fire
        assert not match(fire, "Edit", {"file_path": "d/skip.py", "new_string": "needle"})
        assert match(fire, "Edit", {"file_path": "skip/ok.py", "new_string": "needle"})
        assert match(fire, "Edit", {"file_path": "ok.py", "new_string": "needle skip"})
        assert match(fire, "Edit", {"file_path": "skip/../ok.py", "new_string": "needle"})
        assert not match(fire, "Edit", {"file_path": "ok/../skip.py", "new_string": "needle"})


def test_match_commands_applies_to_bash_only(work):
    with box(work) as (rules, _):
        rule(rules, "r", match_tools=["Bash", "Edit"], match_commands=["git push"])
        fire = load_rules(rules).fire
        assert match(fire, "Bash", {"command": "git push origin x"})
        assert not match(fire, "Bash", {"command": "git pull"})
        assert not match(fire, "Edit", {"file_path": "git push"})


# -------------------------------------------------------------------- bash writes

WRITES = [
    "echo a > out.txt", "echo a >> out.txt", "printf a >| out.txt", "echo a 1> out.txt",
    "echo a &> out.txt", "echo a >& out.txt", "ls > out.txt", "cat in > out.txt",
    "tee out.txt", "tee -a out.txt", "echo a | tee out.txt", "echo a |& tee out.txt",
    "(tee out.txt)", "{ tee out.txt; }", "sed -i s/a/b/ out.txt", "sed -i.bak s/a/b/ out.txt",
    "sed --in-place s/a/b/ out.txt", "sed -n -i -e s/a/b/ out.txt",
    "cat > out.txt <<EOF\nhi\nEOF", "cat <<EOF >> out.txt\nhi\nEOF", "cat <<EOF | tee out.txt\nhi\nEOF",
    "cat > out.txt <<'EOF'\nhi\nEOF", "{ echo a; } > out.txt", "( echo a ) > out.txt",
    "sudo tee out.txt", "sudo -u root tee out.txt", "env X=1 tee out.txt", "FOO=1 tee out.txt",
    "nohup tee out.txt", "time tee out.txt", "/usr/bin/tee out.txt",
    "if true; then echo a > out.txt; fi", "for i in 1; do echo a > out.txt; done", "! echo a > out.txt",
    "cp in.txt out.txt", "mv in.txt out.txt", "install in.txt out.txt", "ln -s in.txt out.txt",
    "dd of=out.txt", "curl -o out.txt http://192.0.2.1/x", "wget -O out.txt http://192.0.2.1/x",
    "bash -c 'echo a > out.txt'", "sh -c 'echo a > out.txt'", "eval 'echo a > out.txt'",
    "echo $(echo a > out.txt)", "echo `echo a > out.txt`", "echo a; echo b > out.txt",
    "false || echo a > out.txt", "echo abc12>out.txt", "echo a \\\n> out.txt",
    "echo a >out.txt >&2", "echo a > ok.txt > out.txt",
    "python3 - <<EOF\nopen('out.txt', 'w').write('x')\nEOF",
    "python3 - <<EOF\nfrom pathlib import Path\nPath('out.txt').write_text('x')\nEOF",
]
NOT_WRITES = [
    'echo "a > out.txt"', "echo 'a > out.txt'", "echo a \\> out.txt", "echo a # > out.txt",
    "echo a 2> out.txt", "echo a 2>out.txt", "echo a 3> out.txt", "echo a >&2",
    "echo a > /dev/null", "echo a > /dev/stdout", "echo a > /dev/stderr", "echo a > /dev/tty",
    "echo a > /dev/fd/3", "echo a > $OUT", "echo a > ${OUT}", "echo $((1 > 2))", "echo ${x#*>}",
    "[[ 1 > 2 ]]", "echo a -> out.txt", "cat out.txt", "ls out.txt", "grep x out.txt",
    "git checkout out.txt", "vim out.txt", "xargs touch out.txt", "find . -exec touch out.txt {} +",
    "ssh host 'echo a > out.txt'", "curl -D out.txt http://192.0.2.1/x", "wget -o out.txt http://192.0.2.1/x",
    "cat <<EOF\nout.txt > x\nEOF",
]


@pytest.mark.parametrize("cmd", WRITES)
def test_documented_shell_writes_are_read(work, cmd):
    with box(work) as (rules, _):
        rule(rules, "r", match_tools=["Write"], match_paths=["out.txt"])
        assert match(load_rules(rules).fire, "Bash", {"command": cmd}), cmd


@pytest.mark.parametrize("cmd", NOT_WRITES)
def test_documented_non_writes_are_not_read(work, cmd):
    with box(work) as (rules, _):
        rule(rules, "r", match_tools=["Write"], match_paths=["out.txt"])
        assert not match(load_rules(rules).fire, "Bash", {"command": cmd}), cmd


@pytest.mark.parametrize("tools,fires", [
    (["Write"], True), (["Edit"], True), (["Edit", "Write"], True), (["Read"], False),
    (["Bash"], False), (["MultiEdit"], False),
])
def test_a_shell_write_counts_as_write_or_edit_only(work, tools, fires):
    with box(work) as (rules, _):
        rule(rules, "r", match_tools=tools, match_paths=["out.txt"])
        hits = match(load_rules(rules).fire, "Bash", {"command": "echo a > out.txt"})
        assert bool(hits) is fires


def test_shell_writes_off_gives_the_plain_matcher(work):
    with box(work) as (rules, _):
        rule(rules, "r", match_tools=["Write"], match_paths=["out.txt"])
        fire = load_rules(rules).fire
        assert match(fire, "Bash", {"command": "echo a > out.txt"})
        assert not match(fire, "Bash", {"command": "echo a > out.txt"}, bash_writes=False)


@given(name=st.from_regex(r"[a-z]{1,8}\.txt", fullmatch=True), body=st.text("abc 123", max_size=10))
@PROP
def test_a_redirect_writes_the_named_file_but_quoted_text_does_not(name, body):
    with box(WD) as (rules, _):
        rule(rules, "r", match_tools=["Write"], match_paths=[name])
        fire = load_rules(rules).fire
        assert match(fire, "Bash", {"command": f"echo {body!r} > {name}"})
        assert not match(fire, "Bash", {"command": f"echo '{body} > {name}'"})
        assert not match(fire, "Bash", {"command": f"echo {body!r} 2> {name}"})


# ---------------------------------------------------------------------- `after`

CALLS = {
    "pre": {"command": "echo secret"}, "own": {"command": "curl host"},
    "both": {"command": "curl secret"}, "other": {"command": "ls"},
}


def after_rule(rules, within=None):
    a = {"tools": ["Bash"], "content": ["secret"]}
    if within is not None:
        a["within"] = within
    rule(rules, "ex", match_tools=["Bash"], match_content=["curl"], after=a, action="exfil advice")


def after_oracle(seq, within):
    last, out = None, []
    for i, k in enumerate(seq, 1):
        out.append(k in ("own", "both") and last is not None and i - last <= within)
        if k in ("pre", "both"):
            last = i
    return out


@given(seq=st.lists(st.sampled_from(sorted(CALLS)), min_size=1, max_size=14), within=st.integers(1, 6))
@example(["pre", "other", "other", "own"], 3).via("doc: i - j <= within")
@example(["pre", "other", "other", "other", "own"], 3).via("doc: one past within")
@example(["both"], 5).via("doc: one call never satisfies itself")
@example(["both", "own"], 1).via("doc: a call that matches both counts for the next one")
@example(["pre", "own"], 1).via("doc: within 1")
@example(["pre", "other", "own"], 1).via("doc: within 1, one call too late")
@example(["pre", "other", "pre", "other", "other", "own"], 3).via("doc: a later match renews the window")
@example(["own"], 5).via("doc: no precondition")
@PROP
def test_after_fires_exactly_inside_the_window(seq, within):
    with box(WD) as (rules, state):
        after_rule(rules, within)
        got = [bool(call(rules, state, "Bash", CALLS[k], minutes=i, repeat=False).text)
               for i, k in enumerate(seq)]
        assert got == after_oracle(seq, within)


@pytest.mark.parametrize("within,gap,fires", [(20, 19, True), (20, 20, True), (20, 21, False)])
def test_after_default_window_is_20_calls(work, within, gap, fires):
    with box(work) as (rules, state):
        after_rule(rules)                      # no `within`: the default
        call(rules, state, "Bash", CALLS["pre"], minutes=0, repeat=False)
        for i in range(gap - 1):
            call(rules, state, "Bash", CALLS["other"], minutes=1 + i, repeat=False)
        res = call(rules, state, "Bash", CALLS["own"], minutes=gap, repeat=False)
        assert bool(res.text) is fires


@pytest.mark.parametrize("gap,fires", [(199, True), (200, True), (201, False)])
def test_after_window_of_200_is_accepted_and_exact(work, gap, fires):
    with box(work) as (rules, state):
        after_rule(rules, 200)
        call(rules, state, "Bash", CALLS["pre"], minutes=0, repeat=False)
        for i in range(gap - 1):
            call(rules, state, "Bash", CALLS["other"], minutes=1 + i, repeat=False)
        assert bool(call(rules, state, "Bash", CALLS["own"], minutes=gap, repeat=False).text) is fires


def test_after_needs_a_session_and_says_so(work):
    with box(work) as (rules, state):
        after_rule(rules, 5)
        call(rules, state, "Bash", CALLS["pre"], sid=None)
        res = call(rules, state, "Bash", CALLS["own"], sid=None)
        assert res.text == "" and kinds(res) == ["unevaluated"]
        quiet = call(rules, state, "Bash", CALLS["other"], sid=None)
        assert quiet.rows == []                # its own filters do not match: no row


def test_after_sessions_do_not_share_history(work):
    with box(work) as (rules, state):
        after_rule(rules, 5)
        call(rules, state, "Bash", CALLS["pre"], sid="one")
        assert call(rules, state, "Bash", CALLS["own"], sid="two", minutes=1).text == ""
        assert call(rules, state, "Bash", CALLS["own"], sid="one", minutes=2).text


def test_after_state_stores_counters_not_call_text(work):
    with box(work) as (rules, state):
        after_rule(rules, 5)
        call(rules, state, "Bash", {"command": "echo secret CANARYTEXT"})
        files = [p for p in state.rglob("*") if p.is_file()]
        assert files
        assert all(b"CANARYTEXT" not in p.read_bytes() for p in files)


# --------------------------------------------------------------- the activation log

def test_the_log_never_holds_call_content_and_names_the_written_path(work):
    with box(work) as (rules, state):
        rule(rules, "r", match_tools=["Write"], match_paths=["out.txt"], match_content=[r"canary\d+"])
        res = call(rules, state, "Bash", {"command": "echo CANARY123 > out.txt"})
        assert res.text
        blobs = b"".join(p.read_bytes() for p in state.rglob("*") if p.is_file())
        assert b"CANARY123" not in blobs
        rows = [json.loads(x) for x in (state / "activations.jsonl").read_text().splitlines()]
        assert [r["file_path"] for r in rows] == ["out.txt"] and rows[0]["via"] == "bash-write"
        assert rows[0]["rule"] == "r.yaml" and rows[0]["session_id"] == "s1"
        assert set(rows[0]) >= set(state_mod.ROW_FIELDS)


def test_the_rows_of_a_call_sum_to_the_bytes_it_emitted(work):
    with box(work) as (rules, state):
        for i in range(3):
            rule(rules, f"r{i}", action="head line\nrest " + "w" * 500)
        res = call(rules, state)
        assert sum(r["bytes"] for r in res.rows) == len(res.text.encode())


def test_files_are_private_whatever_the_umask(work):
    with box(work) as (rules, state):
        rule(rules, "r")
        old = os.umask(0)
        try:
            call(rules, state)
        finally:
            os.umask(old)
        modes = {p: stat.S_IMODE(p.stat().st_mode) for p in [state, *state.rglob("*")]}
        assert modes[state / "activations.jsonl"] == 0o600
        assert modes[state] == 0o700 and modes[state_mod.delivery_dir(state)] == 0o700
        files = [p for p in modes if p.is_file()]
        assert len(files) >= 2 and all(modes[p] == 0o600 for p in files)


def test_an_existing_state_directory_keeps_its_mode(work):
    with box(work) as (rules, state):
        rule(rules, "r")
        state.mkdir()
        state.chmod(0o755)
        call(rules, state)
        assert stat.S_IMODE(state.stat().st_mode) == 0o755


def test_a_log_that_is_a_symlink_is_not_written_through(work):
    with box(work) as (rules, state):
        rule(rules, "r")
        state.mkdir()
        victim = state.parent / "victim.txt"
        victim.write_text("precious")
        os.symlink(victim, state / "activations.jsonl")
        call(rules, state)
        assert victim.read_text() == "precious"


# ------------------------------------------------------------------- state dir

def resolved(**env):
    for k, v in env.items():
        os.environ[k] = v
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return state_mod.state_dir(), err.getvalue()


def test_state_dir_resolution_order(work):
    with box(work) as (_, state):
        home = Path(os.environ["HOME"])
        del os.environ["INSTINCT_STATE_DIR"]
        assert resolved()[0] == home / ".local" / "state" / "instinct-delivery"
        xdg = home / "xdg"
        assert resolved(XDG_STATE_HOME=str(xdg))[0] == xdg / "instinct-delivery"
        got, _ = resolved(INSTINCT_STATE_DIR=str(state))
        assert got == state                                  # beats XDG
        got, _ = resolved(INSTINCT_STATE_DIR="~/mine")
        assert got == home / "mine"                          # ~ is expanded


@given(rel=st.text("abc./", min_size=1, max_size=8).filter(lambda s: not s.startswith("/")))
@PROP
def test_a_relative_state_dir_is_ignored_with_a_note(rel):
    with box(WD):
        home = Path(os.environ["HOME"])
        got, err = resolved(INSTINCT_STATE_DIR=rel)
        assert got == home / ".local" / "state" / "instinct-delivery"
        assert err.strip()


# ---------------------------------------------------------------- hook robustness

@given(raw=st.one_of(st.text(max_size=30), st.integers().map(str), st.lists(st.integers()).map(json.dumps),
                     st.just("{"), st.just("null"), st.just("{}")))
@PROP
def test_the_hook_ignores_bad_input_silently(raw):
    with box(WD) as (rules, state):
        rule(rules, "r")
        out, err = io.StringIO(), io.StringIO()
        rc = hook.main(stdin=io.StringIO(raw), stdout=out, stderr=err,
                       settings=Settings(rules_dir=rules, state_dir=state))
        assert rc == 0 and out.getvalue() == ""
        assert not (state / "activations.jsonl").exists()


def test_the_hook_output_carries_the_advice_and_the_log_row(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="remember the tests")
        text = hook_text(rules, state, "Edit", {"file_path": "a.py", "new_string": "x"})
        assert "remember the tests" in text
        rows = (state / "activations.jsonl").read_text().splitlines()
        assert len(rows) == 1 and json.loads(rows[0])["delivery"] == "full"


# ---------------------------------------------------------------- install command

def cli_run(argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
    except SystemExit as e:
        rc = e.code
    return rc, out.getvalue()


@given(
    foreign=st.lists(st.tuples(st.text("ABCabc|.*", min_size=1, max_size=6),
                               st.text("abcdef -", min_size=1, max_size=10)), max_size=3),
    other=st.lists(st.text("abc", min_size=1, max_size=4), max_size=2),
)
@PROP
def test_install_keeps_every_other_entry_and_is_idempotent(foreign, other):
    with box(WD) as (_, _state):
        proj = _state.parent / "proj"
        (proj / ".claude").mkdir(parents=True)
        entries = [{"matcher": m, "hooks": [{"type": "command", "command": c}]} for m, c in foreign]
        doc = {"model": "m", "hooks": {"PreToolUse": entries, "PostToolUse": [{"matcher": "x", "hooks": []}]},
               "keys": other}
        sf = proj / ".claude" / "settings.json"
        sf.write_text(json.dumps(doc))
        rc, _ = cli_run(["install", "--project", str(proj)])
        assert rc == 0
        after = json.loads(sf.read_text())
        assert after["model"] == "m" and after["keys"] == other
        assert after["hooks"]["PostToolUse"] == doc["hooks"]["PostToolUse"]
        pre = after["hooks"]["PreToolUse"]
        assert len(pre) == len(entries) + 1
        assert all(e in pre for e in entries)
        assert (proj / ".claude" / "instincts").is_dir()
        first = sf.read_bytes()
        assert cli_run(["install", "--project", str(proj)])[0] == 0
        assert sf.read_bytes() == first


@pytest.mark.parametrize("content", [
    "{not json", '{"hooks": []}', '{"hooks": {"PreToolUse": {}}}', '{"a": NaN}', "[]", '{"a": Infinity}',
])
def test_install_refuses_a_settings_file_it_cannot_trust(work, content):
    with box(work) as (_, state):
        proj = state.parent / "proj"
        (proj / ".claude").mkdir(parents=True)
        sf = proj / ".claude" / "settings.json"
        sf.write_text(content)
        rc, _ = cli_run(["install", "--project", str(proj)])
        assert rc == 2 and sf.read_text() == content


def test_install_refuses_a_read_only_settings_file(work):
    with box(work) as (_, state):
        proj = state.parent / "proj"
        (proj / ".claude").mkdir(parents=True)
        sf = proj / ".claude" / "settings.json"
        sf.write_text("{}")
        sf.chmod(0o444)
        rc, _ = cli_run(["install", "--project", str(proj)])
        assert rc == 2 and sf.read_text() == "{}"


# ---------------------------------------------------------------- reports and exits

def cli_in(rules, argv):
    os.environ["INSTINCT_RULES_DIR"] = str(rules)
    return cli_run(argv)


def test_a_report_with_no_log_is_not_evaluable_not_zero(work):
    with box(work):
        rc, out = cli_run(["repeat-rate"])
        assert rc == 2 and "NOT EVALUABLE" in out


def test_a_report_over_an_empty_log_is_not_evaluable(work):
    with box(work) as (_, state):
        state.mkdir()
        (state / "activations.jsonl").write_text("")
        rc, out = cli_run(["repeat-rate"])
        assert rc == 2 and "NOT EVALUABLE" in out


def test_a_negative_days_is_a_usage_error(work):
    with box(work):
        assert cli_run(["repeat-rate", "--days", "-1"])[0] == 64


@given(seq=st.lists(st.tuples(st.sampled_from(["s1", "s2"]), st.sampled_from(["a", "b"])),
                    min_size=1, max_size=9))
@example([("s1", "a"), ("s1", "a")]).via("doc: quick start, 1 of 2")
@FEW
def test_repeat_rate_counts_same_rule_again_in_the_same_session(seq):
    with box(WD) as (rules, state):
        rule(rules, "a")
        rule(rules, "b", match_paths=["never-matches-zz"])
        rule(rules, "b2", match_tools=["Edit"], action="b advice")
        for i, (sid, _) in enumerate(seq):
            call(rules, state, sid=sid, minutes=i, brief_cap=0)
        # rules a and b2 both match every call: 2 fires per call
        n = 2 * len(seq)
        distinct = {(sid, r) for sid, _ in seq for r in ("a.yaml", "b2.yaml")}
        _, out = cli_run(["repeat-rate"])
        m = re.search(r"repeat-rate: (\d+) of (\d+) matched", out)
        assert m, out
        assert (int(m.group(1)), int(m.group(2))) == (n - len(distinct), n)


def test_quick_start_repeat_rate_is_one_of_two(work):
    with box(work) as (rules, state):
        rule(rules, "r", action="first line\nsecond line")
        call(rules, state, minutes=0)
        call(rules, state, minutes=1)
        rc, out = cli_run(["repeat-rate"])
        assert rc == 0 and "1 of 2" in out and "50.0%" in out


def test_test_command_exit_codes(work):
    ok = {"tool": "Edit", "input": {"file_path": "a.py", "new_string": "print(1)"}, "expect": "fires"}
    bad = dict(ok, expect="silent")
    with box(work) as (rules, _):
        rule(rules, "r", match_content=[r"print\("], tests=[ok])
        assert cli_in(rules, ["test"])[0] == 0
        rule(rules, "r", match_content=[r"print\("], tests=[ok, bad])
        assert cli_in(rules, ["test"])[0] == 1
        rule(rules, "r", match_content=[r"print\("])
        assert cli_in(rules, ["test"])[0] == 2


def test_doctor_exit_codes(work):
    with box(work) as (rules, _):
        assert cli_in(rules, ["doctor"])[0] == 2                 # nothing loaded
        rule(rules, "good")
        rule(rules, "bad", action="")
        assert cli_in(rules, ["doctor"])[0] == 1                 # a rejected rule
