"""Quoting, expansion, comment and continuation rules of the Bash-write reader: a
quote, an escape, a `#`, an expansion or a backtick must neither hide a real write
nor invent a phantom one."""
import pytest

from instinct_delivery.bashwrites import write_targets


def _paths(cmd):
    return [p for p, _ in write_targets(cmd)]


@pytest.mark.parametrize("cmd,paths", [
    ('echo "a \\" b" > f.txt', ["f.txt"]),
    ('echo "a \\" > b" > f.txt', ["f.txt"]),
    ('echo "say \\"x > y\\" now" > f.txt', ["f.txt"]),
    ('echo "x" > "a\\"b.txt"', ['a"b.txt']),
    ("echo a\\ b > c", ["c"]),
    ("echo it\\'s > f", ["f"]),
])
def test_escaped_quotes_do_not_break_quote_tracking(cmd, paths):
    assert _paths(cmd) == paths, write_targets(cmd)


def test_content_keeps_the_escaped_quote_and_single_quotes_do_not_escape():
    assert write_targets('echo "say \\"x > y\\" now" > f.txt') == [("f.txt", 'say "x > y" now')]
    # inside single quotes a backslash is a plain character: the quote closes
    assert write_targets("echo 'a\\' > f") == [("f", "a\\")]


@pytest.mark.parametrize("cmd", [
    "echo ok # > .env",
    "cat a.txt # > x",
    "echo ok # a; echo b > g",
    "echo hi \\> src/x",
    "echo hi \\>src/x",
    "echo 'x' # > f",
    "echo hi > /dev/tty",
    "echo hi > /dev/fd/2",
    "echo hi >/dev/fd/12",
    "echo a >> /dev/null",
])
def test_no_phantom_write(cmd):
    assert write_targets(cmd) == [], write_targets(cmd)


def test_a_hash_inside_a_word_or_quotes_is_not_a_comment():
    assert _paths("echo a#b > f") == ["f"]
    assert _paths("echo '# x' > f") == ["f"]
    assert _paths("echo $# > f") == ["f"]


def test_a_line_continuation_joins_the_lines():
    assert write_targets("echo a \\\n> f") == [("f", "a")]
    assert write_targets("tee \\\n  f") == [("f", "")]
    assert write_targets("echo a \\\n b > f") == [("f", "a b")]


@pytest.mark.parametrize("cmd", [
    "echo ${x#*>}", "echo ${a[@]/>/x}", "echo $[1>2]", "echo ${x:-a>b}", "echo ${x//>/-}",
    "echo ${x:-${y:-a>b}}", "echo $[ a[1] > 2 ]", "echo ${x%>*} ${y#>}",
])
def test_an_expansion_holding_a_gt_is_not_a_write(cmd):
    assert write_targets(cmd) == []


def test_the_real_redirect_after_an_expansion_is_still_found():
    # the command word is itself an expansion: still its stdout, and no phantom `b`
    assert _paths("${x:-a>b} > f") == ["f"]
    assert _paths("echo ${x:-a>b} > f") == ["f"]
    assert _paths("echo $[1>2] > f") == ["f"]
    assert write_targets("echo ${x#*>} > f") == [("f", "${x#*>}")]
    assert _paths("echo ${a[@]/>/x} >> f") == ["f"]


def test_a_semicolon_or_pipe_inside_an_expansion_does_not_split_the_command():
    assert write_targets("echo ${x//;/ } > f") == [("f", "${x//;/ }")]
    assert write_targets("echo ${x:-a|b} > f") == [("f", "${x:-a|b}")]


def test_nested_and_unbalanced_braces_are_bounded():
    assert _paths("echo " + "${x:-" * 200 + "a" + "}" * 200 + " > f") == ["f"]      # past the nesting bound
    assert _paths("echo " + "${x:-" * 60 + "a>b" + "}" * 60 + " > f") == ["f"]      # inside it
    assert write_targets("echo ${x > f") == [("f", "${x")]      # unclosed: claims nothing about its extent
    assert write_targets("echo }}} > f") == [("f", "}}}")]


