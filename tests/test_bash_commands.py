"""Which command shapes the Bash-write reader counts as a write, with what target
and content: redirects and file descriptors, `tee`, `sed -i`, `cat`, `cp`,
`python -`, wrappers and prefixes, compound statements, assignments. Forms it does
not read stay unread, and it claims no write it did not see."""
from pathlib import Path

import pytest

from instinct_delivery import bashwrites
from instinct_delivery.bashwrites import write_targets


@pytest.mark.parametrize("cmd,paths", [
    ("echo x | tee src/a.py", ["src/a.py"]),
    ("tee -a notes.txt < in.txt", ["notes.txt"]),
    ("cat in | tee -a log.txt", ["log.txt"]),
    ("cat in | tee a.txt b.txt", ["a.txt", "b.txt"]),
    ("cat in | sudo tee /etc/x.conf", ["/etc/x.conf"]),
    ("cat in | tee out.txt > /dev/null", ["out.txt"]),
    ("cat in | tee out.txt 2>&1", ["out.txt"]),
    ("cat in | tee -- 'my file.txt'", ["my file.txt"]),
    ("tee f.txt <<EOF\nbody\nEOF", ["f.txt"]),
])
def test_tee_writes_are_detected(cmd, paths):
    assert [p for p, _ in bashwrites.write_targets(cmd)] == paths


@pytest.mark.parametrize("cmd", [
    "cat in | tee /dev/null", "cat in | tee", "cat in | tee -a", "echo tee",
    "ls | grep tee", "cat in | tee $OUT",
])
def test_tee_near_misses_are_not_writes(cmd):
    assert bashwrites.write_targets(cmd) == []


def test_a_heredoc_tee_is_reported_once_with_its_body():
    assert bashwrites.write_targets("tee f.txt <<EOF\nbody\nEOF") == [("f.txt", "body")]


NOT_WRITES = [
    "echo $((x > 5))",
    "echo $(( x > 5 ))",
    "echo a -> b.py",
    "echo $(cat x > y.py)x",       # the redirect target is y.py, never `y.py)`
    "echo $(( 1 ))",
    "echo ((a > b))",
    "echo " + "[" * 2 + " a > b " + "]" * 2,
    "echo done >&2",
    "echo hi 2>err.log",
    "echo hi 2>&1",
]


@pytest.mark.parametrize("cmd", NOT_WRITES)
def test_no_phantom_write_paths(cmd):
    for path, _ in write_targets(cmd):
        assert path in ("y.py",), (cmd, path)
        assert ")" not in path and "$" not in path


def test_arithmetic_comparison_is_not_a_write():
    assert write_targets("echo $((x > 5))") == []
    assert write_targets("echo $(( 3 > 2 ))") == []
    assert write_targets("echo a -> b.py") == []


def test_a_command_substitution_is_its_own_segment():
    assert write_targets("echo $(cat x > y.py)") == [("y.py", "")]
    assert write_targets("echo $(echo hi > y.py)") == [("y.py", "hi")]
    assert write_targets('echo "$(echo hi > y.py)"') == [("y.py", "hi")]
    assert write_targets("echo `echo hi > y.py`") == [("y.py", "hi")]
    assert write_targets("echo $(a; echo hi > y.py)") == [("y.py", "hi")]


def test_near_misses_still_write():
    assert write_targets("echo hi > a.py") == [("a.py", "hi")]
    assert write_targets("echo $((1+2)) > a.py") == [("a.py", "$((1+2))")]
    assert write_targets("echo $(date) >> a.log") == [("a.log", "$(date)")]
    assert write_targets("echo a - > b.py") == [("b.py", "a -")]
    assert write_targets("echo hi 1> a.py") == [("a.py", "hi")]
    assert write_targets("echo hi &> a.py") == [("a.py", "hi")]
    assert write_targets("echo hi 2>err.log > a.py") == [("a.py", "hi")]


def test_a_subshell_closing_paren_is_not_part_of_the_target():
    assert write_targets("(cd x; echo hi > y.py)") == [("y.py", "hi")]


def test_a_substitution_nested_deeply_does_not_crash():
    cmd = "echo " + "$(" * 3000 + "echo hi > y.py" + ")" * 3000
    write_targets(cmd)          # must return, not raise


KEY = "AKIA" + "ABCDEFGHIJKLMNOP"      # a synthetic key-shaped string


def test_content_is_not_cut_when_the_redirect_has_no_space():
    assert write_targets("echo " + KEY + ">creds.cfg") == [("creds.cfg", KEY)]
    assert write_targets("echo $x>f") == [("f", "$x")]


