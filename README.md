<h1 align="center">instinct-delivery</h1>

<p align="center"><b>Tell your coding agent once.</b> <br> It hears the lesson at the tool call where it matters.</p>

instinct-delivery is a Claude Code hook that reminds your coding agent of a lesson just
before the tool call where the lesson applies.

You write each lesson as a small YAML rule, for example "when the agent edits `src/`,
remind it to run tests". A rule is called an *instinct*. Before every tool call, the hook
checks the call against your rules and adds the matching advice to the agent's context.
Each time it does, that is a *delivery*, and it is logged.

It is for people who use Claude Code and keep teaching the agent the same thing. The
lesson is there in every session, including when the agent writes a file through the shell
instead of an editor tool. It runs offline, with no model. The full text goes out once per
session, a repeat gets one line, and every call stays under a byte budget.

## Install

```sh
pip install git+https://github.com/lockesec/instinct-delivery
instinct-delivery install
```

Run `install` in your project directory. It registers the hook in `.claude/settings.json`
and keeps every other entry. Restart Claude Code afterwards: hooks are read at session start.
Needs Python 3.9+ and PyYAML, on a POSIX system. If your system Python is externally
managed (PEP 668: Debian, Ubuntu, recent Homebrew), use a virtual environment or `pipx`.

## Quick start

A clone also has the examples and a demo. `sh examples/demo.sh` plays this in a temporary
directory with the example rule `examples/instincts/no-print-debugging.yaml`. A `hook:`
line is one simulated tool call. CI checks each line against the real output (`...` marks
a line cut short here):

```console
$ hook: edit src/app.py, adding print(x)
⚠️ INSTINCT [code-quality] no-print-debugging: A bare print() in library code is debugging residue. ...
INSTINCT ACTIVATED [no-print-debugging, code-quality, confidence=0.6]: or remove it before committing.
$ hook: the same kind of edit again
↻ INSTINCT [code-quality] no-print-debugging (x2 this session, full text delivered earlier): ...
$ instinct-delivery repeat-rate
repeat-rate: 1 of 2 matched fire(s) (50.0%) were the same rule again in the same session, ...
$ instinct-delivery repeat-rate   # no log yet
NOT EVALUABLE — activation log state/activations.jsonl does not exist
```

The first edit gets the full advice, split over two lines with nothing said twice. The
repeat gets one line. `repeat-rate` reads the log; with no log it exits 2 instead of
printing a zero.

## How it works

