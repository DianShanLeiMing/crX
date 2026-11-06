"""Create/open configured local class lists without overwriting user data."""

from pathlib import Path

import pytest

from crX.utils import open_folder as paths


def test_create_and_reopen_class_list_preserves_existing_contents(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    monkeypatch.setattr(paths.os, "startfile", opened.append, raising=False)
    target = tmp_path / "中文标注" / "classes.txt"
    paths.open_text_file(str(target))
    assert target.read_bytes() == b""
    content = b"enemy_knight\nfriendly_knight\n"
    target.write_bytes(content)
    paths.open_text_file(str(target))
    assert target.read_bytes() == content
    assert opened == [str(target), str(target)]


@pytest.mark.parametrize("name", ["classes.exe", "classes.bat", "classes.py", "classes"])
def test_class_list_opener_only_accepts_text_file_types(tmp_path, name):
    target = tmp_path / "not-created" / name
    with pytest.raises(ValueError, match="扩展名"):
        paths.open_text_file(str(target))
    assert not target.parent.exists()


def test_class_list_path_cannot_be_a_directory(tmp_path):
    target = tmp_path / "classes.txt"
    target.mkdir()
    with pytest.raises(ValueError, match="路径指向目录"):
        paths.open_text_file(str(target))


@pytest.mark.parametrize("macos,opener", [(True, "open"), (False, "xdg-open")])
def test_other_desktops_open_exact_text_path_without_shell(tmp_path, monkeypatch, macos, opener):
    calls = []
    monkeypatch.setattr(paths, "is_windows", lambda: False)
    monkeypatch.setattr(paths, "is_macos", lambda: macos)
    monkeypatch.setattr(paths.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    target = tmp_path / "names with spaces.names"
    paths.open_text_file(str(target))
    assert target.is_file()
    assert calls == [([opener, str(target)], {"shell": False})]


def test_class_list_creation_does_not_replace_existing_file_on_open_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: True)

    def fail(_path):
        raise OSError("No associated editor")

    monkeypatch.setattr(paths.os, "startfile", fail, raising=False)
    target = Path(tmp_path) / "classes.txt"
    target.write_text("enemy_knight\n", encoding="utf-8")
    with pytest.raises(OSError, match="editor"):
        paths.open_text_file(str(target))
    assert target.read_text(encoding="utf-8") == "enemy_knight\n"
