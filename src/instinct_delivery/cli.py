"""instinct-delivery command line.

    instinct-delivery hook                 PreToolUse hook (reads the payload on stdin)
    instinct-delivery check TOOL JSON      dry run: what would one call deliver?
    instinct-delivery doctor               rules loaded, rejected (with why), over budget
    instinct-delivery test                 run the `tests` cases written in the rule files
    instinct-delivery replay FILE.jsonl    replay recorded calls through the real engine
    instinct-delivery install [--project]  register the hook in .claude/settings.json
    instinct-delivery repeat-rate          same rule again in the same session
    instinct-delivery cost                 delivered bytes per session, first vs re-arm
    instinct-delivery fire-rate            ratchet on per-rule worst-session bytes
    instinct-delivery cofire               rule pairs that fire on the same call
    instinct-delivery persistence-loss     logged full deliveries missing from transcripts
    instinct-delivery breadth              match rate per rule, replayed over transcripts
    instinct-delivery audit                sample each rule's fires, joined to the triggering call
    instinct-delivery audit label ID       record a TP/FP verdict on a sampled fire
    instinct-delivery labels stats|add     human TP/FP judgements

Exit codes: 0 ok, 1 findings, 2 NOT EVALUABLE, 64 usage. The hook exits 0 once it has started.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from . import __doc__ as _PACKAGE_DOC
from . import __version__, audit, measure
from . import state as st
from .engine import (WITHHELD_RESERVE, Settings, _cost, full_lines, is_input_reason, rules_problem, run,
                     slow_message, unevaluated_message)
from .hidden import printable
from .hook import main as hook_main
from .match import is_parse_slow
from .sequence import is_after_reason
from .rules import load_rules

OK, FINDINGS, NOT_EVALUABLE, USAGE = 0, 1, 2, 64


def _days(v: str) -> float:
    try:
        d = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {v!r}") from None
    if not (d >= 0 and d != float("inf")):
        raise argparse.ArgumentTypeError(f"must be a finite number of days >= 0, got {v}")
    return d


def _positive_int(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {v!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, not {v}")
    return n


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(USAGE)


def _settings(a) -> Settings:
    return Settings.from_env(rules_dir=getattr(a, "rules_dir", None),
                             state_dir=getattr(a, "state_dir", None))


def _log(a) -> Path:
    return Path(a.log) if getattr(a, "log", None) else st.log_path(
        Path(a.state_dir) if getattr(a, "state_dir", None) else None)


def _print(obj, as_json: bool, text: str):
    print(json.dumps(obj, indent=2, default=str) if as_json else text)


def _check_notes(s, tool: str, ti) -> None:
    """On stderr, why a dry run may be silent for a reason that is not "no rule matched":
    an input that is not a JSON object is read as empty, and a tool no rule is scoped to
    cannot fire. Exit stays 0: silence is a valid answer, the note says which silence."""
    if not isinstance(ti, dict):
        kind = {list: "list", str: "string", bool: "boolean", type(None): "null"}.get(type(ti), "number")
        print(f"check: the tool input is a JSON {kind}, not a JSON object; it was read as an "
              f"empty input, so nothing in it could match", file=sys.stderr)
    rs = load_rules(s.rules_dir)
    if rs.rejected:
        print(f"check: {len(rs.rejected)} rule file(s) did not load and could not match "
              f"(see `doctor`)", file=sys.stderr)
    scoped = set()
    for rule in rs.fire + (rs.shadow if s.shadow else []):
        scoped |= set(rule.tools)
    reach = {tool} | ({"Write", "Edit"} if tool == "Bash" and s.bash_writes else set())
    if scoped and not reach & scoped:
        print(f"check: no loaded rule is scoped to tool {tool!r} (the loaded rules are scoped to: "
              f"{', '.join(sorted(scoped))}), so nothing can fire", file=sys.stderr)


def cmd_check(a) -> int:
    try:
        ti = json.loads(a.tool_input)
    except ValueError as e:
        print(f"check: tool input is not JSON ({e})", file=sys.stderr)
        return USAGE
    if a.record and a.scratch_state:
        print("check: --record and --scratch-state are alternatives (--record writes the real "
              "log and state; --scratch-state uses a state dir of your choosing)", file=sys.stderr)
        return USAGE
    if a.state_dir and not a.record:
        print("check: --state-dir only applies with --record (a dry run uses a throwaway state "
              "dir; --scratch-state DIR chooses one)", file=sys.stderr)
        return USAGE
    s = _settings(a)
    if a.record:
        res = run(a.tool, ti, a.session, settings=s)
    elif a.scratch_state:
        s.state_dir = Path(a.scratch_state)
        res = run(a.tool, ti, a.session, settings=s)
    else:
        # a dry run touches nothing real: a throwaway state dir, so the answer
        # is what a FIRST fire in a fresh session would deliver
        import tempfile
        with tempfile.TemporaryDirectory(prefix="instinct-check-") as td:
            s.state_dir = Path(td)
            res = run(a.tool, ti, a.session, settings=s)
    if res.rules_problem:
        # stdout is the advice a call would get: the fault goes to stderr, not into it
        print(f"check: NOT EVALUABLE — {res.rules_problem}", file=sys.stderr)
        return NOT_EVALUABLE
    sys.stdout.write(res.text)
    _check_notes(s, a.tool, ti)
    for rule_file, why in res.slow:
        print(slow_message(rule_file, why), file=sys.stderr)
    for rule_file, why in res.unevaluated:
        print(unevaluated_message(rule_file, why), file=sys.stderr)
    for rule_file in res.after_unmet:
        print(f"check: rule {rule_file} matched this call but needs an earlier call (`after`) that "
              f"matched its precondition, and none did in this state, so it was not evaluated as "
              f"firing; a dry run is a first call. Build the history with `--record --session ID` "
              f"(or `--scratch-state DIR`) over several calls, or write a `before` list in a `tests` case",
              file=sys.stderr)
    if a.verbose:
        for r in res.rows:
            print(f"# {r['delivery']:<9} {r['rule']}  matched={r['matched']!r}"
                  + (f" via={r['via']}" if r.get("via") else ""), file=sys.stderr)
    return OK


def _log_rows_of_kind(s, kind: str):
    """The activation log's rows of one delivery kind inside the keep window, as
    (rule file or None, reason); empty when there is no readable log."""
    log = st.log_path(s.state_dir)
    if not log.exists():
        return
    try:
        rows, _ = st.read_log(log)
    except OSError:
        return
    import datetime as _dt
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=s.keep_days)
    mine = st.project_id(s.rules_dir)
    for r in rows:
        when = st.parse_ts(r.get("ts"))
        if when is not None and when < cutoff:
            continue
        if isinstance(r.get("project"), str) and r["project"] != mine:
            continue                    # another project's row (one log per user)
        if r.get("delivery") == kind and (isinstance(r.get("rule"), str) or r.get("rule") is None):
            yield r.get("rule"), r.get("reason")


def _slow_rules(s, kind: str = "slow") -> dict:
    """{rule file: count} from the activation log's rows of one kind (`slow`, or
    `unevaluated`, whose rule is None for Bash-write parsing); {} when there is no log."""
    out: dict = {}
    for rule, _why in _log_rows_of_kind(s, kind):
        out[rule] = out.get(rule, 0) + 1
    return out


def _unevaluated(s) -> tuple:
    """(parse timeouts, {rule file or None: count} of rules the spent budget never reached),
    read from the logged reason: a parse that ran out of time is its own cause."""
    parse = 0
    spent: dict = {}
    for rule, why in _log_rows_of_kind(s, "unevaluated"):
        if is_after_reason(why):
            continue                    # its own cause: _after_unevaluated
        if is_input_reason(why):
            continue                    # its own cause: _unread_inputs
        if rule is None and is_parse_slow(why):
            parse += 1
        else:
            spent[rule] = spent.get(rule, 0) + 1
    return parse, spent


def _after_unevaluated(s) -> dict:
    """{rule file: count} of calls on which an `after` rule's own filters matched but its
    precondition could not be looked at (no session id), from the log's `unevaluated` rows."""
    out: dict = {}
    for rule, why in _log_rows_of_kind(s, "unevaluated"):
        if is_after_reason(why):
            out[rule] = out.get(rule, 0) + 1
    return out


