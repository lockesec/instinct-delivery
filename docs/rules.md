# Setting up and writing rules

## Set up a project

```console
git clone https://github.com/lockesec/instinct-delivery            # Python 3.11+, PyYAML
pip install ./instinct-delivery        # a plain pip install of the git URL omits examples/
cd your-project
instinct-delivery install                    # registers the hook in .claude/settings.json
cp ../instinct-delivery/examples/instincts/no-print-debugging.yaml .claude/instincts/   # or write your own, below
instinct-delivery doctor                     # what loaded, what was rejected and why
instinct-delivery check Edit '{"file_path":"a.py","new_string":"print(1)"}'   # dry run: what one call would deliver
```

`install` adds one PreToolUse entry (`matcher: ".*"`) and keeps everything else in
the file. It **never changes the matcher or the hooks of an entry that holds a hook
that is not ours**: our hook lives in its own dedicated entry. If exactly one
dedicated entry of ours exists, its matcher stays as you left it and only the command
is upgraded. Otherwise install removes our hook object(s) from any mixed entry
(the other hooks and that entry's matcher are untouched) and writes one dedicated
entry with matcher `.*` (several dedicated entries are merged into one `.*` entry).
The matcher of an entry that holds another tool's hook is never changed. It
prints what it changed, e.g. "removed our hook from 1 mixed entry; left 1 other hook
untouched". Our command is recognised only by exact form (the bare or absolute
`instinct-delivery hook`, `<python> -m instinct_delivery hook`, or `<python> -c` with
the code of a released version verbatim); a foreign command that merely mentions this
tool is left alone. Its command is the absolute path of the Python that ran `install`, run with
`-c` code that first drops every relative `sys.path` entry (so the project directory,
where Claude Code runs hooks, is not imported from through the interpreter's own
current-directory entry) and then calls the `hook`
command. **Limit:** only relative entries are dropped. A `PYTHONPATH` that is
relative, or that names the project directory by absolute path, is still honoured,
and `PYTHONPATH` comes from the environment the hook runs in, which user or project
settings (`env`) can set: whoever controls that environment can still shadow a
module. The command works even when the
`instinct-delivery` script is not on Claude Code's PATH (a bare command there fails
with exit 127 on every call). If you later move to another Python environment, run `install` again from it: it
upgrades the existing entry in place, whichever older form (bare `instinct-delivery
hook`, `-m instinct_delivery hook`, an absolute console script) registered it.
`install --project DIR` registers in `DIR/.claude` instead of the current
directory. Claude Code reads hooks at session start: restart it after installing.
`install` refuses a settings file that is not valid JSON, is not a regular file
(a directory, or a symlink to nothing), or whose `hooks` or `hooks.PreToolUse` has
an unexpected shape (exit 2); a symlinked settings file is written through to its
target, and the file's mode is kept (a new file gets the usual umask-based mode). The
write is atomic: a fresh temporary file in the same directory, created exclusively
so a planted link at a predictable name is never followed, then renamed over the
resolved target; a failed write leaves the original and no temporary file. `install`
rewrites the file as JSON with 2-space indentation, so a hand-formatted file is
reformatted, and a repeated key in one object keeps only its last value (that is how
JSON is read, and the file is rewritten from what was read). It refuses (exit 2,
nothing changed) a settings file that is read-only (no write bit set) and one that holds
`NaN` or `Infinity`, which JSON cannot write. Before and after the write it checks
the result: exactly one entry of ours holding exactly the current command, every other
entry unchanged; if that does not hold it exits 2 and says so. Several entries of ours
are merged into one with matcher `".*"`; a single one keeps the matcher you gave it. `check` is a dry run on a throwaway
state directory (add `--record` to write the log); it always shows a first fire. Silence from `check` has three causes it tells apart: no rule
matched (nothing on stderr), a tool input that is not a JSON object (read as empty; named on
stderr), and a tool no loaded rule is scoped to (named on stderr, with the tools that are; `Bash`
also reaches rules scoped to `Write` or `Edit`). All three exit 0: silence is a valid answer.

Demo, no model needed: `sh examples/demo.sh --tour`. It installs the hook into
a throwaway project, loads the synthetic rules in
[`examples/instincts/`](../examples/instincts), and feeds the real hook a
scripted sequence of tool calls. `sh examples/demo.sh` alone plays the README
quick start.

