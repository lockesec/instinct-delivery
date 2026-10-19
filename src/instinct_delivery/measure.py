"""Measurements over the activation log (and, for two of them, transcripts).

Every report states its denominator. Absent or empty input is NOT EVALUABLE
(exit 2), never a report of zero. A log row whose fields have the wrong type
(a byte count that is not a whole number, an unknown delivery kind) is
excluded and COUNTED; every report carries ``malformed_rows``, and a ratchet
that found nothing while rows were excluded is NOT EVALUABLE rather than
clean. Exit convention: 0 clean / report, 1 findings, 2 not evaluable.

- repeat-rate      how much of what was matched was the same rule again in the
                   same session (the case for repeat suppression);
- cost             per-session delivered bytes, first full delivery vs re-arm;
- fire-rate        a ratchet on per-rule, worst-session delivered bytes;
- cofire           rule pairs that fire on the same call;
- persistence-loss full deliveries logged but absent from the transcript (the
                   harness keeps a large hook output out of context);
- breadth          what fraction of eligible calls each rule matches, replayed
                   over transcripts through the real matcher;
- labels           human TP/FP judgements, with a minimum sample for a rate.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from . import state as st
from .bashwrites import write_targets
from .engine import MARK
from .match import Budget, _Skip, describe, match
from .rules import load_rules
from .sequence import Sequence

OK, FINDINGS, NOT_EVALUABLE = 0, 1, 2
GAP_S = 1800
MATCH_KINDS = ("full", "brief", "truncated", "muted", "budget")   # a real match, not shadow


class NotEvaluable(Exception):
    pass


class Rows(list):
    """Log rows, plus how many lines were excluded: as malformed, as dated in the
    future, or as carrying no usable timestamp (the last two only when a window is asked)."""
    malformed = 0
    future = 0
    unstamped = 0


def _valid(r: dict) -> bool:
    kind = r.get("delivery")
    if kind is not None and kind not in st.DELIVERIES:
        return False
    # a field of the wrong type (a list as the rule or session) cannot be grouped or compared
    if any(not (r.get(k) is None or isinstance(r.get(k), str)) for k in ("rule", "session_id", "ts")):
        return False
    if "bytes" not in r:
        return kind is None or kind not in st.EMITTED        # legacy rows carry no count
    b = r["bytes"]
    return isinstance(b, int) and not isinstance(b, bool) and b >= 0


def read_rows(path: Path, what: str) -> tuple:
    """(rows, bad line count) of a JSONL file, or NotEvaluable naming WHY it cannot be read."""
    if not path.exists():
        raise NotEvaluable(f"{what} {path} does not exist")
    if path.is_dir():
        raise NotEvaluable(f"{what} {path} is a directory, not a file")
    try:
        return st.read_log(path)
    except OSError as e:
        raise NotEvaluable(f"{what} {path} cannot be read ({e.strerror or type(e).__name__})") from None


def _rows(log: Path, days: float | None = None, now=None) -> Rows:
    raw, bad = read_rows(log, "activation log")
    rows = Rows(r for r in raw if _valid(r))
    rows.malformed = bad + (len(raw) - len(rows))
    if days is not None:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        try:
            cut = now - _dt.timedelta(days=days)
        except OverflowError:
            raise NotEvaluable(f"--days {days:g} is too large for a date window (it would start "
                               f"before the year 1)") from None
        kept = Rows()
        for r in rows:
            t = st.parse_ts(r.get("ts"))
            if t is None:
                kept.unstamped += 1
            elif t > now:
                kept.future += 1          # a stamp from the future is in no window; counting it would inflate "the last N days"
            elif t > cut:
                kept.append(r)
        kept.malformed = rows.malformed
        rows = kept
    if not rows:
        why = []
        if days is not None and days == 0:
            why.append("--days 0 is a window of zero length, so no row can be in it")
        elif days is not None:
            why.append(f"in the last {days:g} day(s)")
        if rows.malformed:
            why.append(f"{rows.malformed} malformed row(s) excluded")
        if rows.unstamped:
            why.append(f"{rows.unstamped} row(s) have no parsable timestamp and cannot be placed in the window")
        if rows.future:
            why.append(f"{rows.future} row(s) dated in the future excluded (clock stepped back, or a log "
                       f"copied from a host with a fast clock)")
        raise NotEvaluable(f"no usable rows in {log}" + (": " + "; ".join(why) if why else ""))
    return rows


def sessions_of(rows: list) -> dict:
    """{session key: [rows in time order]}. Rows carry session_id; rows without
    one are grouped by a 30-minute gap and keyed ``gap:<first ts>``."""
    out = defaultdict(list)
    loose = sorted((r for r in rows if not r.get("session_id")),
                   key=lambda r: st.parse_ts(r.get("ts")) or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc))
    for r in rows:
        if r.get("session_id"):
            out[str(r["session_id"])].append(r)
    key, last = None, None
    for r in loose:
        t = st.parse_ts(r.get("ts"))
        if t is None:
            continue
        if last is None or (t - last).total_seconds() > GAP_S:
            key = f"gap:{r.get('ts')}"
        out[key].append(r)
        last = t
    for v in out.values():
        v.sort(key=lambda r: str(r.get("ts")))
    return dict(out)


NON_DELIVERY = ("shadow", "notice", "slow", "unevaluated")   # rows that are not a rule's delivery


def _kind(r) -> str:
    return r.get("delivery") or "legacy"


# ---------------------------------------------------------------- repeat-rate

def repeat_rate(log: Path, kinds=MATCH_KINDS, days=None) -> dict:
    allrows = _rows(log, days)
    rows = [r for r in allrows if _kind(r) in kinds]
    if not rows:
        raise NotEvaluable(f"no rows of kind {', '.join(kinds)}")
    novel = repeat = 0
    for srows in sessions_of(rows).values():
        seen = set()
        for r in srows:
            if r.get("rule") in seen:
                repeat += 1
            else:
                seen.add(r.get("rule"))
                novel += 1
    total = novel + repeat
    if not total:
        raise NotEvaluable(f"no row could be assigned to a session: {len(rows)} row(s) of kind "
                           f"{', '.join(kinds)} have neither a session id nor a valid timestamp")
    return {"kinds": list(kinds), "rows": total, "novel": novel, "repeat": repeat,
            "repeat_pct": round(100.0 * repeat / total, 1),
            "sessions": len(sessions_of(rows)), "malformed_rows": allrows.malformed,
            "future_rows": allrows.future, "unstamped_rows": allrows.unstamped}


# ---------------------------------------------------------------- cost

def cost(log: Path, days: float = 7, reviews: Path | None = None) -> dict:
    rows = _rows(log, days)
    not_deliveries = defaultdict(int)
    for r in rows:
        if _kind(r) in NON_DELIVERY:
            not_deliveries[_kind(r)] += 1
    if sum(not_deliveries.values()) == len(rows):
        raise NotEvaluable(
            f"no delivery rows in the last {days:g} day(s): 0 of {len(rows)} row(s) were deliveries "
            f"({', '.join(f'{n} {k}' for k, n in sorted(not_deliveries.items()))}), so there is "
            f"nothing to cost")
    verdicts = {}
    if reviews is not None:
        rv, _ = read_rows(reviews, "review file")
        for v in rv:
            verdicts[(v.get("rule"), str(v.get("date", ""))[:10])] = str(v.get("verdict", "")).lower()
    bad = {"irrelevant", "incorrect", "wrong", "noise", "false_positive", "not_applicable"}
    per = {}
    tot = defaultdict(int)
    for sid, srows in sessions_of(rows).items():
        p = defaultdict(int)
        seen_full = set()
        for r in srows:
            k = _kind(r)
            p[k] += 1
            b = int(r.get("bytes") or 0)
            if k in ("full", "truncated"):
                p["full_bytes"] += b
                if not r.get("session_id"):
                    # No session to remember: every fire is full by design, so it is
                    # neither a first delivery nor a re-arm.
                    p["sessionless_full_bytes"] += b
                else:
                    first = r.get("rule") not in seen_full
                    seen_full.add(r.get("rule"))
                    p["first_full_bytes" if first else "rearm_full_bytes"] += b
                v = verdicts.get((r.get("rule"), str(r.get("ts", ""))[:10]))
                if v is not None:
                    p["reviewed_full_bytes"] += b
                    if v in bad:
                        p["irrelevant_full_bytes"] += b
            elif k == "brief":
                p["brief_bytes"] += b
            elif k == "notice":
                p["notice_bytes"] += b
        per[sid] = dict(p)
        for k, v in p.items():
            tot[k] += v
    tot = dict(tot)
    in_session = tot.get("first_full_bytes", 0) + tot.get("rearm_full_bytes", 0)
    tot["rearm_share"] = (round(tot.get("rearm_full_bytes", 0) / in_session, 3)
                          if in_session else None)
    tot["irrelevant_share"] = (round(tot.get("irrelevant_full_bytes", 0) / tot["reviewed_full_bytes"], 3)
                               if tot.get("reviewed_full_bytes") else None)
    return {"days": days, "sessions": len(per), "total": tot, "per_session": per,
            "malformed_rows": rows.malformed, "future_rows": rows.future,
            "unstamped_rows": rows.unstamped}


# ---------------------------------------------------------------- fire-rate

def fire_rate(log: Path, days: float = 7, byte_cap: int = 10240,
              baseline: Path | None = None) -> tuple:
    rows = _rows(log, days)
    per = defaultdict(lambda: {"max_bytes": 0, "max_full": 0, "max_n": 0, "sessions_over": 0})
    skipped = defaultdict(int)
    for srows in sessions_of(rows).values():
        acc = defaultdict(lambda: [0, 0, 0])
        for r in srows:
            if _kind(r) in NON_DELIVERY:
                skipped[_kind(r)] += 1
                continue
            a = acc[r.get("rule")]
            a[0] += int(r.get("bytes") or 0)
            a[1] += _kind(r) in ("full", "truncated")
            a[2] += 1
        for rule, (b, f, n) in acc.items():
            p = per[rule]
            p["max_bytes"] = max(p["max_bytes"], b)
            p["max_full"] = max(p["max_full"], f)
            p["max_n"] = max(p["max_n"], n)
            p["sessions_over"] += b > byte_cap
    if not per:
        raise NotEvaluable(
            f"no delivered rows in the last {days:g} day(s): 0 of {len(rows)} row(s) were "
            f"deliveries ({', '.join(f'{n} {k}' for k, n in sorted(skipped.items()))}), "
            f"so there is no rule to measure")
    over = {}
    for rule, p in per.items():
        if p["sessions_over"]:
            avg_full = p["max_bytes"] / p["max_full"] if p["max_full"] else 0
            p["remedy"] = "SHORTEN" if avg_full > 3072 else ("NARROW" if p["max_n"] > 60 else "REVIEW")
            over[rule] = p
    known = set()
    if baseline is not None:
        shape = (f"baseline {baseline} must be a JSON object holding a list of rule file names, "
                 f'for example {{"rules": ["no-print.yaml"]}}')
        try:
            data = json.loads(baseline.read_text(encoding="utf-8"))
        except OSError as e:
            raise NotEvaluable(f"baseline {baseline} cannot be read ({e.strerror or type(e).__name__})") from None
        except ValueError:
            raise NotEvaluable(f"baseline {baseline} is not valid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list) \
                or not all(isinstance(x, str) for x in data["rules"]):
            raise NotEvaluable(shape)
        known = set(data["rules"])
    new = sorted(set(over) - known)
    report = {"days": days, "byte_cap": byte_cap, "rules_seen": len(per), "over": over,
              "regressed": new, "held": sorted(set(over) & known),
              "cleared": sorted(known - set(over)), "malformed_rows": rows.malformed,
              "future_rows": rows.future, "unstamped_rows": rows.unstamped}
    if new:
        return FINDINGS, report
    # a row that was excluded (unreadable byte count, dated in the future, no timestamp) may
    # have been the offender: it cannot vouch for a clean ratchet
    why = [f"{n} {what}" for n, what in ((rows.malformed, "malformed row(s)"),
                                          (rows.future, "row(s) dated in the future"),
                                          (rows.unstamped, "row(s) with no parsable timestamp")) if n]
    if why:
        report["cause"] = (f"no new offender among the {len(rows)} row(s) read, but " + ", ".join(why)
                           + " were excluded and could have been one, so a clean ratchet cannot be claimed")
        return NOT_EVALUABLE, report
    return OK, report


# ---------------------------------------------------------------- cofire

def cofire(log: Path, threshold: float = 0.5, min_support: int = 10,
           kinds=MATCH_KINDS) -> tuple:
    allrows = _rows(log)
    rows = [r for r in allrows if _kind(r) in kinds]
    if not rows:
        raise NotEvaluable("no rows of the chosen kinds")
    events = defaultdict(set)
    for r in rows:
        events[(r.get("session_id"), r.get("ts"))].add(r.get("rule"))
    if len(events) < min_support:
        raise NotEvaluable(f"only {len(events)} call(s) in the log, fewer than min-support "
                           f"{min_support}: no pair can reach the support needed to be reported, "
                           f"so \"0 pairs\" would not be a measurement (lower --min-support to look anyway)")
    fires = defaultdict(int)
    pairs = defaultdict(int)
    for rules in events.values():
        for a in rules:
            fires[a] += 1
        srt = sorted(x for x in rules if x)
        for i, a in enumerate(srt):
            for b in srt[i + 1:]:
                pairs[(a, b)] += 1
    found, thin = [], 0
    for (a, b), c in pairs.items():
        j = c / (fires[a] + fires[b] - c)
        if j > threshold:
            if c >= min_support:
                found.append({"a": a, "b": b, "cofires": c, "jaccard": round(j, 3)})
            else:
                thin += 1
    found.sort(key=lambda d: -d["jaccard"])
    return (FINDINGS if found else OK), {"calls": len(events), "rules": len(fires),
                                         "pairs": found, "thin_pairs_suppressed": thin,
                                         "malformed_rows": allrows.malformed}


# ---------------------------------------------------------------- transcripts

def _jsonl(path: Path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _tool_uses(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use" and isinstance(obj.get("name"), str):
            yield obj["name"], obj.get("input") if isinstance(obj.get("input"), dict) else {}
        for v in obj.values():
            yield from _tool_uses(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _tool_uses(v)


PERSISTED = "<persisted-output>"


def _content_text(c) -> str:
    """Flatten an attachment's content (a string, or a list of strings/blocks)."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_content_text(x) for x in c)
    if isinstance(c, dict):
        for k in ("text", "content"):
            if k in c:
                return _content_text(c[k])
    return ""


