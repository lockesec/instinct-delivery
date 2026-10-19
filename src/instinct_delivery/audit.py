"""audit: look at what a rule actually fired on.

``repeat-rate``, ``cost`` and ``labels`` measure delivery and precision; nothing helps a
person LOOK at the fires. ``audit`` draws a deterministic sample of each rule's fires,
joins each to the tool call that triggered it (from the session transcript), and reports
how many of the sampled fires matched a single bare word or path. A rule whose evidence
is mostly one ordinary word ("README", "verdict", a module path) is matching a word, not
the situation it is about. The person labels the sample (``audit label``); the labels
flow into ``labels stats``.

Absent input is NOT EVALUABLE, never a clean report; every share prints its denominator.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import random
import re
from collections import Counter, OrderedDict
from pathlib import Path

from . import state as st
from .bashwrites import write_targets
from .match import describe, match
from .measure import NotEvaluable, _calls, _rows, _safe_sid
from .rules import load_rules

SAMPLED_KINDS = ("full", "brief", "truncated", "shadow")   # a real match; not notice/slow/unevaluated
MIN_N = 5               # below this the BARE flag is NOT EVALUABLE
BARE_SHARE = 0.6
HEAD = 160
# a "bare token" is declared text with no whitespace and no regex syntax: one ordinary
# word, or a path. `diff --stat`, `git push --force` and `(?m)^\s*print\(` are not.
_ORDINARY = re.compile(r"[\w./:@%=,~+-]+\Z")


# `recover` and the join re-run a rule's own match keys on ONE transcript call. An `after`
# rule's precondition lives in earlier calls, which a single re-run does not see, so it is not
# re-evaluated: its absence is not a sign that the rule changed.
AFTER_NOTE = ("this rule has an `after` precondition; the re-run used its own match keys only "
              "(the precondition was not re-run on earlier calls)")


def slug(rule) -> str:
    rule = str(rule)
    return rule[:-5] if rule.endswith(".yaml") else rule


def fire_id(rule, row) -> str:
    """A stable, content-addressed id for one logged fire: ``<rule>#<8 hex>`` of a hash of the
    row's (ts, session, tool). The draw, the window and ``--n`` do not enter it, so an id printed
    by ``audit`` names the same fire whenever it is used. It holds no call content (the log has
    none)."""
    key = "|".join(str(row.get(k)) for k in ("ts", "session_id", "tool"))
    return f"{slug(rule)}#{hashlib.sha256(key.encode('utf-8', 'replace')).hexdigest()[:8]}"


# a plain word: letters and inner hyphens only (`registry`, `parity`, `README`). Measured on a
# 400-rule corpus's judged fires: rules firing mostly on a plain word were right 0.10 of the time,
# against 0.28 for rules firing on another bare token (an identifier such as
# `spec_from_file_location`, a path) and 0.33 for the rest. The word is the form to narrow first.
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*\Z")


def is_word(matched) -> bool:
    """A bare token that is an ordinary word, not an identifier or a path."""
    return is_bare(matched) and bool(_WORD.match(matched.strip()))


def is_bare(matched) -> bool:
    if not isinstance(matched, str) or not matched:
        return False
    # a regex such as `(?m)^\s*TODO` matches its padding too: the token is what is left of it
    return bool(_ORDINARY.match(matched.strip()))


def window(days: float, until: str | None, now=None):
    """(end, label): the end of the window and the date that seeds the sample."""
    if until:
        try:
            d = _dt.date.fromisoformat(until)
        except ValueError:
            raise ValueError(f"--until must be a date like 2026-03-11, not {until!r}") from None
        end = _dt.datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=_dt.timezone.utc)
        return end, d.isoformat()
    end = now or _dt.datetime.now(_dt.timezone.utc)
    return end, end.date().isoformat()


def _text_of(name: str, ti: dict) -> str:
    for k in ("command", "file_path", "pattern", "path", "url", "query", "prompt", "description"):
        v = ti.get(k)
        if isinstance(v, str) and v:
            extra = ti.get("new_string") or ti.get("content") or ""
            return f"{name}: {v} {extra if isinstance(extra, str) else ''}"
    return f"{name}: {ti!r}"


def head_of(name: str, ti: dict) -> str:
    return " ".join(_text_of(name, ti).split())[:HEAD]


# Calls of one assistant message are stamped within milliseconds of each other; the hook row
# lands after all of them. Calls of the same tool this close to the latest one (and at or before
# the row) are the candidates for "the call that fired".
MESSAGE_S = 2.0
NOT_IDENTIFIED = "call not identified (several calls of this tool in one message)"


def _candidates(cache: dict, transcripts: Path, sid, tool, ts) -> list:
    """The calls of ``tool`` in session ``sid`` that can have produced a hook row stamped ``ts``,
    latest first: stamped at or before ``ts`` and within MESSAGE_S of the latest such call (one
    assistant message). Empty when the session, the stamp or any such call is missing."""
    if not _safe_sid(sid):
        return []
    if sid not in cache:
        f = transcripts / f"{sid}.jsonl"
        try:
            cache[sid] = _calls(f) if f.is_file() else []
        except OSError:
            cache[sid] = []
    t = st.parse_ts(ts)
    if t is None:
        return []
    before = [c for c in cache[sid] if c[0] <= t and c[1] == tool]
    if not before:
        return []
    before.reverse()                        # latest first (the list is time-sorted, stable)
    top = before[0][0]
    return [c for c in before if (top - c[0]).total_seconds() <= MESSAGE_S]


def recover(rule, tool, ti, bash_writes: bool = True, logged=None):
    """(status, text): re-run the rule's matcher on the joined call, locally, to find the
    text its pattern matched. The hook never logs call text; this reads the transcript.
    status: "recovered" (text is the matched substring), "literal" (the rule matched on a
    declared path or command, which is itself the text), "changed" (the rule no longer
    matches this call), "not_recovered" (matched, but no substring could be located)."""
    try:
        hits = [ev for r, ev in match([rule], tool, ti, bash_writes) if r.file == rule.file]
    except Exception:  # noqa: BLE001 - a matcher failure is "could not recover", not a crash
        return "not_recovered", None
    if not hits:
        return "changed", None
    ev = hits[0]
    if logged in rule.content:           # judge the pattern that actually fired this row
        ev = type(ev)(path=ev.path, matched_path=ev.matched_path, matched_pattern=logged,
                      matched_command=ev.matched_command, via=ev.via)
    if not ev.matched_pattern:
        tok = ev.matched_path or ev.matched_command
        return ("literal", tok) if tok else ("not_recovered", None)
    texts = [describe(tool, ti).text]
    if bash_writes and tool == "Bash" and isinstance(ti, dict):
        try:
            texts += [c for _, c in write_targets(ti.get("command") or "")]
        except Exception:  # noqa: BLE001
            pass
    found_rx = False
    for src, rx in zip(rule.content, rule.content_re):
        if src != ev.matched_pattern:
            continue
        found_rx = True
        for t in texts:
            m = rx.search(t)
            if m and m.group(0):
                return "recovered", m.group(0)
    # the logged pattern is in the rule but does not match this call: the call or rule differ
    return ("changed", None) if found_rx and logged in rule.content else ("not_recovered", None)


def _describe(name, r, cache, transcripts, loaded, bash_writes) -> dict:
    """One fire joined to its call, with the text its rule matched when that can be recovered."""
    cands = _candidates(cache, transcripts, r.get("session_id"), r.get("tool"), r.get("ts"))
    joined = cands[0] if cands else None
    status, text = "not_recovered", None
    if cands and slug(name) in loaded:
        # the latest candidate the rule's own match keys MATCH is the call that fired
        for c in cands:
            status, text = recover(loaded[slug(name)], r.get("tool"), c[2], bash_writes,
                                   r.get("matched"))
            if status != "changed":
                joined = c
                break
        else:
            if len(cands) > 1:
                joined, status = None, "unidentified"      # parallel calls, none matches
    head = head_of(joined[1], joined[2]) if joined else None
    if status == "unidentified":
        head = NOT_IDENTIFIED
    return {"id": fire_id(name, r), "ts": r.get("ts"), "tool": r.get("tool"),
            "session_id": r.get("session_id"), "delivery": r.get("delivery"),
            "matched": r.get("matched"), "matched_text": text, "text_status": status,
            "call": head}


def find_fire(log: Path, transcripts: Path, fid: str, days: float = 30, until: str | None = None,
              now=None, rules_dir: Path | None = None, bash_writes: bool = True):
    """(rule slug, fire dict) of the logged fire whose content-addressed id is ``fid``, looked up
    in the window's log rows (not in any sample); None when no row has that id."""
    if not transcripts.is_dir():
        raise NotEvaluable(f"transcripts dir {transcripts} does not exist")
    end, _label = window(days, until, now)
    loaded = {}
    if rules_dir is not None:
        rs = load_rules(rules_dir)
        loaded = {r.slug: r for r in list(rs.fire) + list(rs.shadow)}
    for r in sorted((r for r in _rows(log, days, end)
                     if r.get("delivery") in SAMPLED_KINDS and r.get("rule")),
                    key=lambda r: (str(r.get("ts")), str(r.get("session_id")), str(r.get("rule")))):
        if fire_id(r["rule"], r) == fid:
            return slug(r["rule"]), _describe(r["rule"], r, {}, transcripts, loaded, bash_writes)
    return None


