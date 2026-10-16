"""`/memory approve|reject` on the shared gateway (M1).

The shared owner's `slash.exec` refuses every command outside its reviewed set with `unsupported_command`, and the Ink
client words that as "/memory is not available on the shared gateway yet". `/memory` was reviewed as a read only
(`''` and `pending`); its write verbs had no entry, though `/skills` has the same pair of read and review forms and runs
the messaging handler's own `approve|reject|approval` as a control. So a pending memory write could be listed from the
TUI and never approved there.
"""

import pytest

from gateway.session_commands import command_verdict


@pytest.mark.parametrize(
    "arg",
    [
        "approve d281178d",
        "approve all",
        "approve",
        "apply d281178d",
        "reject d281178d",
        "reject all",
        "deny d281178d",
        "drop d281178d",
        "approval on",
        "approval off",
        "mode on",
        "APPROVE d281178d",
    ],
)
def test_the_review_verbs_of_memory_run_as_controls(arg):
    assert command_verdict("memory", arg) == "control"


@pytest.mark.parametrize("arg", ["", "pending", " pending "])
def test_the_reads_of_memory_stay_reads(arg):
    assert command_verdict("memory", arg) == "read"


@pytest.mark.parametrize("arg", ["wipe", "edit foo", "approvex d281178d", "bogus"])
def test_anything_else_of_memory_is_still_refused(arg):
    assert command_verdict("memory", arg) == "refused"


def test_skills_review_verbs_are_unchanged():
    assert command_verdict("skills", "approve abc") == "control"
    assert command_verdict("skills", "pending") == "read"
    assert command_verdict("skills", "install x") == "refused"