def test_a_digit_ending_a_word_is_not_an_fd():
    assert write_targets("echo secret=abc12>.env") == [(".env", "secret=abc12")]
    assert write_targets("echo secret=abc12 >> .env") == [(".env", "secret=abc12")]


def test_only_a_lone_digit_token_is_an_fd():
    assert write_targets("echo hi 2>.env") == []          # stderr
    assert write_targets("echo hi 1>.env") == [(".env", "hi")]
    assert write_targets("echo 5>f.py") == []              # fd 5, echo has nothing to write


def test_echo_directly_followed_by_a_redirect_is_seen():
    assert write_targets("echo>f.py") == [("f.py", "")]
    assert write_targets("echo>>f.py") == [("f.py", "")]
    assert write_targets("echoes>f.py") == []              # not echo


S2_FOUND = [
    ("if true; then echo A > .env; fi", [".env"]),
    ("for f in a; do echo A >> .env; done", [".env"]),
    ("while read l; do echo $l >> f; done", ["f"]),
    ("f() { echo a > f.txt; }", ["f.txt"]),
    ("function g { echo a > g.txt; }", ["g.txt"]),
    ("! echo a > f", ["f"]),
    ("time echo a > f", ["f"]),
    ("FOO=1 echo a > .env", [".env"]),
    ("A=1 B=2 echo a > .env", [".env"]),
    ("{ tee f; }", ["f"]),
    ("(tee f)", ["f"]),
    ("echo a | (tee f)", ["f"]),
    ("echo a |& tee f", ["f"]),
    ("echo a >| f", ["f"]),
    ("echo a >& f", ["f"]),
    ("echo a &> f", ["f"]),
    ("sudo -n tee f", ["f"]),
    ("sudo -u root tee f", ["f"]),
    ("sudo -u root -g wheel tee -a f", ["f"]),
    ("env X=1 tee f", ["f"]),
    ("env -i X=1 tee f", ["f"]),
    ("command tee f", ["f"]),
    ("nice -n 5 tee f", ["f"]),
    ("/usr/bin/tee f", ["f"]),
    ("/bin/echo a > f", ["f"]),
    ("tee 2>&1 f", ["f"]),
    ("tee 1>/dev/null f", ["f"]),
    ("echo a >y >z", ["y", "z"]),
    ("echo a | tee >(cat) f", ["f"]),
    ("if x; then sudo tee f; else echo b > g; fi", ["f", "g"]),
    ("case x in a) echo hi > f ;; esac", ["f"]),
]


def _paths(cmd):
    return [p for p, _ in write_targets(cmd)]


@pytest.mark.parametrize("cmd,paths", S2_FOUND)
def test_writes_in_compound_statements_and_behind_prefixes_are_found(cmd, paths):
    assert sorted(set(_paths(cmd))) == sorted(paths), write_targets(cmd)


def test_content_survives_a_prefix():
    assert write_targets("FOO=1 echo secret > .env") == [(".env", "secret")]
    assert write_targets("if true; then echo -n 'a b' >> f; fi") == [("f", "a b")]


def test_quiet_direction_prefixes_do_not_invent_writes():
    for cmd in ("if true; then echo A; fi", "sudo -u root ls", "env X=1 ls > out.txt",
                "command -v tee f", "FOO=1 ls", "time ls", "tee", "echo a | (cat)",
                "(cd src && ls)"):
        assert write_targets(cmd) == [], cmd


SED = [
    ("sed --follow-symlinks -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -n -i 's/a/b/p' f.py", [("f.py", "s/a/b/p")]),
    ("sed --quiet -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed --regexp-extended -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -E -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -l 5 -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -i -e x -- f", [("f", "x")]),
    ("sed -i '' 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -i.bak 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed --in-place=.bak 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -i --expression=s/a/b/ f.py g.py", [("f.py", "s/a/b/"), ("g.py", "s/a/b/")]),
    ("sed -i --expression 's/a/b/' f.py", [("f.py", "s/a/b/")]),
    ("sed -i --file=prog.sed f.py", [("f.py", "")]),
    ("sed -i -f prog.sed f.py", [("f.py", "")]),
    ("sed -i -es/a/b/ f.py", [("f.py", "s/a/b/")]),
    ("sed -ni 's/a/b/p' f.py", [("f.py", "s/a/b/p")]),
    ("sed -i -e 's/a/b/' -e 's/c/d/' f.py", [("f.py", "s/a/b/\ns/c/d/")]),
    ("sed -i 's/a/b/' -- -weird", [("-weird", "s/a/b/")]),
    ("/usr/bin/sed -i 's/a/b/' f.py", [("f.py", "s/a/b/")]),
]