## Writing a rule

```yaml
# .claude/instincts/no-print-debugging.yaml
trigger: Writing a print() debugging line into a module
action: |
  A bare print() in library code is debugging residue. Use the logging module,
  or remove it before committing.
match_tools: [Edit, Write]            # required; exact tool names
match_paths: [".py"]                  # substring of the path (case-sensitive)
match_content: ['(?m)^\s*print\(']    # regex over the text (case-insensitive)
match_exclude: ["tests/"]             # a substring anywhere silences the rule
confidence: 0.6                       # ranks rules when the budget is tight
domain: code-quality
status: active                        # active | candidate | retired | ...
```

| Key | Meaning |
|---|---|
| `match_tools` | Required. The rule can only fire on these tools (exact, case-sensitive). A file a Bash command visibly writes is also matched: as a `Write` for a rule scoped to `Write`, as an `Edit` for a rule scoped to `Edit` and not `Write`; a rule scoped to neither does not fire on it. Which commands count as writes, and which do not, is listed under "Bash writes" below. |
| `match_paths` | Substrings of the call's path. The path fields read are `file_path`, `notebook_path`, `path` and each `file_path` in `edits`. |
| `match_content` | Regexes, case-insensitive, over the call's text: `content`, `new_string`, `old_string`, `new_source`, `pattern`, `prompt`, `query`, `url`, `description`, the Bash `command`, the strings inside `edits`, and every string in any other argument, however nested (an MCP tool names its own arguments; up to 10,000 strings). |
| `match_commands` | Substrings of the Bash command. Bash only. |
| `match_exclude` | Substrings; any one found in the command, paths or text silences the rule, including a hit through a file the Bash command writes. Checked first. The call can contain these words on purpose, so never use this to excuse a security rule: use `match_exclude_names`. |
| `match_exclude_names` | Substrings of a path's file name, after `..` is resolved. A path whose name holds one does not count; if every path in the call is excused, the rule stays silent. Nothing else in the call can excuse it. |
| `confidence` | A finite number, or absent. Ranks rules when the budget is tight. |
| `status` | `active`/`validated` fire; `candidate` shadows; `retired`, `retiring`, `rejected`, `archived`, `expired` never load. Blank means active. |
| `follow` | What acting on the advice looks like: a matcher of its own (`tools` required; `paths`, `content`, `commands`, `exclude` as for the rule) plus `within`, how many later calls it may take (1-50, default 5). Read only by `follow-rate`; never by the hook. A malformed block rejects the rule. |
| `after` | A sequence precondition: the rule fires only if an EARLIER call of the same session matched this matcher (`tools` required; `paths`, `content`, `commands`, `exclude` as for the rule) within the last `within` calls (1-200, default 20). Read by the hook, `breadth` and `audit`. A malformed block rejects the rule. See "Order across calls: `after`" below. |
| `tests` | Cases the rule must pass: a list of `{tool, input, expect}`, `expect` being `fires` or `silent`, `input` the tool input as the hook receives it, and optionally `before`, a list of `{tool, input}` calls (at most 100) run first as the session's history. Run by `instinct-delivery test`; never read by the hook. A malformed block rejects the rule. |

**Testing a rule.** `instinct-delivery test [RULE...]` runs every `tests` case of every
loaded rule (shadow rules included) against the same matcher the hook uses, one rule at
a time, so a case tests only its own rule; a Bash case is matched the way the hook
matches it, write targets included. It prints one PASS/FAIL line per case and the
rules that carry no case (not evaluable), and exits 0 when every case passes, 1 on a
failure, 2 when no rule carries a case. `--json` gives the same as an object. Write at
least one `silent` case for the near miss you are worried about: the shipped
`force-push-main` example matched `--force-with-lease`, the very command it
recommends, until its first `silent` case caught it.

A case with `before` runs the way a live session would: the `before` calls, in order, then the
case's own call, each through the same engine path as the hook (`engine.run`), in a fresh scratch
session on a throwaway state directory, with only the rule under test loaded. `expect` judges the
case's own call only: the `before` calls are never judged, even when the rule fires on one of
them. Every case of a rule that has `after` goes through that path, with or without `before`
(the bare matcher ignores `after`, so it would call a lone write "fires"); a plain rule's
cases without `before` keep using the bare matcher. Each case gets its own session, so history
never leaks from one case to the next, and the real state directory is never touched. The PASS/FAIL
line says `(after N earlier calls)` for a case with history, and `--json` carries `before: N`.