def _unread_inputs(s) -> int:
    """Calls the hook could not read at all, from the log's `unevaluated` rows."""
    return sum(1 for _rule, why in _log_rows_of_kind(s, "unevaluated") if is_input_reason(why))


def _log_problem(s) -> str:
    """Why the activation log cannot be read, or "" (an absent log is simply no history)."""
    log = st.log_path(s.state_dir)
    try:
        if log.exists():
            st.read_log(log)
    except OSError as e:
        return f"activation log {log} cannot be read ({e.strerror or type(e).__name__})"
    return ""


def _state_problem(s) -> str:
    """Why the hook cannot save what it must remember, or "". Writes and removes one temp file."""
    ddir = st.delivery_dir(s.state_dir)
    try:
        st._mkdir_private(ddir, 2)
        with tempfile.TemporaryFile(dir=ddir):
            pass
    except OSError as e:
        return f"{ddir} cannot be written ({e.strerror or type(e).__name__})"
    return ""


KNOWN_TOOLS = ("Bash", "Edit", "Glob", "Grep", "MultiEdit", "NotebookEdit", "Read", "Task",
               "WebFetch", "WebSearch", "Write")


def _cannot_fire(rule) -> str:
    """Why this rule's own filters can never match a call, or ""."""
    tools = set(rule.tools)
    if rule.commands and "Bash" not in tools:
        return "`match_commands` is read only on Bash calls, and the rule is not scoped to Bash"
    if rule.paths and tools == {"Bash"}:
        return "`match_paths` needs a path, and a Bash call has none; scope it to Write to catch shell writes"
    for t in rule.tools:
        if t not in KNOWN_TOOLS and not t.startswith("mcp__"):
            near = difflib.get_close_matches(t, KNOWN_TOOLS, n=1, cutoff=0.6)
            if near:
                return f"`match_tools` names {t!r}; tool names are exact, did you mean {near[0]!r}?"
    return ""


_SCRATCH_SESSION = "instinct-test-case"
_MATCHED = ("full", "brief", "truncated", "muted", "budget", "shadow")


def _case_through_engine(s, rule, tool, ti, before):
    """("fires"|"silent", matched key) for one `tests` case, run the way the hook runs a call: the
    `before` calls in order, then the case's own call, in a fresh scratch session on a throwaway
    state dir, with only this rule loaded. Only the own call is judged."""
    import copy
    with tempfile.TemporaryDirectory(prefix="instinct-test-") as td:
        cs = copy.copy(s)
        cs.state_dir = Path(td)
        only = {rule.file}
        for b_tool, b_ti in before:
            run(b_tool, b_ti, _SCRATCH_SESSION, settings=cs, only=only)
        res = run(tool, ti, _SCRATCH_SESSION, settings=cs, only=only)
    rows = [r for r in res.rows if r.get("rule") == rule.file and r.get("delivery") in _MATCHED]
    return ("fires", rows[0].get("matched")) if rows else ("silent", None)


def _hist(x) -> str:
    n = x.get("before", 0)
    return f" (after {n} earlier call{'s' if n != 1 else ''})" if n else ""


def cmd_test(a) -> int:
    """Run the `tests` cases written in each rule file against the real matcher. Each case
    tests only its own rule, through the same path the hook takes (a Bash write is matched
    as the write the hook sees)."""
    from .match import match
    s = _settings(a)
    rs = load_rules(s.rules_dir)
    if not rs.dir_exists or rs.dir_problem:
        why = rs.dir_problem or "does not exist"
        print(f"test: NOT EVALUABLE — rules dir {s.rules_dir} {why}")
        return NOT_EVALUABLE
    pool = rs.fire + rs.shadow
    if a.names:
        want = {n if n.endswith(".yaml") else n + ".yaml" for n in a.names}
        missing = sorted(want - {r.file for r in pool})
        if missing:
            print(f"test: no loaded rule named {', '.join(missing)} (see `doctor`)")
            return USAGE
        pool = [r for r in pool if r.file in want]
    results, untested = [], [r for r in pool if not r.tests]
    for r in pool:
        for i, (tool, ti, exp, before) in enumerate(r.tests, 1):
            if before or r.after is not None:
                # history, or a rule whose `after` the bare matcher would ignore: the engine path
                got, matched = _case_through_engine(s, r, tool, ti, before)
            else:
                hits = match([r], tool, ti)
                got = "fires" if hits else "silent"
                ev = hits[0][1] if hits else None
                matched = (getattr(ev, "matched_pattern", None) or getattr(ev, "matched_command", None)
                           or getattr(ev, "matched_path", None)) if ev else None
            results.append({"rule": r.file, "case": i, "tool": tool, "expect": exp, "got": got,
                            "pass": got == exp, "before": len(before), "matched": matched})
    passed = sum(1 for x in results if x["pass"])
    tested = len(pool) - len(untested)
    if a.json:
        print(json.dumps({"rules": len(pool), "tested": tested, "untested": [r.file for r in untested],
                          "cases": len(results), "passed": passed, "failed": len(results) - passed,
                          "rejected": [r.file for r in rs.rejected], "results": results}, indent=2))
    else:
        print(f"test: {tested} rule(s) with tests: {len(results)} case(s), {passed} pass, "
              f"{len(results) - passed} fail; {len(untested)} rule(s) without tests")
        for x in results:
            if x["pass"]:
                print(f"  PASS {x['rule'][:-5]} #{x['case']} {x['tool']}: {x['got']}{_hist(x)}")
            else:
                why = f" (matched {x['matched']!r})" if x["matched"] else ""
                print(f"  FAIL {x['rule'][:-5]} #{x['case']} {x['tool']}: expected {x['expect']}, "
                      f"was {x['got']}{why}{_hist(x)}")
        if untested:
            print(f"{len(untested)} rule(s) without tests (not evaluable): "
                  + ", ".join(r.file[:-5] for r in untested))
        if rs.rejected:
            print(f"{len(rs.rejected)} rejected rule(s) not tested (see `doctor`): "
                  + ", ".join(r.file for r in rs.rejected))
    if not results:
        if not a.json:
            print("test: NOT EVALUABLE — no rule carries a `tests` case, so nothing was checked")
        return NOT_EVALUABLE
    return OK if passed == len(results) and not rs.rejected else FINDINGS


