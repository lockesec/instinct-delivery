# instinct-delivery

**Rules that remind your coding agent of a lesson at the exact tool call where it
matters, and a log of what it was told.**

You tell an AI coding agent the same things over and over: use logging, not
`print()`; do not hand-edit the lockfile; run the tests after touching `src/`;
never force-push `main`. instinct-delivery turns each of those into a small YAML
rule, an *instinct*: when the agent calls this tool, on this path, with this
content or command, remind it of this. A PreToolUse hook matches every tool call
against your rules, offline and without a model, and adds the advice to the
agent's context right at that call. Write the lesson once and it is there in
every session, including when the agent writes a file through a shell command
instead of an editor tool. A rule *fires* when it matches a call. A *delivery* is
the advice the hook sends to the agent for that call.

It also decides how much to say. Most matches are a rule the agent already saw
this session, and Claude Code silently drops a hook message over 10,000
characters. So the hook sends the whole text the first time a rule fires in a
session, one line on a repeat, and never more than 8,000 bytes per call. The
numbers behind those choices are under [Measured delivery](#measured-delivery).
The reports that produced them run on your own log.

## How it works

1. **Write a rule.** One YAML file per lesson in `.claude/instincts/`: a
   `trigger`, an `action`, the `match_tools` it applies to, and the filters you
   need: `match_paths` (substrings), `match_content` (regexes over the text),
   `match_commands` (substrings of a Bash command), `match_exclude`. A
   `confidence` ranks it when room is tight.
2. **Install once.** `instinct-delivery install` registers one PreToolUse hook
   in `.claude/settings.json` and keeps every other entry in the file. Restart
   Claude Code: hooks are read at session start.
3. **Let the agent work.** Every tool call goes through the hook. A matched
   rule's advice lands in the agent's context at that call, and Claude reads it
   on its next model request, so it shapes what the agent does next (it does not
   block or alter the call that triggered it). A Bash command that visibly
   writes a file (`echo ... > f`, `tee`, `sed -i`, a heredoc) is matched as a
   write of that file, so a rule about `src/` fires whether the agent used
   `Edit` or the shell.
4. **Say it once.** The first fire of a rule in a session delivers the full
   text. A repeat within 90 minutes is one line naming the rule. After six such
   lines the rule goes quiet until the window re-arms.
5. **Stay inside a budget.** Every call carries at most 8,000 bytes. The
   strongest rules are kept, withheld rules are named in one closing line and go
   first next time, and a rule larger than the whole budget is cut to fit rather
   than never delivered. A rule with `status: candidate` is logged when it
   matches and never shown, so you can trial a rule before it speaks.
6. **Check before you trust it.** `doctor` lists what loaded, what was rejected
   and why; `check <tool> <json>` is a dry run that shows what one call would
   deliver, without touching your log.
7. **Measure.** Every delivery is a row in an activation log. The reports
   (`repeat-rate`, `cost`, `fire-rate`, `cofire`, `breadth`, `persistence-loss`,
   `follow-rate`, `audit`, `labels`) answer the questions you will have. Is this
   rule noise? Which rules fire together? Did the model see it (`persistence-loss`
   counts deliveries that never reached the model)? Did it act on it? What did it
   actually fire on? Each report prints its denominator.

## Quick start

```bash
pip install git+https://github.com/sethpsonnier/instinct-delivery     # Python 3.9+, PyYAML
```

Other routes and the virtual-environment note: [Install](#install). This is what
the first minutes look like. From a clone, `sh examples/demo.sh` plays it in a
temporary directory, using the example rule `examples/instincts/no-print-debugging.yaml`,
and CI checks every line below against the real output:

```console
$ instinct-delivery install
install: registered the PreToolUse hook in /path/to/project/.claude/settings.json; rules go in /path/to/project/.claude/instincts
$ hook: edit src/app.py, adding print(x)
⚠️ INSTINCT [code-quality] no-print-debugging: A bare print() in library code is debugging residue. Use the logging module,
INSTINCT ACTIVATED [no-print-debugging, code-quality, confidence=0.6]: or remove it before committing.
$ hook: the same kind of edit again
↻ INSTINCT [code-quality] no-print-debugging (x2 this session, full text delivered earlier): A bare print() in library code is debugging residue. Use the logging module,
$ instinct-delivery repeat-rate
repeat-rate: 1 of 2 matched fire(s) (50.0%) were the same rule again in the same session, over 1 session(s); kinds counted: full, brief, truncated, muted, budget
$ instinct-delivery repeat-rate   # no log yet
NOT EVALUABLE — activation log state/activations.jsonl does not exist
(exit 2)
```

`/path/to/project` stands for your project directory (`install` prints absolute
paths). The second edit got one line, not the whole text again, and a report with
nothing to count says so instead of printing a zero. Suppression is per session:
a payload with no `session_id` has nothing to remember, so every fire is full.

## Your first rule

```console
$ mkdir -p .claude/instincts && cat > .claude/instincts/no-print.yaml <<'EOF'
trigger: adding a print() to a module
action: Use logging, not print(), in library code.
match_tools: [Edit, Write]
match_content: ['(?m)^\s*print\(']
tests:
  - {tool: Edit, input: {file_path: a.py, new_string: "print(1)"}, expect: fires}
  - {tool: Edit, input: {file_path: a.py, new_string: "log.info(1)"}, expect: silent}
EOF
$ instinct-delivery doctor     # 1 firing, 0 REJECTED
$ instinct-delivery test       # 2 case(s), 2 pass, 0 fail
$ instinct-delivery check Edit '{"file_path":"a.py","new_string":"print(1)"}'   # dry run
```

Different kinds of filter AND together; entries inside one list OR together. The
`tests` cases live in the rule and run against the real matcher, so a rule's
false positives are caught when it is written. `after:` fires a rule only if an
earlier call of the session matched (read a secret, then write); `replay FILE.jsonl`
runs recorded calls through the real engine. The
full format and every key: [docs/rules.md](docs/rules.md). The 8 rules in
[`examples/instincts/`](examples/instincts) are synthetic demos you can copy from
([`examples/packs/agent-security`](examples/packs/agent-security) is a pack built on `after`),
and `sh examples/demo.sh --tour` feeds the real hook six scripted tool calls
through every delivery kind: full, brief, a Bash write, a shadowed candidate, a
withheld rule, a truncated one.

## Agent security

Three things the hook does that a plain matcher does not. All are advisory: the hook
adds text to the agent's context and never blocks a call.

- **Rule files cannot hide text.** A rule's `action` reaches the model verbatim. A file
  holding a character a reviewer cannot see is rejected at load, with the code point and
  field named. That covers Unicode tag characters (`U+E0049...` spelling "Ignore"), bidi
  overrides, zero-width and blank-rendering code points, and stray variation selectors
  (`hidden.py`, 51 cases in `tests/test_hidden_unicode.py`). It checks rule files only;
  tool calls are not normalised.
- **A shell write is a write.** `echo ... > f`, `tee`, `sed -i` and heredocs (also behind
  `sudo`, `env` or `VAR=1`, and inside `if`, `{ }` or `$( )`) are matched as a write of that
  file and content. A rule about `src/` or secrets in config fires whether the agent used
  `Edit` or Bash. The reader is lexical and conservative, and is checked against real bash:
  181 cases across `test_bashwrites`, `test_heredocs`, `test_bash_quoting` and
  `test_bash_scan_limits`.
- **Read a secret, then exfiltrate, across calls.** `after:` fires a rule only when an
  earlier call of the same session matched a precondition, within N calls (1 to 200,
  default 20). The session state is a call count and, per rule, the call number of its last
  precondition match, never call text (`sequence.py`). The
  [`examples/packs/agent-security`](examples/packs/agent-security) pack (9 rules, 86 cases)
  replays reconstructed attack sessions: the expected rule fires on the attack call, and
  nothing fires on the benign twin (`tests/test_agent_security_pack.py`). It matches
  shapes, not values. A private-repository read followed by a public pull request is not
  caught, and the pack says so.

See it used: the
[MCP prompt-injection case study](https://github.com/sethpsonnier/case-studies/tree/main/mcp-prompt-injection)
replays the agent's side of the attacks it studies through this pack.

## Install

```console
pip install git+https://github.com/sethpsonnier/instinct-delivery     # the tool only
git clone https://github.com/sethpsonnier/instinct-delivery           # or: with examples/ and the demo
cd instinct-delivery && pip install .
```

On systems that mark the system Python as externally managed (PEP 668: Debian,
Ubuntu, recent Homebrew), install into a virtual environment (`python3 -m venv
~/.venvs/instinct && ~/.venvs/instinct/bin/pip install .`) or with `pipx`.

Then, in your project, `instinct-delivery install` (or `install --project DIR`).
It registers the absolute path of the Python that runs it, with code that ignores
the project directory, so the hook neither depends on Claude Code's PATH nor
imports a `yaml.py` or `json.py` from the project it runs in. Re-running
`install` from a new Python environment upgrades the registration in place.
Hooks are read at session start: restart Claude Code after installing.

Python 3.9+ and PyYAML; nothing else. POSIX. CI runs the suite, the demo and
the README check on Ubuntu with Python 3.9 through 3.14.

## Why you can trust it

- **Offline and deterministic.** Rules match on tool names, paths and text. No
  model calls, no network: whether a rule fires is decided by the rule you wrote.
- **It says when it could not look.** A report over an absent or empty log exits
  2 (NOT EVALUABLE) with the cause, never 0, and prints its denominator next to
  every count. `doctor` with no rules loaded exits 2 and says the hook will never
  say anything.
- **Nothing counts as delivered until it was.** The log row and the session
  state are written only after the output reached stdout, so the log never says
  `full` for advice the agent did not receive. A withheld rule is not marked
  delivered and goes first on its next fire; a rule cut to fit is logged as
  `truncated`, never as `full`.
- **The budget is the bytes that leave the hook.** It counts every byte as the
  JSON envelope carries it, closing line included, and the rows of a call sum to
  exactly what it emitted.
- **A rule is loaded whole or not at all.** An empty `action`, a misspelled
  `status`, a pattern that does not compile, a missing `match_tools`: the file
  is `REJECTED` with the reason and `doctor` names it. One bad file costs only
  itself; the other rules keep delivering.
- **A rule cannot hide text from its reviewer.** The `action` reaches the model
  verbatim, so a rule file holding a character a person cannot see (tag
  characters, bidi controls, zero-width or blank-rendering code points: the
  "Rules File Backdoor" class) is rejected, with the code point named.
- **Slow patterns are caught.** A regex that nests an unbounded repeat (`(a+)+`)
  is rejected at load. A per-call time budget (1 s in total, a quarter per rule)
  skips a rule that runs long, logs it, and `doctor` lists it as `SLOW`.
- **It never blocks a tool call.** The hook is advisory and exits 0 once it has
  started; bad input produces no output, and a failing rule is skipped without
  silencing the others.
- **`install` touches only what is its own.** It adds one dedicated entry,
  recognised by its exact command, never edits another tool's entry or matcher,
  writes the file atomically, and refuses (exit 2, nothing changed) a settings
  file that is not valid JSON or not the shape it expects.
- **The log is private.** It records the rule, the rule's own declared pattern,
  the tool, the session id and the path touched; never the content of a call, a
  command line or a prompt. Log and state files are created mode 0600.
- **These are tested.** [tests/INVARIANTS.md](tests/INVARIANTS.md) maps the
  behaviours above to the test that fails if one breaks; CI checks the quick
  start and the first-rule block above against the real output; and
  [DESIGN.md](DESIGN.md) lists the reviews the tool went through before
  release, run by separate AI sessions that had not written the code, each
  finding reproduced against the source and pinned to a test.

Settings are environment variables (`INSTINCT_REPEAT`, `INSTINCT_REARM_MIN`,
`INSTINCT_BRIEF_CAP`, `INSTINCT_PAYLOAD_BUDGET`, `INSTINCT_SHADOW`,
`INSTINCT_BASH_EDITS`, `INSTINCT_MATCH_BUDGET_MS`, `INSTINCT_STATE_DIR`), each
with a parity value that restores the plain matcher's output byte for byte, and
a value that does not parse keeps the default, so a typo never turns a safety
setting off. The table: [docs/delivery-policy.md](docs/delivery-policy.md).

## Measured delivery

The defaults are measured, not guessed. They come from about 90 days of the
author's own daily use with a few hundred rules (265,423 logged rule matches;
the log itself stays private). Two results set them:

- **Most matches are repeats.** 93.8% were a rule the agent had already been
  shown in the same session (measured before repeat suppression existed). So the
  first match gets the full text and a repeat gets one line.
- **Too much text never arrives.** Claude Code keeps hook output over 10,000
  characters out of the model's context. Joining the log to the session and
  subagent transcripts showed about a quarter of full deliveries (942 of 3,785
  in one week) never reached the model. So the hook sends at most 8,000 bytes
  per call.

`repeat-rate` and `persistence-loss` measure the same two things on your own
log. The method and its caveats are in [docs/delivery-policy.md](docs/delivery-policy.md).

## Exit codes

- **0** ok. The hook itself exits 0 once it has started (a missing package or a shadowing `PYTHONPATH` can stop it starting): it is advisory and never blocks a tool call.
- **1** findings: a new `fire-rate` offender, a `cofire` pair, a `breadth` report with a
  BROAD rule (match rate at or above `--threshold`), or `doctor` reporting a
  rejected rule, an unread rule file (NOT READ), OVER BUDGET, SLOW, or NOT EVALUATED
  (rules or Bash-write parsing the time budget did not let it check).
- **2** NOT EVALUABLE: it could not look (absent or empty input, nothing loaded). Never a pass.
- **64** usage error.

`install` also exits **2** when `.claude/settings.json` is not valid JSON, not a JSON
object, resolves to something that is not a regular file, or has a `hooks` /
`hooks.PreToolUse` of the wrong shape: it refuses to rewrite it. `cofire` exits 2 when
the log holds fewer calls than `--min-support`. `check` exits 2 (message on
stderr) when the rules path is missing, unreadable or not a directory.

## Known limits

- **Matching is lexical**, not semantic; `breadth` finds rules that match too much.
- **Bash writes are read from common forms, not by a bash parser.** Read:
  `echo`/`printf`/`cat` with a redirect, `tee`, `sed -i`, heredocs, also inside
  `if`/`for`/`{ }` and behind `sudo`, `env`, `VAR=1`. Not read: another command
  redirected to a file (`ls > f`, `python x.py > f`), `eval`, `xargs`,
  `bash -c '...'`, `exec > f`, `cp`/`mv`/`dd of=`/`curl -o`, a write made by a
  script. Where the reader and bash disagree it can miss a write or name one bash
  would not make; the forms read and the known differences are listed in
  [docs/rules.md](docs/rules.md). A rule that must see every shell command:
  scope it to `Bash` with a `match_content` pattern, which reads the whole command.
- **Not every slow regex is rejected at load.** The load check is structural, of
  known shapes: overlapping alternation such as `(a|aa)*$`, and `(a?){30}a{30}`,
  load, and the per-call time budget is what bounds them. That timer arms only
  on a POSIX main thread with no other SIGALRM handler or timer; elsewhere
  matching is unbounded. Review the patterns of rules you did not write; the
  measured cases are in [docs/rules.md](docs/rules.md).
- **Two hook processes of one session can race on the state file**, giving an
  extra full delivery, never a missing one; for an `after` rule a lost counter
  update can cost a fire.
- **`persistence-loss` assumes Claude Code's transcript layout** (a change shows
  up as a join of zero, not a clean result) and counts `truncated` deliveries as
  full ones.
- **POSIX only.** Tested on Linux (CI runs on Ubuntu); macOS is expected to work
  but is untested.
- **A rule is text the agent reads.** Review a cloned repository's
  `.claude/instincts/` like its `CLAUDE.md` ([SECURITY.md](SECURITY.md)).

## Prior art and credits

Developed in a private AI-experimentation environment and extracted as a standalone tool. The maintainer designed and directed it; much of the code was written with AI coding agents, and adversarial review rounds were run by separate AI sessions.

[joeblackwaslike/lessons-learned](https://github.com/joeblackwaslike/lessons-learned) (MIT) injects lessons at PreToolUse with deduplication, a 4 KB budget and an eval suite; this project was built independently of it. Anthropic's [hookify](https://github.com/anthropics/claude-code/tree/main/plugins/hookify) turns regex rules into warn/block hooks, and [gptme](https://github.com/gptme/gptme) has keyword- and tool-matched lessons. What this project adds is the measured delivery economics behind its defaults, the reports that compute the same quantities on your own log, and reports that say NOT EVALUABLE instead of zero.

The idea of an agent **"instinct"**, an atomic learned behaviour carrying a confidence, comes from **ECC** (formerly everything-claude-code; GitHub redirects the old name), [affaan-m/ECC](https://github.com/affaan-m/ECC) (MIT), `continuous-learning-v2`. ECC's instincts are natural-language triggers. No ECC code is included: the deterministic matcher, the delivery policy and the measurements here are this project's own. See [NOTICE](NOTICE). The rules in `examples/instincts/` are synthetic.

Attribution corrections are welcome.

## Docs

- [docs/rules.md](docs/rules.md): set up a project, the rule format, every filter key, what counts as a Bash write
- [docs/delivery-policy.md](docs/delivery-policy.md): delivery kinds, settings, and the measurement
- [docs/reports.md](docs/reports.md): the activation log fields and the reports
- [DESIGN.md](DESIGN.md): how it was built: the contract, the reviews, what each found
- [SECURITY.md](SECURITY.md): what the hook reads and writes, file modes, the trust boundary

## License

Apache-2.0. See [LICENSE](https://github.com/sethpsonnier/instinct-delivery/blob/main/LICENSE).
