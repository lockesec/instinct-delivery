"""Per-user state: the activation log and the per-session delivery state.

Both live under STATE_DIR (``INSTINCT_STATE_DIR``, default
``$XDG_STATE_HOME/instinct-delivery``, else ``~/.local/state/instinct-delivery``).

Both files are created mode 0600 and a directory this tool creates for them
mode 0700 (they hold session ids and file paths; the delivery file names are
session ids). A directory that already exists keeps its mode.

The activation log is one JSON line per matched rule per call, whatever was
delivered (see ``ROW_FIELDS``), plus one ``notice`` row (``rule`` null) for
the closing line when rules were withheld, so the rows of a call account for
every byte it emitted. The row carries the rule's own declared token
that matched, never the text of the call. It is appended without a lock;
POSIX appends of one short line are atomic enough for a report, and a log
write failure never blocks delivery.

Delivery state is one file per session: ``{rule file: {ts, n, nw}}`` — the
time of the last FULL delivery, fires this session, and brief lines since the
last full one (and ``wh`` while a rule waits after being withheld). It is
read once and written once per hook call, atomically. A session id that is not
already a safe file name gets a `~digest` suffix (`~` cannot occur in a literal
name), so two ids never share a file. Files written by the older `-digest`
scheme are simply not found: one extra full delivery, then pruned by age.
Two hooks of one session racing can lose an update; the failure direction is
an extra full delivery, never a missing one.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

# The activation-log row. One home for the writer and every reader/test.
ROW_FIELDS = ("ts", "rule", "domain", "confidence", "tool", "session_id",
              "file_path", "matched", "delivery", "bytes")
OPTIONAL_FIELDS = ("via", "shadow")
DELIVERIES = ("full", "brief", "truncated", "muted", "budget", "shadow", "notice", "slow",
              "unevaluated")
DELIVERED = ("full", "brief", "truncated")   # advice text reached the model
EMITTED = DELIVERED + ("notice",)            # rows whose bytes were emitted

KEEP_DAYS_DEFAULT = 7
MAX_STATE_BYTES = 4 * 1024 * 1024


class ConfigError(Exception):
    """A setting that names a place which cannot be resolved (``~nosuchuser/x``)."""


def expand(value: str, what: str) -> Path:
    """Path(value) with ``~`` expanded, or ConfigError naming the setting."""
    try:
        return Path(value).expanduser()
    except RuntimeError as e:     # `~user` of a user that does not exist, or no home directory
        raise ConfigError(f"{what}={value!r} cannot be resolved ({e})") from None


def state_dir() -> Path:
    """``INSTINCT_STATE_DIR`` if it is an absolute path (``~`` is expanded), else
    ``$XDG_STATE_HOME/instinct-delivery`` if that is absolute, else
    ``~/.local/state/instinct-delivery``. A RELATIVE value is never honoured: it
    would resolve against the project directory, which can be a cloned repository
    that an attacker controls. An ignored INSTINCT_STATE_DIR is named on stderr."""
    env = os.environ.get("INSTINCT_STATE_DIR", "").strip()
    if env:
        p = expand(env, "INSTINCT_STATE_DIR")
        if p.is_absolute():
            return p
        print(f"instinct-delivery: INSTINCT_STATE_DIR={env!r} is not an absolute path and was "
              f"ignored; using the default state directory", file=sys.stderr)
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = expand(xdg, "XDG_STATE_HOME") if xdg else _home() / ".local" / "state"
    if not base.is_absolute():       # the XDG spec: a relative value is invalid and ignored
        base = _home() / ".local" / "state"
    return base / "instinct-delivery"


def _home() -> Path:
    try:
        return Path.home()
    except RuntimeError as e:
        raise ConfigError(f"the home directory cannot be found ({e}); set INSTINCT_STATE_DIR") from None


def log_path(sdir: Path | None = None) -> Path:
    return (sdir or state_dir()) / "activations.jsonl"


def delivery_dir(sdir: Path | None = None) -> Path:
    return (sdir or state_dir()) / "delivery"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def parse_ts(s):
    """An aware datetime, or None. Naive stamps are read as UTC."""
    if not isinstance(s, str) or not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _mkdir_private(d: Path, levels: int = 1) -> None:
    """Create d (and the missing ones of its last `levels` components) mode 0700,
    whatever the umask: the file names inside are session ids. Missing ancestors
    above those get the ordinary default. A directory that already exists keeps
    its mode: it may be shared on purpose and is not ours to change."""
    if d.is_dir():
        return
    if levels > 1:
        _mkdir_private(d.parent, levels - 1)
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass


def append_rows(rows: list, path: Path) -> bool:
    """Append rows as JSON lines. True only if EVERY row was written: a row
    that cannot be strict JSON (a date, NaN, a lone surrogate) is skipped and
    makes the result False, and it never stops the rows around it."""
    lines, ok = [], True
    for r in rows:
        try:
            lines.append((json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        except (TypeError, ValueError):
            ok = False
    if not lines:
        return ok
    try:
        _mkdir_private(path.parent)
        # O_NOFOLLOW: a planted link at the log's name is refused, not appended through.
        # O_NONBLOCK: a FIFO with no reader fails to open instead of hanging the hook.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            data = b"".join(lines)
            while data:
                data = data[os.write(fd, data):]
        finally:
            os.close(fd)
        return ok
    except OSError:
        return False


def _session_file(ddir: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80]
    if safe != session_id or safe in ("", ".", ".."):
        # `~` is outside the literal alphabet, so an encoded name can never be
        # the spelling of a literal id: the map is injective across all ids.
        digest = hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        safe = f"{safe[:63].lstrip('.')}~{digest}"
    return ddir / f"{safe}.json"


def load_state(ddir: Path, session_id: str) -> dict:
    """Absent, corrupt or non-mapping state reads as empty: fail toward
    delivering MORE, never toward silence."""
    try:
        # Like the log writer: no link followed, no FIFO waited on, regular files only.
        fd = os.open(_session_file(ddir, session_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return {}
            with os.fdopen(fd, "rb", closefd=False) as fh:
                raw = fh.read(MAX_STATE_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_STATE_BYTES:
            return {}
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def save_state(ddir: Path, session_id: str, state: dict) -> bool:
    target = _session_file(ddir, session_id)
    try:
        _mkdir_private(ddir, 2)       # the delivery dir and the state dir above it
        # A fresh O_EXCL temp file in the target's directory (never a name that could be
        # pre-planted as a link), renamed over the target: a link AT the target is replaced,
        # never written through.
        fd, tmp = tempfile.mkstemp(dir=ddir, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(state, sort_keys=True))
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def prune_state(ddir: Path, keep_days: float) -> int:
    """Delete session files (and temp files of a killed hook) not written for keep_days. Returns the count."""
    cutoff = time.time() - keep_days * 86400
    n = 0
    try:
        # stale `.*.tmp` are what a hook killed between mkstemp and rename leaves behind
        entries = list(ddir.glob("*.json")) + list(ddir.glob(".*.tmp"))
    except OSError:
        return 0
    for p in entries:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            continue
    return n


def read_log(path: Path) -> tuple:
    """(rows, bad_line_count). Rows are dicts; unparseable lines are counted."""
    rows, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
    return rows, bad
