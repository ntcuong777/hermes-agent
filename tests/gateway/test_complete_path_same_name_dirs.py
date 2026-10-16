"""`@name` shows where each directory is, so two with the same name can be told apart (B20).

A file's completion row carries its parent path as `meta`; a directory's carried only the word `dir`. With
`python/core/tests/file_types` and `python/core/pageai_core/file_types` both on disk, `@file_types` showed two
identical rows and the person could not pick the right one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tui_gateway import server


def _items(word: str) -> list[dict]:
    resp = server.handle_request(
        {"id": "1", "method": "complete.path", "params": {"word": word}}
    )
    return resp["result"]["items"]


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    server._fuzzy_cache.clear()
    monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
    yield
    server._fuzzy_cache.clear()


def _tree(root: Path) -> None:
    for parent in ("tests", "pageai_core"):
        (root / "python" / "core" / parent / "file_types").mkdir(parents=True)
        (root / "python" / "core" / parent / "file_types" / "base.py").write_text("x")


def test_two_directories_with_one_name_show_different_locations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)

    rows = [it for it in _items("@file_types") if it["text"].startswith("@folder:")]

    assert {it["text"] for it in rows} == {
        "@folder:python/core/tests/file_types/",
        "@folder:python/core/pageai_core/file_types/",
    }
    assert len({(it["display"], it["meta"]) for it in rows}) == 2, (
        "the two rows look identical"
    )
    shown = {it["meta"] for it in rows}
    assert any("pageai_core" in m for m in shown) and any("tests" in m for m in shown)


def test_a_directory_row_still_reads_as_a_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)

    rows = [it for it in _items("@file_types") if it["text"].startswith("@folder:")]

    assert all(it["display"].endswith("/") for it in rows)


def test_a_top_level_directory_keeps_a_plain_dir_label(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "file_types").mkdir()

    (row,) = [it for it in _items("@file_types") if it["text"] == "@folder:file_types/"]

    assert row["meta"] == "dir"
