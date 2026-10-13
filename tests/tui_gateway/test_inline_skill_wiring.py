"""The turn path hands the user's own text to `inline_skill_note` and prepends its note to the model input.

`inline_skill_note` itself is tested in tests/agent/test_inline_skill_note.py. This pins the seam: it
must read `text` (what the user typed), not `prompt` (which may carry attached file bodies), and it must
go through `_prepend_note`, so the note reaches the model without entering the transcript.
"""

from __future__ import annotations

import inspect

from tui_gateway import prompt_turn


def test_the_turn_path_notes_skills_named_in_the_users_text():
    source = inspect.getsource(prompt_turn._prepare_turn_input)
    assert "_prepend_note(run_message, inline_skill_note(text))" in source
    assert "inline_skill_note(prompt)" not in source
