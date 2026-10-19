"""replay: feed a recorded sequence of tool calls through the REAL engine.

The input is a JSON-lines file. A row is replayable when it carries a ``tool_name`` (text)
and a ``tool_input`` (an object); its ``session`` value (text or a whole number) names the
session it belongs to, and any other field is ignored. Anything else is skipped and COUNTED:
a line that is not JSON, one that is not an object, and one that is not a tool call (a user
prompt, a tool result: no ``tool_name`` or no ``tool_input`` object). Rows are run in file
order through ``engine.run`` against the rules directory, on a throwaway state directory
(deleted afterwards), so delivery suppression and ``after`` sequences behave exactly as they
would in a live session and nothing outside that directory is written.

``step`` is the row's index in the file, counting every row, 0-based, so a step names a line
(line = step + 1) whether or not the rows before it were calls.
"""
from __future__ import annotations

import json
import tempfile
from collections import Counter, OrderedDict
from pathlib import Path

from .engine import Settings, run


class ReplayError(Exception):
    """The file cannot be replayed at all (NOT EVALUABLE), with the cause."""


def _session_of(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v:
        return v
    if isinstance(v, int):
        return str(v)
    return None


def read_calls(path: Path):
    """([(step, session, tool, input)], rows, {skip reason: count}). Raises ReplayError when
    the file cannot be read."""
    calls, skipped, rows = [], Counter(), 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for step, line in enumerate(fh):
                if not line.strip():
                    continue
                rows += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    skipped["not_json"] += 1
                    continue
                if not isinstance(obj, dict):
                    skipped["not_an_object"] += 1
                    continue
                tool, ti = obj.get("tool_name"), obj.get("tool_input")
                if not (isinstance(tool, str) and tool and isinstance(ti, dict)):
                    skipped["no_tool_call"] += 1
                    continue
                calls.append((step, _session_of(obj.get("session")), tool, ti))
    except OSError as e:
        raise ReplayError(f"{path} cannot be read: {e.strerror or type(e).__name__}") from None
    return calls, rows, dict(skipped)


def replay(path: Path, settings: Settings) -> dict:
    calls, rows, skipped = read_calls(path)
    if not calls:
        raise ReplayError(f"no replayable row in {path}: 0 of {rows} row(s) carry a tool_name and a "
                          f"tool_input object" + (f" (skipped: {', '.join(f'{k} {v}' for k, v in sorted(skipped.items()))})"
                                                   if skipped else ""))
    out_calls, problem, loaded, rejected = [], "", None, 0
    with tempfile.TemporaryDirectory(prefix="instinct-replay-") as td:
        s = settings
        s.state_dir = Path(td)
        for step, session, tool, ti in calls:
            res = run(tool, ti, session, settings=s)
            if res.rules_problem:
                problem = res.rules_problem
                break
            out_calls.append({"session": session, "step": step, "tool": tool,
                              "deliveries": [{"rule": r["rule"], "delivery": r["delivery"]}
                                             for r in res.rows if r.get("rule")]})
    if problem:
        raise ReplayError(problem)
    per: "OrderedDict" = OrderedDict()
    totals = Counter()
    with_delivery = 0
    for c in out_calls:
        d = per.setdefault(c["session"], {"session": c["session"], "calls": 0, "calls_with_delivery": 0,
                                          "deliveries": Counter(), "rules": Counter()})
        d["calls"] += 1
        if c["deliveries"]:
            d["calls_with_delivery"] += 1
            with_delivery += 1
        for x in c["deliveries"]:
            d["deliveries"][x["delivery"]] += 1
            d["rules"][x["rule"]] += 1
            totals[x["delivery"]] += 1
    summary = [{**d, "deliveries": dict(d["deliveries"]), "rules": dict(d["rules"])} for d in per.values()]
    return {"file": str(path), "rows": rows, "replayed": len(out_calls), "skipped": skipped,
            "sessions": len(per), "calls": out_calls, "sessions_summary": summary,
            "totals": {"calls": len(out_calls), "calls_with_delivery": with_delivery,
                       "deliveries": dict(totals)}}


def render(rep: dict) -> str:
    skipped = rep["skipped"]
    skip_txt = (f"; skipped {sum(skipped.values())} ("
                + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) + ")") if skipped else "; skipped 0"
    lines = [f"replay: {rep['replayed']} replayed of {rep['rows']} row(s){skip_txt}",
             f"rules dir {rep['rules_dir']}: {rep['rules_loaded']} firing/shadow rule(s) loaded"
             + (f", {rep['rules_rejected']} rule(s) rejected (see `doctor`)" if rep["rules_rejected"] else "")
             + "; throwaway state, nothing written outside it; step = row index (0-based)"]
    width = max(len(c["session"] or "(no session)") for c in rep["calls"])
    for c in rep["calls"]:
        who = (c["session"] or "(no session)").ljust(width)
        got = ", ".join(f"{d['rule'][:-5] if d['rule'].endswith('.yaml') else d['rule']}:{d['delivery']}"
                        for d in c["deliveries"]) or "-"
        lines.append(f"  {who}  row {c['step']}  {c['tool']}  {got}")
    lines.append("per session:")
    for d in rep["sessions_summary"]:
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(d["deliveries"].items())) or "none"
        lines.append(f"  {d['session'] or '(no session)'}: {d['calls']} call(s), "
                     f"{d['calls_with_delivery']} with a delivery ({kinds})")
    t = rep["totals"]
    kinds = ", ".join(f"{k} {v}" for k, v in sorted(t["deliveries"].items())) or "none"
    lines.append(f"total: {t['calls']} call(s) in {rep['sessions']} session(s); "
                 f"{t['calls_with_delivery']} call(s) with a delivery ({kinds})")
    return "\n".join(lines)
