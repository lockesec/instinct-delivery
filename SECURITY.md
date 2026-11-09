# Security policy

## Supported versions

Only the main branch receives security fixes.

## What the hook can and cannot do

- It **reads** every tool call's input (the PreToolUse payload) and **adds text**
  to the agent's context. It never blocks, edits or runs a tool call.

### What it writes at run time

- The hook writes only under its state directory: the activation log and one small
  state file per session.
- **Where that is.** `INSTINCT_STATE_DIR` if it is an absolute path, else under
  `XDG_STATE_HOME` (absolute) or `~/.local/state`. A relative value is ignored, because it
  would resolve against the project directory, which can be a cloned repository.
- **How files are written.** State files go through a fresh exclusively-created temporary
  file in the same directory, then a rename. The log is opened with `O_NOFOLLOW` and used
  only if it is a regular file. A symlink planted at a file name inside the state directory
  is never written through. State files are read under the same no-link,
  regular-files-only rule, and temporary files left by a killed hook are pruned with the old state.
- **What you must trust.** The state directory itself (and its `delivery` subdirectory) is
  trusted: a symlink used AS that directory is followed, so keep it somewhere only you can write.
- **Modes.** The log and state files are created mode 0600 (owner only), whatever the umask.
  A state, `delivery` or log directory this tool has to create is mode 0700, because the
  delivery file names are session ids. A directory that already exists keeps its mode, so
  tighten it yourself if it is shared. Both hold session ids and file paths.
- **What the log records.** The rule that matched, the rule's own declared pattern, the tool
  name, the session id and the path of the file the call touched (for a Bash command that
  writes a file, the path it writes). It never records the content of a call, a command
  line or a prompt.
- **What `after` adds.** A rule with `after` adds counters to the session's state file: the
  number of calls so far, and the call number of each precondition match. No text, path,
  tool name or pattern of a call is stored there.

### What it reads from rule files

- Rule files are parsed with a loader that refuses YAML aliases and anchors and nesting
  deeper than 32 levels. Limits: 256 KiB per file, and 512 KiB and 500 files per rules
  directory. A file over a limit is rejected and named by `doctor`, never parsed, because
  every tool call loads every rule. A hostile rule file is rejected instead of expanding in
  memory, and the other rules keep working. The caps count in file-name order, so large files
  that sort first can use them up and keep later rules out. When that happens the agent is told
  once a session (every call if the payload has no session id), and `doctor` names the files.

### What `install` writes

- `install` writes `.claude/settings.json` (`.claude/settings.local.json` with `--local`) and creates `.claude/instincts` in the project.
  It refuses a settings file that is not valid JSON or not a regular file.
- It writes through a fresh exclusively-created temporary file renamed over the resolved
  target, so a planted symlink at a temporary name is never followed.
- It never changes the matcher or hooks of another tool's entry. It removes only hooks that
  are exactly its own registered command and adds one dedicated entry.
- `labels add --store FILE` and the report `--log` options write only where you point them.

### The hook command and its limit

- The hook command `install` registers is the absolute path of your Python interpreter (machine-specific: use `install --local` when
  `settings.json` is committed), run
  so that the project directory is not on its import path (relative `sys.path` entries are dropped).
- That is a limit, not a sandbox. A `PYTHONPATH` that is relative or names the project
  directory absolutely is still honoured, and the environment the hook runs in can be set by
  user or project settings, so whoever controls it can still shadow a module.

## Trust boundary

The rules directory is advice the agent will read. A rule is text injected into
the model's context, so a cloned repository's `.claude/instincts/` deserves the
same review as its `CLAUDE.md`: a malicious rule could steer the agent. Run
`instinct-delivery doctor` and read the rules before installing in a repository
you did not write.

A rule's regex runs on every tool call. A pattern that nests an unbounded
quantifier inside a repeat (`(a+)+`) is rejected at load, because it can hold a
hook for minutes on one input. That check is structural and does not catch
every slow pattern (overlapping alternation such as `(a|aa)*` passes), so
review the patterns of rules you did not write. A per-call time budget
(`INSTINCT_MATCH_BUDGET_MS`, default 1 s in total, a quarter per rule) skips a rule that runs long, on a POSIX
main thread only; elsewhere matching is unbounded.

## Reporting

Please report a vulnerability **privately**, using GitHub's "Report a vulnerability"
button on this repository (Security, then Advisories), not a public issue.
