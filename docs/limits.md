# Known limits

The README lists the limits that change a decision. This is the full list.

- **Matching is lexical**, not semantic; `breadth` finds rules that match too much.
- **Bash writes are read from common forms, not by a bash parser.** Read:
  `echo`/`printf`/`cat` with a redirect, `tee`, `sed -i`, heredocs, also inside
  `if`/`for`/`{ }` and behind `sudo`, `env`, `VAR=1`. Not read: another command
  redirected to a file (`ls > f`, `python x.py > f`), `eval`, `xargs`,
  `bash -c '...'`, `exec > f`, `cp`/`mv`/`dd of=`/`curl -o`, a write made by a
  script. Where the reader and bash disagree it can miss a write or name one bash
  would not make; the forms read and the known differences are listed in
  [docs/rules.md](rules.md). A rule that must see every shell command:
  scope it to `Bash` with a `match_content` pattern, which reads the whole command.
- **Not every slow regex is rejected at load.** The load check is structural, of
  known shapes: overlapping alternation such as `(a|aa)*$`, and `(a?){30}a{30}`,
  load, and the per-call time budget is what bounds them. That timer arms only
  on a POSIX main thread with no other SIGALRM handler or timer; elsewhere
  matching is unbounded. Review the patterns of rules you did not write; the
  measured cases are in [docs/rules.md](rules.md).
- **Two hook processes of one session can race on the state file**, giving an
  extra full delivery, never a missing one; for an `after` rule a lost counter
  update can cost a fire.
- **`persistence-loss` assumes Claude Code's transcript layout** (a change shows
  up as a join of zero, not a clean result) and counts `truncated` deliveries as
  full ones.
- **POSIX only.** Tested on Linux (CI runs on Ubuntu); macOS is expected to work
  but is untested.
- **A rule is text the agent reads.** Review a cloned repository's
  `.claude/instincts/` like its `CLAUDE.md` ([SECURITY.md](../SECURITY.md)).