@pytest.mark.parametrize("cmd,want", SED)
def test_sed_options_are_parsed_not_taken_for_operands(cmd, want):
    assert write_targets(cmd) == want


def test_sed_without_in_place_or_files_is_no_write():
    for cmd in ("sed --follow-symlinks 's/a/b/' f.py", "sed -n -e p f", "sed -i 's/a/b/'",
                "sed --quiet 's/a/b/p' f"):
        assert write_targets(cmd) == [], cmd


@pytest.mark.parametrize("cmd,paths", [
    ("echo a >out_$(date +%s).txt", []),
    ("echo a >f.txt$(echo b>g.txt)", ["g.txt"]),
    ("echo a > `echo f`.txt", []),
    ("echo a > \"x_$(date)\"", []),
    ("echo $(date) > f", ["f"]),
])
def test_a_command_substitution_in_a_target_leaves_no_garbage_path(cmd, paths):
    assert _paths(cmd) == paths, write_targets(cmd)


def test_echo_content_has_no_redirect_text():
    assert write_targets("echo a <in >&2 > out") == [("out", "a")]
    assert write_targets("echo a 2>&1 > out") == [("out", "a")]


S1_ASSIGNED = [
    'FOO="a b" echo secret > config.yaml',
    "FOO='x' echo secret > config.yaml",
    "FOO=$(date) echo secret > config.yaml",
    "FOO=\\ x echo secret > config.yaml",
    'LC_ALL="C" echo secret > config.yaml',
    'PATH="$X:$PATH" echo secret > config.yaml',
    'A=1 B="2 3" C=$(x) echo secret > config.yaml',
    'env FOO="a b" echo secret > config.yaml',
    'FOO="a b" sudo -n echo secret > config.yaml',
    'if true; then FOO="a b" echo secret > config.yaml; fi',
]


@pytest.mark.parametrize("cmd", S1_ASSIGNED)
def test_a_quoted_or_substituted_assignment_does_not_hide_the_command(cmd):
    assert write_targets(cmd) == [("config.yaml", "secret")]


def test_tee_behind_a_quoted_assignment():
    assert write_targets('FOO="a b" tee f') == [("f", "")]


@pytest.mark.parametrize("cmd", [
    '"FOO=1" echo a > f',            # a quoted name is the command, not an assignment
    'FOO"x"=1 echo a > f',
])
def test_quiet_direction_a_quoted_name_is_a_command_not_an_assignment(cmd):
    assert write_targets(cmd) == []


def test_quiet_direction_an_assignment_alone_writes_nothing():
    assert write_targets('FOO="a b"') == []
    assert write_targets('FOO="a > b"') == []


def test_a_group_redirect_is_a_write_of_unknown_content():
    assert write_targets("{ echo a; } > f") == [("f", "")]
    assert write_targets("( echo a ) > f") == [("f", "")]
    assert write_targets("{ echo a; } >> f") == [("f", "")]
    assert sorted(write_targets("{ echo a > g; } > f")) == [("f", ""), ("g", "a")]


def test_quiet_direction_a_group_redirect_to_a_dup_or_sink_or_stderr_is_no_write():
    assert write_targets("{ echo a; } >&2") == []
    assert write_targets("( echo a ) > /dev/null") == []
    assert write_targets("( echo a ) 2> f") == []
    assert write_targets("{ echo a; } 2>&1") == []


@pytest.mark.parametrize("cmd", [
    "sudo --user bob tee f", "sudo --group wheel --user bob tee f", "sudo --user=bob tee f",
    "time -f %e tee f", "time --format %e tee f", "time -o out.txt tee f", "nice --adjustment 5 tee f",
    "env --unset X tee f", "env --chdir /x tee f", "stdbuf --output L tee f", "sudo -n --user bob tee f",
])
def test_wrapper_long_options_with_values_are_skipped(cmd):
    assert write_targets(cmd) == [("f", "")]


def test_quiet_direction_a_wrapper_option_value_is_not_taken_for_the_command():
    assert write_targets("sudo --user tee echo a") == []     # `tee` is the user name here