def sample(log: Path, transcripts: Path, days: float = 30, n: int = 10, until: str | None = None,
           rule: str | None = None, now=None, rules_dir: Path | None = None,
           bash_writes: bool = True) -> dict:
    if not transcripts.is_dir():
        raise NotEvaluable(f"transcripts dir {transcripts} does not exist")
    end, label = window(days, until, now)
    rows = _rows(log, days, end)
    by = OrderedDict()
    for r in sorted((r for r in rows if r.get("delivery") in SAMPLED_KINDS and r.get("rule")),
                    key=lambda r: (str(r.get("ts")), str(r.get("session_id")), str(r.get("rule")))):
        if rule and slug(r["rule"]) != slug(rule):
            continue
        by.setdefault(r["rule"], []).append(r)
    if not by:
        raise NotEvaluable(f"no sampleable fire (full, brief, truncated, shadow) in {log} in the "
                           f"last {days:g} day(s) to {label}" + (f" for rule {rule}" if rule else ""))
    loaded = {}
    if rules_dir is not None:
        rs = load_rules(rules_dir)
        loaded = {r.slug: r for r in list(rs.fire) + list(rs.shadow)}
    cache, out = {}, []
    for name in sorted(by):
        fires = by[name]
        rnd = random.Random(f"{slug(name)}|{label}")
        picked = sorted(rnd.sample(range(len(fires)), min(n, len(fires))))
        smp = [_describe(name, fires[i], cache, transcripts, loaded, bash_writes) for i in picked]
        # the judged text: the recovered substring when there is one, else the logged key
        for s in smp:
            s["judged"] = (s["matched_text"] if s["matched_text"]
                           else s["matched"] if s["text_status"] == "not_recovered" else None)
        changed = sum(s["text_status"] == "changed" for s in smp)
        unidentified = sum(s["text_status"] == "unidentified" for s in smp)
        unrecovered = sum(s["text_status"] == "not_recovered" for s in smp
                          if isinstance(s["matched"], str) and s["matched"])
        have = [s for s in smp if isinstance(s["judged"], str) and s["judged"]]
        bare = [s for s in have if is_bare(s["judged"])]
        words = [s for s in bare if is_word(s["judged"])]
        n_ = len(have)
        if n_ < MIN_N:
            flag = "NOT_EVALUABLE"
        else:
            flag = "BARE" if len(bare) / n_ >= BARE_SHARE else "ok"
        counts = Counter(s["judged"] for s in have)
        out.append({"rule": slug(name), "eligible": len(fires), "sampled": len(smp),
                    "n": n_, "no_matched_text": len(smp) - n_ - changed - unidentified, "changed": changed,
                    "unidentified": unidentified,
                    "text_not_recovered": unrecovered, "bare": len(bare), "words": len(words),
                    "distinct_matched": len(counts), "matched_counts": dict(counts.most_common()),
                    "after": bool(slug(name) in loaded and loaded[slug(name)].after is not None),
                    "flag": flag, "sample": smp})
    return {"window_end": label, "days": days, "n": n, "min_n": MIN_N, "bare_share": BARE_SHARE,
            "malformed_rows": rows.malformed, "future_rows": rows.future,
            "unstamped_rows": rows.unstamped, "rules": out}


