"""Deterministic matching of one tool call against the rules.

The input is exactly what a PreToolUse hook receives: a tool name and the
tool's input object. What is searched is DECLARED here, per field, rather
than guessed per call:

- paths:    file_path, notebook_path, path, and each file_path inside an
            ``edits`` list (so a notebook edit or a search root is a path);
- text:     content, new_string, old_string, new_source, pattern, prompt,
            query, url, description, command, and each new_string/old_string
            inside an ``edits`` list;
- command:  ``command``, for Bash only.

Any value of the wrong type is read as empty, never raised on: a malformed
payload must not take down the rules that do apply to it.

Evaluation per rule, in file-name order: the tool filter (exact name), then
the exclusions (a substring anywhere in command, paths or text silences the
rule before any positive filter), then paths (substring, case-sensitive),
content (regex, case-insensitive) and commands (substring, case-sensitive).
Kinds AND together; entries within one list OR together. A hit carries
EVIDENCE: which of the rule's own declared tokens matched, never the call's
text, so the log can say why a rule fired without recording what was typed.
"""
from __future__ import annotations

import posixpath
import signal
import threading
import time
from contextlib import contextmanager, nullcontext as _nullcontext
from dataclasses import dataclass
from typing import Optional

from .bashwrites import write_targets

PATH_FIELDS = ("file_path", "notebook_path", "path")
TEXT_FIELDS = ("content", "new_string", "old_string", "new_source", "pattern",
               "prompt", "query", "url", "description", "command")
EDIT_TEXT_FIELDS = ("new_string", "old_string")


@dataclass(frozen=True)
class Evidence:
    path: Optional[str] = None        # the call's path the hit was judged on
    matched_path: Optional[str] = None
    matched_pattern: Optional[str] = None
    matched_command: Optional[str] = None
    via: Optional[str] = None         # "bash-write" for a hit through a derived write

    @property
    def token(self) -> Optional[str]:
        """The declared token that fired: pattern, else path, else command."""
        return self.matched_pattern or self.matched_path or self.matched_command


@dataclass(frozen=True)
class Call:
    tool: str
    paths: tuple
    text: str
    command: str


def _s(v) -> str:
    return v if isinstance(v, str) else ""


MAX_OTHER_STRINGS = 10_000   # strings read from arguments outside the named fields


def _other_strings(ti: dict) -> list:
    """Strings in the arguments outside the named fields, however nested: a tool from an MCP
    server names its own arguments. A stack, not recursion, so depth cannot stop it."""
    known = set(PATH_FIELDS) | set(TEXT_FIELDS) | {"edits"}
    stack = [v for k, v in ti.items() if k not in known]
    out: list = []
    while stack and len(out) < MAX_OTHER_STRINGS:
        v = stack.pop()
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return out


def describe(tool_name, tool_input) -> Call:
    tool = _s(tool_name)
    ti = tool_input if isinstance(tool_input, dict) else {}
    paths = [_s(ti.get(k)) for k in PATH_FIELDS]
    texts = [_s(ti.get(k)) for k in TEXT_FIELDS if k != "command"]
    edits = ti.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                paths.append(_s(e.get("file_path")))
                texts += [_s(e.get(k)) for k in EDIT_TEXT_FIELDS]
    command = _s(ti.get("command")) if tool == "Bash" else ""
    texts.append(command)
    texts += _other_strings(ti)
    return Call(tool=tool, paths=tuple(p for p in paths if p),
                text="\n".join(t for t in texts if t), command=command)


class MatchTimeout(Exception):
    """Raised by the SIGALRM handler inside one rule's evaluation."""


MAX_TOTAL_MS = 3_600_000     # an absurd INSTINCT_MATCH_BUDGET_MS is clamped here (one hour)


# Logged reason when Bash-write parsing itself ran out of its time cap. Older logs
# carry "bash-write parsing exceeded the match time budget"; both start with this.
PARSE_SLOW = "bash-write parsing ran out of time"
PARSE_ALLOWANCE = "bash-write parsing ran out of its scan allowance"   # a hostile command spent it
PARSE_SLOW_PREFIX = "bash-write parsing"


def is_parse_slow(why) -> bool:
    return isinstance(why, str) and why.startswith(PARSE_SLOW_PREFIX)


