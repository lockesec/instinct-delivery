"""Heredocs as bash reads them: which command owns the body, how the terminator is
matched, what quote and continuation state does to the tag, bodies inside `$( )`,
and the here-string, arithmetic and expansion forms that look like a heredoc but
are not."""
import pytest

from instinct_delivery.bashwrites import write_targets


def test_a_heredoc_for_another_command_is_not_the_content_written():
    assert write_targets("psql <<EOF > out.sql\nselect 1;\nEOF") == [("out.sql", "")]
    assert write_targets("bash <<EOF > build.log\nmake\nEOF") == [("build.log", "")]
    assert write_targets("psql <<EOF | tee out.log\nselect 1;\nEOF") == [("out.log", "")]


def test_commands_that_copy_stdin_still_write_the_body():
    assert write_targets("cat > a.txt <<EOF\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("cat <<EOF >> a.txt\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("tee a.txt <<EOF\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("sudo tee -a a.txt <<EOF\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("sudo -u root tee a.txt <<EOF\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("FOO=1 cat > a.txt <<EOF\nhi\nEOF") == [("a.txt", "hi")]
    assert write_targets("cat <<EOF | tee a.txt\nhi\nEOF") == [("a.txt", "hi")]


def test_a_cat_redirect_is_a_write_of_unknown_content():
    assert write_targets("cat x > y.py") == [("y.py", "")]
    assert write_targets("cat x > /dev/null") == []


def test_a_heredoc_body_belongs_to_the_command_that_owns_the_operator():
    cmd = "cat <<EOF > a.txt; cat b.txt > c.py\nSECRETTOKEN\nEOF"
    assert write_targets(cmd) == [("a.txt", "SECRETTOKEN"), ("c.py", "")]


def test_a_second_command_on_the_line_gets_nothing_from_the_body():
    assert write_targets("cat <<EOF >&2; tee d\nbody\nEOF") == [("d", "")]
    assert write_targets("tee d; cat <<EOF > e\nbody\nEOF") == [("d", ""), ("e", "body")]
    assert write_targets("cat <<EOF > a && tee b\nbody\nEOF") == [("a", "body"), ("b", "")]


def test_a_pipe_still_carries_the_body_to_the_next_command():
    assert write_targets("cat <<EOF | tee f\nbody\nEOF") == [("f", "body")]
    assert write_targets("cat <<EOF | tee f | tee g\nbody\nEOF") == [("f", "body"), ("g", "body")]


def test_quiet_direction_a_pipe_through_another_command_or_a_redirect_carries_nothing():
    assert write_targets("cat <<EOF | grep x | tee f\nbody\nEOF") == [("f", "")]
    assert write_targets("cat <<EOF > a | tee b\nbody\nEOF") == [("a", "body"), ("b", "")]
    assert write_targets("cat <<EOF | psql > o\nbody\nEOF") == []


def test_the_heredoc_owner_keeps_its_own_body_in_every_position():
    assert write_targets("tee f <<EOF\nbody\nEOF") == [("f", "body")]
    assert write_targets("cat > f <<EOF\nbody\nEOF") == [("f", "body")]
    assert write_targets("echo x; cat > f <<EOF\nbody\nEOF") == [("f", "body")]
    assert write_targets("psql <<EOF > out\nbody\nEOF") == [("out", "")]


def test_a_backslash_quoted_heredoc_tag_is_recognised():
    assert write_targets("cat <<\\EOF > f\nbody $x\nEOF") == [("f", "body $x")]
    assert write_targets("cat <<'EOF' > f\nbody\nEOF") == [("f", "body")]
    assert write_targets('cat <<"EOF" > f\nbody\nEOF') == [("f", "body")]
    assert write_targets("cat <<-\\EOF > f\nbody\nEOF") == [("f", "body")]


S2_TAGS = [("'EOF.txt'", "EOF.txt"), ("EOF-1", "EOF-1"), ("'END-OF'", "END-OF"), ("1", "1"),
           ("'---'", "---"), ("'a b'", "a b"), ('E"O"F', "EOF"), ('"E OF"', "E OF"),
           ("\\EOF", "EOF"), ("EOF.txt", "EOF.txt"), ("a-b_c", "a-b_c"), ("''", "")]


@pytest.mark.parametrize("tag,term", S2_TAGS, ids=[t for t, _ in S2_TAGS])
def test_a_heredoc_tag_is_any_shell_word(tag, term):
    cmd = f"cat <<{tag} > f\necho x > phantom\n{term}\necho y > g"
    got = write_targets(cmd)
    assert got == [("f", "echo x > phantom"), ("g", "y")], got


def test_the_reported_case():
    assert write_targets("cat <<EOF-1 > f\necho x > phantom\nEOF-1") == [("f", "echo x > phantom")]


def test_quote_removal_decides_the_terminator():
    # <<E"O"F ends at the line EOF, not at the line E"O"F
    assert write_targets('cat <<E"O"F > f\na\nEOF\n') == [("f", "a")]
    # quoted or not, the body is never expanded or executed
    assert write_targets("cat <<'A B' > f\n$(echo > p)\nA B") == [("f", "$(echo > p)")]


def test_quiet_direction_a_tag_is_not_taken_from_a_here_string_or_arithmetic():
    assert write_targets("cat <<< x > f") == [("f", "")]
    assert write_targets("echo $((1<<2)) > f") == [("f", "$((1<<2))")]


def test_two_heredocs_on_one_line_each_have_their_body():
    cmd = "cat <<A <<B > f\na\nA\nb\nB\necho z > g"
    assert write_targets(cmd) == [("f", "b"), ("g", "z")]


def test_an_indented_tag_line_does_not_terminate():
    cmd = "cat <<EOF > f\nx\n  EOF\necho phantom > p\nEOF"
    assert write_targets(cmd) == [("f", "x\n  EOF\necho phantom > p")]


def test_a_tab_indented_tag_terminates_only_with_dash():
    assert write_targets("cat <<EOF > f\nx\n\tEOF\necho y > p\nEOF") == [("f", "x\n\tEOF\necho y > p")]
    assert write_targets("cat <<-EOF > f\n\tx\n\ty\n\tEOF\necho y > p") == [("f", "x\ny"), ("p", "y")]
    assert write_targets("cat <<-EOF > f\nx\nEOF\necho y > p") == [("f", "x"), ("p", "y")]


def test_a_dash_heredoc_strips_tabs_only_not_spaces():
    assert write_targets("cat <<-EOF > f\n  EOF\nEOF\n") == [("f", "  EOF")]


def test_a_trailing_space_or_cr_does_not_terminate():
    assert write_targets("cat <<EOF > f\nx\nEOF \necho y > p\nEOF") == [("f", "x\nEOF \necho y > p")]
    assert write_targets("cat <<EOF > f\nx\nEOF\r\nEOF") == [("f", "x\nEOF\r")]


def test_quiet_direction_the_exact_line_still_terminates():
    assert write_targets("cat <<EOF > f\nx\nEOF\necho y > p") == [("f", "x"), ("p", "y")]


def test_the_last_heredoc_of_a_command_is_its_stdin():
    assert write_targets("cat <<A <<B > f\na\nA\nb\nB") == [("f", "b")]
    assert write_targets("cat <<A <<B | tee f\na\nA\nb\nB") == [("f", "b")]


def content(cmd, path):
    return [c for p, c in write_targets(cmd) if p == path]


def paths(cmd):
    return sorted({p for p, _c in write_targets(cmd)})


def test_an_unterminated_heredoc_body_runs_to_the_end():
    assert paths('cat > a.py <<EOF\nprint(1)\necho "x" > b.py') == ["a.py"]
    assert content('cat > a.py <<EOF\nprint(1)\necho "x" > b.py', "a.py") == ['print(1)\necho "x" > b.py']
    assert paths("echo q <<E\necho s > SENT") == []


def test_a_quote_open_across_lines_does_not_hide_a_later_heredoc():
    cmd = 'echo "a\nb" ; cat > f <<E\necho hi > g\nE'
    assert paths(cmd) == ["f"]
    assert content(cmd, "f") == ["echo hi > g"]


def test_a_heredoc_after_a_multiline_commit_message_keeps_its_body():
    cmd = 'git commit -m "title\n\nbody" && cat > f <<EOF\nsecret=1\nEOF'
    assert content(cmd, "f") == ["secret=1"]


def test_a_heredoc_marker_inside_a_multiline_string_is_text():
    cmd = 'echo "x\ncat > f <<E\nsecret\nE\n" > out'
    got = content(cmd, "out")
    assert got and "secret" in got[0] and "\x01" not in got[0]
    assert "f" not in paths(cmd)


@pytest.mark.parametrize("pre", [
    "x=$[1<<2]; ", "echo $(( ((1+2)*(3)) << 2 )); ", "x=1; (( y = (x+(x*2)) << 2 )) && ",
    "echo ${x:-<<}; ",
])
def test_shift_operators_do_not_start_a_heredoc(pre):
    cmd = pre + "cat > f <<E\necho hi > g\nE"
    assert paths(cmd) == ["f"]
    assert content(cmd, "f") == ["echo hi > g"]


@pytest.mark.parametrize("tag,term", [("$'EOF'", "EOF"), ("$'E\\x4fF'", "EOF"), ('$"EOF"', "EOF"),
                                      ("E\\\nOF", "EOF")])
def test_dollar_quoted_and_continued_tags(tag, term):
    cmd = f"cat > a <<{tag}\necho hi > g\n{term}"
    assert paths(cmd) == ["a"]
    assert content(cmd, "a") == ["echo hi > g"]


def test_a_heredoc_inside_a_command_substitution_keeps_its_body():
    assert content("echo $(cat > f <<E\nx\nE\n)", "f") == ["x"]


def test_an_unquoted_heredoc_body_expands_command_substitutions():
    assert "g" in paths("cat > f <<E\n$(echo hi > g)\nE")
    assert "g" not in paths("cat > f <<'E'\n$(echo hi > g)\nE")


@pytest.mark.parametrize("body", ["it's $(echo hi > g)", "'$(echo hi > g)'", "'`echo it > g`'"])
def test_a_quote_in_an_unquoted_body_does_not_hide_a_substitution(body):
    assert "g" in paths(f"cat <<E > f\n{body}\nE\n")


@pytest.mark.parametrize("cmd", ['echo "$(cat <<E > f\nit\'s\nE\n)"', 'x="$(cat <<E > f\nit\'s\nE\n)"',
                                 'echo "$(cat <<E > f\nit\\"s\nE\n)"'])
def test_a_heredoc_inside_a_double_quoted_substitution_keeps_its_write(cmd):
    assert "f" in paths(cmd)


def test_a_continuation_in_an_unquoted_body_is_joined_before_the_terminator():
    assert paths("cat <<A\nx \\\nA\necho z > g\n") == []
    got = write_targets("cat <<A > f\nx \\\nA\nA\necho z > g\n")
    assert ("f", "x A") in got and ("g", "z") in got


def test_a_quoted_tag_keeps_its_continuations():
    assert content("cat <<'A' > f\nx \\\nA\n", "f") == ["x \\"]


def test_backslash_pairs_in_an_unquoted_body_collapse():
    assert content("cat <<A > f\na\\\\b\\$c\nA\n", "f") == ["a\\b$c"]


def test_dash_heredoc_strips_tabs_only_at_the_start_of_a_joined_line():
    assert content("cat <<-A > f\n\tx \\\n\tA\nA\n", "f") == ["x \tA"]