def cmd_replay(a) -> int:
    """Replay a JSON-lines file of {session, tool_name, tool_input} rows through the real engine
    on a throwaway state dir; print what each call would have delivered."""
    from . import replay as rp
    s = _settings(a)
    rs = load_rules(s.rules_dir)
    problem = rules_problem(s.rules_dir, rs)
    if problem:
        print(f"replay: NOT EVALUABLE — {problem}")
        return NOT_EVALUABLE
    try:
        rep = rp.replay(Path(a.file), s)
    except rp.ReplayError as e:
        print(f"replay: NOT EVALUABLE — {e}")
        return NOT_EVALUABLE
    rep["rules_dir"] = str(s.rules_dir)
    rep["rules_loaded"] = len(rs.fire) + len(rs.shadow)
    rep["rules_rejected"] = len(rs.rejected)
    _print(rep, a.json, rp.render(rep))
    return OK


def cmd_doctor(a) -> int:
    s = _settings(a)
    rs = load_rules(s.rules_dir)
    if not rs.dir_exists:
        print(f"doctor: NOT EVALUABLE — rules dir {s.rules_dir.resolve()} does not exist; "
              f"nothing can fire. The hook reads $INSTINCT_RULES_DIR, else "
              f"$CLAUDE_PROJECT_DIR/.claude/instincts, else .claude/instincts under the "
              f"current directory: run from the project root or set CLAUDE_PROJECT_DIR / "
              f"INSTINCT_RULES_DIR")
        return NOT_EVALUABLE
    if rs.dir_problem:
        print(f"doctor: NOT EVALUABLE — rules dir {s.rules_dir} {rs.dir_problem}; "
              f"nothing was read, so nothing can fire")
        return NOT_EVALUABLE
    print(f"rules dir {s.rules_dir}: {len(rs.fire)} firing · {len(rs.shadow)} shadow "
          f"(candidate) · {rs.terminal} retired/rejected by status · {len(rs.rejected)} "
          f"REJECTED as unusable")
    for r in rs.rejected:
        print(f"  REJECTED {r.file}: {r.reason}")
    for r in rs.unread:
        print(f"  NOT READ {r.file}: {r.reason}")
    over = []
    cap = s.budget - min(WITHHELD_RESERVE, s.budget)
    if s.budget:
        for r in rs.fire:
            n = _cost(full_lines(r))
            if n > cap:
                over.append((r.file, n))
    for f, n in over:
        print(f"  OVER BUDGET {f}: its full text is {n} bytes as sent, more than the "
              f"{cap} a call can carry; it is delivered TRUNCATED")
    slow = _slow_rules(s)
    cap_ms = s.match_budget_ms / 4
    for f, n in sorted(slow.items()):
        print(f"  SLOW {f}: skipped {n} time(s) in the last {s.keep_days:g} day(s) of the log because its "
              f"matching exceeded the per-rule cap ({cap_ms:g} ms, a quarter of the {s.match_budget_ms} ms "
              f"call budget); simplify its pattern")
    parse_slow, unev = _unevaluated(s)
    if parse_slow:
        print(f"  NOT EVALUATED Bash-write parsing: ran out of time {parse_slow} time(s) in the last "
              f"{s.keep_days:g} day(s) of the log, so the writes in those Bash commands were not "
              f"evaluated; the call budget was not the cause (parsing is capped at a quarter of the "
              f"{s.match_budget_ms} ms budget, or what was left of it). Very long or deeply "
              f"nested commands do this; raise INSTINCT_MATCH_BUDGET_MS")
    for f, n in sorted(unev.items(), key=lambda kv: str(kv[0])):
        what = f or "Bash-write parsing"
        print(f"  NOT EVALUATED {what}: not evaluated {n} time(s) in the last {s.keep_days:g} day(s) of the "
              f"log because the {s.match_budget_ms} ms call budget was already spent; it was not itself found slow "
              f"(look for the SLOW rule above, or raise INSTINCT_MATCH_BUDGET_MS)")
    after_unev = _after_unevaluated(s)
    for f, n in sorted(after_unev.items(), key=lambda kv: str(kv[0])):
        print(f"  NOT EVALUATED {f}: its `after` precondition could not be checked {n} time(s) in the "
              f"last {s.keep_days:g} day(s) of the log (no session id to remember earlier calls: the "
              f"payload carried none), so the rule did not fire on those calls")
    print(f"state dir {s.state_dir} · repeat={'on' if s.repeat else 'off'} "
          f"rearm={s.rearm_min}m brief_cap={s.brief_cap} budget={s.budget} "
          f"shadow={'on' if s.shadow else 'off'} bash_writes={'on' if s.bash_writes else 'off'}")
    if not rs.fire and not rs.shadow:
        print("doctor: NOT EVALUABLE — 0 rules loaded; the hook will never say anything")
        return NOT_EVALUABLE
    dead = [(r.file, _cannot_fire(r)) for r in rs.fire + rs.shadow if _cannot_fire(r)]
    for f, why in dead:
        print(f"  CANNOT FIRE {f}: {why}")
    unread = _unread_inputs(s)
    if unread:
        print(f"  NOT EVALUATED hook input: {unread} call(s) in the last {s.keep_days:g} day(s) of the "
              f"log could not be read at all, so no rule saw them")
    state_bad = _state_problem(s)
    if state_bad:
        print(f"  STATE {state_bad}: rules with `after` cannot fire, and repeats are not shortened")
    registered, blocked = _hook_registration(s)
    if not registered:
        print("  NOT REGISTERED no settings file here holds the hook, so it will not run; "
              "run `instinct-delivery install`")
    elif blocked:
        print(f"  HOOK MAY NOT RUN {blocked}")
    log_bad = _log_problem(s)
    if log_bad:
        print(f"doctor: NOT EVALUABLE — {log_bad}, so skipped rules cannot be listed")
        return NOT_EVALUABLE
    return FINDINGS if (rs.rejected or rs.unread or over or slow or unev or parse_slow or after_unev
                        or dead or unread or state_bad or not registered or blocked) else OK


