"""Rule files: one YAML document per rule, loaded from one directory.

A rule pairs a deterministic matcher with advice text. Only ``*.yaml`` files
directly inside the rules directory are read (subdirectories are never read,
so moving a file into ``archive/`` retires it). The file name is the rule's
identity everywhere: in the activation log, in the delivery state, and in
every report.

Loading never raises. A rule that cannot be used is REJECTED with a reason,
and ``doctor`` lists every rejection. It is never half-loaded: a rule that
would fire with no text, fire under a misspelled status, or match nothing
because its pattern does not compile is a rule the author thinks is working.
The same goes for a ``confidence`` that is not a finite number, and for a
``match_content`` pattern with a nested unbounded quantifier (``(a+)+``),
which can backtrack for minutes on one tool call. ``*.yml`` files are not
read; ``doctor`` names them.

Loading is bounded, because every tool call loads every rule: at most
``MAX_RULE_FILES`` files and ``MAX_TOTAL_BYTES`` bytes together (in file-name
order), each file at most ``MAX_RULE_BYTES``. A file past a cap is REJECTED
with the cap named, unparsed. A rules path that exists but cannot be read, or
is not a directory, is ``dir_problem`` with the real cause.
"""
from __future__ import annotations

import math
import os
import re
import reprlib
import stat
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .hidden import describe as describe_hidden
from .hidden import first_hidden

# Closed status vocabulary. A blank or absent status means ACTIVE.
FIRE = {"active", "validated"}
SHADOW = {"candidate"}
TERMINAL = {"retired", "retiring", "rejected", "archived", "expired"}
STATUSES = FIRE | SHADOW | TERMINAL

MAX_RULE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 512 * 1024  # all rule files together: parsing is ~1 s per 250 KB, per tool call
MAX_RULE_FILES = 500          # *.yaml files read from one directory
MAX_DEPTH = 32               # deepest nesting of lists/mappings a rule file may hold
MAX_REASON = 300             # longest cause text kept for a rejected file

_short = reprlib.Repr()
_short.maxstring = 40
_short.maxother = 40
_short.maxlevel = 2
_short.maxlist = _short.maxtuple = _short.maxdict = 4


def _r(value) -> str:
    """A bounded repr, safe to put in a message whatever the value is."""
    try:
        return _short.repr(value)
    except Exception:  # noqa: BLE001
        return f"<{type(value).__name__}>"

try:                                    # the parser `re` itself uses
    from re import _constants as _sc
    from re import _parser as _sp
except ImportError:                     # Python < 3.11
    import sre_constants as _sc         # type: ignore[no-redef]
    import sre_parse as _sp             # type: ignore[no-redef]

_REPEATS = {_sc.MAX_REPEAT, _sc.MIN_REPEAT}
_WIDE = 10       # an outer repeat allowing this many passes counts as unbounded


def _children(av):
    if isinstance(av, _sp.SubPattern):
        yield av
    elif isinstance(av, (list, tuple)):
        for x in av:
            yield from _children(x)


def _unbounded_repeats(p) -> list:
    """Every unbounded repeat anywhere inside parsed pattern p."""
    out = []
    for op, av in p:
        if op in _REPEATS:
            if av[1] == _sc.MAXREPEAT:
                out.append(av)
            out += _unbounded_repeats(av[2])
        else:
            for ch in _children(av):
                out += _unbounded_repeats(ch)
    return out


def _mandatory_literals(p) -> list:
    """Characters every match of p must contain at its top level."""
    out = []
    for op, av in p:
        if op is _sc.LITERAL:
            out.append(chr(av))
        elif op is _sc.SUBPATTERN:
            out += _mandatory_literals(av[-1])
    return out


def _category(cat, c: str) -> bool:
    name = str(cat).upper()
    neg = "NOT_" in name
    if "DIGIT" in name:
        hit = c.isdigit()
    elif "SPACE" in name:
        hit = c.isspace()
    elif "WORD" in name:
        hit = c.isalnum() or c == "_"
    elif "LINEBREAK" in name:
        hit = c == "\n"
    else:
        return True
    return hit != neg


