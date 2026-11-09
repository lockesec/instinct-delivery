"""The delivery policy: what a matched rule puts in front of the agent.

For each rule matched on a call, one of:

- ``full``      the whole advice text, the first time in a session (and again
                after the re-arm window);
- ``brief``     one bounded line naming the rule, on a repeat inside the
                window: the agent already has the text;
- ``muted``     nothing, once a rule has sent more than ``BRIEF_CAP`` brief
                lines since its last full delivery;
- ``budget``    withheld: the call's payload budget is spent. The rule is NOT
                recorded as delivered, so its next fire is full;
- ``truncated`` a full delivery cut to fit the room left on this call, for a
                rule whose text alone is larger than the whole budget
                (otherwise it could never be delivered at all), or, with no
                session to remember a withheld rule, for any rule that would
                otherwise be withheld;
- ``shadow``    a candidate rule matched: logged as evidence, never emitted,
                and it never touches delivery state.

The budget counts EVERY byte this engine emits, newlines included, as those
bytes appear in the hook's JSON output (quotes, backslashes and newlines are
escaped there, so that count is never smaller than the raw text). When rules
are withheld, one closing line names them, and that line's room is reserved
inside the budget, so the total emitted never exceeds it. With a session, a
withheld rule goes first on its next fire.

Nothing is recorded until the text is emitted: ``run(commit=False)`` returns
the pending log rows and state, and the caller commits them only after its
write succeeded, so the log never claims a delivery that did not happen.

Every choice has an escape hatch that restores the unsuppressed behaviour
byte for byte: ``INSTINCT_REPEAT=full``, ``INSTINCT_PAYLOAD_BUDGET=0``,
``INSTINCT_BASH_EDITS=off``, ``INSTINCT_SHADOW=off``.

One rule can never silence another: each hit is handled in isolation, and a
rule that fails is reported on stderr and skipped.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import state as st
from .match import MAX_TOTAL_MS, PARSE_ALLOWANCE, Budget, is_parse_slow, match
from .rules import load_rules
from .bashwrites import GuessedPath
from .sequence import KEY as SEQ_KEY
from .sequence import NO_SESSION, Sequence, after_rules, is_after_reason

MARK = "INSTINCT"
FIRST_LINE_MAX = 80
WITHHELD_RESERVE = 240       # bytes kept for the closing "withheld" line
FIELD_MAX = 64               # characters of a rule's domain shown in a line
MIN_TRUNCATED_BODY = 32      # a cut smaller than this is not worth sending
DEFAULTS = {"rearm_min": 90, "brief_cap": 6, "budget": 8000, "match_budget_ms": 1000}
_OFF = {"0", "off", "false", "no", "disabled"}


def _env_flag(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in _OFF


def _env_int(name: str, default: int, *, off_is_zero: bool = False) -> int:
    """A non-negative int. Unparseable or negative falls back to the DEFAULT,
    never to 0: a typo must not silently switch a safety off."""
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    if off_is_zero and v in _OFF:
        return 0
    try:
        n = int(v)
    except ValueError:
        return default
    return n if n >= 0 else default


@dataclass
class Settings:
    rules_dir: Path
    state_dir: Path
    repeat: bool = True
    rearm_min: int = DEFAULTS["rearm_min"]
    brief_cap: int = DEFAULTS["brief_cap"]
    budget: int = DEFAULTS["budget"]
    shadow: bool = True
    bash_writes: bool = True
    keep_days: float = st.KEEP_DAYS_DEFAULT
    match_budget_ms: int = DEFAULTS["match_budget_ms"]   # 0 = unbounded matching

    @classmethod
    def from_env(cls, rules_dir=None, state_dir=None) -> "Settings":
        rd = rules_dir or os.environ.get("INSTINCT_RULES_DIR") or \
            str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".") / ".claude" / "instincts")
        return cls(
            rules_dir=st.expand(rd, "INSTINCT_RULES_DIR" if not rules_dir else "--rules-dir"),
            state_dir=Path(state_dir) if state_dir else st.state_dir(),
            repeat=os.environ.get("INSTINCT_REPEAT", "").strip().lower()
            not in ({"full"} | _OFF),
            rearm_min=_env_int("INSTINCT_REARM_MIN", DEFAULTS["rearm_min"]),
            brief_cap=_env_int("INSTINCT_BRIEF_CAP", DEFAULTS["brief_cap"]),
            budget=_env_int("INSTINCT_PAYLOAD_BUDGET", DEFAULTS["budget"], off_is_zero=True),
            shadow=_env_flag("INSTINCT_SHADOW"),
            bash_writes=_env_flag("INSTINCT_BASH_EDITS"),
            keep_days=_env_int("INSTINCT_STATE_KEEP_DAYS", st.KEEP_DAYS_DEFAULT),
            match_budget_ms=min(_env_int("INSTINCT_MATCH_BUDGET_MS", DEFAULTS["match_budget_ms"],
                                         off_is_zero=True), MAX_TOTAL_MS),
        )


@dataclass
class Result:
    lines: list = field(default_factory=list)    # model-facing text, one entry per line
    rows: list = field(default_factory=list)     # activation-log rows (written on commit)
    errors: list = field(default_factory=list)   # (rule file, message), also on stderr
    slow: list = field(default_factory=list)     # (rule file, reason) skipped by the match budget
    unevaluated: list = field(default_factory=list)   # (rule file or None, reason) never evaluated
    after_unmet: list = field(default_factory=list)   # rule files whose own filters matched but no earlier call met `after`
    rules_problem: str = ""                      # the rules dir is missing/unreadable/not a directory, and why
    _log: Path | None = None
    _state: tuple | None = None                  # (delivery dir, session id, state) to save
    committed: bool = False
    _project: str | None = None                  # stamped on every row at commit
    _lock: object = None                         # the session's lock, held from the state read to commit

    def release(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()

    @property
    def text(self) -> str:
        return "".join(line + "\n" for line in self.lines)

    def commit(self) -> bool:
        """Write the log rows and the delivery state. Call it only once the
        text has reached its reader. A failure is reported, never raised."""
        if self.committed:
            return True
        self.committed = True
        try:
            return self._commit()
        finally:
            self.release()

    def _commit(self) -> bool:
        ok = True
        if self._project:
            for r in self.rows:
                r.setdefault("project", self._project)
        if self.rows and self._log is not None and not st.append_rows(self.rows, self._log):
            ok = False
            print(f"instinct-delivery: could not write every row to the activation log {self._log}",
                  file=sys.stderr)
        if self._state is not None:
            ddir, sid, state = self._state
            if not st.save_state(ddir, sid, state):
                ok = False
                print(f"instinct-delivery: could not save delivery state in {ddir}", file=sys.stderr)
        return ok


def _one_line(s, limit: int | None = None) -> str:
    s = " ".join(str(s).split())
    s = s.encode("utf-8", "replace").decode("utf-8")
    if limit is not None and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


def _split_action(action: str):
    """(first line, rest) of the stripped action, split at ONE boundary: whatever
    str.splitlines() treats as a line break (\\n, \\r, \\x0b, \\x0c, \\x1c-\\x1e,
    U+0085, U+2028, U+2029), so the head and the body never disagree about where the
    first line ends and no character falls between them."""
    action = action.strip()
    if not action:
        return "", ""
    first = action.splitlines()[0]
    return first, action[len(first):].strip()


def _first_line(action: str) -> str:
    first = _split_action(action)[0]
    return first if len(first) <= FIRST_LINE_MAX else first[:FIRST_LINE_MAX - 1] + "…"


def _nbytes(lines: list) -> int:
    """UTF-8 bytes of the lines as the agent reads them, newlines included."""
    return sum(len(x.encode("utf-8")) + 1 for x in lines)


def escaped_bytes(text: str) -> int:
    """Bytes `text` occupies inside a JSON string (UTF-8, not ASCII-escaped)."""
    return len(json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8"))


def _cost(lines: list) -> int:
    """What the budget counts: the lines as they appear in the hook's JSON
    output, each with its escaped newline (two bytes)."""
    return sum(escaped_bytes(x) + 2 for x in lines)


def _domain(rule) -> str:
    return _one_line(rule.domain, FIELD_MAX)


def _tag(rule) -> str:
    """'[domain] ' for the one-line forms; empty when the rule has no domain."""
    d = _domain(rule)
    return f"[{d}] " if d != "?" else ""


def full_lines(rule) -> list:
    parts = [rule.slug]
    if _domain(rule) != "?":
        parts.append(_domain(rule))
    if rule.confidence != "?":
        parts.append(f"confidence={rule.confidence}")
    # The head carries the first line, so the gist survives a preview that shows only the
    # start of the output; the body is the rest, so no line is said twice. A first line cut to
    # fit the head is given whole in the body.
    action = rule.action.strip()
    first, rest = _split_action(action)
    body = rest if len(first) <= FIRST_LINE_MAX else action
    marker = f"{MARK} ACTIVATED [{', '.join(parts)}]"
    return [f"⚠️ {MARK} {_tag(rule)}{rule.slug}: {_first_line(rule.action)}",
            f"{marker}: {body}" if body else marker]


def brief_line(rule, n: int) -> str:
    return (f"↻ {MARK} {_tag(rule)}{rule.slug} (x{n} this session, full text "
            f"delivered earlier): {_first_line(rule.action)}")


def _truncate_to(lines: list, limit: int, rule):
    """Cut the full block to fit `limit` budget bytes, saying so; None when
    even the head, the notice and a minimal body do not fit."""
    tail = f" …[TRUNCATED to fit the payload budget; full text in {rule.file}]"
    head = lines[0]
    room = limit - _cost([head]) - escaped_bytes(tail) - 2
    if room < MIN_TRUNCATED_BODY:
        return None
    body, used = [], 0
    for ch in lines[1]:
        c = escaped_bytes(ch)
        if used + c > room:
            break
        body.append(ch)
        used += c
    return [head, "".join(body) + tail]


def _withheld_line(names: list, limit: int) -> str:
    """The closing line, sized to `limit` budget bytes; empty if nothing fits."""
    line = f"⏸ {MARK} {len(names)} rule(s) withheld on this call (payload budget): "

    def render(shown):
        more = len(names) - len(shown)
        return line + ", ".join(shown) + (f", +{more} more" if more else "")
    shown: list = []
    for n in names:
        # the "+N more" suffix is part of the line: size it before adding a name
        if _cost([render(shown + [n])]) > limit:
            break
        shown.append(n)
    for candidate in (render(shown), f"⏸ {MARK} {len(names)} rule(s) withheld (payload budget)"):
        if _cost([candidate]) <= limit:
            return candidate
    return ""


def _count(v) -> int:
    """A state counter. Anything but a non-negative whole number reads as 0,
    so damaged state fails toward delivering more, never toward silence."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return max(v, 0)
    if isinstance(v, float) and math.isfinite(v) and v >= 0:
        return int(v)
    return 0