LEGACY_HOOK_COMMAND = "instinct-delivery hook"      # a pre-release form; PATH-dependent
# The registered command. Python puts the current directory first on sys.path
# for `-m` and `-c`, and Claude Code runs hooks in the project directory, so a
# project-root yaml.py or json.py would be imported by the hook. This drops
# every non-absolute sys.path entry (the cwd, however spelled) before the first
# import that is not already loaded. It names the installing interpreter by
# absolute path (no PATH lookup) and keeps user site-packages.
# Limit: only relative entries are dropped. An absolute PYTHONPATH (or a relative one
# that the shell has already made absolute) is still honoured, as is anything in the
# environment the hook runs with; whoever controls that environment (user or
# project settings `env`) can still shadow a module.
# Every -c code string any released version registered. `install` recognises its
# own command by EXACT equality with one of these, never by a substring: a
# foreign command that merely mentions this tool must not be taken for ours.
_HOOK_CODE_ROUND2 = ("import os,sys;sys.path[:]=[p for p in sys.path if os.path.isabs(p)];"
                     "from instinct_delivery.cli import main;sys.exit(main([\"hook\"]))")
HOOK_CODE = _HOOK_CODE_ROUND2 + ";#instinct-delivery-hook"
HOOK_CODES = (_HOOK_CODE_ROUND2, HOOK_CODE)     # append a new version here; never remove one
HOOK_COMMAND = f"{shlex.quote(sys.executable)} -c {shlex.quote(HOOK_CODE)}"


def _is_ours(command) -> bool:
    """Is this hook command exactly one of the forms this tool ever registered?
    The bare console script, an absolute path to it, `<python> -m instinct_delivery
    hook`, and `<python> -c <code>` where the code is one of HOOK_CODES verbatim.
    Anything else, however much it mentions this tool, is somebody else's."""
    if not isinstance(command, str):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) == 2 and argv[1] == "hook":
        return argv[0] == "instinct-delivery" or (
            os.path.isabs(argv[0]) and argv[0].endswith("/instinct-delivery"))
    if len(argv) == 4 and argv[1:] == ["-m", "instinct_delivery", "hook"]:
        return True
    return len(argv) == 3 and argv[1] == "-c" and argv[2] in HOOK_CODES


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _plan(pre):
    """Edit the PreToolUse list so that exactly one entry holds only our hook,
    without touching any entry that holds a hook that is not ours. Returns
    (entries_we_left_alone_but_removed_our_hook_from, foreign_hooks_in_them, notes)."""
    def ours_in(e):
        return [h for h in e["hooks"] if isinstance(h, dict) and _is_ours(h.get("command"))]
    ents = [e for e in pre if isinstance(e, dict) and isinstance(e.get("hooks"), list) and ours_in(e)]
    dedicated = [e for e in ents if len(ours_in(e)) == len(e["hooks"])]
    mixed = [e for e in ents if e not in dedicated]
    foreign_left = 0
    for e in mixed:
        e["hooks"] = [h for h in e["hooks"] if h not in ours_in(e)]
        foreign_left += len(e["hooks"])
    if len(dedicated) == 1:
        keep = dedicated[0]
    elif dedicated:
        keep = dedicated[0]
        keep["matcher"] = ".*"
        drop = {id(e) for e in dedicated[1:]}      # by identity: an equal entry is not the same entry
        pre[:] = [e for e in pre if id(e) not in drop]
    else:
        keep = {"matcher": ".*", "hooks": []}
        pre.append(keep)
    keep["hooks"] = [{"type": "command", "command": HOOK_COMMAND}] if not ours_in(keep) else [
        dict(ours_in(keep)[0], command=HOOK_COMMAND)]
    return len(mixed), foreign_left, len(dedicated)


def _hook_blocker(data, pre) -> str:
    """What in a settings file would stop a registered hook from running, or ""."""
    if data.get("disableAllHooks") is True:
        return "`disableAllHooks` is true"
    for e in pre:
        if _has_ours(e) and e.get("matcher", ".*") not in (".*", "*", ""):
            return f"its entry's matcher is {e['matcher']!r}, so it runs only for the tools that matches"
    return ""


def _warn_hook_may_not_run(data, pre, settings) -> None:
    why = _hook_blocker(data, pre)
    if why:
        print(f"install: WARNING: {settings}: {why}; left as you set it")


def _interpreter_problem(command) -> str:
    """Why the program a registered hook command starts cannot run, or "". Only the program
    is checked (an absolute path must be an executable file; a bare name must be on PATH);
    it cannot show that Claude Code ran the hook."""
    try:
        prog = shlex.split(command)[0]
    except (ValueError, IndexError):
        return ""
    if os.path.isabs(prog):
        if not os.path.exists(prog):
            return f"{prog} does not exist"
        if not (os.path.isfile(prog) and os.access(prog, os.X_OK)):
            return f"{prog} is not an executable file"
        return ""
    return "" if shutil.which(prog) else f"{prog} is not on PATH"


def _hook_registration(s) -> tuple:
    """(registered?, problem) from the settings files Claude Code reads for this project. This
    reads the files; it cannot show that Claude Code ran the hook."""
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    found, problem = False, ""
    for p in (root / ".claude" / "settings.json", root / ".claude" / "settings.local.json",
              Path.home() / ".claude" / "settings.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            pre = data.get("hooks", {}).get("PreToolUse", [])
        except (OSError, ValueError, RecursionError, AttributeError):
            continue
        if data.get("disableAllHooks") is True:
            problem = f"{p}: `disableAllHooks` is true"
        if isinstance(pre, list) and any(_has_ours(e) for e in pre):
            found = True
            problem = problem or (f"{p}: {_hook_blocker(data, pre)}" if _hook_blocker(data, pre) else "")
            for e in pre:
                for h in (e.get("hooks") or []) if _has_ours(e) else []:
                    bad = _interpreter_problem(h.get("command")) if _is_ours(h.get("command")) else ""
                    problem = problem or (f"{p}: the registered command cannot start: {bad}" if bad else "")
    return found, problem


def _has_ours(e) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("hooks"), list)
            and any(isinstance(h, dict) and _is_ours(h.get("command")) for h in e["hooks"]))


def _check_result(result_pre, before) -> str:
    """'' if ``result_pre`` is what install promises for ``before``: exactly one entry of
    ours, holding exactly the current command; every other entry unchanged, in order
    (an entry that mixed our hook with another tool's loses only ours). Else the problem."""
    mine = [e for e in result_pre if _has_ours(e)]
    if len(mine) != 1:
        return f"{len(mine)} entries of ours instead of 1"
    hs = mine[0]["hooks"]
    if len(hs) != 1 or not isinstance(hs[0], dict) or hs[0].get("command") != HOOK_COMMAND:
        return "the registered entry does not hold exactly the current command"
    expected = []
    for e in before:
        if not _has_ours(e):
            expected.append(e)
            continue
        rest = [h for h in e["hooks"] if not (isinstance(h, dict) and _is_ours(h.get("command")))]
        if rest:
            expected.append(dict(e, hooks=rest))
    if [e for e in result_pre if not _has_ours(e)] != expected:
        return "another tool's entries differ from what they were"
    return ""


