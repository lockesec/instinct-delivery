# Changelog

## 0.1.0 (2026-10-19)

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