def session_transcripts(transcripts: Path, sid: str) -> list:
    """The session's own transcript and every transcript nested under
    ``<sid>/`` (subagents write theirs to ``<sid>/subagents/*.jsonl``)."""
    files = []
    top = transcripts / f"{sid}.jsonl"
    if top.is_file():
        files.append(top)
    sub = transcripts / sid
    if sub.is_dir():
        files += sorted(p for p in sub.rglob("*.jsonl") if p.is_file())
    return files


def persistence_loss(log: Path, transcripts: Path, days: float = 7) -> dict:
    """Full deliveries the log says were sent vs full blocks found in the
    text the agent was given, over sessions that JOIN only.

    ASSUMPTIONS (stated, not hidden): the harness records the text it gave the
    agent in ``"type": "attachment"`` records, in ``attachment.content`` (or a
    top-level ``content``); one transcript per session named
    ``<session_id>.jsonl``, plus subagent transcripts under ``<session_id>/``.
    Other fields of an attachment record are NOT read: the transcript also
    logs the hook process's raw stdout, which holds the full text even when
    the agent received only a short preview. A session whose transcripts hold
    no attachment record at all is a format change, not a loss: it is counted
    separately and excluded."""
    if not transcripts.is_dir():
        raise NotEvaluable(f"transcripts dir {transcripts} does not exist")
    rows = _rows(log, days)
    prod = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if _kind(r) in ("full", "truncated") and r.get("session_id"):
            rule = str(r.get("rule") or "")
            prod[str(r["session_id"])][rule[:-5] if rule.endswith(".yaml") else rule] += 1
    joined = lost = produced = extra = files_read = no_attach = previews = 0
    for sid, per_rule in prod.items():
        files = session_transcripts(transcripts, sid)
        if not files:
            continue
        cons = defaultdict(int)
        attachments = 0
        for tp in files:
            for rec in _jsonl(tp):
                if not (isinstance(rec, dict) and rec.get("type") == "attachment"):
                    continue
                attachments += 1
                att = rec.get("attachment")
                text = _content_text(att.get("content") if isinstance(att, dict)
                                     else rec.get("content"))
                if text.lstrip().startswith(PERSISTED):
                    previews += 1
                for slug in per_rule:
                    cons[slug] += (text.count(f"{MARK} ACTIVATED [{slug},")
                                   + text.count(f"{MARK} ACTIVATED [{slug}]"))
        if attachments == 0:
            no_attach += 1
            continue
        joined += 1
        files_read += len(files)
        for slug, n in per_rule.items():
            produced += n
            lost += max(0, n - cons[slug])
            extra += max(0, cons[slug] - n)
    if joined == 0:
        raise NotEvaluable(f"no logged session joins a transcript in {transcripts} "
                           f"({len(prod)} session(s) with full deliveries; {no_attach} had "
                           f"transcripts with no attachment records)")
    return {"sessions_with_full": len(prod), "joined": joined, "full_logged": produced,
            "lost": lost, "lost_pct": round(100.0 * lost / produced, 1) if produced else None,
            "consumer_extra": extra, "transcript_files": files_read,
            "sessions_with_no_attachment_records": no_attach,
            "persisted_previews": previews, "malformed_rows": rows.malformed,
            "future_rows": rows.future, "unstamped_rows": rows.unstamped}