def _write_atomic(target: Path, text: str) -> None:
    """Replace ``target`` (already resolved) with ``text``: a fresh O_EXCL temp file in
    the same directory (never a name an attacker could have pre-planted), the final
    mode set before any content is written, fsync, then rename over the target."""
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        um = os.umask(0)
        os.umask(um)
        mode = 0o666 & ~um
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            if fd != -1:
                os.close(fd)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_install(a) -> int:
    try:
        return _install(a)
    except RecursionError:
        # json/copy recurse per level: a settings file nested thousands deep. The write
        # (a temp file renamed over the target) is the last step that can precede this.
        print("install: the settings file is nested too deeply to process; refusing to rewrite it")
        return NOT_EVALUABLE


def _install(a) -> int:
    proj = Path(a.project).resolve()
    local = bool(getattr(a, "local", False))
    settings = proj / ".claude" / ("settings.local.json" if local else "settings.json")
    if settings.is_symlink() and not settings.exists():
        print(f"install: {settings} is a symlink to a file that does not exist; "
              f"refusing to create it through the link")
        return NOT_EVALUABLE
    target = settings.resolve() if settings.is_symlink() else settings    # write through a link
    data = {}
    if target.exists():
        if not target.is_file():
            print(f"install: {settings} resolves to {target}, which is not a regular file; "
                  f"nothing was changed")
            return NOT_EVALUABLE
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except OSError as e:
            print(f"install: could not read {settings} ({e}); it is not a readable file, "
                  f"so nothing was changed")
            return NOT_EVALUABLE
        except ValueError as e:
            print(f"install: {settings} is not valid JSON ({str(e)[:200]}); refusing to rewrite it")
            return NOT_EVALUABLE
        except RecursionError:
            print(f"install: {settings} is nested too deeply to read; nothing was changed")
            return NOT_EVALUABLE
        if not isinstance(data, dict):
            print(f"install: {settings} is not a JSON object; refusing to rewrite it")
            return NOT_EVALUABLE
    hooks = data.get("hooks", {})
    pre = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else None
    if not isinstance(hooks, dict) or not isinstance(pre, list):
        print(f"install: `hooks` / `hooks.PreToolUse` in {settings} has an unexpected shape "
              f"(expected an object holding a list); refusing to rewrite it")
        return NOT_EVALUABLE
    had_ours = any(isinstance(e, dict) and isinstance(e.get("hooks"), list)
                   and any(isinstance(h, dict) and _is_ours(h.get("command")) for h in e["hooks"])
                   for e in pre)
    before = copy.deepcopy(pre)
    data["hooks"] = hooks
    hooks["PreToolUse"] = pre
    n_mixed, n_foreign, n_ded = _plan(pre)
    _warn_hook_may_not_run(data, pre, settings)
    if had_ours and pre == before:
        print(f"install: already registered in {settings}")
        return OK
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    except ValueError:
        print(f"install: {settings} holds a non-finite number (NaN or Infinity), which JSON "
              f"cannot write back; refusing to rewrite it")
        return NOT_EVALUABLE
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        print(f"install: {settings} holds text that is not valid Unicode (a lone surrogate escape "
              f"such as \\ud800), which cannot be written back as UTF-8; refusing to rewrite it. "
              f"Nothing was changed")
        return NOT_EVALUABLE
    problem = _check_result(json.loads(text)["hooks"]["PreToolUse"], before)
    if problem:
        print(f"install: refusing to write {settings}: the result would be wrong ({problem}); "
              f"nothing was changed")
        return NOT_EVALUABLE
    try:
        if target.exists() and not stat.S_IMODE(target.stat().st_mode) & 0o222:
            print(f"install: {settings} is read-only (mode {stat.S_IMODE(target.stat().st_mode):04o}); "
                  f"refusing to replace it. Make it writable, or add the hook yourself")
            return NOT_EVALUABLE
        settings.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, text)
    except OSError as e:
        print(f"install: could not write {settings} ({e}); nothing was changed")
        return NOT_EVALUABLE
    try:
        after = json.loads(target.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        problem = _check_result(after, before)
    except (OSError, ValueError, KeyError, TypeError) as e:
        problem = f"it could not be read back ({type(e).__name__})"
    if problem:
        print(f"install: wrote {settings} but the file does not hold what was intended ({problem}); "
              f"check it by hand")
        return NOT_EVALUABLE
    # "upgraded" only when a dedicated entry of ours existed; moving our hook out of a mixed
    # entry registers it afresh in an entry of its own
    verb = "upgraded the registered" if n_ded else "registered the"
    if n_mixed:
        print(f"install: removed our hook from {n_mixed} mixed {'entry' if n_mixed == 1 else 'entries'}; "
              f"left {_plural(n_foreign, 'other hook')} untouched (their matchers are unchanged)")
    if n_ded > 1:
        print(f"install: merged {n_ded} entries of ours into one with matcher \".*\"")
    try:
        (proj / ".claude" / "instincts").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"install: settings.json was written, but could not create "
              f"{proj / '.claude' / 'instincts'} ({e}); create that directory yourself")
        return NOT_EVALUABLE
    print(f"install: {verb} PreToolUse hook in {settings}; rules go in "
          f"{proj / '.claude' / 'instincts'}")
    print(f"install: the hook command records this interpreter by absolute path: {sys.executable}")
    if local:
        print(f"install: {settings.name} is the per-machine settings file (Claude Code keeps it out of "
              f"version control), so the path stays on this machine")
    else:
        print(f"install: {settings.name} is the project's shared file: if it is committed, the path above "
              f"goes to everyone and fails where it does not exist. For a per-machine hook, "
              f"install with --local (writes settings.local.json)")
    return OK


def _malformed(r) -> str:
    out = ""
    n = r.get("malformed_rows", 0)
    if n:
        out += f"\n  {n} malformed row(s) excluded (see the log); totals do not include them"
    n = r.get("future_rows", 0)
    if n:
        out += (f"\n  {n} row(s) dated in the future excluded from the window (clock stepped back, or "
                f"a log copied from a host with a fast clock)")
    n = r.get("unstamped_rows", 0)
    if n:
        out += f"\n  {n} row(s) with no parsable timestamp excluded from the window"
    return out


def _not_evaluable(cause: str, a, report=None, text: str = "") -> int:
    """Say NOT EVALUABLE with its cause, as text or (with --json) as an object."""
    if getattr(a, "json", False):
        obj = {"status": "NOT_EVALUABLE", "cause": cause}
        if report is not None:
            obj.update({k: v for k, v in report.items() if k not in obj})   # the counts stay beside it
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(f"NOT EVALUABLE — {cause}" + (f"\n{text}" if text else ""))
    return NOT_EVALUABLE