```yaml
after:
  tools: [mcp__supabase__execute_sql]
  content: ['\bfrom\s+\w*(?:token|secret|credential|password|api_?key)']
  within: 20
tests:
  - tool: mcp__supabase__execute_sql
    input: {query: "insert into support_messages (body) values ('x')"}
    before:
      - {tool: mcp__supabase__execute_sql, input: {query: "select * from integration_tokens"}}
    expect: fires
  - {tool: mcp__supabase__execute_sql, input: {query: "insert into support_messages (body) values ('x')"}, expect: silent}
```

**Order across calls: `after`.** A rule with `after` fires on a call only when its own
`match_*` filters match that call AND an earlier call of the same session matched the `after`
matcher. The block is built exactly like `follow` (same keys, same safety checks: invalid
regexes, catastrophic backtracking, hidden characters and unknown keys reject the whole rule,
the reason prefixed `after`), with `within` from 1 to 200 (default 20).

- *Numbering.* The hook counts every call it is run for in a session: call 1, 2, 3, ... whatever
  tool it is and whether or not any rule names it. (A narrower hook `matcher` than the default
  `.*` means the hook never sees, and so never counts, the other calls.) A call numbered `i`
  satisfies the rule when the most recent call that matched `after` was `j`, with `j < i` (strictly
  earlier: one call never satisfies itself) and `i - j <= within`. A later match of the
  precondition replaces `j`, which renews the window. The precondition is checked on every
  call, whether or not another rule matched it.
- *What matches.* The precondition is matched like a rule, Bash writes included (an `after`
  scoped to `Write` is satisfied by `echo x > f`). It runs under the call's time budget; if it
  runs out, that call does not count as a precondition match and the rule's `slow` or
  `unevaluated` row says so.
- *Shadow.* A `candidate` rule with `after` logs its `shadow` row only when the precondition holds.
- *No session id.* An `after` rule cannot be evaluated without a session to remember earlier calls.
  When its own filters match such a call it does not fire, and the call logs an `unevaluated` row
  for the rule with the cause `after: no session id to remember earlier calls`; the hook says so on
  stderr and `doctor` lists the rule as `NOT EVALUATED`. A call its own filters do not match logs nothing.
- *`check`.* A dry run is a first call, so an `after` rule that matched on its own filters is not
  evaluated as firing, and `check` says so on stderr (rule named, "needs an earlier call").
  `--record --session ID` over several calls, or a `tests` case with `before`, builds the history.