class Budget:
    """A time budget for the matching of ONE call, so a pattern that backtracks
    for minutes cannot stall every tool call.

    Each rule is evaluated under an interval timer of at most ``total / 4``
    seconds (and never more than what is left of ``total``). A rule that runs
    out is SKIPPED for this call and named in ``slow``. Once the total is
    spent, the rules after that point are NOT EVALUATED for this call: they are
    named in ``unevaluated`` (reason ``budget spent``), not in ``slow``, because
    they were not themselves slow. The parsing of a Bash command for the files
    it writes runs under the same timer; if it runs out, the derived writes of
    that call are not evaluated and ``unevaluated`` says so.
    ``re`` polls signals while it matches, so the alarm interrupts a long match.

    The timer is armed only when it is safe: POSIX, the main thread, no other
    SIGALRM handler installed and no other real-time timer running. Otherwise
    ``armed`` is False and matching is unbounded exactly as before. Handler and
    timer are restored on exit. The handler raises only while a guarded
    evaluation is in flight, so an alarm that lands just after one ends is
    ignored instead of aborting the caller.
    """

    def __init__(self, total_ms: int = 0):
        self.total = min(max(total_ms, 0), MAX_TOTAL_MS) / 1000.0
        self.slow: list = []          # [(rule file, reason)]: ran too long
        self.unevaluated: list = []   # [(rule file or None, reason)]: never evaluated
        self.armed = False
        self._prev = None
        self._deadline = 0.0
        self._live = False            # a guarded evaluation is in flight

    def __enter__(self) -> "Budget":
        if not self.total or not hasattr(signal, "setitimer"):
            return self
        try:
            if (threading.current_thread() is not threading.main_thread()
                    or signal.getsignal(signal.SIGALRM) != signal.SIG_DFL
                    or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
                return self
            self._prev = signal.signal(signal.SIGALRM, self._fired)
            self.armed = True
            self._deadline = time.monotonic() + self.total
        except (ValueError, OSError, OverflowError):
            self.armed = False
        return self

    def __exit__(self, *exc) -> None:
        if self.armed:
            self._live = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._prev)
            self.armed = False

    def _fired(self, signum, frame):
        if self._live:
            raise MatchTimeout()

    @contextmanager
    def guard(self, name: Optional[str], what: str = "rule"):
        """Run one evaluation under the timer. On a timeout the exception is
        swallowed: a rule is recorded in ``slow`` and counts as not matched
        (``what != "rule"`` records an ``unevaluated`` entry instead)."""
        if not self.armed:
            yield
            return
        left = self._deadline - time.monotonic()
        if left <= 0:
            self.unevaluated.append((name, "budget spent"))
            raise _Skip()
        try:
            self._live = True
            signal.setitimer(signal.ITIMER_REAL, min(left, self.total / 4))
            try:
                yield
            finally:
                self._live = False
                signal.setitimer(signal.ITIMER_REAL, 0)
        except MatchTimeout:
            if what == "rule":
                self.slow.append((name, "too slow"))
            else:
                self.unevaluated.append((None, PARSE_SLOW if what == "bash-write parsing"
                                                 else f"{what} ran out of time"))
            raise _Skip() from None


class _Skip(Exception):
    pass


def _name_excluded(rule, path: str) -> bool:
    """Is this path's last component, after resolving `..`, one the rule excludes by name?"""
    name = posixpath.basename(posixpath.normpath(path))
    return any(x in name for x in rule.exclude_names)


def _match_rule(rule, call: Call, haystack: str):
    """Evidence when the rule matches the call, else None."""
    if call.tool not in rule.tools:
        return None
    if any(x in haystack for x in rule.exclude):
        return None
    paths = [cp for cp in call.paths if not _name_excluded(rule, cp)] if rule.exclude_names else call.paths
    if call.paths and not paths:
        return None
    ev = {"path": paths[0] if paths else None}
    if rule.paths:
        found = next(((p, cp) for p in rule.paths for cp in paths if p in cp), None)
        if found is None:
            return None
        ev["matched_path"], ev["path"] = found
    if rule.content:
        pat = next((src for src, rx in zip(rule.content, rule.content_re)
                    if rx.search(call.text)), None)
        if pat is None:
            return None
        ev["matched_pattern"] = pat
    if rule.commands:
        cmd = next((c for c in rule.commands if c in call.command), None)
        if cmd is None:
            return None
        ev["matched_command"] = cmd
    return Evidence(**ev)


def match_call(rules, call: Call, budget: Optional[Budget] = None) -> list:
    """[(rule, Evidence)] in rule order. With a Budget, a rule that runs out of
    time is skipped and recorded on the budget."""
    hits = []
    haystack = " ".join((call.command,) + call.paths + (call.text,))
    for rule in rules:
        try:
            if budget is None:
                ev = _match_rule(rule, call, haystack)
            else:
                with budget.guard(rule.file):
                    ev = _match_rule(rule, call, haystack)
        except (_Skip, MatchTimeout):
            continue
        if ev is not None:
            hits.append((rule, ev))
    return hits


def match(rules, tool_name, tool_input, bash_writes: bool = True,
          budget: Optional[Budget] = None) -> list:
    """Hits for one call. For Bash, each file the command visibly WRITES is
    also matched as a Write of that content (as an Edit, for a rule scoped to
    Edit and not Write); a rule already hit by the command
    itself is not added twice, and each rule gets at most one derived hit.
    A rule's exclusions are checked against the whole command too, so an
    exclusion found anywhere in it silences the derived hit as well."""
    call = describe(tool_name, tool_input)
    hits = match_call(rules, call, budget)
    if not (bash_writes and call.tool == "Bash" and call.command):
        return hits
    seen = {r.file for r, _ in hits}
    haystack = " ".join((call.command,) + call.paths + (call.text,))
    try:
        with (budget.guard(None, "bash-write parsing") if budget else _nullcontext()):
            targets = write_targets(call.command)
    except (_Skip, MatchTimeout):
        return hits
    if getattr(targets, "out_of_budget", False) and budget is not None:
        # a scan spent its allowance: the writes found are matched, but the list is not
        # complete, and that is said rather than left to read as "no more writes"
        budget.unevaluated.append((None, PARSE_ALLOWANCE))
    for path, content in targets:
        # A write is a Write; a rule scoped to Edit (and not Write) sees it as an Edit.
        # A shell redirect creates or overwrites, `sed -i` edits: users write rules about
        # file changes, not about which tool made them.
        for tool_as in ("Write", "Edit"):
            derived = Call(tool=tool_as, paths=(path,), text=content, command="")
            pool = rules if tool_as == "Write" else [r for r in rules if "Write" not in r.tools]
            for rule, ev in match_call(pool, derived, budget):
                if rule.file in seen or any(x in haystack for x in rule.exclude):
                    continue
                seen.add(rule.file)
                hits.append((rule, Evidence(path=ev.path, matched_path=ev.matched_path,
                                            matched_pattern=ev.matched_pattern,
                                            matched_command=ev.matched_command, via="bash-write")))
    return hits
