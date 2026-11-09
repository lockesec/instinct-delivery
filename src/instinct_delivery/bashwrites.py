"""Files a shell command visibly writes, with the content written.

A rule about what goes INTO a file (a secret in a config, a debug print in a
module) is scoped to Write/Edit. The same file can be written from Bash, and
then the rule would never see it. This module finds the writes a command makes
in plain sight, so each can be matched as if it were a Write of that content.

It is pure (no I/O) and conservative: it never claims a write it cannot see,
and an unrecognised shape yields nothing. It is NOT a bash parser: it splits a
command into simple commands, strips what can stand in front of one, and reads
the few commands below.

Read:

- ``echo`` / ``printf`` ``... > f`` (also ``>>``, ``>|``, ``1>``, ``&>``, ``>& f``), content
  = its arguments; ``cat ... > f``, content unknown (empty). Every stdout redirect
  of the command is a target, but bash applies them left to right and only the last
  receives the text: ``echo a >y >z`` creates y empty and writes ``a`` to z;
- ``tee [-a] f ...``: every file operand, content unknown unless a heredoc feeds it;
- ``sed -i`` / ``--in-place``: every file operand, content = the expression(s).
  Options are parsed (``-n``, ``-E``, ``-l N``, ``--follow-symlinks``, ``-e``/``-f``,
  ``--expression=``, ``--``); a GNU ``-i[SUFFIX]`` and a BSD ``-i ''`` are both read;
- a heredoc whose introducing line redirects (``cat > f <<EOF``, ``cat <<EOF >> f``)
  or tees: the body is the content for commands that copy stdin (``cat``, ``tee``,
  ``dd``); for any other command (``cp`` included) (``psql <<EOF > out``) the write has empty
  content. ``cat file <<EOF`` ignores its heredoc (a file operand), ``cat -n`` and ``cat -``
  read it. The tag is a shell word: quote removal gives the terminator (``<<'EOF.txt'``,
  ``<<EOF-1``, ``<<1``, ``<<'a b'``, ``<<E"O"F`` ends at ``EOF``) and any quoting means the body
  is not expanded. The terminator is the line equal to the tag (``<<-``: after leading TABs
  only, which are also removed from the body); ``  EOF`` and ``EOF `` do not end it. A line may
  introduce several heredocs, their bodies in order; the last is the command's stdin. A heredoc
  with no terminator runs to the end, as in bash; quote state carries across lines;
- a heredoc fed to ``python -`` whose body writes files (``Path('x').write_text``,
  ``open('x', 'w')``): the literal targets, else every quoted path-like literal in a
  body that does write (over-approximates, never invents a write);
- a redirected group (``{ echo a; } > f``, ``( echo a ) > f``): ``f`` with unknown content;
- a command substitution (``$( )`` or backticks) and a process substitution
  (``>( )``) is read as a command of its own, so ``echo $(cat x > y.py)`` writes ``y.py``.

What may stand in front of the command is stripped: shell keywords and grouping
(``if``/``then``/``else``/``elif``/``while``/``until``/``do``, ``{``, ``(``, ``!``,
``function name``, ``name()``), ``VAR=value`` assignments (any quoting of the value), and the wrappers ``sudo``, ``doas``,
``env``, ``time``, ``nice``, ``command``, ``exec``, ``stdbuf``, ``nohup`` with their
options; an absolute command path is read by its base name (``/usr/bin/tee``).
Commands are separated at ``;``, ``|``, ``|&``, ``&&``, ``||``, ``&``, newlines and
parentheses.

Not writes: a redirect inside quotes (quotes inside a double-quoted ``${ }`` start fresh, so
``"${y:-"a > b"}"`` holds none), ``$(( ))``/``(( ))``/``[[ ]]``/``${ }``/``$[ ]``, an escaped
``\\>``, anything after an unquoted ``#`` (a comment), an arrow ``->``, a stderr
redirect (``2>f``, a lone digit token other than 1), a dup (``>&2``). A digit that
ends a word (``abc12>f``) is not an fd, so that IS a write to ``f``. A target that contains a command
substitution (``>out_$(date).txt``) or starts with ``$`` is dropped (the inner
command is still read).

``$"text"`` (a locale string) is the text without its ``$``. ``(( x )) > f`` is a redirected group
(``f``, unknown content); ``A="b" > f`` (an assignment alone, which creates ``f`` empty) is NOT read.

Any other command with a stdout redirect (``ls > f``, ``python x.py > f``, ``: > f``, ``exec > f``,
``if ...; fi > f``) writes that file, content unknown. So does the destination of ``cp``, ``mv``,
``install``, ``ln`` (``-t DIR`` included), ``dd of=``, ``curl -o``/``--output`` and ``wget -O``.
The string given to ``eval``, ``bash -c``, ``sh -c``, ``zsh -c`` and ``dash -c`` is read as a command.

NOT read (no write is claimed): ``xargs``,
``sh -c '...'``, a ``python`` heredoc without ``-``, ``exec > f``, ``: > f``, ``cp``/``mv``/``dd of=``/``curl -o`` and
editors.

Sinks (``/dev/null``, ``/dev/stdout``, ``/dev/stderr``, ``/dev/stdin``, ``/dev/tty``,
``/dev/fd/N``) and ``$VAR`` targets are dropped, as are duplicate (path, content) pairs.
"""
from __future__ import annotations

import bisect
import re

HEREDOC_OP = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*")
MAX_TAG = 256                  # a longer delimiter word is not read as a heredoc
MAX_TERM_LINE = 512            # lines longer than this are never terminators
ARITH = re.compile(r"\$?\(\((?:[^()]|\([^()]*\))*\)\)")
SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty"}
SINK_FD = re.compile(r"/dev/fd/\d+\Z")
PY_WRITE_CALL = re.compile(r"\.write_(?:text|bytes)\(|\bopen\([^(),]*,\s*['\"][wax]b?\+?['\"]")
PY_PATH_VAR = re.compile(r"(\w+)\s*=\s*Path\(\s*['\"]([^'\"]+)['\"]\s*\)")
PY_VAR_WRITE = re.compile(r"(\w+)\.write_(?:text|bytes)\(|open\(\s*(\w+)\s*,\s*['\"][wax]")
PY_DIRECT = re.compile(r"Path\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_(?:text|bytes)\("
                       r"|open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wax]b?\+?['\"]")