- *State.* The session's existing delivery file gains one entry, `~seq`: `{"n": calls so far,
  "last": {rule file: call number of its last precondition match}}`. Counters only: no tool name,
  path, pattern or any text of a call is stored, and the activation log is unchanged. It is read
  and written on every call that has a session id when (and only when) a loaded rule has `after`;
  with no such rule nothing is read on a call that matches nothing, as before. It is kept even with
  `INSTINCT_REPEAT=full`, pruned with the other session files, and written only after the output
  reached stdout. A `~seq` entry that cannot be read (a counter that is not a whole number) reads
  as no history, and a precondition stamp ahead of the call counter (a number from the future) is
  dropped when the state is read, with a one-time "damaged delivery state" line on stderr, so it
  can never satisfy a rule, now or once the counter catches up: the precondition must be met again
  by a real call. Damaged state makes an `after` rule SILENT, the opposite direction to delivery
  state, which fails toward delivering more.
  Two hook processes of one session racing can lose a counter update, which can likewise cost a
  fire.
- *Reports.* `breadth` replays each transcript file's calls in file order with a fresh sequence
  per file (a transcript is a session), so an `after` rule's rate counts only calls that had
  their precondition; the per-rule entry carries `"after": true`. Calls are the `tool_use`
  blocks of the file, which are the calls the hook saw only if the hook was registered for every
  tool. `audit` recovers a fire's matched text by re-running the rule's OWN match keys on the one
  joined call: the precondition lives in earlier calls, is not re-run, and its absence is never
  reported as "the rule changed since"; the audit output says so for an `after` rule.
  `follow-rate` does not read `after`.
- *What it is not.* `after` says that an earlier call of this SHAPE happened. It cannot compare
  values across calls ("the same repository", "the table that was read"), so a flow whose danger is
  in which value two calls share is out of its reach.

**Hidden characters.** A rule whose text (any value or key, not only `action`) holds a
character a person reviewing the file cannot see is rejected, with the code point and
the field named: every Unicode format character (category Cf: zero-width characters,
bidi controls, the soft hyphen, a BOM), the whole tag block U+E0000-E007F (assigned
or not), every unassigned code point (category Cn, e.g. U+2065, U+FFF0-FFF8), the
blank-rendering code points [injection-screen](https://github.com/lockesec/injection-screen) also flags (Hangul fillers, Braille blank,
Mongolian variation selectors, the combining grapheme joiner, Khmer inherent vowels,
the Khitan filler), private-use code points, control characters other than whitespace (ESC can
conceal text in a terminal), and variation selectors except a single U+FE0E/U+FE0F directly after a visible
character (not after whitespace, not at the start of the text, not after another
selector). U+200D (zero-width joiner) is allowed only between two emoji, the left one
optionally followed by U+FE0F, so emoji sequences such as a family keep working;
anywhere else it is rejected. The `action` is delivered to the model verbatim, so
this is where instructions would be smuggled past review ("Rules File Backdoor",
Pillar Security, 2025-03-18). Tool calls are *not* normalised before matching: a hidden
character inside `rm` means bash does not run `rm`, so matching it anyway would only
add false alarms.

A rule's file name is delivered to the model too, so it is held to the same check and cannot hold a
line break. An unknown top-level key rejects the rule: a misspelled `match_content` would otherwise
leave a broader rule that still reads as healthy.

Different kinds of filter AND together; entries inside one list OR together.
The rules directory is `$INSTINCT_RULES_DIR`, else
`$CLAUDE_PROJECT_DIR/.claude/instincts`, else (neither set) `.claude/instincts`
under the current directory, so a command run from a subdirectory looks in the
wrong place: `doctor` then exits 2 and prints the absolute path it looked in. A rule
without `domain` or `confidence` simply omits them from the text it delivers. Only `*.yaml` files directly inside it
are read, so moving a file into a subdirectory retires it. A `*.yml` file is
not read, and `doctor` names it; so is a `*.yaml` entry that is not a regular file (a directory,
a FIFO, a dangling or looping symbolic link), as `NOT READ` with the reason (a symbolic link to a
real file is read). The hook never opens such an entry, so a FIFO cannot hang it.

**Bash writes: what is read.** A lexical reader of common forms, not a bash parser. The Bash
command is split into simple commands at `;`, `|`, `|&`, `&&`, `||`, `&`, newlines and
parentheses; a command substitution (`$( )`, backticks) and a process substitution (`>( )`) is read
as a command of its own. A shape not listed here is meant to yield no write; where the reader and
bash disagree (see "Known differences from bash" below) it can miss a write or name one bash would
not make. A rule that must see every shell command should be scoped to `Bash` with a
`match_content` pattern, which reads the whole command text.

Read as writes:

- `echo` / `printf` with a stdout redirect to a file: `>`, `>>`, `>|`, `1>`, `&>`, `>& file`.
  Every stdout redirect of the command is a target, but bash applies them left to right and only
  the last receives the text: `echo a >y >z` creates `y` empty and writes `a` to `z`, and
  `echo a >y >&2` leaves `y` empty. The content is the arguments (`echo $"a > b"` is `a > b`: a
  locale string loses its `$`); `cat ... > f` is a write of unknown content.
- `tee [-a] f ...`: every file operand, content unknown (also in a pipeline, with `|&`, in a
  subshell `(tee f)` or a group `{ tee f; }`, with `tee 2>&1 f` or `tee >(cat) f`).
- `sed -i` / `--in-place` (GNU `-i[SUFFIX]` and BSD `-i ''`): every file operand; the content is
  the `-e`/script expression(s). Options are parsed (`-n`, `-E`, `-l N`, `--follow-symlinks`,
  `-e`, `-f`, `--expression=`, `--`); an option this parser does not know yields no write.
  A BSD suffix other than the empty string (`-i .bak script file` on macOS) cannot be told from a
  script and is read as GNU.
- a heredoc whose line redirects or tees (`cat > f <<EOF`, `cat <<EOF >> f`, `cat <<EOF | tee f`):
  the body is the content when the command the heredoc feeds copies its stdin (`cat`, `tee`, `dd`;
  `cp` never reads stdin, so `cp <<EOF > y` writes `y` empty); for another command (`psql <<EOF > out.sql`) the write has empty content. `cat file <<EOF`
  ignores its heredoc (a file operand) and carries nothing; `cat -n <<EOF` and `cat - <<EOF` read
  it; a `cat` whose stdout is redirected (`cat <<EOF >&2 | tee f`) carries nothing down the pipe. A body belongs
  only to the command that owns its `<<TAG`, and reaches a later command only through a pipe from
  a `cat` or `tee` (`cat <<EOF | tee f`): in `cat <<EOF > a; cat b > c` the body is `a`'s and `c`
  has none, and in `cat <<EOF >&2; tee d` `d` has none. The tag is read as a shell word and
  quote removal gives the terminator: `<<'EOF.txt'`, `<<EOF-1`, `<<1`, `<<'---'`, `<<'a b'`,
  `<<E"O"F` (ends at `EOF`), `<<"E OF"`, `<<\EOF`, `<<$'E\x4fF'` (ANSI-C, decoded) and
  `<<$"EOF"` are heredocs; any quoting means the body is not expanded, and an unquoted body is
  expanded, so a `$( )` or backtick in it is read as a command (`cat > f <<E` with `$(echo hi > g)`
  in the body writes `g` too; quotes in a body are literal, so `it's $(...)` is read as well). In an
  unquoted body a backslash-newline joins two lines before the terminator test, and `\\`, `\$` and
  `` \` `` lose their backslash, as in bash. The recorded content is the body as written: its
  expansions are not evaluated. The terminator is the line equal to the tag, nothing else: `  EOF`, `EOF `
  and (for `<<`) `\tEOF` do not end it; `<<-EOF` also accepts leading TABs (not spaces) and
  strips them from the body. A line may introduce several heredocs (`cat <<A <<B > f`); their
  bodies follow in order and the last one is the command's stdin, so `f` gets the body of `B`.
  A heredoc whose terminator never comes runs to the end of the command, as in bash (its body is
  all the rest, none of which is read as commands). The quote state carries across lines, so a
  `<<` inside a string that spans lines is text, and a backslash-newline joins two lines of the
  command (also inside the tag). `<<` inside `$(( ))`, `(( ))` (any nesting), `${ }` or `$[ ]` is a
  shift, not a heredoc. A heredoc inside `$( )` keeps its body, also inside a double-quoted
  `"$( )"`, where quoting starts fresh. A tag longer than 256 characters,
  or a terminator line longer than 512, is not read as a heredoc.
- a redirected group, `{ echo a; } > f` or `( echo a ) > f`: a write of `f` with unknown content
  (the commands inside are read as well).
- a `python -` heredoc whose body writes files (`Path('x').write_text`, `open('x', 'w')`), judged on
  the command that owns the heredoc: its command word (after assignments and the wrappers above)
  must be `python`, `python3.x` or `pypy`, with `-` after its options (`python3 -u -`), so neither
  a `python -` elsewhere on the line nor `cat python3 -` makes a body Python. The literal targets
  are found anywhere in the body, comments and docstrings included, and when a body writes but no
  literal target is found, every quoted path-like literal in it is claimed: both over-approximate
  and can name a file it only reads. Not read: a body piped in (`cat <<E | python3 -`), an f-string
  or keyword `mode=` in `open()`.

Found behind a prefix: inside `if`/`then`/`else`/`elif`/`while`/`until`/`do` (`for`, `case`, `{ }`,
`( )`, a function body `f() { ...; }`), after `!`, `VAR=value` assignments, and the wrappers `sudo`
(with `-u USER`, `--user USER` and the like), `doas`, `env` (`-u`, `--unset`, `--chdir`), `time`
(`-f FORMAT`, `-o FILE`), `nice` (`-n`, `--adjustment`), `command`, `exec`, `stdbuf`, `nohup`;
an absolute command path is read by its base name (`/usr/bin/tee`). A `VAR=value` assignment is
recognised by its unquoted `NAME=` prefix whatever quotes the value has (`FOO="a b"`,
`LC_ALL='C'`, `X=$(date)`, `PATH="$X:$PATH"`). A line continuation (backslash-newline) joins the
lines. ANSI-C quoting (`$'it\'s > x'`) is read as one quoted word; its other escapes (`\n`) stay as
written, not decoded (except in a heredoc tag). Inside backticks a backslash quotes only `$`, a
backtick and a backslash (and `"` inside double quotes), as in bash, so ``echo `echo a \\> g` ``
writes nothing and nested escaped backticks are read as commands.

