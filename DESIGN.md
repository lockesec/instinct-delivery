# How instinct-delivery was built

## Starting point

The tool began as the delivery layer of a private AI-experimentation environment,
where a few hundred rules ("instincts") were matched against every Claude Code
tool call. The concept of an agent instinct, an atomic learned behaviour carrying
a confidence, comes from everything-claude-code (ECC); see [NOTICE](NOTICE). The
extraction kept the matcher deterministic and the measurements honest:

1. **Match lexically and deterministically.** A rule sees tool names, paths and
   text. No model call decides whether a lesson fires.
2. **Decide how much to inject, not only what.** Repeat suppression, a byte
   budget, a re-arm window and shadow mode are the project's contribution. Why
   they are the defaults is in [docs/delivery-policy.md](docs/delivery-policy.md).
3. **Nothing counts as delivered until it was written.** The log and the state
   are committed after the output reached stdout.
4. **Absence is not a measurement.** Every report prints its denominator, and an
   absent or empty input is NOT EVALUABLE (exit 2), never a zero.

## Adversarial reviews

Adversarial reviews were run by separate AI sessions that had not written the code; each finding was reproduced against the source and pinned to a test before it was fixed. The behaviour each fix pins is mapped to its test in
[tests/INVARIANTS.md](tests/INVARIANTS.md).

| Date | What the review found |
|---|---|
| 2026-09-25 | Four boundary failures that contradicted the documented contracts: a small positive payload budget was exceeded (a 100-byte budget emitted 164 bytes); `install` crashed on a settings file whose `hooks` was valid JSON of the wrong shape; session ids that differ only in punctuation shared a state file; `labels add` reported success after losing the label. |
| 2026-09-25 | The default budget also overflowed, because an unbounded heading and suffix survived truncation (a 9,000-character domain emitted 9,093 bytes against a budget of 8,000). A corrupt per-rule state entry silenced the rule with no output and no log row. The closing notice of withheld rules had no accounting row, so telemetry bytes and emitted bytes disagreed. |
| 2026-09-25 | Fixes for the two findings above, plus: the log and state are recorded only after a delivery was written. |
| 2026-09-29 | Partial delivery was counted as full delivery. One huge `confidence` rejected more than its own rule. Session ids now map to state files injectively. |

### Pre-release reviews (2026-10-15)

A second series: each was a review by a separate AI session of the unpublished
repository, fixed in one commit before the next began.