def test_cat_with_a_file_operand_ignores_stdin():
    assert write_targets("cat file <<A | tee f\nbody\nA") == [("f", "")]
    assert write_targets("cat file <<A > f\nbody\nA") == [("f", "")]
    assert write_targets("cat -- file <<A | tee f\nbody\nA") == [("f", "")]


def test_quiet_direction_cat_options_and_dash_read_stdin():
    assert write_targets("cat -n <<A | tee f\nbody\nA") == [("f", "body")]
    assert write_targets("cat - <<A | tee f\nbody\nA") == [("f", "body")]
    assert write_targets("cat -n <<A > f\nbody\nA") == [("f", "body")]
    assert write_targets("cat <<A | tee f\nbody\nA") == [("f", "body")]


def test_a_stdout_redirect_of_cat_carries_nothing_through_the_pipe():
    assert write_targets("cat <<A >&2 | tee f\nbody\nA") == [("f", "")]
    assert write_targets("cat <<A > $x | tee f\nbody\nA") == [("f", "")]
    assert write_targets("cat <<A &>/dev/null | tee f\nbody\nA") == [("f", "")]


def test_quiet_direction_a_stderr_redirect_still_carries():
    assert write_targets("cat <<A 2>&1 | tee f\nbody\nA") == [("f", "body")]
    assert write_targets("cat <<A 2>/dev/null | tee f\nbody\nA") == [("f", "body")]


def test_the_text_goes_only_to_the_last_stdout_redirect():
    assert write_targets("echo a >y >z") == [("y", ""), ("z", "a")]
    assert write_targets("echo a >y >&2") == [("y", "")]
    assert write_targets("echo a >y >$z") == [("y", "")]
    assert write_targets("echo a >y 2>&1") == [("y", "a")]
    assert write_targets("echo a >y 2>z") == [("y", "a")]
    assert write_targets("cat <<A >y >z\nb\nA") == [("y", ""), ("z", "b")]


ROOT = Path(__file__).resolve().parents[1]


def test_arithmetic_command_and_bare_assignment_redirects():
    assert write_targets("(( x )) > f") == [("f", "")]
    assert write_targets('A="b" > f') == []
    doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    assert "`(( x )) > f`" in doc and "`A=\"b\" > f`" in doc


def content(cmd, path):
    return [c for p, c in write_targets(cmd) if p == path]


def test_cp_is_not_a_stdin_copier():
    assert content("cp <<E > y\nx\nE", "y") == [""]


def paths(cmd):
    return sorted({p for p, _c in write_targets(cmd)})


def test_python_in_an_earlier_command_does_not_own_a_cat_heredoc():
    cmd = "git commit -m 'python - fix' && cat > doc.md <<A\nopen('x.txt','w')\nA"
    assert paths(cmd) == ["doc.md"]


def test_a_cat_body_before_a_python_body_is_not_python():
    cmd = ("cat > notes.md <<A; python - <<B\nPath('x.py').write_text('1')\nA\n"
           "Path('y.py').write_text('2')\nB")
    assert "x.py" not in paths(cmd) and "y.py" in paths(cmd) and "notes.md" in paths(cmd)


@pytest.mark.parametrize("cmd", ["cat python3 - <<E > f\nopen('x','w').write('1')\nE",
                                 "echo python3 - <<E > f\nopen('x','w').write('1')\nE"])
def test_python_must_be_the_command_word(cmd):
    assert "x" not in paths(cmd)


def test_python_behind_options_is_still_python():
    assert "x.txt" in paths("python3 -u - <<E\nopen('x.txt','w').write('1')\nE")


@pytest.mark.parametrize("pre", ["sudo -u x ", "sudo -u x -- ", "env -u X ", "nice -n 5 ", "time -f %e ",
                                 "doas -u x ", 'FOO="a b" ', ""])
def test_python_stdin_behind_wrappers_with_option_values(pre):
    assert "out.txt" in paths(f"{pre}python3 - <<'E'\nopen('out.txt','w').write('1')\nE")


@pytest.mark.parametrize("opt", ["-W ignore", "-X dev", "-u", "-I"])
def test_python_options_with_values_before_the_dash(opt):
    assert "out.txt" in paths(f"python3 {opt} - <<'E'\nopen('out.txt','w').write('1')\nE")


@pytest.mark.parametrize("cmd", ["python3 -c 'pass' - <<'E'\nopen('x','w')\nE",
                                 "python3 script.py - <<'E'\nopen('x','w')\nE"])
def test_a_program_argument_is_not_stdin(cmd):
    assert "x" not in paths(cmd)