Not writes: a redirect inside quotes or after an escape (`\>`); anything after an unquoted `#` (a
comment); `$(( ))`, `(( ))`, `[[ ]]`, a parameter expansion `${ }` and legacy arithmetic `$[ ]`
(a `>` inside `echo ${x#*>}` is not a redirect; quotes nested in a double-quoted `${ }` start
fresh, so `echo "${y:-"a > b"}"` holds no redirect); an arrow `->` (bash itself would redirect at the `>`; this
reads it as prose); a stderr redirect (`2>f`) and a dup (`>&2`); the sinks `/dev/null`, `/dev/stdout`, `/dev/stderr`, `/dev/stdin`, `/dev/tty` and
`/dev/fd/N`; a bare `$VAR` target and a `${VAR}/...` target (`$VAR/rest` is read, with `$VAR` kept as written); a target containing a command substitution (`>out_$(date).txt`: the
inner command is still read). A digit ending a word is not an fd, so `echo abc12>f` is a write
to `f` (content `abc12`). `(( x )) > f` is read like any redirected group: `f`, unknown content.

Also read, content unknown: any command with a stdout redirect (`ls > f`, `python x.py > f`, `: > f`,
`exec > f`, `if ...; fi > f`), and the destination of `cp`, `mv`, `install`, `ln`, `dd of=`, `curl -o`
and `wget -O`. The string given to `eval`, `bash -c '...'` and `sh -c '...'` is read as a command, when
it is written out rather than held in a variable, and behind `timeout` and `su -c`. Only stdout redirects count: `2> f` and `3> f` are not read.