def render(rep: dict) -> str:
    lines = [f"audit: up to {rep['n']} fire(s) per rule, window ending {rep['window_end']} "
             f"({rep['days']:g} day(s)); the same log and window give the same sample"]
    for r in rep["rules"]:
        lines.append(f"{r['rule']}: {r['sampled']} sampled of {r['eligible']} fire(s)")
        if r.get("after"):
            lines.append(f"  note: {AFTER_NOTE}")
        for s in r["sample"]:
            key = s["matched"] if s["matched"] else "(none logged)"
            lines.append(f"  {s['id']}  {s['ts']}  {s['tool']}  matched rule key: {key}")
            st_ = s["text_status"]
            if st_ in ("recovered", "literal"):
                shown = " ".join(s["matched_text"].split())[:80]
                lines.append(f"      matched text: {shown}")
            elif st_ == "unidentified":
                lines.append("      matched text: not recovered (no call of the message matches the rule)")
            elif st_ == "changed":
                lines.append("      matched text: the joined call no longer matches this rule "
                             "(the rule changed since)")
            else:
                lines.append("      matched text: not recovered (call or rule not available); "
                             "the logged key is judged instead")
            lines.append(f"      call: {s['call'] if s['call'] else 'call not found'}")
        lines.append(f"  what matched: {r['distinct_matched']} distinct text(s) over {r['n']} sampled "
                     f"fire(s): " + (", ".join(f"{t!r} x{c}" for t, c in r["matched_counts"].items())
                                     if r["n"] else "none"))
        if r["text_not_recovered"]:
            lines.append(f"  {r['text_not_recovered']} of {r['n']} counted fire(s): text not recovered, "
                         f"judged on the logged rule key")
        if r["changed"]:
            lines.append(f"  {r['changed']} sampled fire(s) no longer match the rule and are not "
                         f"in the counts above")
        if r["unidentified"]:
            lines.append(f"  {r['unidentified']} sampled fire(s) could not be tied to one call "
                         f"(parallel calls of one tool, none matching) and are not in the counts above")
        if r["no_matched_text"]:
            lines.append(f"  {r['no_matched_text']} sampled fire(s) logged no matched text and are "
                         f"not in the counts above")
        share = f"{r['bare']} of {r['n']}"
        if r["flag"] == "NOT_EVALUABLE":
            lines.append(f"  bare-token share: {share} sampled fire(s) matched a single bare word or "
                         f"path; the BARE flag is NOT EVALUABLE below n={rep['min_n']}")
        elif r["flag"] == "BARE":
            lines.append(f"  BARE rule: {share} sampled fire(s) matched a single bare word or path "
                         f"(flag at n >= {rep['min_n']} and share >= {rep['bare_share']:g})")
            if r.get("words"):
                lines.append(f"    {r['words']} of those are a plain word (not an identifier or a "
                             f"path): the form to narrow first")
        else:
            lines.append(f"  bare-token share: {share} sampled fire(s) matched a single bare word or "
                         f"path; not flagged")
    return "\n".join(lines)


