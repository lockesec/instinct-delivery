#!/bin/sh
# The README quick start, and a longer tour.
#
#   sh examples/demo.sh          # the README's quick start (needs instinct-delivery on PATH)
#   sh examples/demo.sh --tour   # six scripted tool calls through every delivery kind
#
# CI runs both. demo.sh itself only checks that each step exits as shown;
# examples/check_readme.py plays the quick start and fails if a line of the README
# transcript is not in its real output (CI runs it too). The README's "Your first
# rule" block is run by tests/test_readme_first_rule.py. No model and no network
# are involved.
set -eu
# The demo owns its configuration: an INSTINCT_* setting of the caller (a rules dir, a state
# dir, a budget) must not leak in and change what the transcript shows.
for _v in $(env | sed -n 's/^\(INSTINCT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$_v"; done
unset XDG_STATE_HOME
HERE=$(cd "$(dirname "$0")" && pwd)
command -v instinct-delivery >/dev/null 2>&1 || { echo "demo: instinct-delivery is not on PATH (pip install .)" >&2; exit 1; }
WORK=$(mktemp -d "${TMPDIR:-/tmp}/instinct-demo.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
export INSTINCT_STATE_DIR="$WORK/state"
export CLAUDE_PROJECT_DIR="$WORK/project"
mkdir -p "$CLAUDE_PROJECT_DIR"
cd "$CLAUDE_PROJECT_DIR"
PHYS=$(pwd -P)      # install prints the resolved, absolute project path

if [ "${1:-}" != "--tour" ]; then
  edit() {  # one PreToolUse call, as the harness makes it: an edit that adds a print()
    printf '{"session_id":"demo","tool_name":"Edit","tool_input":{"file_path":"src/app.py","old_string":"x = 1","new_string":"print(x)"}}' \
      | instinct-delivery hook \
      | python3 -c 'import json,sys; d=sys.stdin.read(); print(json.loads(d)["hookSpecificOutput"]["additionalContext"] if d.strip() else "(nothing delivered)")'
  }
  run() {  # run <exit-code> <label> <command...>: show it, run it, check its exit code
    want=$1; label=$2; shift 2
    printf '$ %s\n' "$label"
    out=$("$@" 2>&1) && got=0 || got=$?
    [ -z "$out" ] || printf '%s\n' "$out" | sed -e "s#$PHYS#/path/to/project#g" -e "s#$CLAUDE_PROJECT_DIR#/path/to/project#g" -e "s#$INSTINCT_STATE_DIR#state#g"
    [ "$got" = 0 ] || printf '(exit %s)\n' "$got"
    [ "$got" = "$want" ] || { echo "demo: expected exit $want, got $got" >&2; exit 1; }
  }
  mkdir -p .claude/instincts
  cp "$HERE"/instincts/no-print-debugging.yaml .claude/instincts/
  run 0 "instinct-delivery install" instinct-delivery install --project .
  run 0 "hook: edit src/app.py, adding print(x)" edit
  run 0 "hook: the same kind of edit again" edit
  run 0 "instinct-delivery repeat-rate" instinct-delivery repeat-rate
  rm -rf "$INSTINCT_STATE_DIR"
  run 2 "instinct-delivery repeat-rate   # no log yet" instinct-delivery repeat-rate
  exit 0
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
call() {  # call TOOL JSON-INPUT : one hook invocation, as the harness makes it
  printf '{"session_id":"demo-session","tool_name":"%s","tool_input":%s}' "$1" "$2" \
    | instinct-delivery hook \
    | python3 -c 'import json,sys; d=sys.stdin.read(); print(json.loads(d)["hookSpecificOutput"]["additionalContext"] if d.strip() else "(nothing delivered)")'
}

say "install the hook and the synthetic rules"
instinct-delivery install --project .
cp "$HERE"/instincts/*.yaml .claude/instincts/
instinct-delivery doctor || true

say "the registered hook command, run in a project that ships its own yaml.py and json.py"
# Claude Code runs hooks in the project directory. The registered command must not import
# from it: a project-root yaml.py would otherwise run inside the hook (and break it).
REGISTERED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["hooks"]["PreToolUse"][0]["hooks"][0]["command"])' .claude/settings.json)
HOSTILE="$WORK/hostile"; mkdir -p "$HOSTILE"
for m in yaml json signal; do printf 'print("SHADOW: project code ran in the hook")\n' > "$HOSTILE/$m.py"; done
OUT=$(cd "$HOSTILE" && printf '{"session_id":"hostile","tool_name":"Edit","tool_input":{"file_path":"a.py","new_string":"print(1)"}}' | sh -c "$REGISTERED" 2>&1) \
  || { echo "demo: the registered hook command failed in a directory with yaml.py: $OUT" >&2; exit 1; }
case "$OUT" in *SHADOW*) echo "demo: project code ran inside the hook" >&2; exit 1 ;; esac
case "$OUT" in *INSTINCT*) ;; *) echo "demo: the hook delivered nothing from a directory with yaml.py" >&2; exit 1 ;; esac
echo "ok: no project module was imported; the hook ran"

say "1. edit a module and add a print(): two rules fire, in full"
call Edit '{"file_path":"src/app.py","old_string":"x = 1","new_string":"print(x)"}'

say "2. the same kind of edit again: the agent already has the text, so one line each"
call Edit '{"file_path":"src/app.py","old_string":"y = 2","new_string":"print(y)"}'

say "3. a Bash command that WRITES a module: matched as a Write of that content"
call Bash '{"command":"echo \"print(1)\" > src/gen.py"}'

say "4. edit a lockfile: the rule is a candidate, so it is logged and never shown"
call Edit '{"file_path":"poetry.lock","old_string":"a","new_string":"b"}'

say "5. a small payload budget: the stronger rule is kept, the other withheld"
INSTINCT_PAYLOAD_BUDGET=700 INSTINCT_REPEAT=full \
  call Write '{"file_path":"src/settings.py","content":"api_key = \"abcdef0123456789\""}'

say "6. a rule bigger than the whole budget is cut to fit, not starved"
call Edit '{"file_path":"CHANGELOG.md","old_string":"a","new_string":"b"}' | head -c 300; echo " ..."

say "what the log says"
instinct-delivery repeat-rate
instinct-delivery cost
echo
echo "activation log: $INSTINCT_STATE_DIR/activations.jsonl ($(wc -l < "$INSTINCT_STATE_DIR/activations.jsonl") rows)"