SESSION_ROW_MAX = 128        # longest session id kept whole in a log row


def _encodable(v):
    """v with any lone surrogate spelled out (``\\ud800``): such a string cannot be
    written to the JSON-lines log, and its row would be dropped after the delivery."""
    if isinstance(v, str) and _lone_surrogate(v):
        return v.encode("utf-8", "backslashreplace").decode("utf-8")
    return v


def _clean_row(r: dict) -> dict:
    """The row with every string made encodable and an over-long session id cut to
    SESSION_ROW_MAX characters, ending in a digest of the whole id (so two long ids that
    differ late still differ in the log)."""
    out = {k: _encodable(v) for k, v in r.items()}
    sid = r.get("session_id")
    if isinstance(sid, str) and len(sid) > SESSION_ROW_MAX:
        digest = hashlib.sha256(sid.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        out["session_id"] = _encodable(sid)[:SESSION_ROW_MAX - 17] + "~" + digest
    return out


def rules_problem(rules_dir: Path, rs) -> str:
    """Why nothing can fire because of the rules PATH itself, or "" when it is readable."""
    if not rs.dir_exists:
        return f"rules dir {rules_dir.resolve()} does not exist; nothing can fire"
    if rs.dir_problem:
        return f"rules dir {rules_dir} {rs.dir_problem}; nothing was read, so nothing can fire"
    return ""


def _row(rule, ev, tool, session_id, delivery, nbytes, ts) -> dict:
    # a rule with no domain / confidence reads "?" in its own text; the log says null, not a string
    r = {"ts": ts, "rule": rule.file, "domain": None if rule.domain == "?" else rule.domain,
         "confidence": None if rule.confidence == "?" else rule.confidence, "tool": tool, "session_id": session_id or None,
         "file_path": None if isinstance(ev.path, GuessedPath) else ev.path or None, "matched": ev.token, "delivery": delivery,
         "bytes": nbytes}
    if ev.via:
        r["via"] = ev.via
    if delivery == "shadow":
        r["shadow"] = True
    return _clean_row(r)


def _notice_row(tool, session_id, nbytes, ts) -> dict:
    return _clean_row({"ts": ts, "rule": None, "domain": None, "confidence": None, "tool": tool,
            "session_id": session_id or None, "file_path": None, "matched": None,
            "delivery": "notice", "bytes": nbytes})


def slow_message(rule_file: str, why: str) -> str:
    return (f"instinct-delivery: rule {rule_file} skipped for this call ({why}: matching "
            f"exceeded the match time budget)")


def unevaluated_message(rule_file, why: str) -> str:
    if rule_file and is_after_reason(why):
        return (f"instinct-delivery: rule {rule_file} not evaluated for this call ({why}; the rule's "
                f"own filters matched, so it would have been checked against earlier calls)")
    if rule_file is None and why == PARSE_ALLOWANCE:
        return ("instinct-delivery: Bash-write parsing spent its scan allowance on this command "
                "(thousands of unclosed substitutions); the writes found were matched, but the "
                "list may be incomplete")
    if rule_file is None and is_parse_slow(why):
        return ("instinct-delivery: Bash-write parsing ran out of time for this call (its own "
                "cap: a quarter of the match time budget, or what was left of it); the writes "
                "in this command were not evaluated")
    what = f"rule {rule_file}" if rule_file else "Bash-write parsing"
    return (f"instinct-delivery: {what} not evaluated for this call ({why}: the match time "
            f"budget was spent; the rule itself was not found slow)")


def _unevaluated_row(rule_file, tool, session_id, ts, why) -> dict:
    row = _slow_row(rule_file, tool, session_id, ts, why)
    row["delivery"] = "unevaluated"
    return row


def _slow_row(rule_file, tool, session_id, ts, why) -> dict:
    return _clean_row({"ts": ts, "rule": rule_file, "domain": None, "confidence": None, "tool": tool,
            "session_id": session_id if isinstance(session_id, str) and session_id else None,
            "file_path": None, "matched": None, "delivery": "slow", "bytes": 0, "reason": why})


def _lone_surrogate(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


INPUT_UNREAD = "hook input could not be read"
NOTICE_KEY = "__rules_over_caps__"


def is_input_reason(why) -> bool:
    return isinstance(why, str) and why.startswith(INPUT_UNREAD)


def record_unread_input(why: str, settings: Settings | None, stderr) -> None:
    """The hook could not read a call: no rule saw it. Say so on stderr and in the log."""
    s = settings or Settings.from_env()
    reason = f"{INPUT_UNREAD}: {why}"
    print(f"instinct-delivery: NOT EVALUATED — {reason}; no rule saw this call", file=stderr)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")
    row = _unevaluated_row(None, None, None, now, reason)
    row["project"] = st.project_id(s.rules_dir)
    st.append_rows([row], st.log_path(s.state_dir))


def _over_caps_line(n: int) -> str:
    return (f"⏸ {MARK} {n} rule file(s) did not load: the rules directory is past its size or file "
            f"cap. Some protection is off; run `instinct-delivery doctor`.")


def run(tool_name, tool_input, session_id=None, settings: Settings | None = None,
        now: _dt.datetime | None = None, commit: bool = True, only=None, agent_id=None) -> Result:
    """Match one call and decide what to deliver. With ``commit=False`` the
    log rows and state are left pending on the Result: call ``commit()`` once
    the text has been written. ``only`` (a set of rule file names) narrows the
    loaded rules to those, for a caller that tests one rule through this path."""
    s = settings or Settings.from_env()
    if isinstance(session_id, str):
        # a lone surrogate cannot be encoded: its row would be dropped. Spell it out.
        session_id = session_id.encode("utf-8", "backslashreplace").decode("utf-8") \
            if _lone_surrogate(session_id) else session_id
    now = now or _dt.datetime.now(_dt.timezone.utc)
    res = Result(_log=st.log_path(s.state_dir), _project=st.project_id(s.rules_dir))
    ts = now.isoformat(timespec="microseconds")   # one stamp per call: rows of a call group exactly
    rs = load_rules(s.rules_dir)
    res.rules_problem = rules_problem(s.rules_dir, rs)
    tool = tool_name if isinstance(tool_name, str) else ""
    pool = rs.fire + (rs.shadow if s.shadow else [])
    if only is not None:
        pool = [r for r in pool if r.file in only]
    sid = session_id if isinstance(session_id, str) and session_id else None
    suppress = s.repeat and sid is not None
    afters = after_rules(pool)
    seq_live = bool(afters) and sid is not None      # order is remembered only for a session
    ddir = st.delivery_dir(s.state_dir)
    # state is read when delivery suppression needs it, or when an `after` rule needs the
    # session's counters (which it does on EVERY call: the counter counts every call)
    tell_caps = rs.over_caps > 0 and only is None
    need_state = suppress or seq_live or (tell_caps and sid)
    # held from this read to commit, so two hooks of one session do not overwrite each other
    res._lock = st.session_lock(ddir, sid, create=seq_live) if need_state else None
    state = st.load_state(ddir, sid) if need_state else {}
    if (suppress or seq_live) and not state:
        st.prune_state(ddir, s.keep_days)
    seq_changed = False
    seq = Sequence.from_state(state) if seq_live else None
    if seq is not None:
        for f in seq.dropped:
            print(f"instinct-delivery: damaged delivery state for {f} reset (its `after` "
                  f"precondition stamp was ahead of the call counter)", file=sys.stderr)
    with Budget(s.match_budget_ms) as budget:
        hits = match(pool, tool, tool_input, bash_writes=s.bash_writes, budget=budget)
        if afters:
            kept = []
            for rule, ev in hits:
                if rule.after is None:
                    kept.append((rule, ev))
                elif sid is None:
                    budget.unevaluated.append((rule.file, NO_SESSION))
                elif seq is not None and seq.met(rule):
                    kept.append((rule, ev))
                else:
                    res.after_unmet.append(rule.file)
            hits = kept
            if seq is not None:
                seq.advance(afters, tool, tool_input, s.bash_writes, budget)
                state[SEQ_KEY] = seq.to_state()
                seq_changed = True
    for rule_file, why in dict(budget.slow).items():
        res.slow.append((rule_file, why))
        res.rows.append(_slow_row(rule_file, tool, session_id, ts, why))
    for rule_file, why in dict.fromkeys(budget.unevaluated):
        res.unevaluated.append((rule_file, why))
        res.rows.append(_unevaluated_row(rule_file, tool, session_id, ts, why))
    # Said once a session (every call when there is no session to remember it): the agent is the
    # only reader at this moment, and these rules are off whatever this call matched.
    # It takes only room the rules left, and waits for a later call when there is none.
    def tell_over_caps(room: int) -> bool:
        if not tell_caps or state.get(NOTICE_KEY):
            return False
        line = [_over_caps_line(rs.over_caps)]
        if s.budget and _cost(line) > room:
            return False
        res.lines += line
        res.rows.append(_notice_row(tool, sid, _nbytes(line), ts))
        if sid:
            state[NOTICE_KEY] = {"said": 1}
        return bool(sid)

    if not hits:
        if tell_over_caps(s.budget):
            seq_changed = True
        if seq_changed:
            res._state = (ddir, sid, state)
        if commit:
            res.commit()
        return res
    fire = [(r, e) for r, e in hits if not r.shadow]
    shadow = [(r, e) for r, e in hits if r.shadow]

    if s.budget:
        # a rule withheld last time goes first; then strongest first; ties keep
        # file order, derived hits after direct ones
        akey = f"@{agent_id}" if isinstance(agent_id, str) and agent_id else ""
        fire.sort(key=lambda h: (not _count((state.get(h[0].file + akey) or {}).get("wh")),
                                 -h[0].rank, h[1].via is not None))
    changed = False
    budget = s.budget
    reserve = min(WITHHELD_RESERVE, budget)
    cap = budget - reserve           # room for rule text on one call
    spent = 0
    withheld: list = []

    agent = agent_id if isinstance(agent_id, str) and agent_id else None
    for rule, ev in fire:
        try:
            key = f"{rule.file}@{agent}" if agent else rule.file     # a subagent has its own context
            raw = state.get(key) or {}
            digest = hashlib.sha256(rule.action.encode("utf-8", "surrogatepass")).hexdigest()[:16]
            rec = {"n": _count(raw.get("n")), "nw": _count(raw.get("nw")), "h": digest}
            # an edited rule is new text: the old stamp does not say this text was delivered
            if st.parse_ts(raw.get("ts")) is not None and raw.get("h") in (None, digest):
                rec["ts"] = raw["ts"]
            if suppress and any(k in raw and _count(raw[k]) != raw[k] for k in ("n", "nw")):
                print(f"instinct-delivery: damaged delivery state for {rule.file} reset",
                      file=sys.stderr)
            prev = dict(rec)
            brief = False
            if suppress:
                rec["n"] += 1
                last = st.parse_ts(rec.get("ts"))
                if last is not None:
                    age = (now - last).total_seconds() / 60.0
                    # a stamp from the future (clock stepped back, state copied
                    # between hosts) re-arms: fail toward delivering more
                    brief = age >= 0 and (s.rearm_min == 0 or age < s.rearm_min)
                rec["nw"] = rec["nw"] + 1 if brief else 0
                if not brief:
                    rec["ts"] = now.isoformat()
            if brief and s.brief_cap and rec["nw"] > s.brief_cap:
                res.rows.append(_row(rule, ev, tool, sid, "muted", 0, ts))
                state[key] = rec
                changed = True
                continue
            lines = [brief_line(rule, rec.get("n", 1))] if brief else full_lines(rule)
            delivery = "brief" if brief else "full"
            if budget:
                room = cap - spent
                need = _cost(lines)
                if need > room:
                    cut = None
                    # cut to fit when waiting cannot help: the rule is bigger
                    # than any call can carry, or no session remembers it
                    if not brief and (need > cap or not suppress):
                        cut = _truncate_to(lines, room, rule)
                    if cut is None:
                        withheld.append(rule.slug)
                        res.rows.append(_row(rule, ev, tool, sid, "budget", 0, ts))
                        if suppress:
                            prev["n"] = rec.get("n", 1)
                            prev["wh"] = 1
                            state[key] = prev
                            changed = True
                        continue
                    lines, delivery = cut, "truncated"
            spent += _cost(lines)
            res.lines += lines
            res.rows.append(_row(rule, ev, tool, sid, delivery, _nbytes(lines), ts))
            if suppress:
                if delivery == "truncated":
                    # partial delivery is not full delivery: no stamp, so the
                    # next call is not a brief that claims the text was sent
                    rec.pop("ts", None)
                    rec["nw"] = 0
                state[key] = rec
                changed = True
        except Exception as e:  # noqa: BLE001 — one rule must never silence another
            res.errors.append((rule.file, f"{type(e).__name__}: {e}"))
            print(f"instinct-delivery: rule {rule.file} skipped ({type(e).__name__}: {e})",
                  file=sys.stderr)

    if withheld:
        line = _withheld_line(withheld, reserve)
        if line:
            res.lines.append(line)
            res.rows.append(_notice_row(tool, sid, _nbytes([line]), ts))
            spent += _cost([line])
    if tell_over_caps(budget - spent):
        changed = True
    for rule, ev in shadow:
        res.rows.append(_row(rule, ev, tool, sid, "shadow", 0, ts))
    if changed or seq_changed:
        res._state = (ddir, sid, state)
    if commit:
        res.commit()
    return res