def _item_can_match(p, c: str) -> bool:
    """Can a one-item pattern (the body of a repeat) match character c?
    Unknown shapes answer True: the check must err toward rejecting."""
    items = list(p)
    if len(items) != 1:
        return True
    op, av = items[0]
    cs = {c, c.lower(), c.upper()}                    # patterns compile IGNORECASE
    if op is _sc.LITERAL:
        return chr(av) in cs or chr(av).lower() in cs
    if op is _sc.NOT_LITERAL:
        return not (chr(av) in cs or chr(av).lower() in cs)
    if op is _sc.ANY:
        return c != "\n"
    if op is _sc.IN:
        neg, hit = False, False
        for iop, iav in av:
            if iop is _sc.NEGATE:
                neg = True
            elif iop is _sc.LITERAL:
                hit = hit or chr(iav) in cs or chr(iav).lower() in cs
            elif iop is _sc.RANGE:
                hit = hit or any(iav[0] <= ord(x) <= iav[1] for x in cs)
            elif iop is _sc.CATEGORY:
                hit = hit or _category(iav, c)
            else:
                return True
        return hit != neg
    return True


def backtracks(pattern: str) -> bool:
    """True when the pattern nests an unbounded quantifier inside a repeat
    (``(a+)+``, ``(\\w+\\s?)*``, ``(.*a){12}``) and the repeated group has no
    literal that the inner quantifiers cannot consume. Those are the shapes
    that backtrack exponentially (or to a high polynomial) on a near-miss.
    Conservative: a safe pattern of that shape is rejected too, and doctor
    says why. Overlapping alternation (``(a|a)*``) is NOT detected."""
    try:
        parsed = _sp.parse(pattern, re.IGNORECASE)
    except Exception:  # noqa: BLE001 — compile errors are reported elsewhere
        return False
    return _walk_backtracks(parsed)


def _walk_backtracks(p) -> bool:
    for op, av in p:
        if op in _REPEATS:
            lo, hi, body = av
            if hi == _sc.MAXREPEAT or hi >= _WIDE:
                inner = _unbounded_repeats(body)
                if inner:
                    anchors = _mandatory_literals(body)
                    if not any(all(not _item_can_match(r[2], c) for r in inner) for c in anchors):
                        return True
            if _walk_backtracks(body):
                return True
        else:
            for ch in _children(av):
                if _walk_backtracks(ch):
                    return True
    return False


def _confidence(value):
    """A finite number, or "?" when absent. A numeric string is read as its
    number. Anything else is an error: it would reach every log row."""
    if value is None:
        return "?"
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"`confidence` must be a number, not {_r(value)}") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"`confidence` must be a number, not {type(value).__name__} {_r(value)}")
    try:
        finite = math.isfinite(value)
    except OverflowError:       # an int too large to convert to float
        finite = False
    if not finite:
        raise ValueError("`confidence` must be a finite number, "
                         f"not {str(value)[:40]!r}{'...' if len(str(value)) > 40 else ''}")
    return value


class _DuplicateKey(yaml.MarkedYAMLError):
    pass


class _Refused(yaml.MarkedYAMLError):
    pass


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses aliases and anchors (a few hundred bytes of
    them expand to billions of items) and nesting deeper than MAX_DEPTH (the
    composer recurses, so a few thousand brackets raise RecursionError)."""
    _depth = 0

    def compose_node(self, parent, index):
        ev = self.peek_event()
        if isinstance(ev, yaml.events.AliasEvent):
            raise _Refused(problem="YAML aliases (`*name`) and anchors (`&name`) are not allowed in a rule file",
                           problem_mark=ev.start_mark)
        if getattr(ev, "anchor", None) is not None:
            raise _Refused(problem="YAML aliases (`*name`) and anchors (`&name`) are not allowed in a rule file",
                           problem_mark=ev.start_mark)
        if self._depth >= MAX_DEPTH:
            raise _Refused(problem=f"nested more than {MAX_DEPTH} levels deep",
                           problem_mark=ev.start_mark)
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    seen = set()
    for key_node, _v in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            dup = key in seen
        except TypeError:
            continue
        if dup:
            raise _DuplicateKey(problem=f"duplicate key {_r(key)}", problem_mark=key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_status(value) -> str:
    """Text before the first ``#``, stripped and lower-cased; blank = active."""
    s = "" if value is None else str(value)
    s = s.split("#", 1)[0].strip().lower()
    return s or "active"