def _run_measure(fn, a, render):
    """Exit 1 means FINDINGS and nothing else: a report that fails for any
    unexpected reason is NOT EVALUABLE (2), never read as a finding. Exit 2 always
    says NOT EVALUABLE and why, whichever report returned it and in either format."""
    try:
        out = fn()
        code, report = out if isinstance(out, tuple) else (OK, out)
        text = render(report) + _malformed(report)
        if code == NOT_EVALUABLE:
            return _not_evaluable(str(report.get("cause") or "the report could not be evaluated"),
                                  a, report, text)
        _print(report, a.json, text)
    except measure.NotEvaluable as e:
        return _not_evaluable(str(e), a)
    except Exception as e:  # noqa: BLE001 — never let a crash read as exit 1
        return _not_evaluable(f"the report failed ({type(e).__name__}: {e})", a)
    return code


def cmd_repeat_rate(a) -> int:
    kinds = tuple(a.kinds.split(",")) if a.kinds else measure.MATCH_KINDS
    return _run_measure(lambda: measure.repeat_rate(_log(a), kinds, a.days), a, lambda r: (
        f"repeat-rate: {r['repeat']} of {r['rows']} matched fire(s) ({r['repeat_pct']}%) were "
        f"the same rule again in the same session, over {r['sessions']} session(s); "
        f"kinds counted: {', '.join(r['kinds'])}"))


def cmd_cost(a) -> int:
    reviews = Path(a.reviews) if a.reviews else None
    def render(r):
        t = r["total"]
        rearm = (t["rearm_share"] if t["rearm_share"] is not None
                 else "n/a, no full deliveries in a session")
        loose = t.get("sessionless_full_bytes", 0)
        loose = (f", {loose} B of it from rows with no session id, not counted as first or re-arm"
                 if loose else "")
        irr = (t["irrelevant_share"] if t["irrelevant_share"] is not None
               else "n/a, no reviewed full deliveries")
        return (f"cost over {r['sessions']} session(s), last {r['days']:g} day(s): "
                f"full {t.get('full_bytes', 0)} B (first {t.get('first_full_bytes', 0)}, "
                f"re-arm {t.get('rearm_full_bytes', 0)}; re-arm share {rearm}{loose}), "
                f"brief {t.get('brief_bytes', 0)} B; withheld {t.get('budget', 0)}, "
                f"muted {t.get('muted', 0)}, shadow {t.get('shadow', 0)}, "
                f"slow {t.get('slow', 0)}, unevaluated {t.get('unevaluated', 0)}; "
                f"irrelevant share of reviewed full bytes {irr}")
    return _run_measure(lambda: measure.cost(_log(a), a.days, reviews), a, render)


def cmd_fire_rate(a) -> int:
    base = Path(a.baseline) if a.baseline else None
    def render(r):
        lines = [f"fire-rate: {len(r['over'])} of {r['rules_seen']} rule(s) delivered more than "
                 f"{r['byte_cap']} B in some session (last {r['days']:g} day(s))"]
        for rule, p in sorted(r["over"].items()):
            tag = "REGRESSED" if rule in r["regressed"] else "held"
            lines.append(f"  {tag:<9} {rule}: worst session {p['max_bytes']} B over "
                         f"{p['max_n']} fire(s) -> {p['remedy']}")
        for rule in r["cleared"]:
            lines.append(f"  cleared   {rule}")
        return "\n".join(lines)
    return _run_measure(lambda: measure.fire_rate(_log(a), a.days, a.byte_cap, base), a, render)


def cmd_cofire(a) -> int:
    def render(r):
        lines = [f"cofire: {len(r['pairs'])} pair(s) over Jaccard threshold with support, "
                 f"across {r['calls']} call(s) and {r['rules']} rule(s); "
                 f"{r['thin_pairs_suppressed']} thin pair(s) suppressed"]
        lines += [f"  {p['a']} + {p['b']}: {p['cofires']} co-fire(s), J={p['jaccard']}"
                  for p in r["pairs"]]
        return "\n".join(lines)
    return _run_measure(lambda: measure.cofire(_log(a), a.threshold, a.min_support), a, render)


def cmd_persistence_loss(a) -> int:
    return _run_measure(lambda: measure.persistence_loss(_log(a), Path(a.transcripts), a.days), a,
                        lambda r: (f"persistence-loss: {r['lost']} of {r['full_logged']} full "
                                   f"deliveries ({r['lost_pct']}%) are absent from the transcript, "
                                   f"over {r['joined']} of {r['sessions_with_full']} session(s) "
                                   f"that joined a transcript ({r['transcript_files']} transcript "
                                   f"file(s), subagent transcripts included); "
                                   f"{r['sessions_with_no_attachment_records']} session(s) had a "
                                   f"transcript with no attachment records and were NOT counted; "
                                   f"{r['persisted_previews']} hook output(s) reached the agent "
                                   f"only as a persisted-output preview"))


def cmd_follow_rate(a) -> int:
    s = _settings(a)

    def pct(x):
        return f"{round(100 * x)}%" if x is not None else "no rate"

    def render(r):
        lines = [f"follow-rate: did the agent's next calls do what the advice asked? (minimum "
                 f"{r['min_n']} joined deliveries for a rate)"]
        for d in r["rules"]:
            name = d["rule"][:-5]
            if d["followed"] is None:
                line = f"  {name}  {d['deliveries']} delivery(ies); NOT EVALUABLE: {d['why']}"
                if d["within"] is not None:
                    line += (f"; {d['no_transcript']} with no transcript, "
                             f"{d['no_later_call']} with no later call")
                lines.append(line)
                continue
            line = (f"  {name}  {d['followed']} of {d['joined']} joined delivery(ies) followed within "
                    f"{d['within']} call(s) ({pct(d['rate']) if d['rate'] is not None else 'no rate'})")
            if d["base_windows"]:
                line += (f"; base rate {d['base_hits']} of {d['base_windows']} window(s) "
                         f"({pct(d['base_rate'])})")
            skipped = d["no_transcript"] + d["no_later_call"]
            if skipped:
                line += (f"; {d['no_transcript']} with no transcript, {d['no_later_call']} with no "
                         f"later call, not counted")
            if d["why"]:
                line += f"; {d['why']}"
            lines.append(line)
        lines.append("not causal: without a holdout this counts what followed a delivery, not what "
                     "the delivery caused; the base rate is the same predicate over every window "
                     "of those sessions")
        return "\n".join(lines)
    def go():
        r = measure.follow_rate(_log(a), Path(a.transcripts), s.rules_dir, a.days, a.min_n,
                                s.bash_writes)
        return (NOT_EVALUABLE if r["cause"] else OK), r
    return _run_measure(go, a, render)


