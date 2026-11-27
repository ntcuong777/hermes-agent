"""The fork's carriers in upstream-owned Desktop files: each file is upstream's blob plus listed fork lines.

A carrier is a line the fork adds to an upstream file so a fork-owned module under apps/desktop/src/fork/
is reached (an MCP context parsed, a prefill routed, the approval card slot picked). The 0.23.4 upstream
sync moved the `approval` server-request handler to another file and the carrier went with it unnoticed:
the import stayed, nothing called it, and the Desktop showed an MCP call as command chrome. Two guards:
an import from a fork module that nothing uses in its file is a dead carrier, and each file pinned here is
its upstream blob once its fork lines are put back, so a merge that drops or duplicates one fails.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "apps" / "desktop" / "src"
FORK = DESKTOP / "fork"
FORK_MODULE = re.compile(r"""import\s*\{([^}]+)\}\s*from\s*['"]@/fork/([^'"]+)['"]""")

# (file under apps/desktop/src, the upstream git blob it is built on, [(upstream text, fork text), ...]).
# To re-pin after an upstream change: read the merge diff of the file, then take the blob from
# `git rev-parse <upstream ref>:<file>` once the fork lines are exactly these.
CARRIERS = (
    ("app/session/hooks/use-message-stream/gateway-event/approval-request.ts",
     "f4b2f48bb457773d5112acf118848b659d2dc0da", (
         ("", "import { parseApprovalMcp } from '@/fork/approval-mcp' // fork: approvalCard MCP context carrier\n"),
         ("", "    mcp: parseApprovalMcp(p.mcp),\n"),
     )),
    # The handler lived here before upstream moved it: nothing of the fork may remain.
    ("app/session/hooks/use-message-stream/gateway-event/server-requests.ts",
     "8e42ec065cf6dfa5fbedc0fe855371cde480f6e4", ()),
    ("app/session/hooks/use-prompt-actions/slash.ts",
     "367e1aab7861d707d79b1e27bdc2d30ee8567600", (
         ("", "import { requestComposerPrefill } from '@/fork/composer-prefill' // fork: prefill routing\n"),
         ("import { $composerAttachments, setComposerDraft } from '@/store/composer'\n",
          "import { $composerAttachments } from '@/store/composer'\n"),
         ("              setComposerDraft(message)\n",
          "              requestComposerPrefill([sessionId, storedSessionId ?? ''].filter(Boolean), message)\n"),
         ("            setComposerDraft(command)\n", "            requestComposerPrefill([], command)\n"),
     )),
    ("app/session/hooks/use-prompt-actions/slash-session-commands.ts",
     "0ea9ddd8073d2910b025ef4ee5bf1c005a7f8dac", (
         ("", "import { requestComposerPrefill } from '@/fork/composer-prefill' // fork: prefill routing\n"),
         ("import type { ChatMessage } from '@/lib/chat-messages'\nimport { setComposerDraft } from '@/store/composer'\n",
          "import type { ChatMessage } from '@/lib/chat-messages'\n"),
         ("          setComposerDraft(text)\n", "          requestComposerPrefill([out.sessionId], text)\n"),
     )),
    ("components/assistant-ui/thread/list.tsx",
     "31fe6b5a9cf6fc718ff6a0cd756aefe9e8c79856", (
         ("import { PendingApprovalStack } from '../tool/approval'\n",
          "import { PendingApprovalStack } from '@/fork/approval-pick' // fork: approvalCard slot pick (stage 8)\n"),
     )),
    ("store/prompts.ts",
     "25cef7686767570d42ce5b7bb98da135a146211d", (
         ("", "\nimport { parseApprovalMcp, type ApprovalMcpContext } from '@/fork/approval-mcp'"
              " // fork: approvalCard MCP context carrier\n"),
         ("", "  mcp?: ApprovalMcpContext // fork: approvalCard MCP context (parsed in fork/approval-mcp)\n"),
         ("", "  mcp?: unknown // fork: approvalCard MCP context carrier (parseApprovalMcp)\n"),
         ("", "        mcp: parseApprovalMcp(pending.mcp), // fork: approvalCard MCP context carrier\n"),
     )),
)


def _blob(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _upstream_sources():
    return sorted(p for p in DESKTOP.rglob("*") if p.suffix in (".ts", ".tsx")
                  and ".test." not in p.name and FORK not in p.parents)


def test_every_fork_module_import_is_used_in_its_file():
    dead = []
    for source in _upstream_sources():
        text = source.read_text(encoding="utf-8")
        for match in FORK_MODULE.finditer(text):
            elsewhere = text[:match.start()] + text[match.end():]
            for name in match.group(1).split(","):
                name = name.strip().removeprefix("type ").split(" as ")[-1].strip()
                if name and not re.search(rf"\b{re.escape(name)}\b", elsewhere):
                    dead.append(f"{source.relative_to(DESKTOP).as_posix()}: {name} from '@/fork/{match.group(2)}'")
    assert not dead, ("a carrier that nothing calls any more (upstream moved the code that used it): "
                      f"{dead}; re-apply the carrier where the code went, or drop the import")


def test_some_upstream_file_imports_a_fork_module():
    assert any(FORK_MODULE.search(p.read_text(encoding="utf-8")) for p in _upstream_sources())


@pytest.mark.parametrize("path, blob, edits", CARRIERS, ids=[c[0] for c in CARRIERS])
def test_the_file_is_upstream_plus_its_fork_lines(path, blob, edits):
    text = (DESKTOP / path).read_text(encoding="utf-8")
    for upstream, fork in edits:
        assert text.count(fork) == 1, f"{path}: the fork line is not there exactly once: {fork!r}"
        text = text.replace(fork, upstream, 1)
    assert _blob(text) == blob, (f"{path}: not upstream {blob[:12]} plus its fork lines; upstream changed the "
                                 "file (read the merge diff, re-pin the blob) or a fork line moved")


def test_the_approval_card_pick_carries_the_recorded_hash():
    entry = json.loads((FORK / "tui_slots.lock.json").read_text(encoding="utf-8"))["slots"]["approvalCard"]
    pick = (FORK / "approval-pick.ts").read_text(encoding="utf-8")
    assert re.findall(r"'([0-9a-f]{64})'", pick) == [entry["upstream_snapshot"]["sha"]], (
        "approval-pick.ts and tui_slots.lock.json disagree: record the slot, then put the printed hash in the pick")
    assert re.findall(r"^\s*'(skip|apply)'", pick, flags=re.M) == ["skip"], (
        "rule 6.9: the approval card never runs a copy written against a changed upstream card")
    table = json.loads((FORK / "slots.json").read_text(encoding="utf-8"))["slots"]["approvalCard"]
    assert table["symbols"] == entry["symbols"]
    # The copy is the whole module, so the lock covers the whole upstream file, not one component.
    assert (entry["symbols"], entry["upstream_snapshot"]["algo"]) == ([], "sha256-lf")


def _tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True)


def test_the_desktop_slot_lock_matches_the_upstream_file():
    done = _tool(str(ROOT / "scripts/fork/tui-slot-check"), "--verify-locks", str(FORK), "--fork-root", str(ROOT))
    assert done.returncode == 0, (done.stdout + done.stderr).strip()[-800:]


def test_the_approval_card_copy_is_upstream_plus_the_fork_edits():
    done = _tool(str(ROOT / "scripts/fork/desktop-approval-card-copy"), "--fork-root", str(ROOT), "--check")
    assert done.returncode == 0, (done.stdout + done.stderr).strip()[-800:]
