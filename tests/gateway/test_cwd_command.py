"""``/cwd`` workspace binding: the chat's terminal directory is settable, not just persistent.

``set_chat_workspace`` writes the same routing-entry metadata ``restore_turn_cwd`` seeds from, so a
bound chat lands in its workspace on the next turn and after a gateway restart; a ``/new`` (fresh
routing entry) drops it. Refusals: missing directory, non-local backend, no routing entry.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.session import SessionEntry


def test_prefix_matches_basenames_too(tmp_path):
    from gateway.workspace_candidates import match_workspace_prefix
    matches, note = match_workspace_prefix(['/home/me/code/website', '/srv/data'], 'site')
    assert note == 'unique' and matches == ['/home/me/code/website']
    matches, note = match_workspace_prefix(['/x/alpha', '/y/alpine'], 'al')
    assert note == 'ambiguous' and len(matches) == 2


def test_unknown_prefix_falls_through_to_literal_path(tmp_path):
    """No prefix match = empty note: the handler treats the argument as a literal path, so a
    nonexistent one still gets the plain invalid-directory refusal (not a list)."""
    from gateway.workspace_candidates import match_workspace_prefix
    matches, note = match_workspace_prefix(['/home/me/code/website'], 'zzz-nowhere')
    assert (matches, note) == ([], '')