PY_LITERAL_PATH = re.compile(r"['\"]((?:\.{0,2}/|~/)?[\w.-]+(?:/[\w.-]+)*\.\w{1,8})['\"]")
BRACE_TOKENS = re.compile(r"\$\{|\{|\}")
SQUARE_TOKENS = re.compile(r"\$\[|\[|\]")
MAX_NEST = 64              # nesting counted in ${ } / $[ ]; deeper is not tracked
ASSIGN = re.compile(r"[A-Za-z_]\w*\+?=")
HEREDOC_MARK = re.compile(r"\x01(\d+)\x01\Z")
BACKTICK_ESC = re.compile(r"\\([$`\\])")
BODY_ESC = BACKTICK_ESC          # an unquoted heredoc body unescapes the same three characters
BACKTICK_DQ_ESC = re.compile(r"\\([$`\\\"])")
ANSI_ESC = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|[0-7]{1,3}|.)", re.S)
_ANSI_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
                "e": "\x1b", "E": "\x1b", "\\": "\\", "'": "'", '"': '"', "?": "?"}
STDIN_COPIERS = {"cat", "tee", "dd"}
KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "{", "!"}
WRAPPERS = {"sudo", "doas", "env", "time", "nice", "command", "exec", "stdbuf", "nohup", "timeout"}
WRAPPER_ARG_OPTS = {
    "sudo": {"-u", "-g", "-h", "-p", "-r", "-t", "-U", "-C", "-D", "-R", "-T", "-c",
             "--user", "--group", "--host", "--prompt", "--role", "--type", "--other-user",
             "--close-from", "--chdir", "--chroot", "--command-timeout", "--login-class"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "-a", "--unset", "--chdir", "--split-string", "--argv0"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "exec": {"-a"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "time": {"-f", "-o", "--format", "--output"}}
MAX_SUBST_DEPTH = 8


class Word(tuple):
    """(text, plain, dynamic): plain = no quoting/escape/substitution in it. ``assign`` is
    True when the word begins with an unquoted ``NAME=`` (``FOO="a b"``, ``X=$(date)``),
    whatever the quoting of its value."""

    def __new__(cls, text, plain, dyn, assign=False):
        self = tuple.__new__(cls, (text, plain, dyn))
        self.assign = assign
        return self


def _qstep(st: list, s: str, i: int):
    """One step of shell quote tracking at ``s[i]``. ``st`` is the stack of open contexts:
    ``'`` single quotes, ``"`` double quotes, ``{`` a ``${ }`` inside double quotes. Inside
    ``${ }`` a double quote starts fresh (bash accepts ``"${y:-"a > b"}"``), and a single
    quote is an ordinary character anywhere inside double quotes. Returns the index after the
    quote syntax consumed (a quote, ``${``, the closing ``}``, a backslash pair), or None when
    ``s[i]`` is an ordinary character in the current context."""
    ch = s[i]
    top = st[-1] if st else None
    if top == "'":
        if ch == "'":
            st.pop()
            return i + 1
        return None
    if ch == "\\":
        return min(i + 2, len(s))
    if ch == "'":
        if top is None:
            st.append("'")
            return i + 1
        return None
    if ch == '"':
        if top == '"':
            st.pop()
        else:
            st.append('"')
        return i + 1
    if top is not None and ch == "$" and s.startswith("${", i):
        st.append("{")
        return i + 2
    if top == "{" and ch == "}":
        st.pop()
        return i + 1
    return None


def _blank_quotes(s: str) -> str:
    """s with quoted content and escaped characters blanked (offsets kept). The outermost
    quote characters stay; everything inside them, nested quotes and ``${ }`` included, goes."""
    out, st, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and (not st or st[-1] != "'") and i + 1 < len(s):
            out.append(" " + ("\n" if s[i + 1] == "\n" else " "))
            i += 2
            continue
        before = len(st)
        j = _qstep(st, s, i)
        if j is None:
            out.append(" " if st else ch)
            i += 1
            continue
        outer = before == 0 or not st
        out.append(s[i:j] if outer else " " * (j - i))
        i = j
    return "".join(out)


def _comment_start(line: str) -> int:
    """Index of the unquoted ``#`` that begins a comment in this one line, else len."""
    st, i = [], 0
    while i < len(line):
        j = _qstep(st, line, i)
        if j is not None:
            i = j
            continue
        if not st and line[i] == "#" and (i == 0 or line[i - 1] in " \t;|&()"):
            return i
        i += 1
    return len(line)


def _split_spans(cmd: str, steps=None) -> list:
    """(start, end) of each segment: split on ; | && || and newlines outside quotes.
    A backslash escape, a ``>|`` and a process substitution group do not split, and
    a comment (unquoted ``#`` at a word start) is skipped to the end of its line."""
    spans, start, st, i = [], 0, [], 0
    if steps is None:
        steps = [40 * len(cmd) + 10000]

    def push(end):
        if cmd[start:end].strip():
            spans.append((start, end))
    while i < len(cmd):
        j = _qstep(st, cmd, i)
        if j is not None:
            i = j
            continue
        ch = cmd[i]
        if st:
            pass
        elif ch == "#" and (i == 0 or cmd[i - 1] in " \t;|&()\n"):
            nl = cmd.find("\n", i)
            i = len(cmd) if nl < 0 else nl
            continue
        elif ch in "<>" and cmd[i + 1:i + 2] == "(":
            e = _match_paren(cmd, i + 2, steps)
            if e is not None:
                i = e + 1
                continue
        elif ch == "|" and i and cmd[i - 1] == ">":
            pass                                  # `>|`: a clobbering redirect
        elif ch in ";\n|":
            push(i)
            if cmd[i:i + 2] == "||":
                i += 1
            start = i + 1
        elif cmd[i:i + 2] == "&&":
            push(i)
            i += 1
            start = i + 1
        i += 1
    push(len(cmd))
    return spans


def _match_paren(s: str, i: int, steps: list):
    """Index of the ')' closing the '(' just before i, or None. ``steps[0]`` is the
    scan allowance left for the whole command (unclosed ``$(`` repeated thousands
    of times would otherwise rescan the tail each time); None when it runs out."""
    depth, st = 1, []
    while i < len(s):
        steps[0] -= 1
        if steps[0] < 0:
            return None
        j = _qstep(st, s, i)
        if j is not None:
            i = j
            continue
        ch = s[i]
        if st:
            pass
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _subst_spans(s: str, steps=None, heredoc: bool = False) -> list:
    """[(start, end, inner)] of the outermost command substitutions, ``$( )`` and
    backticks, not inside single quotes. ``$(( ))`` is arithmetic, not one. ``steps[0]`` is
    the scan allowance (negative once spent: the list is then incomplete)."""
    spans, i, st = [], 0, []
    if steps is None:
        steps = [40 * len(s) + 10000]
    while i < len(s):
        if steps[0] < 0:
            break
        if heredoc and not st and s[i] in "'\"":     # quotes are literal in a heredoc body
            i += 1
            continue
        j = _qstep(st, s, i)
        if j is not None:
            i = j
            continue
        ch = s[i]
        if st and st[-1] == "'":
            pass
        elif ch == "$" and s.startswith("$(", i) and not s.startswith("$((", i):
            e = _match_paren(s, i + 2, steps)
            if e is not None:
                spans.append((i, e + 1, s[i + 2:e]))
                i = e + 1
                continue
        elif ch == "`":
            j = i + 1
            while j < len(s) and s[j] != "`":
                j += 2 if s[j] == "\\" else 1
            if j < len(s):
                # inside backticks a backslash quotes only $ ` \ (and " inside double quotes)
                inner = (BACKTICK_DQ_ESC if st and st[-1] == '"' else BACKTICK_ESC).sub(r"\1", s[i + 1:j])
                spans.append((i, j + 1, inner))
                i = j + 1
                continue
        i += 1
    return spans


def _mask(s: str, spans: list) -> str:
    """s with each span blanked, offsets kept."""
    out = list(s)
    for a, b, *_ in spans:
        out[a:b] = " " * (b - a)
    return "".join(out)


def _blank_arith(line: str) -> str:
    """The line with every $(( )) / (( )) span blanked, keeping offsets."""
    return ARITH.sub(lambda m: " " * len(m.group(0)), line)


def _blank_spans(raw: str, spans) -> str:
    """raw with each (start, end) span blanked, offsets kept."""
    out = list(raw)
    for a, b in spans:
        out[a:b] = " " * (b - a)
    return "".join(out)


def _bracket_spans(scan: str) -> list:
    """(start, end) of each ``[[ ... ]]`` test in ``scan``: one forward scan, each ``[[`` is
    paired with the next ``]]`` once (an unterminated ``[[`` ends the scan, since no later
    one can close either), so it is linear however many ``[[`` there are."""
    spans, pos = [], 0
    while True:
        a = scan.find("[[", pos)
        if a < 0:
            return spans
        b = scan.find("]]", a + 2)
        if b < 0:
            return spans
        spans.append((a, b + 2))
        pos = b + 2


def _nested_spans(scan: str, tokens, dollar: str, plain: str) -> list:
    """Outermost (start, end) of each ``dollar``-opened group (``${ }``, ``$[ ]``) in
    ``scan``, balanced against the bare ``plain`` opener inside it. One pass; nesting past
    MAX_NEST is not tracked. An unclosed group claims nothing."""
    stack, over, found = [], 0, []
    for m in tokens.finditer(scan):
        t = m.group()
        if t == dollar or t == plain:
            if len(stack) >= MAX_NEST:
                over += 1
            else:
                stack.append((m.start(), t == dollar))
        elif over:
            over -= 1
        elif stack:
            start, is_dollar = stack.pop()
            if is_dollar:
                found.append((start, m.end()))
    found.sort()
    out, end = [], -1
    for a, b in found:
        if a >= end:
            out.append((a, b))
            end = b
    return out


def _expansion_spans(scan: str) -> list:
    """Parameter expansions ``${ }`` and legacy arithmetic ``$[ ]``: a ``>`` (or ``;``,
    ``|``) inside is not shell syntax."""
    return sorted(_nested_spans(scan, BRACE_TOKENS, "${", "{")
                  + _nested_spans(scan, SQUARE_TOKENS, "$[", "["))


def _ansi_c(s: str) -> str:
    """``$'...'`` rewritten as ordinary single quotes (a ``\\'`` inside becomes ``'"'"'``,
    ``\\\\`` a backslash), so the quote tracking below sees one quoted word. Other escapes
    (``\\n``) stay as written. Outside quotes only; an unterminated ``$'`` is left alone."""
    if "$'" not in s:
        return s
    out, i, n, st = [], 0, len(s), []
    while i < n:
        ch = s[i]
        if ch == "\\" and (not st or st[-1] != "'"):
            out.append(s[i:i + 2])
            i += 2
        elif ch == "$" and not st and s.startswith("$'", i):
            j, buf = i + 2, ["'"]
            while j < n and s[j] != "'":
                if s[j] == "\\" and j + 1 < n:
                    c = s[j + 1]
                    buf.append("'\"'\"'" if c == "'" else "\\" if c == "\\" else c if c == '"' else s[j:j + 2])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            if j >= n:                          # unterminated: not ours to rewrite
                out.append(ch)
                i += 1
            else:
                out.append("".join(buf) + "'")
                i = j + 1
        else:
            j = _qstep(st, s, i) or i + 1
            out.append(s[i:j])
            i = j
    return "".join(out)


def _ansi_decode(t: str) -> str:
    """The value of the body of a ``$'...'`` word."""
    def one(m):
        e = m.group(1)
        if e[0] == "x" and len(e) > 1:
            return chr(int(e[1:], 16))
        if e[0] in "01234567":
            return chr(int(e, 8) & 0xFF)
        return _ANSI_SIMPLE.get(e, "\\" + e)
    return ANSI_ESC.sub(one, t)


def _tag_word(line: str, j: int):
    """(delimiter, end, quoted) for the shell word at ``line[j:]`` after ``<<``: quote removal
    gives the delimiter (``E"O"F`` -> ``EOF``, ``'a b'`` -> ``a b``, ``\\EOF`` -> ``EOF``,
    ``$'E\\x4fF'`` -> ``EOF``, ``$"EOF"`` -> ``EOF``); any other character up to whitespace or
    ``; & | ( ) < >`` belongs to it (``EOF-1``, ``a.txt``, ``1``). ``quoted`` is True when any
    part was quoted or escaped (the body is then not expanded). None when there is no word, it
    is not closed, it is one of our own markers, or it is longer than MAX_TAG."""
    buf, k, n, any_, quoted = [], j, len(line), False, False
    if line.startswith("\x01", j):
        return None
    while k < n:
        c = line[k]
        if c in " \t;&|()<>":
            break
        any_ = True
        if c == "\\":
            if k + 1 >= n:
                return None
            buf.append(line[k + 1])
            quoted = True
            k += 2
        elif line.startswith("$'", k):
            e = k + 2
            while e < n and line[e] != "'":
                e += 2 if line[e] == "\\" else 1
            if e >= n:
                return None
            buf.append(_ansi_decode(line[k + 2:e]))
            quoted = True
            k = e + 1
        elif c == "'":
            e = line.find("'", k + 1)
            if e < 0:
                return None
            buf.append(line[k + 1:e])
            quoted = True
            k = e + 1
        elif c == '"' or line.startswith('$"', k):
            k += 2 if c == "$" else 1
            while k < n and line[k] != '"':
                if line[k] == "\\" and k + 1 < n and line[k + 1] in '"\\$`':
                    k += 1
                buf.append(line[k])
                k += 1
            if k >= n:
                return None
            quoted = True
            k += 1
        else:
            buf.append(c)
            k += 1
        if sum(len(x) for x in buf) > MAX_TAG:
            return None
    if not any_:
        return None
    return "".join(buf), k, quoted


def _bq_line(line: str, st: list) -> str:
    """``line`` with quoted text blanked (offsets kept), the quote state ``st`` carried in from
    the lines before and updated, so a string opened on an earlier line still hides a ``<<``.
    Contexts: ``'`` and ``"`` quotes, ``{`` a ``${ }`` inside double quotes, and ``(`` a
    ``$( )`` inside double quotes, where quoting starts fresh and the text is code again (so
    ``echo "$(cat <<E`` introduces a heredoc)."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        top = st[-1] if st else None
        code = top is None or top == "("
        if top == "'":
            if ch == "'":
                st.pop()
                out.append("'" if not st or st[-1] == "(" else " ")
            else:
                out.append(" ")
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append("  ")
            i += 2
            continue
        if code:
            if ch in "'\"":
                st.append(ch)
                out.append(ch)
            elif top == "(" and ch == "(":
                st.append("(")
                out.append(ch)
            elif top == "(" and ch == ")":
                st.pop()
                out.append(ch if not st or st[-1] == "(" else " ")
            else:
                out.append(ch)
            i += 1
            continue
        if top == '"':
            if ch == '"':
                st.pop()
                out.append('"' if not st or st[-1] == "(" else " ")
                i += 1
            elif line.startswith("${", i):
                st.append("{")
                out.append("  ")
                i += 2
            elif line.startswith("$(", i) and not line.startswith("$((", i):
                st.append("(")
                out.append("$(")
                i += 2
            else:
                out.append(" ")
                i += 1
            continue
        # top == "{": a ${ } inside double quotes, where a double quote starts fresh
        if ch == "}":
            st.pop()
        elif ch == '"':
            st.append('"')
        out.append(" ")
        i += 1
    return "".join(out)


def _strip_comments(cmd: str) -> str:
    """``cmd`` with every comment (an unquoted ``#`` at a word start, to the end of its line)
    blanked, offsets kept; the quote state is carried across lines, so a ``#`` inside a string
    that spans lines is text. A ``$( )`` or backtick in a comment is then never read."""
    if "#" not in cmd:
        return cmd
    out, st = [], []
    for line in cmd.split("\n"):
        bq = _bq_line(line, st)
        cut = -1
        for k in range(len(bq)):
            if bq[k] == "#" and (k == 0 or bq[k - 1] in " \t;|&()"):
                cut = k
                break
        out.append(line if cut < 0 else line[:cut] + " " * (len(line) - cut))
        if cut >= 0:
            st.clear()                  # a comment ends the line's quoting too
    return "\n".join(out)


def _arith_spans(scan: str) -> list:
    """(start, end) of each ``$(( ))`` / ``(( ))`` in ``scan`` (quotes already blanked), with
    any parenthesis nesting inside: one pass with a stack, so it is linear."""
    stack, spans, i, n = [], [], 0, len(scan)
    while i < n:
        ch = scan[i]
        if ch == "(" and scan.startswith("((", i):
            st0 = i - 1 if i and scan[i - 1] == "$" else i
            stack.append(("A", st0))
            stack.append(("p", i + 1))
            i += 2
            continue
        if ch == "(":
            stack.append(("p", i))
        elif ch == ")" and stack:
            kind, at = stack.pop()
            if kind == "A":
                spans.append((at, i + 1))
        i += 1
    return spans


def _owner_head(bq: str, a: int, line: str = "") -> str:
    """The text of the simple command that owns the ``<<`` at ``a``: back to the last unquoted
    ``;``, ``&``, ``|`` or ``(`` before it (found in the quote-blanked ``bq``, returned from the
    original ``line`` so quoted values survive)."""
    cut = max(bq.rfind(c, 0, a) for c in ";&|(")
    return (line or bq)[cut + 1:a]


PY_CMD = re.compile(r"(?:.*/)?(?:python|pypy)[0-9.]*\Z")
PY_STD_PREFIX = {"sudo", "doas", "env", "time", "nice", "command", "exec", "stdbuf", "nohup"}


PY_ARG_OPTS = {"-W", "-X", "-Q"}                 # python options that take a value
PY_PROGRAM_OPTS = {"-c", "-m"}                   # the program is not stdin


def _python_reads_stdin(head: str) -> bool:
    """True when the simple command ``head`` (the text before its ``<<``) is a Python
    interpreter reading its program from stdin: after assignments and the wrappers (with their
    options and option values, the same table the main reader uses) the command word is
    ``python``/``python3.x``/``pypy``, and a ``-`` comes after its options."""
    import shlex
    try:
        words = shlex.split(head)
    except ValueError:
        words = head.split()
    k, n = 0, len(words)
    while k < n:
        w = words[k]
        if ASSIGN.match(w):
            k += 1
            continue
        base = w.rsplit("/", 1)[-1]
        if base in PY_STD_PREFIX:
            k += 1
            opts = WRAPPER_ARG_OPTS.get(base, set())
            while k < n and words[k].startswith("-") and words[k] != "-":
                if words[k] == "--":
                    k += 1
                    break
                k += 2 if words[k] in opts else 1
            continue
        break
    if k >= n or not PY_CMD.match(words[k]):
        return False
    k += 1
    while k < n:
        w = words[k]
        if w == "-":
            return True
        if w in PY_PROGRAM_OPTS or not w.startswith("-"):
            return False
        k += 2 if w in PY_ARG_OPTS else 1
    return False


def _heredocs(cmd: str, base: int = 0):
    """Yield (owner_head, body, quoted) and return the command with each marker replaced by
    ``<<\x01N\x01`` (N = base + index of its heredoc) and bodies removed, as bash reads them:
    the quote state carries across lines (a ``<<`` inside a string that spans lines is text),
    a backslash-newline joins two lines of the command, ``<<`` inside ``$(( ))``, ``(( ))``,
    ``${ }`` or ``$[ ]`` is a shift, not a heredoc, a line may introduce several heredocs whose
    bodies follow in order, a terminator is the line that equals the delimiter exactly
    (``<<-``: after removing leading TABs only), and a heredoc whose terminator never comes
    runs to the end of the command (its body is all the rest)."""
    lines = cmd.split("\n")
    maps = None                         # (exact, tabless): line -> ascending line numbers
    rest, found, i, st = [], [], 0, []
    while i < len(lines):
        piece = lines[i]
        lparts, bparts = [piece], [_bq_line(piece, st)]
        # a backslash-newline joins the lines; each new piece is scanned once, with the quote
        # state carried on, so joining stays linear however many lines are joined
        while piece.endswith("\\") and i + 1 < len(lines) and (not st or st[-1] != "'") \
                and (len(piece) - len(piece.rstrip("\\"))) % 2 == 1:
            i += 1
            lparts[-1] = lparts[-1][:-1]
            bparts[-1] = bparts[-1][:-1]
            piece = lines[i]
            lparts.append(piece)
            bparts.append(_bq_line(piece, st))
        line = "".join(lparts)
        bq = "".join(bparts)
        ops, pos = [], 0
        if "<<" in bq:
            cut = len(bq)
            for k2 in range(len(bq)):
                if bq[k2] == "#" and (k2 == 0 or bq[k2 - 1] in " \t;|&()"):
                    cut = k2
                    break
            scan = _blank_spans(bq, _arith_spans(bq) + _expansion_spans(bq))
            while True:
                m = HEREDOC_OP.search(scan, pos)
                if not m or m.start() >= cut:
                    break
                j = m.start() + 2 + len(m.group(1))           # measured on the line itself:
                while j < len(line) and line[j] in " \t":       # the scan blanks escapes
                    j += 1
                w = _tag_word(line, j)
                if w is None:
                    pos = m.end()
                    continue
                ops.append((m.start(), w[1], bool(m.group(1)), w[0], w[2]))
                pos = w[1]
        if not ops:
            rest.append(line)
            i += 1
            continue
        if maps is None:
            exact, tabless = {}, {}
            for k, ln in enumerate(lines):
                if len(ln) <= MAX_TERM_LINE:
                    exact.setdefault(ln, []).append(k)
                    tabless.setdefault(ln.lstrip("\t"), []).append(k)
            maps = (exact, tabless)
        k, taken = i + 1, []
        for a, b, dash, tag, quoted in ops:
            if not quoted:
                # an unquoted body: bash joins a backslash-newline before it tests a line for the
                # terminator, and `\\`, `\$`, `` \` `` lose their backslash; read line by line
                body, j, ended = [], k, False
                while j < len(lines):
                    parts = [lines[j].lstrip("\t") if dash else lines[j]]
                    while parts[-1].endswith("\\") and (len(parts[-1]) - len(parts[-1].rstrip("\\"))) % 2 == 1 \
                            and j + 1 < len(lines):
                        parts[-1] = parts[-1][:-1]
                        j += 1
                        parts.append(lines[j])         # <<- strips TABs only where a logical line starts
                    logical = "".join(parts)
                    j += 1
                    if logical == tag:
                        ended = True
                        break
                    body.append(BODY_ESC.sub(r"\1", logical))
                taken.append((a, b, "\n".join(body), quoted))
                k = j
                if not ended:
                    break
                continue
            at = maps[1 if dash else 0].get(tag, [])
            x = bisect.bisect_left(at, k)
            end = at[x] if x < len(at) else len(lines)     # unterminated: the body is all the rest
            body = lines[k:end]
            if dash:
                body = [ln.lstrip("\t") for ln in body]
            taken.append((a, b, "\n".join(body), quoted))
            k = end + 1
            if x >= len(at):
                break
        out, last = [], 0
        for a, b, body, quoted in taken:
            found.append((_owner_head(bq, a, line), body, quoted))
            out.append(line[last:a] + f"<<\x01{base + len(found) - 1}\x01")
            last = b
        rest.append("".join(out) + line[last:])
        i = k
    return found, "\n".join(rest)


class GuessedPath(str):
    """A literal from a python body taken for a path only because no write target was found. It
    may be any string, a credential included, so it is matched but never written to the log."""


def _python_writes(body: str) -> list:
    """Paths a ``python -`` body writes: one pass collects the ``NAME = Path('..')``
    assignments, one pass the names used as ``NAME.write_text(`` / ``open(NAME, 'w'``, and a
    dict lookup joins them, so the cost is linear in the body however many names it holds."""
    if not PY_WRITE_CALL.search(body):
        return []
    used = {m.group(1) or m.group(2) for m in PY_VAR_WRITE.finditer(body)}
    targets = []
    for var, lit in PY_PATH_VAR.findall(body):
        if var in used:
            targets.append(lit)
    for a, b in PY_DIRECT.findall(body):
        targets.append(a or b)
    if not targets:
        targets = [GuessedPath(p) for p in PY_LITERAL_PATH.findall(body)]
    return targets


# ------------------------------------------------------------------ tokenizer

class _Cmd:
    """One simple command: its words and its redirections."""
    __slots__ = ("words", "redirs", "heredoc", "piped", "group")

    def __init__(self):
        self.words: list = []          # [Word]
        self.redirs: list = []         # [(op, fd or None, Word or None)]
        self.heredoc = None            # index of the heredoc feeding it
        self.piped = False             # its stdin is the previous command's stdout (`a | b`)
        self.group = False             # redirections that follow a closed `( )` group

    def empty(self) -> bool:
        return not self.words and not self.redirs


def _tokenize(text: str, off: int, raw: str, starts: dict, procs: list, steps: list) -> list:
    """The simple commands in ``text`` (a segment of ``raw`` at offset ``off``).
    ``starts`` maps the start of each command substitution in ``raw`` to its end; a
    substitution inside a word is kept as raw text and makes the word dynamic. A
    process substitution is a dynamic word, its inner text appended to ``procs``."""
    n = len(text)
    cmds, cur = [], _Cmd()

    def read_word(i):
        buf, plain, dyn, started = [], True, False, False
        lit, lit_open = [], True        # the unquoted literal prefix: where `NAME=` is looked for
        while i < n:
            ch = text[i]
            if off + i in starts and ch in "$`(":
                end = starts[off + i]
                buf.append(raw[off + i:end])
                plain, dyn, started, lit_open = False, True, True, False
                i = end - off
                continue
            if ch == "\\":
                if i + 1 < n and text[i + 1] == "\n":      # a line continuation joins the lines
                    i += 2
                    continue
                plain, started, lit_open = False, True, False
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2
                continue
            if ch == "'":
                plain, started, lit_open = False, True, False
                j = text.find("'", i + 1)
                if j < 0:
                    j = n
                buf.append(text[i + 1:j])
                i = j + 1
                continue
            if ch == "$" and text[i + 1:i + 2] == '"':
                i += 1                              # `$"..."`: a locale string, the `$` is not text
                continue
            if ch == '"':
                plain, started, lit_open = False, True, False
                i += 1
                st = ['"']                          # `${ }` inside holds quotes that start fresh
                while i < n and st:
                    c = text[i]
                    if off + i in starts and c in "$`(":
                        end = starts[off + i]
                        buf.append(raw[off + i:end])
                        dyn = True
                        i = end - off
                        continue
                    if c == "\\" and i + 1 < n:
                        buf.append(text[i + 1] if st[-1] == '"' and text[i + 1] in '"\\$`'
                                   else text[i:i + 2])
                        i += 2
                        continue
                    if c == '"':
                        if st[-1] == '"':
                            st.pop()
                            if st:
                                buf.append(c)       # a nested quote is kept as the expansion's text
                        else:
                            st.append('"')
                            buf.append(c)
                        i += 1
                        continue
                    if c == "$" and text[i + 1:i + 2] == "{":
                        st.append("{")
                        buf.append("${")
                        i += 2
                        continue
                    if c == "}" and st[-1] == "{":
                        st.pop()
                    buf.append(c)
                    i += 1
                continue
            if ch in " \t\n;|&()<>":
                break
            buf.append(ch)
            if lit_open:
                lit.append(ch)
            started = True
            i += 1
        if not started:
            return None, i
        return Word("".join(buf), plain, dyn, bool(ASSIGN.match("".join(lit)))), i

    def procsub(i):
        """Word for ``>( )`` / ``<( )`` at i, and the index after it, or None."""
        e = _match_paren(text, i + 2, steps)
        if e is None:
            return None
        procs.append(text[i + 2:e])
        return Word("".join(text[i:e + 1]), False, True), e + 1

    def redirect(i, fd):
        """Parse the redirection operator at i; returns the index after its target."""
        c = text[i]
        if c == "&":
            op, j = ("&>>", i + 3) if text[i + 2:i + 3] == ">" else ("&>", i + 2)
        elif c == ">":
            nx = text[i + 1:i + 2]
            op, j = {">": (">>", i + 2), "|": (">|", i + 2), "&": (">&", i + 2)}.get(nx, (">", i + 1))
        else:
            for cand in ("<<<", "<<-", "<<", "<&", "<>"):
                if text.startswith(cand, i):
                    op, j = cand, i + len(cand)
                    break
            else:
                op, j = "<", i + 1
        while j < n and text[j] in " \t":
            j += 1
        w, j = read_word(j)
        cur.redirs.append((op, fd, w))
        if op in ("<<", "<<-") and w is not None:
            m = HEREDOC_MARK.match(w[0])
            if m:
                cur.heredoc = int(m.group(1))
        return j

    def flush():
        nonlocal cur
        if not cur.empty():
            cmds.append(cur)
        cur = _Cmd()

    i, closed = 0, False
    while i < n:
        ch = text[i]
        after_group = closed and cur.empty()        # `( ... ) > f`: the redirect is the group's
        if ch not in " \t":
            closed = False
        if ch in " \t":
            i += 1
        elif ch == "#":                             # a comment: the rest is not shell
            break
        elif ch == "&" and text[i + 1:i + 2] == ">":
            cur.group = after_group
            i = redirect(i, None)
        elif ch in ";&()|\n":
            flush()
            closed = ch == ")"
            i += 1
        elif ch == ">" and i and text[i - 1] == "-":
            i += 1                                  # `->`, prose: bash would redirect, this does not claim it
        elif ch in "<>":
            if text[i + 1:i + 2] == "(":
                ps = procsub(i)
                if ps is not None:
                    cur.words.append(ps[0])
                    i = ps[1]
                    continue
            cur.group = after_group
            i = redirect(i, None)
        else:
            w, j = read_word(i)
            if w is None:                           # a stray character or a continuation: step over it
                i = max(j, i + 1)
            elif w[1] and w[0].isdigit() and j < n and text[j] in "<>" and \
                    text[j + 1:j + 2] != "(":
                i = redirect(j, int(w[0]))
            else:
                cur.words.append(w)
                i = j
    flush()
    return cmds


# ------------------------------------------------------------------ commands

def _base(word: Word) -> str:
    t = word[0]
    return t.rsplit("/", 1)[-1] if t.startswith("/") and word[1] else t


def _skip_wrapper(words: list, i: int, base: str) -> int:
    """Index after the wrapper at words[i] and its options/assignments; len(words)
    when it only asks about the command (``command -v``)."""
    j, takes = i + 1, WRAPPER_ARG_OPTS.get(base, set())
    while j < len(words):
        t = words[j][0]
        if t == "--":
            return j + 1
        if getattr(words[j], "assign", False) and base in ("env", "sudo", "doas"):
            j += 1
        elif t.startswith("-") and len(t) > 1:
            if base == "command" and t in ("-v", "-V"):
                return len(words)
            j += 2 if t in takes else 1
        else:
            if base == "timeout":      # its first operand is the duration
                j += 1
            break
    return j


def _command_index(words: list) -> int:
    """Index of the command word: past keywords, assignments and wrappers."""
    i = 0
    while i < len(words):
        t, plain, _ = words[i]
        if getattr(words[i], "assign", False):      # `FOO=1`, `FOO="a b"`, `FOO=$(date)`
            i += 1
        elif not plain:
            break
        elif t in KEYWORDS:
            i += 1
        elif t == "function":
            i += 2
        elif _base(words[i]) in WRAPPERS:
            i = _skip_wrapper(words, i, _base(words[i]))
        else:
            break
    return i


def _text(w: Word) -> str:
    return w[0]


def _stdout_dests(redirs: list) -> list:
    """Where this command's stdout goes, one entry per redirection that moves it, in order:
    a static path, or None (a dynamic target, or a dup to another descriptor). Bash applies
    them left to right, so only the last one receives what the command writes."""
    out = []
    for op, fd, w in redirs:
        dyn = w is None or w[2]
        if op in (">", ">>", ">|") and fd in (None, 1) or op in ("&>", "&>>"):
            out.append(None if dyn else w[0])
        elif op == ">&" and fd in (None, 1):
            if dyn:
                out.append(None)
            elif w[0].isdigit() or w[0].startswith("-"):
                if w[0] != "1":
                    out.append(None)         # `>&2`, `>&-`: stdout leaves the files; `>&1` is a no-op
            else:
                out.append(w[0])             # `>& f`: both streams to f
    return out


def _stdout_targets(redirs: list) -> list:
    """Static paths this command's redirections send stdout to."""
    return [d for d in _stdout_dests(redirs) if d is not None]


def _written(redirs: list, content: str) -> list:
    """[(path, content)] for the stdout redirections: the last one receives ``content``;
    an earlier one is created empty (``echo a >y >z`` writes the text to z only)."""
    dests = _stdout_dests(redirs)
    out = []
    for n, d in enumerate(dests):
        if d is None:
            continue
        last = n == len(dests) - 1
        if not last and d in dests[n + 1:]:
            continue                          # the same file again later: that one decides
        out.append((d, content if last else ""))
    return out


def _cat_reads_stdin(args: list) -> bool:
    """False when cat has a file operand (``cat file``): it ignores its stdin then. Options
    and ``-`` do not count (``cat -n`` and ``cat -`` read stdin); a dynamic operand does."""
    opts = True
    for t, plain, _dyn in args:
        if opts and t == "--" and plain:
            opts = False
        elif (opts and t.startswith("-")) or t == "-":
            continue
        else:
            return False
    return True


def _echo_text(base: str, args: list) -> str:
    args = [a for a in args]
    if base == "echo":
        while args and args[0][1] and re.fullmatch(r"-[neE]+", args[0][0]):
            args = args[1:]
    return " ".join(a[0] for a in args)


def _tee_files(args: list) -> list:
    """Every non-option operand of tee that is a static name."""
    files, opts = [], True
    for t, plain, dyn in args:
        if opts and t == "--" and plain:
            opts = False
        elif opts and t.startswith("-") and t != "-":
            continue
        elif not dyn:
            files.append(t)
    return files


SED_SHORT_NOARG = set("nsuzEbr")        # -r is GNU's -E; -b/--binary; -s/-u/-z
SED_LONG_NOARG = ["debug", "sandbox", "follow-symlinks", "binary", "regexp-extended", "posix",
                  "quiet", "silent", "separate", "unbuffered", "null-data", "help", "version"]
SED_LONG_ARG = ["expression", "file", "line-length"]


def _sed_in_place(args: list) -> list:
    """[(file, expressions)] when this sed (its arguments, past the command word) edits
    in place. GNU ``-i[SUFFIX]``/``--in-place[=SUFFIX]`` and BSD ``-i ''``."""
    inplace, exprs, files, script_given = False, [], [], False
    operands, i, opts = [], 0, True
    while i < len(args):
        t, _plain, dyn = args[i]
        i += 1
        if not opts or not t.startswith("-") or t == "-":
            operands.append(t)
            continue
        if t == "--":
            opts = False
            continue
        if t.startswith("--"):
            name, eq, val = t[2:].partition("=")
            cands = [c for c in SED_LONG_NOARG + SED_LONG_ARG + ["in-place"] if c.startswith(name)]
            if name in cands:
                cands = [name]
            if len(cands) != 1:
                return []                      # an option we do not know: claim nothing
            c = cands[0]
            if c == "in-place":
                inplace = True
            elif c in SED_LONG_ARG:
                if not eq:
                    if i >= len(args):
                        return []
                    val, i = args[i][0], i + 1
                if c == "expression":
                    exprs.append(val)
                script_given = True if c in ("expression", "file") else script_given
            continue
        j = 1
        while j < len(t):
            c = t[j]
            if c == "i":
                inplace = True
                if j + 1 == len(t) and i < len(args) and args[i][0] == "":
                    i += 1                     # BSD: `-i ''`, the suffix is the next argument
                break                          # GNU: the rest of this word is the suffix
            if c in "efl":
                val = t[j + 1:]
                if not val:
                    if i >= len(args):
                        return []
                    val, i = args[i][0], i + 1
                if c == "e":
                    exprs.append(val)
                    script_given = True
                elif c == "f":
                    script_given = True
                break
            if c in SED_SHORT_NOARG:
                j += 1
                continue
            return []                          # an option we do not know
    if not inplace:
        return []
    if not script_given:
        if not operands:
            return []
        exprs.append(operands[0])
        operands = operands[1:]
    return [(f, "\n".join(exprs)) for f in operands]


COPIERS = {"cp", "mv", "install", "ln"}
SHELLS = {"bash", "sh", "zsh", "dash", "su"}


def _copy_dest(base: str, args: list) -> list:
    """The destination of cp, mv, install or ln: ``-t DIR`` (clustered or glued too) or
    ``--target-directory=DIR``, else the last of two or more operands; every operand of
    ``install -d``. One operand (``ln -s a``) names no destination we can see."""
    takes = "tSmog" if base == "install" else "tS"   # short options that take a value
    operands, opts, i, dirs = [], True, 0, False
    while i < len(args):
        t, plain, dyn = args[i]
        i += 1
        if opts and t == "--" and plain:
            opts = False
        elif opts and t.startswith("--target-directory="):
            return [t.split("=", 1)[1]]
        elif opts and t == "--target-directory":
            return [args[i][0]] if i < len(args) and not args[i][2] else []
        elif opts and t.startswith("--"):
            dirs = dirs or t == "--directory"
        elif opts and t.startswith("-") and t != "-":
            for j, c in enumerate(t[1:], 1):
                dirs = dirs or (c == "d" and base == "install")
                if c in takes:
                    value = t[j + 1:] or (args[i][0] if i < len(args) and not args[i][2] else None)
                    if c == "t":
                        return [value] if value else []
                    if not t[j + 1:]:
                        i += 1
                    break
        else:
            operands.append((t, dyn))
    if dirs:
        return [t for t, dyn in operands if not dyn]
    return [operands[-1][0]] if len(operands) >= 2 and not operands[-1][1] else []


CURL_ARG_FLAGS = set("AbcCdDeEFhHKmoPQrtTuUwxXyYz")
WGET_ARG_FLAGS = set("aABDegiIloOPQRtTUwXY")


def _fetch_dest(base: str, args: list) -> list:
    """Where curl -o/--output or wget -O/--output-document saves. A short cluster is walked
    letter by letter, so a value glued to another flag (``-H'a: o'``) is not read as a name.
    ``-`` means stdout."""
    short, long = ("o", "--output") if base == "curl" else ("O", "--output-document")
    takes = CURL_ARG_FLAGS if base == "curl" else WGET_ARG_FLAGS
    i = 0
    while i < len(args):
        t, _plain, dyn = args[i]
        i += 1
        if t == "--":
            return []
        if t.startswith(long + "="):
            value = t.split("=", 1)[1]
            return [value] if not dyn and value != "-" else []
        if t == long:
            return [args[i][0]] if i < len(args) and not args[i][2] and args[i][0] != "-" else []
        if not t.startswith("-") or t.startswith("--") or t == "-":
            continue
        for j, c in enumerate(t[1:], 1):
            if c not in takes:
                continue
            value = t[j + 1:]
            if c == short:
                if not value:
                    value = args[i][0] if i < len(args) and not args[i][2] else ""
                return [value] if value and value != "-" else []
            if not value:
                i += 1
            break
    return []


def _inner_command(base: str, args: list):
    """The command string eval, ``sh -c`` or ``su -c`` will run, when it is written out, else None."""
    if base == "eval":
        return " ".join(t for t, _p, _d in args) if args and not any(d for _t, _p, d in args) else None
    i = 0
    while i < len(args):
        t, _plain, _dyn = args[i]
        i += 1
        if t in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 1
        elif t == "--" or not t.startswith(("-", "+")):
            if base != "su":
                return None
        elif t == "--command" or (t.startswith("-") and not t.startswith("--") and t.endswith("c")):
            while i < len(args) and args[i][0] == "--":
                i += 1
            return args[i][0] if i < len(args) and not args[i][2] else None
        elif t.startswith("--command="):
            return t.split("=", 1)[1]
    return None


def _writes_of(cmd: _Cmd, incoming) -> list:
    """[(path, content)] a simple command visibly writes. ``incoming`` is the heredoc body
    arriving on its stdin (its own ``<<TAG``, or carried through a pipe from the command
    that owns one), or None; it is the content for commands that copy stdin."""
    k = _command_index(cmd.words)
    if k >= len(cmd.words):
        # `( echo a ) > f`: the group's stdout, content unknown; `exec > f`: all later output
        if cmd.group or (cmd.words and _text(cmd.words[0]) == "exec"):
            return _written(cmd.redirs, "")
        return []
    base, args = _base(cmd.words[k]), cmd.words[k + 1:]
    out = []
    if base in ("echo", "printf"):
        out += _written(cmd.redirs, _echo_text(base, args))
    elif base == "cat":
        out += _written(cmd.redirs, (incoming or "") if _cat_reads_stdin(args) else "")
    elif base == "tee":
        out += [(f, incoming or "") for f in _tee_files(args)] + _written(cmd.redirs, incoming or "")
    elif base == "sed":
        out += _sed_in_place(args)
    elif base == "}":                          # `{ echo a; } > f`: the group's stdout, content unknown
        out += _written(cmd.redirs, "")
    elif base in COPIERS:
        out += [(f, "") for f in _copy_dest(base, args)] + _written(cmd.redirs, "")
    elif base == "dd":
        out += [(t[3:], incoming or "") for t, _p, dyn in args if t.startswith("of=") and not dyn]
        out += _written(cmd.redirs, incoming or "")
    elif base in ("curl", "wget"):
        out += [(f, "") for f in _fetch_dest(base, args)] + _written(cmd.redirs, "")
    else:                                      # any other command: its stdout, content unknown
        out += _written(cmd.redirs, incoming or "" if base in STDIN_COPIERS else "")
    return out


VAR_THEN_PATH = re.compile(r"\$\{?\w+\}?/[^$`]+\Z")     # `$HOME/.mcp.json`: the rest is literal


def _keep(path: str) -> bool:
    return bool(path) and path not in SINKS and not SINK_FD.match(path) \
        and (not path.startswith("$") or bool(VAR_THEN_PATH.match(path)))


class Writes(list):
    """The writes found, as a plain list of (path, content). ``out_of_budget`` is True when a
    scan spent its allowance (a long hostile command: hundreds of unclosed ``$(``), so the list
    may be incomplete; it is then not evidence that nothing else is written."""
    out_of_budget = False


def write_targets(cmd: str) -> Writes:
    """[(path, content)] for every write visible in cmd."""
    spent = [False]
    res = Writes(_scan(cmd, 0, spent))
    res.out_of_budget = spent[0]
    return res


def _scan(cmd, _depth: int, spent: list, outer=None) -> list:
    """``outer``: the heredoc bodies of the enclosing command, which a substitution inside it
    refers to by number (``echo $(cat > f <<E ...)``)."""
    if not isinstance(cmd, str) or not cmd:
        return []
    if _depth > MAX_SUBST_DEPTH:
        spent[0] = True
        return []
    out = []
    outer = outer or []
    docs, rest = _heredocs(cmd, len(outer))
    rest = _strip_comments(_ansi_c(rest))
    for head, body, quoted in docs:
        if _python_reads_stdin(head):
            out += [(p, body) for p in _python_writes(body)]
        if not quoted and ("$(" in body or "`" in body):   # an unquoted body is expanded
            for _a, _b, inner in _subst_spans(body, heredoc=True):
                out += _scan(inner, _depth + 1, spent)
    bodies = outer + [b for _h, b, _q in docs]
    sub_steps = [40 * len(rest) + 10000]
    subs = _subst_spans(rest, sub_steps)
    scan = _blank_quotes(_mask(rest, subs))
    arith = [m.span() for m in ARITH.finditer(scan)]
    brackets = _bracket_spans(scan)
    expansions = _expansion_spans(scan)
    masked = _blank_spans(_mask(rest, subs), arith + brackets + expansions)
    work = _blank_spans(rest, brackets)
    # arithmetic and expansions are kept as raw text inside a word (`echo $((1+2)) > f`,
    # `echo ${x#*>} > f`): their `>` is not a redirect
    starts = {a: b for a, b in arith + expansions}
    starts.update({a: b for a, b, _ in subs})
    procs: list = []
    steps = [40 * len(rest) + 10000]
    split_steps = [40 * len(masked) + 10000]
    cmds = []
    for a, b in _split_spans(masked, split_steps):
        piped = a >= 2 and masked[a - 1] == "|" and masked[a - 2] != "|"
        for n, c in enumerate(_tokenize(work[a:b], a, rest, starts, procs, steps)):
            c.piped = piped and n == 0
            cmds.append(c)
    if sub_steps[0] < 0 or steps[0] < 0 or split_steps[0] < 0:
        spent[0] = True
    flow = None                                # the heredoc body on the stdout of the previous command
    for c in cmds:
        incoming = flow if c.piped else None
        if c.heredoc is not None and c.heredoc < len(bodies):
            incoming = bodies[c.heredoc]       # only the command that owns `<<TAG` is fed by it
        out += _writes_of(c, incoming)
        k = _command_index(c.words)
        base = _base(c.words[k]) if k < len(c.words) else ""
        if base == "eval" or base in SHELLS:
            inner = _inner_command(base, c.words[k + 1:])
            if inner:
                out += _scan(inner, _depth + 1, spent)
        carries = base == "tee" or (base == "cat" and _cat_reads_stdin(c.words[k + 1:]))
        flow = incoming if carries and not _stdout_dests(c.redirs) else None
    for _a, _b, inner in subs:
        out += _scan(inner, _depth + 1, spent, bodies)
    for inner in procs:
        out += _scan(inner, _depth + 1, spent, bodies)
    seen, uniq = set(), []
    for path, content in out:
        if _keep(path) and (path, content) not in seen:
            seen.add((path, content))
            uniq.append((path, content))
    return uniq