def brief(rep: dict, rules_dir: Path) -> str:
    """Narrowing options for each BARE rule. Deterministic, no model, edits nothing."""
    rs = load_rules(rules_dir)
    files = {r.slug: r for r in list(rs.fire) + list(rs.shadow)}
    out = []
    for r in rep["rules"]:
        if r["flag"] != "BARE":
            continue
        out.append(f"{r['rule']}: BARE, {r['bare']} of {r['n']} sampled fire(s) matched a single bare "
                   f"word or path")
        rule = files.get(r["rule"])
        if rule is None:
            out.append(f"  rule file not found or not loaded in {rules_dir}; its keys are unknown")
        else:
            out.append("  current filters: " + "; ".join(
                f"{k}={v}" for k, v in (("match_tools", rule.tools), ("match_paths", rule.paths),
                                        ("match_content", rule.content),
                                        ("match_commands", rule.commands),
                                        ("match_exclude", rule.exclude)) if v) )
        bare = sorted(((t, c) for t, c in r["matched_counts"].items() if is_bare(t)),
                      key=lambda tc: (not is_word(tc[0]), -tc[1]))     # plain words first
        out.append("  bare tokens it fired on:")
        for t, c in bare:
            ctx = next((s["call"] for s in r["sample"] if s["judged"] == t and s["call"]),
                       "call not found")
            out.append(f"    {t!r} x{c}  e.g. {ctx}")
        out += ["  options (yours to choose; audit edits no rule file):",
                "    - add match_paths so the rule applies only where its situation lives",
                "    - add a match_exclude for the commonest wrong context shown above",
                "    - require a second match_content pattern, so one word alone no longer fires",
                "    - scope match_tools to the tools the situation is actually about"]
    return "\n".join(out)
