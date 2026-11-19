"""Every relative import in apps/desktop/src/fork/ must resolve from that directory.

approval.fork.tsx is a copy of components/assistant-ui/tool/approval.tsx and kept upstream's './approval-activity'
style sibling imports, which resolve only from the original directory. Nothing but the Vite build (the nix webapp and
desktop renderer builds) noticed: UNRESOLVED_IMPORT. The copy must import siblings through the '@/' alias."""
import re
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parents[2] / "apps" / "desktop" / "src" / "fork"
SUFFIXES = ("", ".ts", ".tsx", ".js", ".mjs", ".json", "/index.ts", "/index.tsx", "/index.js")
_RELATIVE = re.compile(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""")


def _sources():
    return sorted(p for p in FORK.glob("*") if p.suffix in (".ts", ".tsx", ".mjs"))


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_every_relative_import_resolves_from_the_fork_dir(source):
    missing = []
    for spec in _RELATIVE.findall(source.read_text(encoding="utf-8")):
        target = (source.parent / spec).resolve()
        if not any(Path(str(target) + suffix).is_file() for suffix in SUFFIXES):
            missing.append(spec)
    assert not missing, f"{source.name}: unresolved relative imports {missing}"


def test_the_approval_copy_is_among_the_checked_files():
    assert "approval.fork.tsx" in {p.name for p in _sources()}