def breadth(transcripts: Path, rules_dir: Path, threshold: float = 0.25,
            sessions: int = 5, bash_writes: bool = True, budget_ms: int = 1000) -> tuple:
    """What fraction of eligible calls each rule matches, replayed through the real
    matcher. Every call is matched under the hook's per-call time budget (``budget_ms``,
    ``INSTINCT_MATCH_BUDGET_MS``; 0 = unbounded, as for the hook): a rule that runs out
    of it on a call is SKIPPED for that call, counted in the rule's ``skipped``, and left
    out of its rate's denominator (a rate over zero evaluated calls is None)."""
    if not transcripts.is_dir():
        raise NotEvaluable(f"transcripts dir {transcripts} does not exist")
    rs = load_rules(rules_dir)
    if not rs.fire:
        raise NotEvaluable(f"no loadable rules in {rules_dir}")
    if sessions < 1:
        raise ValueError("sessions must be 1 or more")
    files = sorted((p for p in transcripts.rglob("*.jsonl") if p.is_file()),
                   key=lambda p: p.stat().st_mtime)[-sessions:]
    per_file = [[c for rec in _jsonl(f) for c in _tool_uses(rec)] for f in files]
    calls = [(fi, name, ti) for fi, fc in enumerate(per_file) for name, ti in fc]
    if not calls:
        raise NotEvaluable(f"no tool calls in the {len(files)} newest transcript(s)")
    writes_cache: dict = {}

    def has_writes(k, name, ti) -> bool:
        if k not in writes_cache:
            found = False
            if name == "Bash":
                # parsed under the same per-call budget as the match itself; a parse that runs
                # out is not "no writes": the call counts as eligible and the match skips it
                with Budget(budget_ms) as parse_budget:
                    try:
                        with parse_budget.guard(None, "bash-write parsing"):
                            found = bool(write_targets(describe(name, ti).command))
                    except _Skip:
                        found = True
            writes_cache[k] = found
        return writes_cache[k]
    out = []
    for rule in rs.fire:
        eligible = fires = skipped = 0
        # a Bash write is matched as a Write, or an Edit for an Edit-only rule (see
        # match.py): only a rule scoped to one of them can fire on one, so only
        # such a rule counts it as eligible
        writes_in_scope = "Write" in rule.tools or "Edit" in rule.tools
        seq, seq_file = None, None
        for k, (fi, name, ti) in enumerate(calls):
            if rule.after is not None and fi != seq_file:
                seq, seq_file = Sequence(), fi         # a new transcript is a new session
            derived = bash_writes and writes_in_scope and has_writes(k, name, ti)
            if name in rule.tools or derived:
                eligible += 1
                with Budget(budget_ms) as budget:
                    hit = match([rule], name, ti, bash_writes=bash_writes, budget=budget)
                    if hit and seq is not None and not seq.met(rule):
                        hit = []
                if budget.slow or budget.unevaluated:
                    skipped += 1
                elif hit:
                    fires += 1
            if seq is not None:
                with Budget(budget_ms) as pre_budget:
                    seq.advance([rule], name, ti, bash_writes, pre_budget)
        evaluated = eligible - skipped
        rate = fires / evaluated if evaluated else None
        out.append({"rule": rule.file, "eligible": eligible, "fires": fires, "skipped": skipped,
                    "after": rule.after is not None,
                    "rate": None if rate is None else round(rate, 3),
                    "flag": rate is not None and rate >= threshold})
    if not any(d["eligible"] for d in out):
        raise NotEvaluable(f"no rule had an eligible call: 0 eligible of {len(calls)} call(s) in the "
                           f"{len(files)} newest transcript(s) for the {len(out)} loaded rule(s), "
                           f"so no rate exists")
    out.sort(key=lambda d: -(d["rate"] or 0))
    return (FINDINGS if any(d["flag"] for d in out) else OK), {
        "transcripts": len(files), "calls": len(calls), "rules": out, "budget_ms": budget_ms}


