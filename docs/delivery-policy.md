# The delivery policy

What the hook sends, when, and the measurement behind the defaults.

## When the agent sees it

The hook runs at PreToolUse and returns `additionalContext`. Per Claude Code's
hooks documentation ("Add context for Claude"), Claude Code inserts it into the
conversation at the point where the hook fired and "Claude reads the reminder on
the next model request". It does not change the call that triggered it: advice
about a command arrives with that command already on its way, and shapes what the
agent does after it. Write rules as lessons for the next step, not as a veto of
this one. To block or alter a call, a hook needs `permissionDecision` or
`updatedInput`, which this tool never uses.

## What gets delivered

| Kind | When | What the agent sees |
|---|---|---|
| `full` | first fire in a session, or after the re-arm window | a head line with the rule and the first line of its `action` (so the gist survives a preview that shows only the start), then the rest of the `action` after the `ACTIVATED [...]` marker; no line is said twice and no text is dropped: both halves split at the same boundary, any line break `str.splitlines()` knows (`\n`, `\r`, form feed, U+0085, U+2028, U+2029, ...) |
| `brief` | a repeat inside the window | one line: the rule, its count this session, the first line of advice |
| `muted` | more than `BRIEF_CAP` brief lines since the last full one | nothing |
| `budget` | the call's payload budget is spent | one closing line naming the withheld rules (logged as one `notice` row) |
| `truncated` | the rule alone is larger than the budget, or it is crowded out with no session to remember it | the full block, cut to fit the room left, saying so |
| `shadow` | a `candidate` rule matched | nothing (it is logged) |

Settings (environment):

| Variable | Default | Off / parity value |
|---|---|---|
| `INSTINCT_REPEAT` | on | `full` (or `off`, `0`, `no`): every fire is full |
| `INSTINCT_REARM_MIN` | 90 | `0` means never re-arm within a session |
| `INSTINCT_BRIEF_CAP` | 6 | `0` means no cap |
| `INSTINCT_PAYLOAD_BUDGET` | 8000 bytes | `0` or `off` means no budget |
| `INSTINCT_SHADOW` | on | `off` |
| `INSTINCT_BASH_EDITS` | on | `off` |
| `INSTINCT_MATCH_BUDGET_MS` | 1000 | `0` or `off`: matching is unbounded (see [rules](rules.md)) |
| `INSTINCT_STATE_DIR` | `$XDG_STATE_HOME/instinct-delivery` | must be an absolute path (`~` is expanded). A relative value is ignored, with a note on stderr, because it would resolve against the project directory, which can be a cloned repository. A relative `XDG_STATE_HOME` is ignored too |
| `INSTINCT_STATE_KEEP_DAYS` | 7 | session state files older than this are pruned |

Suppression is per session: a payload with no `session_id` has nothing to remember,
so every fire is `full` and nothing is suppressed (and a rule that would be
withheld is cut to fit instead). A rule with `after` also needs a session: it keeps two
counters per session (calls so far, and the call number of its last precondition match) in
the same state file, never any call content; see [rules](rules.md).

A value that does not parse keeps the default. A typo never turns a safety
setting off. With `REPEAT=full`, `PAYLOAD_BUDGET=0`, `BASH_EDITS=off` and
`SHADOW=off`, the output is the plain matcher's, byte for byte.

The hook is advisory. It never blocks a tool call and exits 0 once it has started (a missing package or a shadowing `PYTHONPATH` can stop it starting), including when its stdout is a closed pipe or a full device (the output is dropped, the failure is named on stderr, and nothing is recorded as delivered). Bad
input produces no output, and a failing rule is skipped (named on stderr)
without silencing the others. The output is written as UTF-8 whatever the
console encoding.

## The measurement behind the defaults

Measured on about 90 days of the author's own daily use (**N = 265,423**
activation rows from a rule set of a few hundred rules; the log and the rules
stay private, only these aggregates are published):

- **93.8%** of matched fires were the same rule again in the same session.
  Sessions were cut at 30-minute gaps, and this was measured *before* any
  suppression existed: nearly everything injected was text the agent had
  already been given.
- **About a quarter of full deliveries (942 of 3,785, 25%, over one week)**
  never reached the model, so a hook that emits too much silently loses its
  advice. Claude Code caps a hook's `additionalContext` at 10,000 characters:
  larger output is saved to a file in the session directory and Claude gets the
  file path plus a preview of up to the first 2,000 characters; it does not ask
  Claude to read the file. That was measured by joining the log to the
  text the model was given in the session transcripts, subagent transcripts
  included. An earlier cut that read only the main session transcripts said
  28% (1,054 of 3,765); that input was partial, because rules that fire inside
  a subagent are recorded in the subagent's transcript. The share moves with
  the rule set and the work: a 7-day window ending four days later measured
  12% (472 of 3,978).

Those two numbers are why the defaults are what they are. Only aggregates are
published; no rule text or log content is. `repeat-rate` and
`persistence-loss` compute the same quantities on your own log. `persistence-loss`
counts `truncated` deliveries as full ones: both put rule text in front of the model.