def cmd_breadth(a) -> int:
    s = _settings(a)
    def render(r):
        lines = [f"breadth: {len(r['rules'])} rule(s) replayed over {r['calls']} call(s) from "
                 f"the {r['transcripts']} newest transcript file(s), subagent transcripts included"]
        for d in r["rules"]:
            flag = "BROAD " if d["flag"] else "      "
            skipped = d.get("skipped", 0)
            note = (f"; {skipped} call(s) skipped, the {r['budget_ms']} ms per-call time budget ran "
                    f"out, so the rate is over {d['eligible'] - skipped} evaluated call(s)") if skipped else ""
            lines.append(f"  {flag}{d['rule']}: {d['fires']}/{d['eligible']} = {d['rate']}{note}")
        return "\n".join(lines)
    return _run_measure(lambda: measure.breadth(Path(a.transcripts), s.rules_dir, a.threshold,
                                                a.sessions, s.bash_writes,
                                                s.match_budget_ms), a, render)


def cmd_labels(a) -> int:
    store = Path(a.store)
    if a.labels_cmd == "add":
        try:
            row = measure.make_label(a.rule, a.tool, a.verdict, a.source, a.excerpt,
                                     a.session, a.reason, a.labeler)
        except ValueError as e:
            print(f"labels add: {e}", file=sys.stderr)
            return USAGE
        if not st.append_rows([row], store):
            print(f"labels add: could not write {store}; nothing was added", file=sys.stderr)
            return NOT_EVALUABLE
        print(f"labels: added {a.verdict} for {a.rule}")
        return OK
    def render(r):
        lines = [f"labels: {r['labels']} label(s), {r['bad_lines']} unreadable line(s); a "
                 f"precision needs n >= {r['min_n']}"]
        for rule, p in sorted(r["rules"].items(), key=lambda kv: str(kv[0])):
            prec = p["precision"] if p["precision"] is not None else f"NOT REPORTED (n={p['n']})"
            lines.append(f"  {rule}: {p['tp']}/{p['n']} TP, precision {prec}, "
                         f"{p['holdout']} hold-out admissible")
        return "\n".join(lines)
    return _run_measure(lambda: measure.label_stats(store), a, render)


def cmd_audit(a) -> int:
    if a.audit_cmd == "label":
        return _audit_label(a)
    if a.audit_cmd is not None or a.fire_id or a.verdict or a.store:
        print("audit: usage: audit [options] | audit label FIRE_ID --verdict V --store PATH",
              file=sys.stderr)
        return USAGE
    s = _settings(a)

    def go():
        rep = audit.sample(_log(a), Path(a.transcripts), a.days, a.n, a.until, a.rule,
                           rules_dir=s.rules_dir, bash_writes=s.bash_writes)
        if a.brief:
            rep["brief"] = audit.brief(rep, s.rules_dir)
        flags = [r["flag"] for r in rep["rules"]]
        if "BARE" in flags:
            code = FINDINGS
        elif "ok" not in flags:
            rep["cause"] = (f"no rule had {audit.MIN_N} or more sampled fires with matched text, so "
                            f"the BARE flag could not be evaluated for any")
            code = NOT_EVALUABLE
        else:
            code = OK
        return code, rep

    def render(r):
        text = audit.render(r)
        return text + ("\n\n" + r["brief"] if r.get("brief") else "")
    try:
        return _run_measure(go, a, render)
    except ValueError as e:                 # a bad --until
        print(f"audit: {e}", file=sys.stderr)
        return USAGE