# ---------------------------------------------------------------- follow-rate

DELIVERED = ("full", "brief", "truncated")     # the agent was given the advice (or a cut of it)


def _calls(path: Path) -> list:
    """[(datetime, tool, input)] of one transcript's tool calls, in time order. A record
    without a parsable timestamp cannot be placed after a delivery and is left out."""
    out = []
    for rec in _jsonl(path):
        if not isinstance(rec, dict):
            continue
        t = st.parse_ts(rec.get("timestamp"))
        if t is None:
            continue
        for name, ti in _tool_uses(rec):
            out.append((t, name, ti))
    out.sort(key=lambda c: c[0])
    return out


def _safe_sid(sid) -> bool:
    """A session id names ONE file directly under --transcripts: no separator, no `..`, no NUL."""
    return (isinstance(sid, str) and sid not in ("", ".", "..")
            and not any(c in sid for c in "/\\\0"))


def follow_rate(log: Path, transcripts: Path, rules_dir: Path, days: float = 7, min_n: int = 10,
                bash_writes: bool = True) -> dict:
    """For each rule with a `follow` predicate: of the deliveries that join a transcript, how
    many were followed by a matching call within the rule's next `within` calls, beside the
    base rate (the same predicate over every window of that many calls in the same sessions,
    delivered or not). Without a holdout this is what followed, not what was caused.

    Joins on session id: a delivery's later calls are the session's tool calls stamped after
    the log row, read from ``<session_id>.jsonl`` (subagent transcripts are not read: their
    calls are another agent's). The predicate is matched by the real matcher, Bash writes
    included."""
    if not transcripts.is_dir():
        raise NotEvaluable(f"transcripts dir {transcripts} does not exist")
    rs = load_rules(rules_dir)
    rows = _rows(log, days)
    by_rule = defaultdict(list)
    for r in rows:
        if _kind(r) in DELIVERED and r.get("rule"):
            by_rule[str(r["rule"])].append(r)
    cache: dict = {}

    def calls_of(sid):
        if sid not in cache:
            p = transcripts / f"{sid}.jsonl" if _safe_sid(sid) else None
            cache[sid] = _calls(p) if p is not None and p.is_file() else None
        return cache[sid]

    out = []
    for rule in rs.fire:
        dl = by_rule.get(rule.file, [])
        d = {"rule": rule.file, "deliveries": len(dl), "joined": 0, "followed": None,
             "no_transcript": 0, "no_later_call": 0, "unsafe_session_id": 0, "within": None, "rate": None,
             "base_windows": 0, "base_hits": 0, "base_rate": None, "why": ""}
        if rule.follow is None:
            if dl:
                d["why"] = "no `follow` predicate: nothing says what acting on it looks like"
                out.append(d)
            continue
        pred, within = rule.follow
        d["within"] = within
        followed = 0
        hit: dict = {}

        def follows(sid, i, c):
            k = (sid, i)
            if k not in hit:
                hit[k] = bool(match([pred], c[1], c[2], bash_writes=bash_writes))
            return hit[k]
        sessions = set()
        for r in dl:
            sid, t = r.get("session_id"), st.parse_ts(r.get("ts"))
            calls = calls_of(sid) if sid else None
            if sid and not _safe_sid(sid):
                d["unsafe_session_id"] += 1       # refused, never joined; counted as no transcript
            if calls is None or t is None:
                d["no_transcript"] += 1
                continue
            later = [(i, c) for i, c in enumerate(calls) if c[0] > t][:within]
            if not later:
                d["no_later_call"] += 1
                continue
            d["joined"] += 1
            sessions.add(sid)
            if any(follows(sid, i, c) for i, c in later):
                followed += 1
        for sid in sessions:
            indexed = list(enumerate(calls_of(sid)))
            for i in range(len(indexed)):
                window = indexed[i + 1:i + 1 + within]
                if not window:
                    continue
                d["base_windows"] += 1
                if any(follows(sid, j, c) for j, c in window):
                    d["base_hits"] += 1
        if d["joined"]:
            d["followed"] = followed
        if d["joined"] >= min_n:
            d["rate"] = round(followed / d["joined"], 3)
        elif d["joined"]:
            d["why"] = f"fewer than {min_n} joined deliveries: counts only, no rate"
        elif not dl:
            d["why"] = "no deliveries of this rule in the window"
        elif d["no_transcript"] and not d["no_later_call"]:
            d["why"] = "none of its deliveries joined a transcript (no transcript for the session)"
        elif d["no_later_call"] and not d["no_transcript"]:
            d["why"] = "none of its deliveries has a later call in its transcript"
        else:
            d["why"] = ("none of its deliveries joined a transcript with a later call "
                        "(no transcript or no later call)")
        if d["unsafe_session_id"]:
            d["why"] += (("; " if d["why"] else "") +
                         f"{d['unsafe_session_id']} session id(s) refused (not a bare file name)")
        if d["joined"] >= min_n and d["base_windows"] >= min_n:
            d["base_rate"] = round(d["base_hits"] / d["base_windows"], 3)
        out.append(d)
    cause = None
    if not any(d["joined"] for d in out):
        n = sum(len(v) for v in by_rule.values())
        cause = (f"no delivery of a rule with a `follow` predicate joins a transcript in "
                 f"{transcripts} with a later call ({n} delivery row(s) read; "
                 f"{sum(1 for r in rs.fire if r.follow)} rule(s) have a predicate)")
    return {"rules": out, "min_n": min_n, "cause": cause, "malformed_rows": rows.malformed,
            "future_rows": rows.future, "unstamped_rows": rows.unstamped}


