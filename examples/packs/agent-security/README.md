# agent-security: a small rule pack

Advisory rules for the moments an agent is most likely to be steered by text it just read:
reading a credential, querying a secrets table and then writing data back out, turning on
auto-approval, piping a download into a shell, and adding an MCP server. Copy the files you
want into `.claude/instincts/` (a rule is one file) and run `instinct-delivery test`.

**SYNTHETIC DATA.** Every rule and test case was written for this repository from public
write-ups of agent attacks; none is a real transcript or holds a real credential
(`SYNTHETIC-DATA`). The rules are advisory: they put a "stop and check" in front of the agent;
they do not block anything, and an attack that never trips one of these shapes is not seen.

## The rules

- `credential-file-read`: a `Read` or `Grep` of `.ssh/`, `id_rsa`, `~/.aws/credentials`, `mcp.json`, `.env`, `*.pem`.
  The exclusion is exact: a path that contains `.example`, `.sample`, `.template` or `.dist` is silent
  (`.env.example`, `.env.production.example`, `.env.local.example`, `.env.sample`, `.env.template`, `.env.dist`),
  and so is `.envrc`. It is a substring test, so a directory such as `site.example.com/` silences the rule too. Its cases come from the tool-poisoning session.
- `credential-file-shell-read`: the shell half (`cat`, `less`, `head`, `base64`, `grep`, `scp`, and `cp` of such a file as the *source*).
  It reads the command, not just its text: a reader or a path inside a quoted argument of another command (a `sed` replacement,
  a `printf`/`echo` string, a `grep` pattern such as `"path: .mcp.json"`) is text and is silent, and so is anything after a `<<`
  heredoc start (a body written to a file). Still seen: a path in quotes with no space in it (`cat "$HOME/.ssh/id_rsa"`, the first
  quoted argument), `echo "$(cat ~/.ssh/id_rsa)"`, and `bash -c "cat ..."`. A `.env` must start its path component (`bed.env` is not one).
  A rule's path filter and content filter AND together, so a tool with a path and a tool with a command need separate files.
- `secrets-table-query`: an SQL tool whose query reads (`from`, `join`) a table named like `token`, `secret`, `credential`, `password` or `api_key`.
  The tool list is exact: it names `mcp__supabase__execute_sql`; add yours (`mcp__postgres__execute_sql`, ...) to `match_tools`.
- `secret-then-write`: `after:` a secrets-table query, within 20 calls, an SQL insert/update or a `curl`/`wget` POST.
  Cases use `before:` to replay the poisoned-ticket flow, and its benign twin (same insert, no secret read first) stays silent.
- `secret-then-publish`: the same precondition, then `create_pull_request` or `create_issue`
  (separate file: tools share one content filter, and these need none). Add an HTTP post-like tool of your own to `match_tools`.
- `agent-settings-autoapprove`: an `Edit`/`Write` (or a Bash write) to `.vscode/settings.json`, `.claude/settings*`, `.cursor/`
  whose text holds `autoApprove`, `bypassPermissions`, `dangerously-skip-permissions` or `yolo`.
  It reads the whole edit, so removing such a setting fires too.
- `remote-script-pipe`: `curl`/`wget` piped to `sh`, `bash`, `zsh`, or to `python` reading the script from the pipe
  (`| python3 -m json.tool` is formatting and is silent); also `bash <(curl ...)` and `sh -c "$(curl ...)"`.
  A `curl ... | sh` that is only text (inside a quoted argument of `sed`/`printf`/`echo`/`grep`, or after a `<<` heredoc start) is silent;
  `bash -c "curl ... | sh"` still fires. Its `follow` predicate is "the script is read or checksummed within 3 calls".
- `mcp-config-write`: a `Write`/`Edit` to `.mcp.json`, `mcp.json`, `claude_desktop_config.json` or `.claude.json`.
- `mcp-servers-block-write`: an `mcpServers` block written into some other `.json` file.

## Try it

```console
instinct-delivery test --rules-dir examples/packs/agent-security
instinct-delivery replay session.jsonl --rules-dir examples/packs/agent-security
```

`replay` takes JSON lines of `{session, tool_name, tool_input}` and shows which rule each call
delivered. Replayed over reconstructed attack sessions with a benign twin of each, the pack
delivers the expected rule on the attack call and nothing on any twin
(`tests/test_agent_security_pack.py`).

## What it does not catch

- **The GitHub flow is not caught.** An agent reads a private repository, then opens a pull request on a public one that
  publishes what it read. Telling that from ordinary work needs to compare *which repo* the two calls named, and `after`
  matches a precondition shape without comparing values across calls. The pack does not fake it: a replay of that session
  delivers nothing.
- A tool-poisoning attack whose first sign is a call to the poisoned tool (not a file read) is seen only if the files it
  asks for are read with `Read`, `Grep` or a shell reader listed above.
- The two shell rules walk at most 18 quoted strings before the command they look for, and a command with a `<<` heredoc is not
  read past it: a `curl ... | sh` that follows a heredoc in the same call is not seen. A quote left open earlier in the command hides
  what follows it. (These are the price of not firing on text; the rules are advisory.)
- Every rule matches text and paths, in file-name or table-name shapes. A renamed table, a secret read through a view,
  or a command built at run time is missed.