def _encodable(text: str, key: str) -> str:
    """text, or ValueError: a lone surrogate (a `\\ud800` escape) cannot be written to
    the activation log or the hook's output, so the rule is refused like a bad `action`."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"`{key}` holds text that is not valid Unicode") from None
    return text


def _str_list(value, key: str) -> list:
    if value is None:
        return []
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"`{key}` must be a string or a list of strings")
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise ValueError(f"`{key}` holds a non-string item {_r(v)}")
        s = _encodable(str(v), key)
        if not s:
            raise ValueError(f"`{key}` holds an empty string, which would match everything")
        out.append(s)
    return out


@dataclass
class Rule:
    file: str                      # identity: the file name, with .yaml
    action: str
    status: str
    tools: list
    paths: list = field(default_factory=list)
    content: list = field(default_factory=list)          # source patterns
    content_re: list = field(default_factory=list)       # compiled, same order
    commands: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    exclude_names: list = field(default_factory=list)    # substrings of a path's last component
    confidence: object = "?"
    domain: str = "?"
    tests: list = field(default_factory=list)            # [(tool, input, "fires"|"silent")]
    follow: object = None                                 # (Rule, within) or None
    after: object = None                                  # (Rule, within) or None: the precondition

    @property
    def slug(self) -> str:
        return self.file[:-5] if self.file.endswith(".yaml") else self.file

    @property
    def shadow(self) -> bool:
        return self.status in SHADOW

    @property
    def rank(self) -> float:
        try:
            return float(self.confidence)
        except (TypeError, ValueError):
            return 0.0


@dataclass
class Rejected:
    file: str
    reason: str


@dataclass
class RuleSet:
    fire: list = field(default_factory=list)      # emitted when matched
    shadow: list = field(default_factory=list)    # logged when matched, never emitted
    rejected: list = field(default_factory=list)  # unusable, with the reason
    terminal: int = 0                              # present but retired/rejected/...
    unread: list = field(default_factory=list)    # files that look like rules but are never read
    dir_exists: bool = True
    dir_problem: str = ""                          # the directory exists but cannot be read, and why
    over_caps: int = 0                             # rule files the size and count caps kept out


def _hidden_in(doc, where: str = ""):
    """(field, index, char) of the first hidden character anywhere in the document, keys
    included, or None."""
    if isinstance(doc, str):
        h = first_hidden(doc)
        return (where, *h) if h else None
    if isinstance(doc, dict):
        for k, v in doc.items():
            key = str(k)
            h = first_hidden(key)
            if h:
                return (f"key {key!r}", *h)
            found = _hidden_in(v, key if not where else f"{where}.{key}")
            if found:
                return found
    if isinstance(doc, list):
        for v in doc:
            found = _hidden_in(v, where)
            if found:
                return found
    return None


EXPECT = ("fires", "silent")
TEST_KEYS = {"tool", "input", "expect", "before"}
MAX_BEFORE = 100            # calls of history one `tests` case may carry


def _tests(value) -> list:
    """The `tests` block: a list of {tool, input, expect[, before]}. `before` is the history: a list
    of {tool, input} calls run first, in order, in a fresh scratch session. Read only by `test`,
    but checked at load so a rule is loaded whole or not at all. Each case is a tuple
    (tool, input, expect, [(tool, input), ...])."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("`tests` must be a list of {tool, input, expect} cases")
    out = []
    for i, case in enumerate(value, 1):
        if not isinstance(case, dict):
            raise ValueError(f"`tests` case {i} is not a mapping")
        extra = set(map(str, case)) - TEST_KEYS
        if extra:
            raise ValueError(f"`tests` case {i} has unknown key(s) {', '.join(sorted(extra))} "
                             f"(use tool, input, expect, before)")
        tool, ti, exp = case.get("tool"), case.get("input"), case.get("expect")
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"`tests` case {i} needs a `tool` name")
        if not isinstance(ti, dict):
            raise ValueError(f"`tests` case {i} needs an `input` mapping (the tool input)")
        if exp not in EXPECT:
            raise ValueError(f"`tests` case {i}: `expect` must be fires or silent, not {_r(exp)}")
        out.append((tool, ti, exp, _before(case.get("before"), i)))
    return out


