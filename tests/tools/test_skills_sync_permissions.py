"""Copies from immutable packages must leave skills editable."""

import stat
from pathlib import Path

import pytest

from tools.skills_sync import _copy_dir, _replace_skill_dir


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("replace", [False, True])
def test_readonly_bundle_copies_remain_writable(
    tmp_path: Path, replace: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools.skills_sync.SKILLS_DIR", tmp_path / "skills")
    source = tmp_path / "bundle"
    references = source / "references"
    references.mkdir(parents=True)
    document = references / "guide.md"
    document.write_text("bundled guide")
    document.chmod(0o444)
    script = source / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o555)
    references.chmod(0o555)
    source.chmod(0o555)
    destination = tmp_path / "skills" / "example"
    destination.parent.mkdir()
    if replace:
        destination.mkdir()
        (destination / "old.md").write_text("old guide")
        _replace_skill_dir(source, destination)
    else:
        _copy_dir(source, destination)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o755
    assert stat.S_IMODE((destination / "references").stat().st_mode) == 0o755
    assert stat.S_IMODE((destination / "references" / "guide.md").stat().st_mode) == 0o644
    assert stat.S_IMODE((destination / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE(source.stat().st_mode) == 0o555
    assert stat.S_IMODE(document.stat().st_mode) == 0o444
    (destination / "references" / "guide.md").write_text("user edit")
    (destination / "references" / "new.md").write_text("new guide")
    (destination / "references" / "guide.md").unlink()
