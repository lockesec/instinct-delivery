# The activation log and the reports

## The activation log

`STATE_DIR/activations.jsonl`, one row per matched rule per call:

| Field | |
|---|---|
| `ts` | UTC ISO time; every row of one call carries the same stamp |
| `rule` | the rule's file name (its identity) |
| `domain`, `confidence`, `tool` | from the rule and the call. A rule without a `domain` or `confidence` logs `null` for it (a log written by an earlier build may carry the string `"?"` instead) |
| `session_id` | the join key; `null` when unknown, never a substitute. An id longer than 128 characters is cut and ends in `~` and a digest of the whole id, so two long ids that differ late stay distinct |
| `file_path` | the path the hit was judged on, or `null`. A lone surrogate in any text of a row (`\ud800` in a path) is written as its escape spelling, so the row is never dropped |
| `matched` | the rule's own declared pattern, path or command that matched. **Never the text of the call.** |
| `delivery` | `full`, `brief`, `truncated`, `muted`, `budget`, `shadow`, `slow` (the rule ran past its per-rule time cap and was skipped for this call; `bytes` is 0 and `reason` says why), `unevaluated` (the rule, or the Bash-write parsing when `rule` is `null`, was not evaluated for this call, and `reason` says why: the call's whole time budget was already spent, or, for a rule with `after`, `after: no session id to remember earlier calls`; not a verdict on the rule), or `notice` for the closing line of withheld rules (`rule` is `null` on that row) |
| `bytes` | UTF-8 bytes this row put in front of the agent; the rows of a call sum to exactly what it emitted |
| `via` | `"bash-write"` when the hit came through a file written by a Bash command |
| `shadow` | `true` on shadow rows |

## Reports

`slow` and `unevaluated` rows are not matches and carry no bytes: `repeat-rate`,
`fire-rate`, `cofire` and `breadth` leave them out, and `cost` lists them only as
their own counts (a total in the text line, per session in `--json`). A rule that is often skipped therefore looks
*quieter* in those reports, not broken; `doctor` is where skipped rules are named.

Each report prints its denominator. Absent or empty input is **NOT EVALUABLE
(exit 2)**, never a zero. A log row with a wrong-typed field (a `bytes` that is
not a whole number, an unknown `delivery`, a list where `rule`, `session_id` or `ts` should be
text) is excluded and counted in the report, never read as zero; with a window (`--days`),
a row dated in the future or with no parsable timestamp is also excluded and counted
(`future_rows`, `unstamped_rows`), not read as "within the last N days"; `--days 0` is a window of
zero length and says so; `fire-rate` finding no offender while rows were
excluded (malformed, dated in the future, or without a timestamp) is NOT EVALUABLE, not clean:
it prints `NOT EVALUABLE` with the counts that caused it and exits 2. Every exit 2 of a report
says `NOT EVALUABLE` and its cause, and with `--json` prints
`{"status": "NOT_EVALUABLE", "cause": "..."}` (a report that had counts keeps them beside it).
A `--days` too large for a date window, and a `--log` that is a directory or unreadable, name
that cause. A report that fails unexpectedly exits 2, never 1.

| Command | Answers |
|---|---|
| `repeat-rate` | How often was a match the same rule again in the same session? Rows are grouped by `session_id`, else by 30-minute gaps; `--kinds` says what counts. |
| `cost` | Bytes delivered per session, split into first full delivery vs re-arm, with an optional join to human review verdicts. Rows with no session id are always full by design (nothing is remembered), so their bytes are reported separately and are neither first nor re-arm; the re-arm share is over in-session full bytes only, and is `n/a` when there are none. |
| `fire-rate` | Which rules delivered more than `--byte-cap` bytes (a whole number >= 1) in some session? A ratchet: `--baseline` holds known offenders, and a new one exits 1. |
| `cofire` | Which pairs of rules fire on the same call? (Jaccard, with a support floor) |
| `persistence-loss --transcripts DIR` | How many logged full deliveries are missing from the text the model was given (session and subagent transcripts)? |
| `breadth --transcripts DIR` | What share of eligible calls does each rule match, replayed through the real matcher over the newest `--sessions` transcript files? A Bash write counts as eligible only for a rule scoped to `Write` or `Edit` (the two tools a Bash write is matched as). A rule with `after` is replayed with a fresh sequence per transcript file, calls in file order, so its precondition is evaluated (the entry says `after`); see [rules](rules.md). Each call is matched under the hook's per-call time budget (`INSTINCT_MATCH_BUDGET_MS`); a call on which a rule ran out of it is `skipped` for that rule and left out of its rate's denominator. |
| `replay FILE.jsonl [--rules-dir D] [--json]` | What would these recorded calls have delivered? Rows of `{session, tool_name, tool_input}` (text, text, object; any other field is ignored) run in file order through the real engine, one session per `session` value, on a throwaway state directory that is deleted afterwards: nothing is written to your log or state. Prints, per call, the session, the **step** (the row's 0-based index in the file, every row counted) the tool and the rules delivered with how (`full`, `brief`, `shadow`, `unevaluated`, ...), then a per-session summary and the totals. Lines that are not JSON, not an object, or not a tool call (a prompt, a tool result: no `tool_name` or no `tool_input` object) are skipped and COUNTED, by reason. A row with no usable `session` runs with no session (`(no session)`: nothing is remembered, so an `after` rule logs `unevaluated`). Exit 0; 2 NOT EVALUABLE when the file cannot be read, holds no replayable row (the row counts are printed), or the rules directory is missing or unreadable. |
| `follow-rate --transcripts DIR [--min-n N]` | Did the agent do what the advice asked? For each rule with a `follow` predicate: of its deliveries (full, brief, truncated) that join a session transcript, how many were followed by a call matching the predicate within the rule's next `within` calls, beside the **base rate**: the same predicate over every window of that many calls in those sessions, delivered or not. A rule without a predicate is NOT EVALUABLE, not 0; so is a rule with a predicate when no delivery joined (`followed` is null in `--json`, and the line names the cause: no deliveries in the window, none with a transcript, none with a later call, with the delivery / no-transcript / no-later-call counts beside it); below `--min-n` joined deliveries (default 10) only the counts print, the base rate's too (its hit and window counts, no percentage). A session id that is not a bare file name (`/`, `\`, `..`, NUL) is refused and counted as no transcript, never joined outside `--transcripts`. Deliveries with no transcript, or no later call, are counted and left out of the denominator. Not causal: without a holdout it counts what followed, not what the delivery caused. |
| `audit --transcripts DIR` | What did a rule actually fire on? Samples fires per rule, joins each to its triggering call, and flags a rule whose evidence is mostly one bare word. See [`audit`](#audit-look-at-the-fires). `audit label` records a verdict. |
| `labels stats\|add` | Human TP/FP judgements. No precision is reported below n = 20, and the example a rule was written from cannot count as a false positive. `labels add` takes `--verdict` `TRUE_POSITIVE` or `FALSE_POSITIVE`, and `--source` `transcript_sample` (a call sampled from a real session; the only source counted as holdout) or `rule_evidence` (the example the rule was written from; it cannot be a `FALSE_POSITIVE`). Any other value is a usage error (64). |

`--days` takes a number of days >= 0; a negative or non-numeric value is a usage error (64).

Exit codes: 0 ok, 1 findings (a new `fire-rate` offender, a `cofire` pair found, a
`breadth` report with a BROAD rule (rate at or above `--threshold`), or
`doctor` flagging a rejected rule, an unread rule file, OVER BUDGET, SLOW or NOT
EVALUATED), 2 NOT EVALUABLE, 64 usage. A report with nothing to count is also 2:
`fire-rate` or `cost` on a log with no delivered rows (only `slow`, `shadow`, `notice` or `unevaluated` ones), `breadth` when no rule had an eligible call,
`cofire` on fewer calls than `--min-support`, `labels stats` on a store of only unreadable
lines, `repeat-rate` on rows that no session can be assigned to.
`persistence-loss` counts `truncated` deliveries as full ones.
`audit` exits 1 when a rule is flagged BARE and 2 when nothing could be sampled or no rule had enough fires to evaluate the flag.

**`follow-rate`, what it reads.** A delivery's later calls are the tool calls in
`<session_id>.jsonl` stamped strictly after the log row (calls in the same assistant
message as the triggering call share its stamp and are not counted); subagent
transcripts are not read, because their calls are another agent's. The predicate is
matched by the real matcher, Bash writes included. Replayed on 8 of the author's own
Claude Code sessions with a synthetic delivery at every `.py` edit and a predicate
of `pytest` within 5 calls (no advice was shown in them, so this is the habit without
it): 361 of 524 edits were followed by a test run (69%), against a base rate of 1,671
of 4,681 five-call windows (36%). Private data; the method is
`follow-rate` on your own log.

## `audit`: look at the fires

`repeat-rate`, `cost` and `labels` count; `audit` lets a person look.
`instinct-delivery audit --transcripts DIR [--log L] [--n 10] [--days 30] [--until DATE] [--rule NAME] [--brief] [--json]`
draws up to `--n` fires per rule from the log rows of the window whose `delivery` is a
real match (`full`, `brief`, `truncated`, `shadow`; never `notice`, `slow`, `unevaluated`,
`muted` or `budget`). The draw is seeded by the rule name and the window's end date
(`--until`, default today UTC), so the same log and window give the same sample. Each
sampled fire prints its id, its time, the tool, the matched text
as logged, and the first 160 characters of the tool call that triggered it, read from
`<session_id>.jsonl` (see "The join" below; `call not found` when there is none).

**Fire ids** are content-addressed: `<rule>#<8 hex>`, the first 8 hex digits of a SHA-256 of
the row's `ts`, `session_id` and `tool` (no call content). The draw, `--n` and the window
date do not enter it, so an id means the same fire whenever it is used, however many fires
arrive after it was printed. (Two rows of one rule with the same stamp, session and tool
share an id; they are indistinguishable in the log.)

**The join.** A fire is joined to a call of the same tool in the session's transcript,
stamped at or before the log row. The candidates are the calls of that tool within 2 seconds
of the latest such call (one assistant message: parallel calls are stamped milliseconds
apart, and the hook row lands after all of them). When the rule is loaded the join takes the
LATEST candidate the rule's own match keys match (re-run on that call). If none matches: with
exactly one candidate the rule no longer matches the call it joined ("the rule changed
since"); with several, `call not identified (several calls of this tool in one message)`,
counted apart (`unidentified`) and left out of every count. With no rule loaded the join
takes the latest candidate, which for parallel calls can be the wrong one; load the rules
(`--rules-dir`) to resolve it. The log records the rule's own declared
pattern, never the call, so the call head is what lets you judge the fire.

For a `match_content` rule the logged `matched` is the pattern's source, not the text it
matched. `audit` recovers the text locally: when the triggering call was joined from the
transcript, it re-runs the loaded rule's matcher on that call and prints the substring as
`matched text:` (one line, at most 80 characters) beside `matched rule key:` (the logged
token). The text is read from your transcript at audit time and is never logged; the hook
logs no more than before. The bare-token share judges the recovered text; a fire whose call
was not joined (or whose rule is not in `--rules-dir`) falls back to the logged key and is
counted as `text not recovered`; a joined call the rule no longer matches says so ("the
rule changed since") and is left out of the denominator rather than guessed. For a rule with
`after`, the re-run uses the rule's own match keys only (one call has no earlier calls), and the
audit says so under the rule: an absent precondition is never reported as "the rule changed".

Per rule it prints what matched (distinct matched texts and counts) and the **bare-token
share**: sampled fires whose matched text is a single token of ordinary characters (no
whitespace and no regex syntax: `README`, `verdict`, `src/models.py`) over sampled fires
with matched text, printed as `k of n`. A rule is flagged **BARE** at n >= 5 and a share
>= 0.6; below n = 5 the flag is NOT EVALUABLE and no rate is printed. On the author's
private corpus, 7 of 10 sampled fires were single-word matches. Fires that logged no
matched text are counted apart, not in the denominator. This is a lexical heuristic: a
bare word can be the right trigger, so BARE means "look", not "wrong".

A BARE rule's line also says how many of its bare matches are a **plain word** (letters
and inner hyphens: `README`, `verdict`) rather than an identifier or a path
(`spec_from_file_location`, `src/models.py`). On a 400-rule private corpus, rules firing
mostly on a plain word were judged right 0.10 of the time (n = 50 judged fires), against
0.28 for other bare tokens and 0.33 for the rest; `--brief` lists the plain words first.

`--brief` adds, for each BARE rule, its current `match_*` keys, the bare tokens it fired
on with counts and one call each, and the options (add `match_paths`, add a
`match_exclude` for the commonest wrong context, require a second `match_content`
pattern, scope `match_tools`). It is deterministic, uses no model, and edits no rule file.

`audit label FIRE_ID --verdict TRUE_POSITIVE|FALSE_POSITIVE --store PATH [--reason T]
[--labeler N]` (with the same `--log`, `--transcripts`, `--n`, `--days`, `--until`)
appends one row to the `labels` store with source `transcript_sample` and the call head as
the excerpt, so `labels stats` counts it as a hold-out label. The id is looked up in the log
rows of the window (not in a sample): a window that does not reach the fire, or an id no row
has, exits 64 and writes nothing. On success it prints the fire's time, tool and call head,
so you see which fire got the verdict. Exit codes: 0 ok, 1 a BARE rule, 2 NOT
EVALUABLE (no rows in the window, no transcripts dir, nothing sampled, or no rule with n >= 5), 64 usage.