def _audit_label(a) -> int:
    if not (a.fire_id and a.verdict and a.store) or "#" not in a.fire_id:
        print(f"audit label: needs FIRE_ID (<rule>#<hash>, as printed by `audit`), --verdict and --store; got "
              f"{a.fire_id!r}", file=sys.stderr)
        return USAGE
    try:
        st_ = _settings(a)
        hit = audit.find_fire(_log(a), Path(a.transcripts), a.fire_id, a.days, a.until,
                              rules_dir=st_.rules_dir, bash_writes=st_.bash_writes)
    except measure.NotEvaluable as e:
        return _not_evaluable(str(e), a)
    except ValueError as e:
        print(f"audit label: {e}", file=sys.stderr)
        return USAGE
    if hit is None:
        print(f"audit label: no logged fire has the id {a.fire_id!r} in the window ending "
              f"{audit.window(a.days, a.until)[1]} ({a.days:g} day(s)) of {_log(a)}; ids are the "
              f"ones `audit` prints (<rule>#<8 hex>), and --days/--until must reach the fire",
              file=sys.stderr)
        return USAGE
    rule, fire = hit
    row = measure.make_label(rule, fire["tool"], a.verdict, "transcript_sample",
                             fire["call"] or "(call not found)", fire["session_id"],
                             a.reason, a.labeler)
    if not st.append_rows([row], Path(a.store)):
        print(f"audit label: could not write {a.store}; nothing was added", file=sys.stderr)
        return NOT_EVALUABLE
    print(f"audit: labelled {a.fire_id} {a.verdict} for {rule}: {fire['ts']} {fire['tool']} "
          + printable(f"call: {fire['call'] or 'call not found'}"))
    return OK


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="instinct-delivery", description=_PACKAGE_DOC)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    def common(sp, log=False, json_out=True):
        sp.add_argument("--rules-dir", help="rules directory (default .claude/instincts)")
        sp.add_argument("--state-dir", help="where the activation log and session state live")
        if log:
            sp.add_argument("--log", help="activation log (default STATE_DIR/activations.jsonl)")
        if json_out:       # only on the reports, which honour it
            sp.add_argument("--json", action="store_true", help="print the result as JSON")
        return sp

    def command(name, text):
        return sub.add_parser(name, help=text, description=text)

    h = command("hook", "run as the PreToolUse hook: read one tool call on stdin, print the advice")
    h.add_argument("--raw", action="store_true", help="debug: plain text, not the envelope")
    c = common(command("check", "dry run: show what one tool call would deliver, without touching the log"), json_out=False)
    c.add_argument("tool", help="tool name, for example Edit or Bash")
    c.add_argument("tool_input", help="the tool input as JSON")
    c.add_argument("--session", help="session id, so repeat suppression and `after` apply")
    c.add_argument("--record", action="store_true",
                   help="write the log and state as a real call would (--state-dir applies only with this)")
    c.add_argument("--scratch-state", help="a state dir to use instead of a throwaway one")
    c.add_argument("-v", "--verbose", action="store_true", help="also print one line per rule row (delivery kind, what matched) on stderr")
    common(command("doctor", "list the rules that loaded, the ones rejected and why, and any over budget"), json_out=False)
    rp_ = command("replay", "run recorded tool calls through the real engine, in a throwaway state directory")
    rp_.add_argument("file", help="JSON lines of {session, tool_name, tool_input}")
    rp_.add_argument("--rules-dir", help="rules directory (default .claude/instincts)")
    rp_.add_argument("--json", action="store_true", help="print the result as JSON")
    t = common(command("test", "run the `tests` cases written in the rule files"))
    t.add_argument("names", nargs="*", help="rule names (default: every loaded rule)")
    i = command("install", "register the hook in .claude/settings.json (or settings.local.json with --local), leaving other entries alone")
    i.add_argument("--local", action="store_true",
                   help="write .claude/settings.local.json (per machine, not committed) instead of the shared settings.json")
    i.add_argument("--project", default=".", help="project directory to install into (default: the current one)")
    r = common(command("repeat-rate", "how often a match was the same rule again in the same session"), log=True)
    r.add_argument("--kinds", help=f"delivery kinds to count (default {','.join(measure.MATCH_KINDS)})")
    r.add_argument("--days", type=_days, help="only rows from the last N days (default: all)")
    co = common(command("cost", "bytes delivered per session, first delivery versus re-arm"), log=True)
    co.add_argument("--days", type=_days, default=7, help="window in days (default 7)")
    co.add_argument("--reviews", help="JSONL of {rule, date, verdict}")
    f = common(command("fire-rate", "per-rule worst-session bytes; exit 1 on a new offender"), log=True)
    f.add_argument("--days", type=_days, default=7, help="window in days (default 7)")
    f.add_argument("--byte-cap", type=_positive_int, default=10240, help="a rule is an offender when one session got more bytes than this from it (default 10240)")
    f.add_argument("--baseline", help='JSON {"rules": [...]} of known offenders to hold')
    cf = common(command("cofire", "rule pairs that fire on the same call"), log=True)
    cf.add_argument("--threshold", type=float, default=0.5, help="report a pair whose overlap (Jaccard) is above this (default 0.5)")
    cf.add_argument("--min-support", type=int, default=10, help="calls in the log, and fires of a pair, needed before it is reported (default 10)")
    pl = common(command("persistence-loss", "full deliveries in the log that are missing from the session transcripts"), log=True)
    pl.add_argument("--transcripts", required=True, help="directory of Claude Code session transcripts")
    pl.add_argument("--days", type=_days, default=7, help="window in days (default 7)")
    fr = common(command("follow-rate", "did the agent do what the advice asked, within the rule's `follow` window"), log=True)
    fr.add_argument("--transcripts", required=True, help="directory of Claude Code session transcripts")
    fr.add_argument("--days", type=_days, default=7, help="window in days (default 7)")
    fr.add_argument("--min-n", type=_positive_int, default=10,
                    help="joined deliveries a rule needs before a rate is printed")
    b = common(command("breadth", "match rate per rule when replayed over transcripts; finds rules that match too much"))
    b.add_argument("--transcripts", required=True, help="directory of Claude Code session transcripts")
    b.add_argument("--threshold", type=float, default=0.25, help="match rate at or above which a rule is BROAD (default 0.25)")
    b.add_argument("--sessions", type=_positive_int, default=5,
                   help="how many of the newest transcript files to replay")
    au = common(command("audit", "sample what each rule fired on, joined to the triggering call; `audit label` records a verdict"), log=True)
    au.add_argument("audit_cmd", nargs="?", choices=["label"])
    au.add_argument("fire_id", nargs="?", help="with `label`: the fire id, <rule>#<hash>, as printed by `audit`")
    au.add_argument("--transcripts", required=True, help="directory of Claude Code session transcripts")
    au.add_argument("--n", type=_positive_int, default=10, help="fires sampled per rule")
    au.add_argument("--days", type=_days, default=30, help="window in days (default 30)")
    au.add_argument("--until", help="window end date YYYY-MM-DD (default today, UTC); seeds the sample")
    au.add_argument("--rule", help="audit one rule only")
    au.add_argument("--brief", action="store_true",
                    help="for each BARE rule, print narrowing options (edits nothing)")
    au.add_argument("--verdict", choices=measure.VERDICTS, help="with `label`")
    au.add_argument("--store", help="with `label`: the labels file to append to")
    au.add_argument("--reason", default="", help="with `label`: why")
    au.add_argument("--labeler", default="", help="with `label`: who judged")
    lb = command("labels", "human true/false-positive judgements: `stats` summarises, `add` records one")
    lsub = lb.add_subparsers(dest="labels_cmd", required=True, parser_class=_Parser)
    ls = lsub.add_parser("stats", help="summarise a labels file", description="summarise a labels file")
    ls.add_argument("--store", required=True, help="the labels file")
    ls.add_argument("--json", action="store_true", help="print the result as JSON")
    la = lsub.add_parser("add", help="append one judgement to a labels file", description="append one judgement to a labels file")
    la.add_argument("--store", required=True, help="the labels file to append to")
    for k, what in (("rule", "the rule's name"), ("tool", "the tool of the call"),
                    ("excerpt", "the text the rule matched")):
        la.add_argument(f"--{k}", required=True, help=what)
    la.add_argument("--verdict", required=True, choices=measure.VERDICTS, help="the judgement")
    la.add_argument("--source", required=True, choices=measure.SOURCES, help="where the judgement came from")
    for k, what in (("session", "session id"), ("reason", "why"), ("labeler", "who judged")):
        la.add_argument(f"--{k}", default="", help=what)
    return p


COMMANDS = {"check": cmd_check, "doctor": cmd_doctor, "test": cmd_test, "replay": cmd_replay, "install": cmd_install,
            "repeat-rate": cmd_repeat_rate, "cost": cmd_cost, "fire-rate": cmd_fire_rate,
            "cofire": cmd_cofire, "persistence-loss": cmd_persistence_loss,
            "follow-rate": cmd_follow_rate,
            "breadth": cmd_breadth, "audit": cmd_audit, "labels": cmd_labels}


def _detach_broken_stdout() -> None:
    """The hook promises exit 0 once started. If stdout cannot be written (a closed
    pipe, a full device) the interpreter's own flush at exit would turn that into exit
    120, so a failed flush points fd 1 at /dev/null: the unwritable bytes are dropped
    (the hook already reported the failure and recorded nothing as delivered)."""
    try:
        sys.stdout.flush()
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, 1)
        os.close(fd)
    except OSError:
        pass


def _escape_console_errors() -> None:
    """A console that cannot encode a character (``PYTHONIOENCODING=ascii``, the C locale) or a
    path holding a non-UTF-8 byte must degrade the text, not crash the command after its work
    is done: unencodable characters print as ``\\u2014`` / ``\\udcff``. The hook writes bytes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    _escape_console_errors()
    a = build_parser().parse_args(argv)
    if a.cmd == "hook":
        code = hook_main(raw=a.raw)
        _detach_broken_stdout()
        return code
    try:
        return COMMANDS[a.cmd](a)
    except st.ConfigError as e:
        return _not_evaluable(str(e), a)