| Date | What the review found |
|---|---|
| 2026-10-15 | The hook was registered as a bare command that needs `PATH` (exit 127 on every call where the script is not on it); a slow rule could hold the hook with no bound; first-run docs, the clone path and several claims were not exact. |
| 2026-10-15 | The registered command ran in the project directory and so imported a project-root `yaml.py` or `json.py` ahead of the real module; delivery wording claimed more than the model was shown; rules the call budget never reached were logged and blamed as slow; Bash-write parsing of a huge unclosed quote ran outside the time budget; `install` left older registrations in place. |
| 2026-10-15 | A Bash-write parse that itself timed out was blamed on the call budget; the README transcript and first-rule block were not checked against the real output; `fire-rate` and `breadth` with nothing to count printed "0 of 0" instead of NOT EVALUABLE; `install` did not handle a settings directory or a dangling symlink, and did not keep file mode or UTF-8. |
| 2026-10-15 | `install` recognised its command by a substring and merged matchers, so it could rewrite another tool's hook entry; the settings write was not atomic and followed a planted temporary-file link; `cofire`, `labels stats` and `repeat-rate` reported a result when there was nothing to count. |
| 2026-10-15 | `install` could report "upgraded" and exit 0 while the old PATH-dependent command stayed (an equal duplicate entry was removed instead of the kept one); state and log writes followed planted symlinks (a relative `INSTINCT_STATE_DIR` can sit inside a cloned repository); the hook's "exits 0" was false when stdout was a closed pipe or full device; only `tee f <<EOF` was recognised as a Bash write, although the README listed `tee`; `INVARIANTS.md` still described the older install behaviour; `cost` counted rows without a session id as re-arm and `--days` accepted a negative value; `install` replaced a read-only settings file and could emit `NaN`. |
| 2026-10-15 | A README claim (Bash writes count for `Edit`-scoped rules) the code did not honour; one hostile rule file (a regex or YAML nested thousands deep, a YAML alias bomb) silenced every rule and crashed `doctor`; `breadth` exit 1 was undocumented; `install` on a deeply nested settings file, a FIFO at a state file, a lone-surrogate session id, stale temp files and a world-readable log. |
| 2026-10-15 | Rule loading was unbounded (forty large files took tens of seconds per tool call); Bash write detection claimed phantom writes (arithmetic, arrows, substitutions, fd rules, heredoc content for commands that do not copy stdin); an unreadable rules path read as "0 rules"; a lone surrogate in a rule text field; new state directories were world-readable; `cost` on a log with no delivery rows reported zeros. |
| 2026-10-15 | The CLI crashed (exit 1, after the work was done) when stdout could not encode `—`, `·` or `⚠️`, or an argument held a non-UTF-8 byte; Bash writes were missed inside `if`/`for`/`{ }`/functions and behind assignments and `sudo`/`env`/`command`, and with `|&`, `>|`, `>& f`, several redirects; a backslash-escaped quote broke quote tracking; a comment or an escaped `>` read as a write, and `sed` options (`--follow-symlinks`, `-l 5`, BSD `-i ''`) were taken for operands; `check` printed nothing and exited 0 on an unreadable rules path; `--json` was accepted and ignored on `check` and `doctor`; a lone surrogate in a tool input dropped the log row after delivery; a dangling link, FIFO or directory named `x.yaml` was never mentioned; future-dated log rows counted as the last 7 days. |
| 2026-10-15 | A quoted or substituted assignment (`FOO="a b" echo x > f`) hid the command, so a secret written behind one was never seen; one heredoc body was attributed to every `cat`/`tee` on its line; a `>` inside `${x#*>}` or `$[1>2]` read as a redirect; `fire-rate` with excluded rows and no offender exited 2 (malformed) or 0 (future, unstamped) without saying NOT EVALUABLE; `breadth` parsed each Bash command with no time budget (63 s on one 198k-character command) and an unterminated `[[ ` was quadratic, as was a repeated `<<EOF` line (73 s and 16 s at 200k characters). Smaller: group redirects (`{ ...; } > f`), ANSI-C `$'...'`, `<<\EOF`, wrapper long options (`sudo --user bob`), `--json` for NOT EVALUABLE, the log's `null` for an absent domain or confidence, and what `check` says when silent. |
| 2026-10-15 | A `"${y:-"a > b"}"` (quotes nested inside a double-quoted expansion) read as a redirect, so a phantom write was claimed; a heredoc tag was limited to `[A-Za-z_]\w*` (`<<EOF-1`, `<<'a b'` left the body to be read as commands) and the terminator was matched after stripping, so ` EOF` ended a heredoc bash would not; the `python -` write finder was quadratic; `cat file <<EOF`, `cat <<EOF >&2 | tee f`, a second heredoc on a line and `echo a >y >z` attributed content bash does not deliver; `docs/rules.md` listed a write (`abc12>f`) under "Not writes"; the demo scripts inherited the caller's `INSTINCT_*` settings; a spent substitution scan dropped writes without saying so. |
| 2026-10-15 | Heredocs were not read as bash reads them: an unterminated body was parsed as commands (phantom writes), a quote opened on an earlier line or a `<<` inside `$[ ]`, `${ }` or nested arithmetic misplaced bodies, `$'..'`/`$".."` tags and backslash-newlines were not handled, a heredoc inside `$( )` lost its body and an unquoted body's `$( )` was not read; `cp` was taken for a stdin copier; `python -` was judged on the whole line; backticks were not unescaped; the python write finder was still quadratic on unclosed `open(`; a 3 s wall-clock test bound failed on a loaded machine. Each was checked against real bash, fixed test-first, and the timing test now judges growth (4x input, under 8x time). Fixed by the maintainer directly after two failed fix attempts. |
| 2026-10-15 | Review of the previous fix (no blocker): the new backslash-newline join re-scanned the growing line, quadratic (80k chars 43 s); a quote in an unquoted heredoc body hid its `$( )`; a heredoc inside `"$( )"` lost its write when the body held a quote; continuations inside unquoted bodies were not joined; `cat python3 -` was read as Python. Each checked against bash and fixed test-first by the maintainer; the growth-based timing test now includes continuation tokens. |