| Step | What happens |
|---|---|
| Write a rule | One YAML file per lesson in `.claude/instincts/`: tools, paths, text patterns, advice |
| Let the agent work | A matched rule's advice reaches the agent at that call and shapes its next step; the call itself is never blocked |
| Shell writes count | `echo ... > f`, `tee`, `sed -i` and heredocs match as a write of that file |
| Say it once | Full text on a rule's first fire in a session; one line on a repeat within 90 minutes; quiet after six such lines |
| Stay in budget | At most 8,000 bytes per call; the strongest rules are kept and the rest go first next time |
| Check it | `doctor` lists what loaded and what was rejected; `check` is a dry run |
| Measure it | Reports over the log: is a rule noise, did the model see it, did the agent act |

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
$ instinct-delivery check Edit '{"file_path":"a.py","new_string":"print(1)"}'
```

The `tests` cases live in the rule and run against the real matcher, so false positives
show up when the rule is written. `check` is a dry run that leaves your log alone.
Different filter kinds AND together; entries in one list OR together.

- Every key, including `match_paths`, `match_commands`, `match_exclude`, `confidence`,
  `status: candidate` (logged, never shown) and `after:`: [docs/rules.md](docs/rules.md).
- [`examples/instincts/`](examples/instincts): 8 synthetic rules to copy from.
- [`examples/packs/agent-security`](examples/packs/agent-security): a 9-rule pack built on `after:`.
- `sh examples/demo.sh --tour`: six scripted calls through every delivery kind (full, brief,
  a Bash write, a shadowed candidate, a withheld rule, a truncated one).
- `instinct-delivery replay FILE.jsonl` runs recorded calls through the real engine.

## Agent security

All three are advisory: the hook adds text and never blocks a call.
[tests/INVARIANTS.md](tests/INVARIANTS.md) maps each claim to its tests.

1. **Rule files cannot hide text.** A file holding a character a reviewer cannot see (Unicode
   tag characters, bidi overrides, zero-width or blank-rendering code points, stray variation
   selectors) is rejected at load, with the code point and field named. Tool calls are not
   normalised.
2. **A shell write is a write**, also behind `sudo`, `env` or `VAR=1` and inside `if`, `{ }`
   or `$( )`. The reader is lexical and conservative, and is checked against real bash.
3. **Read a secret, then exfiltrate.** `after:` fires a rule only when an earlier call of the
   same session matched a precondition, within N calls (1 to 200, default 20). Session state
   is call counts, never call text. The agent-security pack replays reconstructed attacks:
   the expected rule fires on the attack call and nothing fires on the benign twin. It
   matches shapes, not values; a private-repository read followed by a public pull request
   is not caught. Used in the
   [MCP prompt-injection case study](https://github.com/lockesec/case-studies/tree/main/mcp-prompt-injection).

## Why you can trust it

1. **Offline and deterministic.** Rules match on tool names, paths and text; no model, no network.
2. **It says when it could not look.** A report over an absent or empty log exits 2 (NOT
   EVALUABLE) with the cause, and prints its denominator next to every count. `doctor` with
   no rules loaded exits 2.
3. **Logged means delivered.** The log row and session state are written only after the
   output reached stdout. A withheld rule is not marked delivered and goes first next time; a
   rule cut to fit is logged `truncated`, not `full`.
4. **The budget is the bytes that leave the hook**, counted as the JSON envelope carries them;
   the rows of a call sum to exactly what it emitted.
5. **A rule loads whole or not at all.** An empty `action`, a misspelled `status`, a pattern
   that does not compile, a missing `match_tools`: the file is `REJECTED` with the reason, and
   the other rules keep delivering.
6. **Slow patterns are caught.** A nested unbounded repeat (`(a+)+`) is rejected at load. A
   per-call time budget (1 s in total, a quarter per rule) skips a rule that runs long and
   logs it; `doctor` lists it as `SLOW`.
7. **It never blocks a tool call.** The hook exits 0 once started; bad input produces no
   output; a failing rule is skipped without silencing the others.
8. **`install` touches only its own entry**, found by its exact command. It records the
   absolute path of the Python that ran it, so the hook does not depend on Claude Code's
   PATH or import a project's `yaml.py`. It writes atomically and refuses (exit 2, nothing
   changed) a settings file that is not valid JSON or not the expected shape.
9. **The log is private.** It records the rule, its declared pattern, the tool, the session
   id and the path touched; never call content, a command line or a prompt. Files are mode 0600.
10. **These are tested.** [tests/INVARIANTS.md](tests/INVARIANTS.md) names the test behind
    each behaviour; CI checks the quick start and the first-rule block against real output;
    [DESIGN.md](DESIGN.md) records what each review found.

Settings are environment variables; a value that does not parse keeps the default. With
`INSTINCT_REPEAT=full`, `INSTINCT_PAYLOAD_BUDGET=0`, `INSTINCT_BASH_EDITS=off` and
`INSTINCT_SHADOW=off` the output is the plain matcher's, byte for byte. Full table:
[docs/delivery-policy.md](docs/delivery-policy.md).

## Measured delivery

The defaults come from about 90 days of the author's daily use with a few hundred rules
(265,423 logged rule matches; the log stays private):

- **93.8% of matches were repeats** of a rule already shown in that session (measured before
  repeat suppression existed). Hence one line for a repeat.
- **Large output is lost.** Claude Code keeps hook output over 10,000 characters out of the
  model's context. In one measured week, 942 of 3,785 full deliveries (25%) never reached the
  model; the share varies with the rule set. Hence 8,000 bytes per call.

`repeat-rate` and `persistence-loss` measure both on your own log. Method and caveats:
[docs/delivery-policy.md](docs/delivery-policy.md).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok. The hook itself exits 0 once it has started: it is advisory and never blocks a tool call |
| 1 | findings: a new `fire-rate` offender, a `cofire` pair, a `breadth` report with a BROAD rule (match rate at or above `--threshold`), or `doctor` reporting a rejected rule, an unread rule file (NOT READ), OVER BUDGET, SLOW, or NOT EVALUATED (rules or Bash-write parsing the time budget did not let it check) |
| 2 | NOT EVALUABLE: it could not look (absent or empty input, nothing loaded). Never a pass |
| 64 | usage error |

Command-specific exit-2 cases: [docs/reports.md](docs/reports.md#command-specific-exit-2-cases).

## Known limits

- **Matching is lexical**, not semantic; `breadth` finds rules that match too much.
- **Bash writes are read from common forms, not by a bash parser.** Not read: `ls > f`,
  `eval`, `xargs`, `bash -c '...'`, `cp`, `mv`, `dd of=`, `curl -o`, a write made by a
  script. To see every shell command, scope a rule to `Bash` with `match_content`.
- **Not every slow regex is rejected at load.** Shapes such as `(a|aa)*$` and `(a?){30}a{30}`
  load; the per-call time budget bounds them, and it arms only on a POSIX main thread with
  no other SIGALRM handler or timer.
- **A rule is text the agent reads.** Review a cloned repository's `.claude/instincts/` like
  its `CLAUDE.md` ([SECURITY.md](SECURITY.md)).

Full list (state-file race, transcript layout, platforms): [docs/limits.md](docs/limits.md).

## Prior art and credits

Much of the code was written with AI coding agents, and separate AI sessions reviewed it
adversarially; the tests and [DESIGN.md](DESIGN.md) show what was checked.

- [joeblackwaslike/lessons-learned](https://github.com/joeblackwaslike/lessons-learned) (MIT)
  injects lessons at PreToolUse with deduplication, a 4 KB budget and an eval suite; this
  project was built independently of it.
- Anthropic's [hookify](https://github.com/anthropics/claude-code/tree/main/plugins/hookify)
  turns regex rules into warn/block hooks; [gptme](https://github.com/gptme/gptme) has
  keyword- and tool-matched lessons.
- The idea of an agent **"instinct"**, an atomic learned behaviour with a confidence, comes
  from **ECC** (formerly everything-claude-code), [affaan-m/ECC](https://github.com/affaan-m/ECC)
  (MIT), `continuous-learning-v2`. ECC's instincts are natural-language triggers. No ECC code
  is included. See [NOTICE](NOTICE).

What this project adds: the measured delivery economics behind its defaults, reports that
compute the same quantities on your own log, and reports that say NOT EVALUABLE instead of
zero. The deterministic matcher and the delivery policy are its own. The rules in
`examples/instincts/` are synthetic. Attribution corrections are welcome.

## Docs

- [docs/rules.md](docs/rules.md): set up a project, the rule format, every filter key, what counts as a Bash write
- [docs/delivery-policy.md](docs/delivery-policy.md): delivery kinds, settings, and the measurement
- [docs/reports.md](docs/reports.md): the activation log fields and the reports
- [docs/limits.md](docs/limits.md): the full list of known limits
- [DESIGN.md](DESIGN.md): how it was built: the contract, the reviews, what each found
- [SECURITY.md](SECURITY.md): what the hook reads and writes, file modes, the trust boundary

## License

Apache-2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
