"""Files a shell command visibly writes (pure; never claims an unseen write)."""
import pytest
from conftest import write_rule

from instinct_delivery.bashwrites import write_targets
from instinct_delivery.match import match
from instinct_delivery.rules import load_rules


@pytest.mark.parametrize("cmd,path,content", [
    ("cat > cfg.env <<'EOF'\nAPI_KEY=abc\nEOF", "cfg.env", "API_KEY=abc"),
    ("cat <<EOF >> notes.txt\nhello\nEOF", "notes.txt", "hello"),
    ("tee -a out.log <<EOF\nline\nEOF", "out.log", "line"),
    ("sed -i 's/a/b/' src/m.py", "src/m.py", "s/a/b/"),
    ("sed -i.bak -e 's/x/y/' f1 f2", "f1", "s/x/y/"),
    ("echo 'print(1)' > src/m.py", "src/m.py", "print(1)"),
    ("echo -n hello world >> f.txt", "f.txt", "hello world"),
    ("printf 'x\\n' >> a.txt", "a.txt", "x\\n"),
    ("python3 - <<'PY'\nfrom pathlib import Path\nPath('gen.py').write_text('x')\nPY", "gen.py", None),
    ("python3 - <<'PY'\nopen('o.txt', 'w').write('y')\nPY", "o.txt", None),
])
def test_every_visible_write_shape_is_found(cmd, path, content):
    got = write_targets(cmd)
    paths = [p for p, _ in got]
    assert path in paths, got
    if content is not None:
        assert dict(got)[path] == content


@pytest.mark.parametrize("cmd", [
    "echo 'a > b'",                                   # a quoted > is text
    "echo hi > /dev/null",                            # sinks
    "echo hi > $OUT",                                 # an unknown variable target
    "python3 - <<'PY'\nprint(open('r.txt').read())\nPY",   # reads, never writes
    "sed 's/a/b/' f",                                 # not in place
    "ls -la && grep x y",
    "",
])
def test_no_write_is_claimed_where_none_is_visible(cmd):
    assert write_targets(cmd) == []


def test_duplicates_collapse():
    assert len(write_targets("echo a > f; echo a > f")) == 1


def test_a_write_scoped_rule_fires_on_a_bash_write_with_via(rules):
    write_rule(rules, "noprint", match_tools=["Write", "Edit"], match_paths=[".py"],
               match_content=[r"print\("])
    pool = load_rules(rules).fire
    [(_r, ev)] = match(pool, "Bash", {"command": "echo 'print(1)' > src/m.py"})
    assert ev.via == "bash-write" and ev.path == "src/m.py"
    assert match(pool, "Bash", {"command": "echo 'print(1)' > src/m.py"}, bash_writes=False) == []


def test_a_rule_hit_by_the_command_is_not_delivered_twice(rules):
    write_rule(rules, "both", match_tools=["Bash", "Write"], match_content=["secret"])
    pool = load_rules(rules).fire
    hits = match(pool, "Bash", {"command": "echo secret > f.txt"})
    assert len(hits) == 1 and hits[0][1].via is None
