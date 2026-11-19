"""Plugin absent: a read-only bundle copy stays read-only.

skills-staging's sync wraps add the owner write bit. This file checks the core
functions on their own.
"""

import stat
from pathlib import Path

import pytest

from tools.skills_sync import _copy_dir, _replace_skill_dir


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("replace", [False, True])
def test_a_readonly_bundle_copy_stays_readonly(
    tmp_path: Path, replace: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools.skills_sync.SKILLS_DIR", tmp_path / "skills")
    source = tmp_path / "bundle"
    references = source / "references"
    references.mkdir(parents=True)
    document = references / "guide.md"
    document.write_text("bundled guide")
    document.chmod(0o444)
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

    guide = destination / "references" / "guide.md"
    assert guide.is_file()
    assert not guide.stat().st_mode & stat.S_IWUSR
    assert not source.stat().st_mode & stat.S_IWUSR