def _before(value, i: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"`tests` case {i}: `before` must be a list of {{tool, input}} calls")
    if len(value) > MAX_BEFORE:
        raise ValueError(f"`tests` case {i}: `before` holds {len(value)} calls, more than the {MAX_BEFORE} allowed")
    out = []
    for j, call in enumerate(value, 1):
        if not isinstance(call, dict):
            raise ValueError(f"`tests` case {i}: `before` call {j} is not a mapping")
        extra = set(map(str, call)) - {"tool", "input"}
        if extra:
            raise ValueError(f"`tests` case {i}: `before` call {j} has unknown key(s) "
                             f"{', '.join(sorted(extra))} (use tool, input)")
        tool, ti = call.get("tool"), call.get("input")
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"`tests` case {i}: `before` call {j} needs a `tool` name")
        if not isinstance(ti, dict):
            raise ValueError(f"`tests` case {i}: `before` call {j} needs an `input` mapping")
        out.append((tool, ti))
    return out


SUB_KEYS = {"tools", "paths", "content", "commands", "exclude", "within"}
FOLLOW_KEYS = SUB_KEYS                  # kept: the name `follow` always had
FOLLOW_WITHIN = (1, 50)
AFTER_WITHIN = (1, 200)


def _submatcher(value, name: str, key: str, bounds: tuple, default: int):
    """A nested matcher block (`follow`, `after`): the same keys and the same safety checks as a
    rule's own (built by build_rule on a synthetic document), plus `within`, a whole number of
    calls inside ``bounds``. Returns (Rule, within), or None when the block is absent."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"`{key}` must be a mapping (tools, paths, content, commands, exclude, within)")
    extra = set(map(str, value)) - SUB_KEYS
    if extra:
        raise ValueError(f"`{key}` has unknown key(s) {', '.join(sorted(extra))}")
    within = value.get("within", default)
    lo, hi = bounds
    if isinstance(within, bool) or not isinstance(within, int) or not lo <= within <= hi:
        raise ValueError(f"`{key}` `within` must be a whole number from {lo} to {hi}, not {_r(within)}")
    doc = {"trigger": key, "action": key, "match_tools": value.get("tools"),
           "match_paths": value.get("paths"), "match_content": value.get("content"),
           "match_commands": value.get("commands"), "match_exclude": value.get("exclude")}
    try:
        return build_rule(name, doc), within
    except ValueError as e:
        raise ValueError(f"`{key}`: {e}") from None


def _follow(value, name: str):
    """The `follow` block: what the agent's next calls should look like if it acted on the
    advice, and how many later calls it may take (default 5). Read only by `follow-rate`."""
    return _submatcher(value, name, "follow", FOLLOW_WITHIN, 5)


def _after(value, name: str):
    """The `after` block: the precondition, an earlier call of the same session that matched
    this, within the last `within` calls (default 20). Read by the hook, `breadth` and `audit`."""
    return _submatcher(value, name, "after", AFTER_WITHIN, 20)


RULE_KEYS = {"trigger", "action", "status", "match_tools", "match_paths", "match_content",
             "match_commands", "match_exclude", "match_exclude_names", "confidence", "domain",
             "tests", "follow", "after"}


def _name_problem(name: str) -> str:
    """Why a file name cannot be a rule's name, or "". The name is delivered to the model
    as the rule's slug, so it is held to the rule text's standard, and it must stay on one line."""
    hid = first_hidden(name)
    if hid:
        return f"the file name holds an invisible character ({describe_hidden(hid[1])})"
    if len(name.splitlines()) > 1 or any(c.isspace() and c != " " for c in name):
        return "the file name holds a line break or other non-space whitespace"
    return ""


def build_rule(name: str, doc) -> Rule:
    """A Rule from a parsed document, or ValueError naming what is wrong."""
    if not isinstance(doc, dict):
        raise ValueError("the document is not a mapping")
    extra = set(map(str, doc)) - RULE_KEYS
    if extra:
        raise ValueError(f"unknown key(s) {', '.join(sorted(extra))}: a misspelled key would "
                         "change what the rule does, so the rule is not loaded")
    hid = _hidden_in(doc)
    if hid:
        where, i, c = hid
        raise ValueError(f"`{where}` holds an invisible character ({describe_hidden(c)}) at offset {i}: "
                         "a model reads it but a reviewer cannot see it, so the rule is not loaded")
    if "trigger" not in doc:
        raise ValueError("no `trigger` (say when this rule applies)")
    action = doc.get("action")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("`action` must be non-empty text: it is what gets delivered")
    _encodable(action, "action")
    raw_status = doc.get("status")
    status = parse_status(raw_status)
    if status not in STATUSES:
        raise ValueError(f"unknown status {_r(raw_status)} (use one of {', '.join(sorted(STATUSES))})")
    tools = _str_list(doc.get("match_tools"), "match_tools")
    if not tools:
        raise ValueError("no `match_tools`: a rule scoped to no tool can never fire")
    content = _str_list(doc.get("match_content"), "match_content")
    compiled = []
    for pat in content:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")        # never reach the hook's stderr on every call
            try:
                compiled.append(re.compile(pat, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"`match_content` pattern {_r(pat)} does not compile "
                                 f"({str(e)[:100]})") from e
            except OverflowError:
                raise ValueError(f"`match_content` pattern {_r(pat)} has a repetition count that is "
                                 "too large") from None
            except RecursionError:
                raise ValueError(f"`match_content` pattern {_r(pat)} is nested too deeply "
                                 "to compile") from None
            try:
                broad = backtracks(pat)
            except RecursionError:
                raise ValueError(f"`match_content` pattern {_r(pat)} is nested too deeply "
                                 "to check for catastrophic backtracking") from None
        ambiguous = [w for w in caught if issubclass(w.category, FutureWarning)]
        if ambiguous:
            if "[[:" in pat and ":]" in pat:
                what = ("uses a POSIX character class (like [[:alpha:]]), which Python's `re` does not "
                        "support: it reads it as a different set. Use \\w, \\d, \\s or a range such "
                        "as [a-zA-Z]")
            else:
                what = (f"is ambiguous in Python's `re` ({str(ambiguous[0].message)[:80]}) and will change "
                        "meaning in a future release; escape the character or rewrite it")
            raise ValueError(f"`match_content` pattern {_r(pat)} {what}")
        if broad:
            raise ValueError(f"`match_content` pattern {_r(pat)} nests an unbounded quantifier "
                             "inside a repeat and can backtrack catastrophically; rewrite it "
                             "without the nesting (for example `(a+)+` -> `a+`)")
    confidence = _confidence(doc.get("confidence"))
    domain = doc.get("domain", "?")
    return Rule(file=name, action=action.rstrip("\n"), status=status, tools=tools,
                paths=_str_list(doc.get("match_paths"), "match_paths"),
                content=content, content_re=compiled,
                commands=_str_list(doc.get("match_commands"), "match_commands"),
                exclude=_str_list(doc.get("match_exclude"), "match_exclude"),
                exclude_names=_str_list(doc.get("match_exclude_names"), "match_exclude_names"),
                confidence=confidence,
                domain=_encodable(str(domain)[:200], "domain") if domain not in (None, "") else "?",
                tests=_tests(doc.get("tests")), follow=_follow(doc.get("follow"), name),
                after=_after(doc.get("after"), name))


def _cause(e: Exception) -> str:
    """First line of an exception's text, bounded; its type if it has none."""
    try:
        text = str(e).strip()
    except Exception:  # noqa: BLE001
        text = ""
    first = text.splitlines()[0] if text else type(e).__name__
    return first[:MAX_REASON]