Known NOT read (no write is claimed): `xargs`; `find -exec`; `ssh`; other files `curl` and `wget` write (`curl -D`, `-c`, `--output-dir`, `wget -o`), a second `curl -o`; a `python3 <<EOF` heredoc without the `-`; `git` and
editors; a directory destination is matched as that directory's path, not the file inside it; a write made by a script the command runs; an assignment alone with a redirect
(`A="b" > f`, which creates `f` empty); here-strings (`cat <<< hi > f` writes `f` with unknown content);
what `cat -n`, `cat -A` and other options do to the text. When the scan spends its allowance
(proportional to the command's length, so a few hundred unclosed `$(` can exhaust it) or meets
command substitutions nested deeper than 8, the writes found are matched and the call is logged as
`unevaluated` (`bash-write parsing ran out of its scan allowance`): the list may be incomplete. A
command bash would reject as a syntax error is still read as written, so it can name writes bash
would never make.

**Known differences from bash** (each found by comparing with bash; none is read as bash reads it):

- `\$( )` and `` \` `` in an unquoted heredoc body: read as a live substitution (bash: literal), and
  `\\$( )` read as literal (bash: live). Quote the tag (`<<'EOF'`) to make the body literal in both.
- Thousands of `<<` or redirect operators on one line take more than the per-rule time budget; the
  call is then logged `unevaluated`, never blocked.
- In a `<<-` heredoc, a line that is only a backslash followed by a tab-indented terminator.
- A `case` pattern's `)` inside `$( )` ends the substitution early (the writes after it are missed).
- On malformed input an internal marker can appear in the recorded content of a write.
- `echo -e` / `printf` escapes and `%s` formats are not rendered: the recorded content is the
  arguments as written, so a `(?m)^` pattern does not see the file's line breaks.
- A target with an expansion (`src/$f.py`, `$HOME/.mcp.json`) is recorded with the variable literally
  (a path rule can fire on it). A target that is only `$X` is dropped.
- `uv run python -`, `poetry run python -`, `pipenv run python -`: the runner is not a wrapper, so
  the body is not read as Python.
- Not read: `find ... -exec sed -i ... {} +`, `perl -pi -e`, `wget -O`, `rsync`, and a command
  string run elsewhere (`docker exec c sh -c '...'`, `ssh host '...'`).
- A `cd` earlier in the command is not applied: `(cd src && echo x > a.py)` records `a.py`;
  `~/x` keeps the literal `~`.
- A command of several hundred KB can exceed the parse time cap even without special operators;
  the call is logged `unevaluated` (a Bash-scoped rule is unaffected).
- `match_tools` is exact: a rule scoped to `Edit` does not fire on `NotebookEdit` or `MultiEdit`;
  name those tools in the rule.

**A rule is loaded whole or not at all.** `doctor` lists every rejected file with
the reason: an empty `action`, a misspelled status, a pattern that does not
compile (including a repetition count too large), no `match_tools`, a duplicate key, a `confidence` that is not a
number, a POSIX character class such as `[[:alpha:]]` (Python's `re` reads it as a different set and
warns; use `\w` or `[a-zA-Z]`) or another construct Python marks as ambiguous. None of these is silently half-loaded, because each would look to its
author like a working rule.

**One bad file costs only itself.** Whatever is wrong with a file (a regex nested
thousands deep, YAML nested more than 32 levels, an unreadable file) it is listed as
`REJECTED` with the cause, `doctor` exits 1, and every other rule still loads and
delivers. YAML aliases (`*name`) and anchors (`&name`) are refused in a rule file: a few
hundred bytes of them can expand to billions of items. A file over 256 KiB is refused.

**Loading is bounded, because every tool call loads every rule.** At most 500 `*.yaml`
files and 512 KiB (`MAX_TOTAL_BYTES`) of rule files together are read, counted in
file-name order; a file that would pass a cap is `REJECTED` unparsed with `not loaded`
and the cap named (files beyond the 500th are one `(N more *.yaml file(s))` line), and
`doctor` exits 1. Parsing costs about one second per 250 KB, so the caps keep the
load itself near two seconds at worst. Retire rules into `archive/` to stay inside them.
A rules path that exists but cannot be read, or is not a directory (a plain file, a FIFO),
is reported by `doctor` with that cause and exits 2; it is not "0 rules loaded". `check` does the same
(NOT EVALUABLE on stderr, exit 2, nothing on stdout, which is the advice); the hook stays exit 0 and
writes one line naming the cause to stderr. Text
fields (`domain`, `match_paths`, `match_content`, `match_commands`, `match_exclude`,
`action`) holding a lone surrogate (a `\ud800` escape) reject the file.

**A pattern that can backtrack catastrophically is rejected.** A regex that
nests an unbounded quantifier inside a repeat (`(a+)+`, `(\w+\s?)*`,
`(.*a){12}`) can take minutes on one near-miss input, and it runs on every
tool call. The check is a **structural** one of the known shapes, not a proof: a nested repeat
passes only when the repeated group has a literal its inner quantifiers cannot
consume (`(\.\d+)*` loads). Overlapping alternation such as `(a|aa)*$` is not
detected: it loads, and can take tens of seconds on one input (30 s measured on 40
`a`s plus a `b`). Nor are optional or bounded repeats of a repeat: `(a?){30}a{30}` and
`(a{1,50}){1,50}$` load (neither finished in 20 s on 30 and on 60 `a`s). The time budget below is the backstop for everything the structural
check does not see; it does not make the check complete.

**A per-call time budget bounds that.** Matching runs under `SIGALRM`: each rule gets
at most a quarter of `INSTINCT_MATCH_BUDGET_MS` (default 1000; `0` or `off` disables)
and the call at most the whole. A rule that runs out is skipped for that call, the
hook still exits 0, the log gets a `slow` row naming it, and `doctor` lists it
(`SLOW <rule>`, exit 1) for the last 7 days. **Once the call's whole budget is
spent, the rules that come after are not evaluated for that call** (a rule that
would have matched is lost for it). They are logged as `unevaluated`, not `slow`,
and `doctor` lists them as `NOT EVALUATED <rule>` (exit 1) without blaming their
patterns: look for the `SLOW` rule that used the budget. Parsing a Bash command for
the files it writes runs under the same timer; if that parse itself runs out of
time (its cap is a quarter of the budget, or what is left of it), the writes derived
from that command are not evaluated: an `unevaluated` row with no rule and a reason
starting `bash-write parsing`, which `doctor` lists as `NOT EVALUATED Bash-write
parsing ... ran out of time` (exit 1) and does not blame on the call budget. A budget
above one hour is clamped to one hour. The timer is armed only on the main
thread of a POSIX process with no other SIGALRM handler or timer running; otherwise
matching is unbounded, as before. Review the patterns of rules you did not write.
