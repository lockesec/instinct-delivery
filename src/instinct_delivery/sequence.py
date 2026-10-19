"""Order across calls: the ``after`` precondition of a rule.

A rule with ``after`` fires on a call only when its own filters match it AND an
EARLIER call of the same session matched the ``after`` matcher, no more than
``within`` calls back. The hook remembers two kinds of number per session, and
nothing else:

- ``n``    how many calls the session has made (every call the hook was run for);
- ``last`` per after-rule, the call number of its most recent precondition match.

Call ``i`` (= ``n`` + 1 when it arrives) satisfies a rule when
``0 < i - last <= within``: strictly earlier, inside the window. A number that is
not a whole number reads as "no earlier match"; one in the future of the counter
(damaged or copied state) is dropped on load (and said on stderr), so the precondition
must be met again by a real call. A damaged file cannot make a rule fire. No call
text, path, pattern or tool name is ever stored: the activation log and this state
keep what they always kept (declared rule keys), and a state file holds only
counters.

The state lives in the session's existing delivery file under the key ``~seq`` (a
rule file name always ends ``.yaml``, so the key cannot collide with a rule).
"""
from __future__ import annotations

from .match import match

KEY = "~seq"
NO_SESSION = "after: no session id to remember earlier calls"


def is_after_reason(why) -> bool:
    return isinstance(why, str) and why.startswith("after:")


def _whole(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


class Sequence:
    """The per-session counters, in memory."""

    def __init__(self, n: int = 0, last: dict | None = None):
        self.n = n
        self.last = dict(last or {})
        self.dropped: list = []            # rule files whose stamp was damage and was dropped

    @classmethod
    def from_state(cls, state: dict) -> "Sequence":
        raw = state.get(KEY) if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return cls()
        n = _whole(raw.get("n"))
        if n is None:
            return cls()                       # a counter we cannot read: forget the lot
        last = raw.get("last")
        kept = {}
        if isinstance(last, dict):
            kept = {k: v for k, v in last.items() if isinstance(k, str) and _whole(v) is not None}
        # a stamp past the counter can only be damaged or copied state: left in, it would
        # satisfy the rule with no precondition once the counter caught up to it
        future = sorted(k for k, v in kept.items() if v > n)
        seq = cls(n, {k: v for k, v in kept.items() if v <= n})
        seq.dropped = future
        return seq

    def to_state(self) -> dict:
        return {"n": self.n, "last": dict(sorted(self.last.items()))}

    def met(self, rule) -> bool:
        """Did an earlier call, inside the rule's window, match its precondition? Asked
        BEFORE the current call is counted."""
        _pred, within = rule.after
        last = self.last.get(rule.file)
        return last is not None and 0 < (self.n + 1) - last <= within

    def advance(self, rules, tool, tool_input, bash_writes: bool = True, budget=None) -> None:
        """Count this call and note which after-rules' preconditions it matched."""
        i = self.n + 1
        for rule in rules:
            pred, _within = rule.after
            if match([pred], tool, tool_input, bash_writes=bash_writes, budget=budget):
                self.last[rule.file] = i
        self.n = i
        live = {r.file: r.after[1] for r in rules}
        # forget what can no longer matter: rules gone from the directory, matches out of window
        self.last = {f: v for f, v in self.last.items() if f in live and i - v <= live[f]}


def after_rules(pool) -> list:
    return [r for r in pool if r.after is not None]