def _kind_of(mode: int) -> str:
    for test, name in ((stat.S_ISREG, "regular file"), (stat.S_ISFIFO, "FIFO"),
                       (stat.S_ISSOCK, "socket"), (stat.S_ISCHR, "character device"),
                       (stat.S_ISBLK, "block device")):
        if test(mode):
            return name
    return "special file"


def _not_a_file(path: Path) -> str:
    """Why an entry named ``*.yaml`` is not a rule file, or "" when it is (or may be) one.
    The entry is never opened here, so a FIFO cannot hang the load."""
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return "a symbolic link to a path that does not exist (dangling)" if path.is_symlink() \
            else "vanished while the directory was being read"
    except OSError as e:
        if path.is_symlink():
            return f"a symbolic link that cannot be followed ({e.strerror or type(e).__name__}; a loop?)"
        return ""                      # permission and the like: left to the read, which names it
    if stat.S_ISREG(mode):
        return ""
    if stat.S_ISDIR(mode):
        return "is a directory, not a file; only regular files are read"
    return f"is a {_kind_of(mode)}, not a regular file; only regular files are read"


def _total_cap_reason() -> str:
    return (f"not loaded: the rule files together would pass the total size cap "
            f"({MAX_TOTAL_BYTES} bytes, counted in file-name order); parsing them all "
            f"on every tool call would take too long. Retire rules into `archive/` or shorten them")


