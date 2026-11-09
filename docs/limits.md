# Limits

## How well it works

- **Reading shell writes: checked against real bash.** `tests/test_bash_ground_truth.py` runs commands in a throwaway folder and compares the files bash created with the writes the reader names. It lists the differences that remain: only stdout redirects are read (`2> f` and `3> f` are not), a directory destination is named as the directory, and a syntax error is read as written.
- **False alarms from the security pack: checked, not published.** Replayed over everyday sessions of the maintainer's work, it fired rarely. That found one false fire, on a log named `mcp.jsonl`, now fixed. The sessions and counts stay private, and yours will differ: `replay` and `breadth` measure it on your own log.
- **Catching real attacks: not measured.** The pack's rules are built from published attacks and pass their own written cases, which is not a catch rate. No held-out set of real attack sessions exists for it.
- **Does advice change what the agent does? Not measured here.** `follow-rate` measures it on your own log.

## Known holes

- **Matching is lexical**, not semantic; `breadth` finds rules that match too much.
- **Bash writes are read from common forms, not by a bash parser.** Read:
  `echo`/`printf`/`cat` with a redirect, `tee`, `sed -i`, heredocs, also inside
  `if`/`for`/`{ }` and behind `sudo`, `env`, `VAR=1`; any redirected command, `cp`/`mv`/`install`/`ln`,
  `dd of=`, `curl -o`, `wget -O` and the string given to `eval` or `bash -c`, where the content is often
  unknown, so a rule that needs the content stays quiet. Not read: `xargs`, `find -exec`, `ssh`, `git`, editors, a command
  held in a variable, other files `curl` or `wget` write (`curl -D`, `-c`, `--output-dir`, `wget -o`), a second `curl -o`, a write made by a script. Where the reader and bash disagree it can miss a write or name one bash
  would not make; the forms read and the known differences are listed in
  [docs/rules.md](rules.md). A rule that must see every shell command:
  scope it to `Bash` with a `match_content` pattern, which reads the whole command.
- **Not every slow regex is rejected at load.** The load check is structural, of
  known shapes: overlapping alternation such as `(a|aa)*$`, and `(a?){30}a{30}`,
  load, and the per-call time budget is what bounds them. That timer arms only
  on a POSIX main thread with no other SIGALRM handler or timer; elsewhere
  matching is unbounded. Review the patterns of rules you did not write; the
  measured cases are in [docs/rules.md](rules.md).
- **The activation log is one file per user, shared by every project.** Each row carries a
  `project` field (a digest of the rules directory's path) and `doctor` reads only its own
  project's rows. The reports (`repeat-rate`, `cost`, `fire-rate`, `cofire`, ...) do not
  filter by project, rule identity in them is the bare file name, and rows written before
  the field existed belong to every project. Give each project its own `INSTINCT_STATE_DIR`
  if those reports must not mix them.
- **Two hook processes of one session are serialised by a file lock** (`flock`,
  held from reading the state to writing it, waiting at most 2 seconds). Where the
  lock is not available (no `fcntl`, such as Windows; a lock file that cannot be
  created) or the wait runs out, the hook goes on unlocked and the old race
  returns: an extra full delivery, or for an `after` rule a lost counter update
  that costs a fire.
- **`persistence-loss` assumes Claude Code's transcript layout** (a change shows
  up as a join of zero, not a clean result) and counts `truncated` deliveries as
  full ones.
- **A call can spend the time budget that checks it.** A padded command makes a rule run out of time.
  The rule is logged `slow` and `doctor` names it, but the agent got nothing on that call.
- **A call the hook cannot read is not advised on.** Over 32 MiB, nested too deep to parse, or not valid
  text: the agent gets nothing; the log has an `unevaluated` row and `doctor` reports it. Malformed JSON
  and a non-object payload are ignored and leave no row.
- **Damaged state forgets.** A corrupt or deleted session state file reads as empty: repeats get full
  text again, and an `after` rule loses its precondition, without a message. An agent that can write the
  state directory can do this on purpose. If the directory cannot be written at all, `doctor` says so.
- **Muting is per rule, not per target.** After six brief lines a rule is quiet until it re-arms, so
  repeats on harmless files can mute it for a real target, an `after` rule included. Set
  `INSTINCT_BRIEF_CAP=0` to keep the lines. A rule withheld by the budget reaches the agent as a name only.
- **`doctor` reads settings files, not Claude Code.** It can say no file holds the hook, or that
  `disableAllHooks` or a narrow matcher stands in the way. It cannot show the hook ran, and it does not
  read managed settings.
- **`test` passing does not mean the hook delivers.** It runs with Bash writes on, counts `candidate`
  rules as firing, and a rule over the per-call time budget still passes `silent`. Use `check` and `doctor`.
- **Some reports exit 0 on thin input, with the count printed.** `repeat-rate` and `cost` when most of the
  log is unreadable, `breadth` at 0 of 0 evaluated, `persistence-loss` when only a preview was seen, and
  `replay`, whose "with a delivery" count includes skipped rules. Read the printed counts beside the number.
- **POSIX only.** Tested on Linux (CI runs on Ubuntu); macOS is expected to work
  but is untested.
- **A rule is text the agent reads.** Review a cloned repository's
  `.claude/instincts/` like its `CLAUDE.md` ([SECURITY.md](../SECURITY.md)).
