<h1 align="center">instinct-delivery</h1>

<p align="center"><b>Tell your coding agent once.</b> <br> It hears the lesson at the tool call where it matters.</p>

instinct-delivery is a Claude Code hook that reminds your coding agent of a lesson just
before the tool call where the lesson applies (Claude Code's PreToolUse hook).

You write each lesson as a small YAML rule, for example "when the agent edits `src/`,
remind it to run tests". A rule is called an *instinct*. Before every tool call, the hook
checks the call against your rules and adds the matching advice to the agent's context.
A rule that matches a call *fires*. Each time the hook adds advice, that is a *delivery*, and
it is logged.

Designed for:

- **Claude Code. You need it.** Delivery is a Claude Code hook, so nothing reaches any other agent.
- **Rules you write.** It ships examples, but a reminder exists only once you have a rule for it. The
  hook adds advice, and the agent decides what to do with it.
- **One project at a time.** You install it per project, and each project keeps its own rules.

It is for people who keep teaching their coding agent the same thing. The lesson is there in
every session, even when the agent writes a file through the shell instead of an editor tool. It
runs offline, with no model. The first reminder in a session is complete, a repeat gets one line,
and by default no call adds more than 8,000 bytes.

## Install

```sh
pip install git+https://github.com/lockesec/instinct-delivery
instinct-delivery install
```

Run `install` in your project directory. It registers the hook in `.claude/settings.json`
and keeps every other entry, and creates `.claude/instincts/` for your rules. The log and session
state go to `~/.local/state/instinct-delivery/`, outside the project. Restart Claude Code afterwards:
hooks are read at session start.

The registered command holds the absolute path of the Python that ran `install`, which is
specific to your machine. If `.claude/settings.json` is committed, that path goes to your team
and fails where it does not exist; run `instinct-delivery install --local` to write
`.claude/settings.local.json` (per machine, not committed) instead. `install` prints the path
it recorded.
Needs Python 3.11+ and PyYAML, on a POSIX system. If your system Python is externally
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

After `install`:

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
- [`examples/packs/agent-security`](examples/packs/agent-security): a 12-rule pack built on `after:`.
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
   no rules loaded, or a log it cannot read, exits 2.
3. **Logged means delivered.** The log row and session state are written only after the
   output reached stdout. A withheld rule is not marked delivered and goes first next time; a
   rule cut to fit is logged `truncated`, not `full`.
4. **The budget is the bytes that leave the hook**, counted as the JSON envelope carries them;
   the rows of a call sum to exactly what it emitted.
5. **A rule loads whole or not at all.** An empty `action`, a misspelled key or `status`, a pattern
   that does not compile, a missing `match_tools`: the file is `REJECTED` with the reason, and
   the other rules keep delivering.
6. **Slow patterns are caught.** A nested unbounded repeat (`(a+)+`) is rejected at load. A
   per-call time budget (1 s in total, a quarter per rule) skips a rule that runs long and
   logs it; `doctor` lists it as `SLOW`.
7. **It never blocks a tool call.** The hook exits 0 once started; bad input gives the agent no
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

## Why the defaults are what they are

The defaults come from daily use of a large rule set, not from theory.

- **Most matches are repeats.** Once a rule has been shown in a session, it tends to match again and again. So a repeat gets one line instead of the full text.
- **Large output gets lost.** Claude Code keeps hook output over 10,000 characters out of the model's context, and in real use a meaningful share of full deliveries never reached the model. So each call is capped at 8,000 bytes.

`repeat-rate` and `persistence-loss` measure both on your own log.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok. The hook itself exits 0 once it has started: it is advisory and never blocks a tool call |
| 1 | findings: a new `fire-rate` offender, a `cofire` pair, a `breadth` report with a BROAD rule (match rate at or above `--threshold`), or `doctor` reporting a rejected rule, an unread rule file (NOT READ), OVER BUDGET, SLOW, NOT EVALUATED (rules or Bash-write parsing the time budget did not let it check, or calls the hook could not read), CANNOT FIRE, a state directory it cannot write, or a hook that is NOT REGISTERED or MAY NOT RUN |
| 2 | NOT EVALUABLE: it could not look (absent or empty input, nothing loaded). Never a pass |
| 64 | usage error |

Command-specific exit-2 cases: [docs/reports.md](docs/reports.md#command-specific-exit-2-cases).

## Where it falls short

- **It is advice, not a guard.** It never blocks a call, and a padded command can make a rule, a security rule included, run out of time, so the agent gets nothing on that call. The log and `doctor` record it afterwards.
- **How many real attacks the security pack catches has not been measured.** Its rules come from published attacks and are tested on written cases.
- **Matching is lexical.** A renamed file, an unusual command or a command held in a variable gets past it.

How it was checked, known holes and what is untested: [docs/limits.md](docs/limits.md).

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

What this project adds: why its defaults are what they are, reports that
compute the same quantities on your own log, and reports that say NOT EVALUABLE instead of
zero. The deterministic matcher and the delivery policy are its own. The rules in
`examples/instincts/` are synthetic. Attribution corrections are welcome.

## Docs

- [docs/rules.md](docs/rules.md): set up a project, the rule format, every filter key, what counts as a Bash write
- [docs/delivery-policy.md](docs/delivery-policy.md): delivery kinds, settings, and the measurement
- [docs/reports.md](docs/reports.md): the activation log fields and the reports
- [docs/limits.md](docs/limits.md): how it was checked, known holes, what is untested
- [DESIGN.md](DESIGN.md): how it was built: the contract, the reviews, what each found
- [SECURITY.md](SECURITY.md): what the hook reads and writes, file modes, the trust boundary

## License

Apache-2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
