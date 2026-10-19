"""`install` and the settings file, which belongs to the user: directories,
symlinks, mode and encoding are respected, the write is atomic and verified, and
every refusal is a message and exit 2, never a traceback."""
import json
import os
import stat

from instinct_delivery import cli


def test_install_on_a_settings_directory_exits_2_with_a_message(tmp_path, capsys):
    (tmp_path / ".claude" / "settings.json").mkdir(parents=True)
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "settings.json" in out and "Traceback" not in out


def test_install_writes_through_a_symlinked_settings_file(tmp_path):
    real = tmp_path / "shared" / "settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"model": "x"}))
    (tmp_path / "p" / ".claude").mkdir(parents=True)
    link = tmp_path / "p" / ".claude" / "settings.json"
    link.symlink_to(real)
    assert cli.main(["install", "--project", str(tmp_path / "p")]) == 0
    assert link.is_symlink() and link.resolve() == real.resolve()
    d = json.loads(real.read_text())
    assert d["model"] == "x" and d["hooks"]["PreToolUse"]


def test_install_refuses_a_dangling_symlinked_settings_file(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").symlink_to(tmp_path / "nowhere" / "s.json")
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert "symlink" in capsys.readouterr().out
    assert not (tmp_path / "nowhere").exists()


def _write(proj, doc):
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    p = proj / ".claude" / "settings.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_install_preserves_the_file_mode(tmp_path):
    p = _write(tmp_path, {"model": "x"})
    os.chmod(p, 0o600)
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_install_writes_non_ascii_as_utf8_not_escapes(tmp_path):
    p = _write(tmp_path, {"note": "café — 日本"})
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    raw = p.read_bytes().decode("utf-8")
    assert "café — 日本" in raw and "\\u" not in raw


OLD = "instinct-delivery hook"


def _hook(cmd):
    return {"type": "command", "command": cmd}


def _pre(proj):
    return json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


def test_a_result_that_fails_the_post_write_check_exits_2(tmp_path, monkeypatch, capsys):
    _write(tmp_path, {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [_hook(OLD)]}]}})
    real = cli._plan

    def broken(pre):          # a planner that leaves a second entry of ours behind
        out = real(pre)
        pre.append({"matcher": "Edit", "hooks": [_hook(cli.HOOK_COMMAND)]})
        return out
    monkeypatch.setattr(cli, "_plan", broken)
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert "refus" in capsys.readouterr().out.lower()
    assert _pre(tmp_path)[0]["hooks"] == [_hook(OLD)]     # nothing written


def test_install_refuses_non_finite_numbers(tmp_path, capsys):
    p = tmp_path / ".claude"
    p.mkdir()
    (p / "settings.json").write_text('{"x": NaN, "hooks": {}}')
    before = (p / "settings.json").read_text()
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert (p / "settings.json").read_text() == before
    assert "non-finite" in capsys.readouterr().out


def test_install_respects_a_read_only_settings_file(tmp_path, capsys):
    s = _write(tmp_path, {"hooks": {}})
    s.chmod(0o444)
    before = s.read_text()
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert s.read_text() == before and stat.S_IMODE(s.stat().st_mode) == 0o444
    assert "read-only" in capsys.readouterr().out


def test_a_planted_tmp_symlink_is_not_followed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    claude = tmp_path / "p" / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json.tmp").symlink_to(victim)
    (claude / ".settings.json.tmp").symlink_to(victim)
    assert cli.main(["install", "--project", str(tmp_path / "p")]) == 0
    assert victim.read_text() == "precious"
    assert "PreToolUse" in json.loads((claude / "settings.json").read_text())["hooks"]


def test_no_temp_file_is_left_behind(tmp_path):
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    assert sorted(p.name for p in (tmp_path / ".claude").iterdir()) == ["instincts", "settings.json"]


def test_a_failed_write_leaves_neither_the_file_nor_a_temp(tmp_path, monkeypatch, capsys):
    _write(tmp_path, {"a": 1})
    before = (tmp_path / ".claude" / "settings.json").read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert (tmp_path / ".claude" / "settings.json").read_text() == before
    assert sorted(p.name for p in (tmp_path / ".claude").iterdir()) == ["settings.json"]
    assert "disk full" in capsys.readouterr().out


def test_a_new_file_gets_an_ordinary_mode_not_0600(tmp_path):
    assert cli.main(["install", "--project", str(tmp_path)]) == 0
    mode = stat.S_IMODE((tmp_path / ".claude" / "settings.json").stat().st_mode)
    assert mode & 0o044          # group/other readable under any normal umask


def test_a_symlink_to_a_non_regular_file_is_refused(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    d = tmp_path / "somedir"
    d.mkdir()
    (tmp_path / ".claude" / "settings.json").symlink_to(d)
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert "not a regular file" in capsys.readouterr().out


def test_an_instincts_dir_failure_is_not_reported_as_a_settings_failure(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "instincts").write_text("a file, not a dir")
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "instincts" in out and "could not write" not in out.split("instincts")[0]
    assert "settings.json was written" in out


def test_install_on_a_deeply_nested_settings_file_exits_2(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    p = tmp_path / ".claude" / "settings.json"
    p.write_text("[" * 100000)
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    assert "Traceback" not in capsys.readouterr().out
    assert p.read_text() == "[" * 100000


def test_install_on_a_lone_surrogate_settings_file_exits_2_and_changes_nothing(tmp_path, capsys):
    s = tmp_path / ".claude" / "settings.json"
    s.parent.mkdir()
    s.write_text('{"x": "\\ud800"}', encoding="utf-8")
    before = s.read_bytes()
    assert cli.main(["install", "--project", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "install:" in out and "Unicode" in out
    assert s.read_bytes() == before
    assert [p.name for p in s.parent.iterdir()] == ["settings.json"]      # no temp left
