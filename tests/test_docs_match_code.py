"""The documentation says what the code does: documented limits, modes, forms and
messages are checked against the source, and every row of INVARIANTS.md names a
test that exists."""
from pathlib import Path

from instinct_delivery.bashwrites import write_targets


def _unresolved(md):
    """(references checked, those that name nothing real) in INVARIANTS-style markdown:
    `test_x.py`, `test_x.py::test_name`, bare `test_name`; a trailing `*` is a prefix."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent
    texts = {f.name: f.read_text(encoding="utf-8") for f in root.glob("test_*.py")}
    allsrc = "\n".join(texts.values())

    def exists(name, src):
        if name.endswith("*"):
            return re.search(rf"def {re.escape(name[:-1])}\w*\(", src) is not None
        return f"def {name}(" in src

    checked, missing = 0, []
    for tok in re.findall(r"`([^`\n]+)`", md):
        m = re.fullmatch(r"(test_\w+\.py)(?:::(test_\w+\*?))?|(test_\w+\*?)", tok)
        if not m:
            continue
        f, name, bare = m.groups()
        checked += 1
        if f and f not in texts:
            missing.append(tok)
        elif f and name and not exists(name, texts[f]):
            missing.append(tok)
        elif bare and not exists(bare, allsrc):
            missing.append(tok)
    return checked, missing


def test_every_invariants_row_names_an_existing_test():
    from pathlib import Path
    checked, missing = _unresolved((Path(__file__).resolve().parent / "INVARIANTS.md")
                                   .read_text(encoding="utf-8"))
    assert checked > 50, f"only {checked} test references found in INVARIANTS.md"
    assert not missing, missing


def test_the_invariants_check_fails_on_a_row_that_names_nothing():      # negative control
    _, missing = _unresolved("| a | `test_hardening.py::test_no_such_thing` |\n| b | `test_nofile.py` |\n"
                             "| c | `test_no_such_bare` |\n| d | `test_no_such_pre*` |")
    assert len(missing) == 4


ROOT = Path(__file__).resolve().parents[1]


def test_the_exit_one_lists_name_breadth():
    for doc in ("README.md", "docs/reports.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        i = text.index("findings")
        seg = text[i:i + 700]
        assert "BROAD" in seg, doc


def test_security_md_documents_the_modes():
    assert "0600" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_the_readme_empty_log_comment_matches_the_real_message():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "# empty log" not in text
    assert "# no log yet" in text


def test_design_md_does_not_point_at_a_private_review_file():
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "INSTINCT-DELIVERY-REVIEW.md" not in text


def test_the_caps_are_documented():
    for doc in ("docs/rules.md", "SECURITY.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "MAX_TOTAL_BYTES" in text or "512 KiB" in text, doc
        assert "500" in text, doc


def test_security_md_documents_the_directory_mode():
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "0700" in text


READ = {"echo a > f": "f", "printf a >> f": "f", "cat x > f": "f", "tee -a f": "f", "sed -i s/a/b/ f": "f",
        "if x; then echo a > f; fi": "f", "sudo -u root tee f": "f", "FOO=1 echo a > f": "f"}


UNREAD = ['eval "echo a > f"', "xargs -I{} sh -c 'echo {} > f'", "bash -c 'echo a > f'", "sh -c 'echo a > f'",
          "python3 <<EOF\nopen('f','w').write('x')\nEOF", "ls > f", "python x.py > f", ": > f", "exec > f",
          "cp a f", "mv a f", "dd if=a of=f", "curl -o f http://x"]


def _paths(cmd):
    return [p for p, _ in write_targets(cmd)]


def test_the_docs_list_exactly_the_forms_the_code_reads_and_the_unread_ones_stay_unread():
    doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    for cmd in UNREAD:
        assert write_targets(cmd) == [], cmd
    for needle in ("`eval`", "`xargs`", "`bash -c", "`python3 <<EOF`", "`ls > f`", "`exec > f`", "`cp`, `mv`",
                   "/dev/tty", "/dev/fd/N", "comment"):
        assert needle in doc, needle
    for cmd, path in READ.items():
        assert _paths(cmd) == [path], cmd
    for needle in ("`sudo`", "`doas`", "`env`", "`nice`", "`command`", "`|&`", "`>|`", "`>& file`",
                   "`tee 2>&1 f`", "`-i ''`"):
        assert needle in doc, needle


def test_the_docs_say_null():
    doc = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")
    assert "`null`" in doc.split("`domain`, `confidence`")[1].split("\n")[0]


def test_the_docs_call_the_backtracking_check_structural_with_the_budget_as_backstop():
    doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    assert "(a?){30}a{30}" in doc and "(a{1,50}){1,50}$" in doc
    assert "backstop" in doc
    assert "(a?){30}a{30}" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_heredoc_review_is_recorded():
    assert "the command a quoted assignment hides" in (ROOT / "DESIGN.md").read_text(encoding="utf-8").lower() or "quoted or substituted assignment" in (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "## The command a quoted assignment hides" in (ROOT / "tests" / "INVARIANTS.md").read_text(encoding="utf-8")


def test_the_docs_do_not_list_a_digit_ending_a_word_under_not_writes():
    doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    listed = doc.split("Not writes:")[1].split("A digit ending a word")[0]
    assert "abc12>f" not in listed
    assert write_targets("echo abc12>f") == [("f", "abc12")]
    assert write_targets("echo abc12 >f") == [("f", "abc12")]
    assert "`echo abc12>f` is a write" in doc.replace("\n", " ")   # documented, as a write
