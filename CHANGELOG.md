# Changelog

## 0.1.1 (2026-11-09)

- Python 3.11 or newer is required (3.9 and 3.10 are end-of-life). CI tests 3.11 to 3.14 on `ubuntu-latest`.
- **agent-security pack: `credential-path-inventory`.** Fires on a shell search for credential or wallet file
  names (`find`, `locate`, `fd`, `ls -R`, `grep -rl`) whose paths are written to a file or piped onward: the
  enumeration step that precedes collecting the files, which the read rules do not see.
- **agent-security pack: `wallet-keystore-read`** (a `Read`/`Grep` of an Ethereum keystore, Electrum wallet or
  `wallet.dat`) and its shell half **`wallet-keystore-shell-read`**; `credential-file-read` had none of these names.

## 0.1.0 (2026-11-09)

First public release. What it does:

- **Delivers lessons at the tool call.** A PreToolUse hook matches YAML rules against every
  Claude Code tool call, offline and without a model, and adds the advice to the agent's context.
- **Decides how much to say.** The full text on the first match in a session, one line on a
  repeat, a re-arm window, a byte budget per call, and `status: candidate` rules that are logged
  and never shown.
- **Reads shell writes as writes.** `echo ... > f`, `tee`, `sed -i` and heredocs are matched as a
  write of that file, so a rule about `src/` fires whether the agent used `Edit` or Bash.
- **Orders calls.** `after:` fires a rule only when an earlier call of the same session matched
  (read a secret, then write). The session state holds counters, never call text.
- **Tests rules where they live.** `tests:` cases in each rule file (`before:` steps for `after:`
  rules), `instinct-delivery test`, `check` (a dry run of one call), `replay FILE.jsonl`, and
  `doctor`. A rule is loaded whole or rejected with its reason.
- **Says when it could not look.** `doctor` names a rule that can never fire, a missing or blocked
  hook, a state directory it cannot write and calls the hook could not read; it exits 2 on a log it
  cannot read. So `doctor` in CI, or anywhere the hook is not installed, now exits 1. The agent is
  told when rule files are past the size cap.
- **Rejects rules that hide text or run slow.** A rule file holding invisible characters (Unicode
  tag characters, bidi controls, zero-width code points) is rejected at load. A regex that nests an
  unbounded repeat is rejected, and a per-call time budget skips a rule that runs long.
- **Measures what it delivered.** An activation log and the reports `repeat-rate`, `cost`,
  `fire-rate`, `cofire`, `breadth`, `persistence-loss`, `follow-rate`, `audit` and `labels`. Each
  prints its denominator, and an absent or empty input exits 2 (NOT EVALUABLE), never 0.
- **Installs without touching other tools.** `install` writes one dedicated hook entry, atomically,
  using the absolute path of your Python interpreter.
- **Ships examples.** Eight synthetic rules in `examples/instincts` and the `agent-security`
  pack (nine rules) in `examples/packs`.

Hardening history: [DESIGN.md](DESIGN.md).