## Fixes by area

The reviews above are in time order. The same fixes, grouped by what they protect (the
changelog of the unreleased 0.1.0 used to list them one by one):

- **Rule matching.** The first `test` run caught `force-push-main` firing on
  `--force-with-lease`, the command its own advice recommends; it now matches `--force` and
  `-f` only. A first line that ended at a line break other than `\n` (`\r`, form feed,
  U+0085, U+2028, U+2029) lost text on delivery; the head and block now split at one boundary.
  A POSIX class (`[[:alpha:]]`) or another construct Python marks ambiguous is rejected with
  that cause. The catastrophic-backtracking check is structural, for known shapes, with the time
  budget as the backstop (`(a?){30}a{30}` loads).
- **Hidden text.** The rejected set grew to the whole Unicode tag block, every unassigned code
  point, variation selectors not directly after a visible character, and U+200D unless between
  two emoji. Character classes follow [injection-screen](https://github.com/lockesec/injection-screen)'s `invisible-unicode` detector.
- **Delivery accounting.** Log and state are written only after the output reached stdout; a
  partial delivery is `truncated`, not `full`; a full delivery no longer repeats its first line;
  session ids map to state files injectively; the hook exits 0 even when stdout is a closed pipe
  or a full device.
- **Files and install.** State files go through a fresh exclusively-created temporary file and
  the log opens with `O_NOFOLLOW` as a regular file; a relative `INSTINCT_STATE_DIR` or
  `XDG_STATE_HOME` is ignored with a note; new directories are 0700. `install` recognises its
  command by exact form, never edits another tool's entry, removes duplicates of its own by
  identity, verifies the result before and after writing, and refuses a read-only file, one
  holding `NaN`/`Infinity` or a lone surrogate, a FIFO, or a dangling link. Rule loading is
  bounded (500 files, 512 KiB together; the excess is rejected and named).
- **Bash writes.** Checked against real bash, review by review: compound statements and wrappers
  (`sudo`, `env`, `time`, `nice`, assignments), `|&`, `>|`, several redirects, quote and escape
  tracking, `${ }`, `$[ ]` and arithmetic that are not redirects, heredoc tags, terminators,
  ownership of a body and `<<-`, `python -`, and `sed -i` options. Several scanners were
  quadratic on adversarial input and now grow linearly (a timing test judges growth: 4x input,
  under 8x time); a scan that spends its allowance logs the call `unevaluated` instead of
  dropping writes silently.
- **Reports.** Every report with nothing to count exits 2 with NOT EVALUABLE and its cause (text
  and `--json`): `fire-rate` with only excluded rows, `cofire` below `--min-support`, `cost` with
  no delivery rows, `labels stats` on unreadable lines, `repeat-rate` with no assignable session,
  `follow-rate` with no joined delivery. Rows dated in the future or without a parsable timestamp
  are excluded from a `--days` window and counted; `--days 0` is NOT EVALUABLE with the cause and a negative
  `--days` is a usage error.
- **Command line.** A console that cannot encode `—`, `·` or `⚠️`, or a non-UTF-8 byte in an
  argument, no longer crashes the CLI; unencodable characters print escaped. `--json` is accepted
  only where it does something. `check` names a missing or unreadable rules path and exits 2.
  `audit` fire ids are content-addressed and `audit label` looks the id up in the log.
  `demo.sh` and `check_readme.py` ignore the caller's `INSTINCT_*` settings.

## Test suite

`tests/` holds the behaviour tests; `tests/INVARIANTS.md` maps the behaviour claims
the README and docs make to the test that fails if one breaks. The README
quick-start transcript is not a pytest test: `examples/check_readme.py` compares it
with the real demo output in CI. A new check needs a negative control (a test that fails
if the check is removed).
