"""The Claude Code PreToolUse adapter.

Reads one JSON object on stdin (``tool_name``, ``tool_input``, ``session_id``),
runs the engine, and — only if something is to be delivered — writes the
harness's context envelope on stdout:

    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                            "additionalContext": "<the lines>"}}

Raw text on a PreToolUse hook's stdout does not reach the model; the envelope
does. The session id comes from the payload and nowhere else, so every entry
point resolves it the same way.

The hook is advisory. It never blocks a tool call and, once running, always exits 0 (a missing
package or a shadowing PYTHONPATH can stop it starting): empty,
unparseable or non-object input produces no output, and an engine failure is
reported on stderr with nothing on stdout.

The envelope is written as UTF-8 bytes whatever the console encoding, and the
activation log and delivery state are committed only AFTER that write
succeeded. A failed write is reported on stderr and records nothing, so the
log never says ``full`` for advice the agent did not receive.
"""
from __future__ import annotations

import json
import sys

from .engine import Settings, record_unread_input, run, slow_message, unevaluated_message

MAX_INPUT = 32 * 1024 * 1024


def envelope(text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "additionalContext": text}},
                      ensure_ascii=False)


def envelope_context_bytes(text: str) -> bytes:
    """The additionalContext value exactly as the envelope carries it."""
    return json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8")



def _write(stdout, data: str) -> None:
    buf = getattr(stdout, "buffer", None)
    if buf is not None:
        buf.write(data.encode("utf-8"))
        buf.flush()
    else:
        stdout.write(data)
        stdout.flush()


def main(stdin=None, stdout=None, stderr=None, settings: Settings | None = None,
         raw: bool = False) -> int:
    stderr = stderr or sys.stderr
    try:
        return _main(stdin or sys.stdin, stdout or sys.stdout, stderr, settings, raw)
    except Exception as e:  # noqa: BLE001 — advisory: never block, whatever happened
        try:
            print(f"instinct-delivery: hook failed ({type(e).__name__})", file=stderr)
        except Exception:  # noqa: BLE001, S110
            pass
        return 0


def _main(stdin, stdout, stderr, settings, raw) -> int:
    try:
        data = stdin.read(MAX_INPUT + 1)
        if not data.strip():
            return 0
        if len(data) > MAX_INPUT:
            record_unread_input(f"over the {MAX_INPUT} character limit", settings, stderr)
            return 0
        payload = json.loads(data)
    except RecursionError:
        record_unread_input("nested too deeply", settings, stderr)
        return 0
    except UnicodeDecodeError:
        record_unread_input("not valid text", settings, stderr)
        return 0
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        res = run(payload.get("tool_name"), payload.get("tool_input"),
                  payload.get("session_id"), settings=settings, commit=False,
                  agent_id=payload.get("agent_id"))
    except Exception as e:  # noqa: BLE001 — advisory: never block, never half-emit
        print(f"instinct-delivery: engine failed ({type(e).__name__}: {e})", file=stderr)
        return 0
    if res.rules_problem:
        print(f"instinct-delivery: NOT EVALUABLE — {res.rules_problem}", file=stderr)
    for rule_file, why in res.slow:
        print(slow_message(rule_file, why), file=stderr)
    for rule_file, why in res.unevaluated:
        print(unevaluated_message(rule_file, why), file=stderr)
    text = res.text
    if text.strip():
        try:
            _write(stdout, text if raw else envelope(text) + "\n")
        except Exception as e:  # noqa: BLE001 — nothing reached the agent: record nothing
            print(f"instinct-delivery: could not write the hook output ({type(e).__name__}); "
                  f"nothing was recorded as delivered", file=stderr)
            return 0
    res.commit()
    return 0
