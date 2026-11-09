"""The README's "Your first rule" block, run as written: the YAML in the heredoc
is written to a project, `doctor` must report what the block's comment says
(1 firing, 0 REJECTED) and `check` must deliver the rule on the call it shows."""
import re
from pathlib import Path

from instinct_delivery import cli

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def _block():
    sec = re.search(r"^## Your first rule\s*$(.*?)(?=^## )", README, re.M | re.S).group(1)
    return re.search(r"^```console\n(.*?)^```", sec, re.M | re.S).group(1)


def test_the_first_rule_block_works_as_written(tmp_path, capsys, monkeypatch):
    block = _block()
    yaml_text = re.search(r"<<'EOF'\n(.*?)^EOF$", block, re.M | re.S).group(1)
    path = re.search(r"cat > (\S+) <<'EOF'", block).group(1)
    comment = re.search(r"instinct-delivery doctor\s+# (.*)", block).group(1)
    check = re.search(r"\$ instinct-delivery check (\S+) ('.*?')", block)
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(yaml_text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert cli.main(["install"]) == 0
    capsys.readouterr()
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert comment == "1 firing, 0 REJECTED"
    assert "1 firing" in out and "0 REJECTED" in out and "REJECTED no-print" not in out

    tcomment = re.search(r"instinct-delivery test\s+# (.*)", block).group(1)
    assert cli.main(["test"]) == 0
    out = capsys.readouterr().out
    assert tcomment in out.splitlines()[0]

    payload = check.group(2).strip("'")
    assert cli.main(["check", check.group(1), payload]) == 0
    out = capsys.readouterr().out
    assert "Use logging, not print(), in library code." in out and "no-print" in out