def _lone_cr_problem(text: str) -> str:
    """Why a bare carriage return makes the file unsafe to load, or "". A bare CR is a YAML line
    break, so it never reaches a parsed value, but a terminal draws the text after it over the
    text before it: a reviewer would see a different file than the loader reads."""
    m = re.search(r"\r(?!\n)", text)
    if not m:
        return ""
    line = text.count("\n", 0, m.start()) + 1
    return (f"line {line} holds a carriage return not followed by a line feed: a terminal shows the "
            "text after it over the text before it, so a reviewer would not see what the loader "
            "reads. The rule is not loaded")


def _read_capped(path: Path) -> bytes:
    """The file's bytes, reading at most MAX_RULE_BYTES + 1 from the opened descriptor.
    Refuses anything that is not a regular file (checked on the descriptor, not the name)
    and any file that yields more than the cap, whatever its st_size claimed."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file; only regular files are read")
        chunks, got = [], 0
        while got <= MAX_RULE_BYTES:
            chunk = os.read(fd, MAX_RULE_BYTES + 1 - got)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
    finally:
        os.close(fd)
    if got > MAX_RULE_BYTES:
        raise ValueError(f"file is over {MAX_RULE_BYTES} bytes")
    return b"".join(chunks)


def load_rules(rules_dir: Path) -> RuleSet:
    """Every ``*.yaml`` directly in rules_dir, in sorted file-name order, up to
    MAX_RULE_FILES files and MAX_TOTAL_BYTES bytes together; the excess is
    REJECTED (named, never silently dropped) without being parsed."""
    rs = RuleSet()
    rules_dir = Path(rules_dir)
    try:
        mode = os.stat(rules_dir).st_mode
    except (FileNotFoundError, NotADirectoryError):
        rs.dir_exists = False
        return rs
    except OSError as e:
        rs.dir_problem = f"cannot be read: {e.strerror or type(e).__name__}"
        return rs
    if not stat.S_ISDIR(mode):
        rs.dir_problem = f"is not a directory (it is a {_kind_of(mode)})"
        return rs
    try:
        entries = sorted(rules_dir.iterdir())
    except OSError as e:
        rs.dir_problem = f"cannot be listed: {e.strerror or type(e).__name__}"
        return rs
    for path in entries:
        low = path.name.lower()
        if low.endswith((".yml", ".yaml")) and not path.name.endswith(".yaml"):
            rs.unread.append(Rejected(path.name, "only files ending in `.yaml` are read"))
    used, seen = 0, 0
    over_count = 0
    for path in (p for p in entries if p.name.endswith(".yaml")):
        name = path.name
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            rs.rejected.append(Rejected(name.encode("utf-8", "replace").decode(),
                                        "the file name is not valid Unicode"))
            continue
        bad = _name_problem(name)
        if bad:
            rs.rejected.append(Rejected(ascii(name)[1:-1], bad))
            continue
        why = _not_a_file(path)
        if why:
            rs.unread.append(Rejected(name, why))
            continue
        try:
            size = path.stat().st_size
        except OSError as e:
            rs.rejected.append(Rejected(name, "unreadable: " + _cause(e)))
            continue
        seen += 1
        if seen > MAX_RULE_FILES:
            over_count += 1
            continue
        if size > MAX_RULE_BYTES:
            rs.rejected.append(Rejected(name, "unreadable: " + f"file is over {MAX_RULE_BYTES} bytes"))
            continue
        if used + size > MAX_TOTAL_BYTES:
            rs.over_caps += 1
            rs.rejected.append(Rejected(name, _total_cap_reason()))
            continue
        try:
            data = _read_capped(path)
        except Exception as e:  # noqa: BLE001 — one bad file costs exactly itself
            rs.rejected.append(Rejected(name, "unreadable: " + _cause(e)))
            continue
        # st_size is only a hint (a /proc file or a file being appended to reports less than it
        # yields): the bytes actually read are what count toward both caps.
        if used + len(data) > MAX_TOTAL_BYTES:
            rs.over_caps += 1
            rs.rejected.append(Rejected(name, _total_cap_reason()))
            continue
        used += len(data)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            rs.rejected.append(Rejected(name, "unreadable: " + _cause(e)))
            continue
        cr_bad = _lone_cr_problem(text)
        if cr_bad:
            rs.rejected.append(Rejected(name, cr_bad))
            continue
        try:
            doc = yaml.load(text, Loader=_StrictLoader)  # noqa: S506
        except Exception as e:  # noqa: BLE001
            rs.rejected.append(Rejected(name, "unreadable: " + _cause(e)))
            continue
        try:
            rule = build_rule(name, doc)
        except ValueError as e:
            rs.rejected.append(Rejected(name, str(e)[:MAX_REASON]))
            continue
        except Exception as e:  # noqa: BLE001
            rs.rejected.append(Rejected(name, "could not be checked: " + _cause(e)))
            continue
        # a value is checked in build_rule (and named there); this catches what the parser
        # drops, such as a comment
        hid = first_hidden(text)
        if hid:
            rs.rejected.append(Rejected(name, (
                f"the file holds an invisible character ({describe_hidden(hid[1])}) at offset {hid[0]}: "
                "a reviewer cannot see it, so the rule is not loaded")))
            continue
        if rule.status in TERMINAL:
            rs.terminal += 1
        elif rule.shadow:
            rs.shadow.append(rule)
        else:
            rs.fire.append(rule)
    if over_count:
        rs.over_caps += over_count
        rs.rejected.append(Rejected(f"({over_count} more *.yaml file(s))", (
            f"not loaded: more than {MAX_RULE_FILES} rule files in one directory; only the first "
            f"{MAX_RULE_FILES} in file-name order are read. Retire rules into `archive/`")))
    return rs


def rule_named(rules_dir: Path, name: str) -> Optional[Rule]:
    rs = load_rules(rules_dir)
    for r in rs.fire + rs.shadow:
        if r.file == name or r.slug == name:
            return r
    return None
