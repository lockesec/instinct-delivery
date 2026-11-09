"""Cost bounds of the Bash-write reader: every scanner stays linear on hostile
input (growth is measured as time against input size, never as a wall-clock bound)."""
import time

import pytest

from instinct_delivery import bashwrites
from instinct_delivery.bashwrites import write_targets


def test_unclosed_substitutions_repeated_do_not_go_quadratic():
    for cmd in ("echo " + "$(" * 20000, "echo " + "$(" * 20000 + ")", "echo " + "`a" * 20000):
        t = time.monotonic()
        write_targets(cmd)
        assert time.monotonic() - t < 3


def test_an_unterminated_double_bracket_is_linear():
    t0 = time.monotonic()
    write_targets("[[ " * 66000)
    assert time.monotonic() - t0 < 2.0


TOKENS = ["[[ ", "${", "$[", "$((", "((", "<<", "$(", "`", "{", "(", '"', "'", "<<EOF\n",
          "<<-X ", "$'", "\\", "> ", ">(", "<(", "# ", 'a="', "echo ", "| ", "; ", "&& ", "\n",
          "cat <<EOF >f\n", "${x:-", "<<'", "\\\n", "[[ a ]] ", "(( (", "$((a(", "} ", "] ",
          "${x} ", "$[1] ", "$'a\\'", "{ echo a; } > f; ", "( echo a ) > f; ",
          "echo a\\\n", "cat <<E\\\n", 'echo "a\\\n']


@pytest.mark.parametrize("token", TOKENS, ids=repr)
def test_every_scanner_is_linear_on_repetitions_of_its_own_opening_token(token):
    """Linear, judged by growth rather than a wall-clock bound that a loaded machine misses:
    four times the input must cost well under sixteen times the time (a quadratic), with a
    loose absolute ceiling as a backstop."""
    def took(n):
        cmd = token * (n // len(token))
        t0 = time.monotonic()
        write_targets(cmd)
        return time.monotonic() - t0
    small, big = took(50_000), took(200_000)
    assert big < 8 * small + 0.5, f"{token!r}: 50k {small:.2f}s, 200k {big:.2f}s (not linear)"
    assert big < 20.0, f"{token!r}: 200k {big:.1f}s"


def _py_body(n):
    return "\n".join(f"v{i} = Path('f{i}.txt')" for i in range(n)) + "\nv5.write_text('x')\n"


def test_python_writes_is_linear_in_the_number_of_assignments():
    body = _py_body(8000)
    t0 = time.monotonic()
    got = bashwrites._python_writes(body)
    took = time.monotonic() - t0
    assert got == ["f5.txt"]
    assert took < 1.0, f"{took:.1f}s"


def test_and_through_the_heredoc():
    cmd = "python3 - <<EOF\n" + _py_body(8000) + "EOF\n"
    t0 = time.monotonic()
    got = write_targets(cmd)
    assert [p for p, _ in got] == ["f5.txt"]
    assert time.monotonic() - t0 < 1.5


def test_the_semantics_are_unchanged():
    f = bashwrites._python_writes
    assert f("p = Path('a.txt')\nq = Path('b.txt')\nq.write_text('x')") == ["b.txt"]
    assert f("p = Path('a.txt')\nopen(p, 'w')") == ["a.txt"]
    assert f("p = Path('a.txt')\np = Path('b.txt')\np.write_text('x')") == ["a.txt", "b.txt"]
    assert f("p = Path('a.txt')\nx.write_text('x')") == ["a.txt"]    # no var matches: every path-like literal
    assert f("Path('a.txt').write_text('x')") == ["a.txt"]
    assert f("p = Path('a.txt')\nprint(p)") == []                    # no write call at all
    assert f("open('a.txt', 'w'); 'b.cfg'") == ["a.txt"]
    assert f("x = 'conf/a.yaml'\nopen(x, 'w')") == ["conf/a.yaml"]


@pytest.mark.parametrize("unit", ["open(x,", "open(", "open(x, 'r'"])
def test_python_writes_is_linear_on_unclosed_opens(unit):
    cmd = "python - <<E\n" + unit * 40000 + "\nE\n"
    t = time.monotonic()
    write_targets(cmd)
    assert time.monotonic() - t < 3.0


@pytest.mark.parametrize("unit", ["echo a\\\n", "cat <<E\\\n", 'echo "a\\\n', "echo \\\\\\\n"])
def test_backslash_newline_joining_is_linear(unit):
    def took(n):
        t0 = time.monotonic()
        write_targets(unit * (n // len(unit)))
        return time.monotonic() - t0
    small, big = took(20_000), took(80_000)
    assert big < 8 * small + 0.5, (small, big)