def test_a_double_bracket_test_is_still_not_a_write():
    assert write_targets("[[ 1 > 2 ]] && echo a > f") == [("f", "a")]
    assert write_targets("[[ a > b ]]") == []
    assert write_targets("[[ a ]] [[ b > c ]] echo x") == []
    assert [p for p, _ in write_targets("[[ a > b")] == ["b"]   # unterminated, so bash rejects it; read as written


def test_ansi_c_quoting_is_parsed_not_garbled():
    assert write_targets("echo $'it\\'s > x' > f") == [("f", "it's > x")]
    assert write_targets("echo $'a > b' > f") == [("f", "a > b")]
    assert write_targets("echo $'a\\\\' > f") == [("f", "a\\")]
    assert write_targets("echo \"$'a\" > f") == [("f", "$'a")]


def test_ansi_c_quoting_hides_a_redirect_inside_it():
    assert write_targets("echo $'x\\' > g' ; echo ok") == []


S1_NESTED = [
    'echo "${y:-"a > b"}"',
    'echo "${y:-"a; rm > b"}"',
    'echo "${y:-"a | b > c"}"',
    'echo "${y:-"a && b > c"}"',
    'echo "${y:-"a #b > c"}"',
    'echo "${y:-"x"} and ${z:-"a > b"}"',
    'echo "${y:-"${z:-"a > b"}"}"',
    'echo "${y:-\\"a > b}"',
]


@pytest.mark.parametrize("cmd", S1_NESTED, ids=repr)
def test_quotes_nested_in_a_dquoted_expansion_start_fresh_and_hold_no_write(cmd):
    assert write_targets(cmd) == []


def test_the_real_redirect_after_one_is_found_with_sensible_content():
    assert write_targets('echo "${y:-">"}" > f') == [("f", '${y:-">"}')]
    assert write_targets('echo "${y:-"a > b"}" > f') == [("f", '${y:-"a > b"}')]
    assert _paths('x="${y:-"a > b"}" tee f') == ["f"]
    assert _paths('echo "${y:-"a; b"}" > f; echo z > g') == ["f", "g"]
    assert sorted(_paths('echo "${y:-"$(cat x > z)"}" > f')) == ["f", "z"]


def test_quiet_direction_plain_quotes_and_expansions_still_work():
    assert write_targets('echo "${y}" > f') == [("f", "${y}")]
    assert write_targets('echo "a}" > f') == [("f", "a}")]
    assert write_targets("echo '${y:-\"a' > f") == [("f", '${y:-"a')]
    assert _paths('echo "${y}" > f; echo hi > g') == ["f", "g"]
    assert write_targets('echo ${y:-"a > b"} > f') == [("f", '${y:-"a > b"}')]
    write_targets('echo "${y:-"a > b')                      # unterminated: no crash
    write_targets('echo "${' * 500)


def test_a_locale_string_loses_its_dollar():
    assert write_targets('echo $"a > b" > f') == [("f", "a > b")]
    assert write_targets('echo "a $"b > f') == [("f", "a $b")]
    assert write_targets("echo $5 > f") == [("f", "$5")]


def content(cmd, path):
    return [c for p, c in write_targets(cmd) if p == path]


def test_a_line_continuation_before_the_redirect():
    assert content("cat <<E \\\n> f\nx\nE", "f") == ["x"]


def paths(cmd):
    return sorted({p for p, _c in write_targets(cmd)})


@pytest.mark.parametrize("cmd,want", [
    ('echo "`echo \\"a > g\\"`" > f', ["f"]),
    ("echo `echo a \\\\> g` > f", ["f"]),
])
def test_backtick_escapes(cmd, want):
    assert paths(cmd) == want


def test_nested_escaped_backticks_find_the_inner_write():
    assert "g" in paths("echo `echo \\`echo a > g\\`` > f")


@pytest.mark.parametrize("cmd", ["echo hi # $(echo a > p1)", "echo hi # `tee y.secret`", "echo a #$(echo b > p12)"])
def test_a_substitution_inside_a_comment_is_not_a_write(cmd):
    assert paths(cmd) == []