# ---------------------------------------------------------------- labels

VERDICTS = ("TRUE_POSITIVE", "FALSE_POSITIVE")
SOURCES = ("transcript_sample", "rule_evidence")
MIN_N = 20


def make_label(rule, tool, verdict, source, excerpt, session_id=None, reason="", labeler="") -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if source == "rule_evidence" and verdict == "FALSE_POSITIVE":
        raise ValueError("the example a rule was written from cannot testify to a false fire")
    ex = (excerpt or "")[:300]
    return {"rule": rule, "tool": tool, "verdict": verdict, "source": source, "excerpt": ex,
            "excerpt_sha256": hashlib.sha256(ex.encode("utf-8")).hexdigest()[:16],
            "session_id": session_id, "reason": reason, "labeler": labeler,
            "ts": st.now_iso()}


def label_stats(store: Path) -> dict:
    rows, bad = read_rows(store, "label store")
    if not rows and not bad:
        raise NotEvaluable(f"label store {store} is empty")
    if not rows:
        raise NotEvaluable(f"label store {store} has no readable label: {bad} unreadable line(s) "
                           f"(not JSON objects) and 0 labels, so there is nothing to report")
    per = defaultdict(lambda: {"n": 0, "tp": 0, "holdout": 0})
    for r in rows:
        p = per[r.get("rule")]
        p["n"] += 1
        p["tp"] += r.get("verdict") == "TRUE_POSITIVE"
        p["holdout"] += r.get("source") == "transcript_sample"
    for p in per.values():
        p["precision"] = round(p["tp"] / p["n"], 3) if p["n"] >= MIN_N else None
    return {"labels": len(rows), "bad_lines": bad, "min_n": MIN_N, "rules": dict(per)}
