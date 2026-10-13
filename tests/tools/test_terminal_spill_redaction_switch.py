"""`tool_output.redact_spill: false` leaves the terminal spill file as the collector wrote it.

The core redacts the whole spill file after a big command (about 0.1 s per MB) so no secret stays
unmasked on disk. A setup that redacts each page when it is read back, as Pi's output-cap does,
can turn the full pass off; the handle the model gets (path, total, note) is the same either way.
"""

from pathlib import Path

import pytest

SECRET = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0" * 3


@pytest.fixture
def spill(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("agent.redact._REDACT_ENABLED", True)
    path = tmp_path / "cache" / "terminal-output" / "out-1-2-aa.log"
    path.parent.mkdir(parents=True)
    path.write_text(f"first line\nkey {SECRET}\n" + "pad\n" * 20)
    return path


def run(path: Path):
    from tools.terminal_tool_result import _redact_spill_file

    return dict(_redact_spill_file(str(path), 4000, "cat out.txt"))


def configure(path: Path, body: str) -> None:
    (path.parents[2] / "config.yaml").write_text(body)


def test_by_default_the_whole_spill_file_is_redacted(spill):
    handle = run(spill)
    assert SECRET not in spill.read_text()
    assert handle["full_output_path"] == str(spill)


def test_switched_off_the_file_is_left_as_written(spill):
    before = spill.read_text()
    configure(spill, "tool_output:\n  redact_spill: false\n")
    handle = run(spill)
    assert spill.read_text() == before
    assert SECRET in spill.read_text()
    assert handle["full_output_path"] == str(spill)
    assert handle["output_total_chars"] == 4000
    assert "read_file" in handle["truncation_note"]


def test_switched_on_explicitly_still_redacts(spill):
    configure(spill, "tool_output:\n  redact_spill: true\n")
    run(spill)
    assert SECRET not in spill.read_text()


@pytest.mark.parametrize("value", ["maybe", "0", "null", "[]"])
def test_an_unusable_value_keeps_the_safe_default(spill, value):
    configure(spill, f"tool_output:\n  redact_spill: {value}\n")
    run(spill)
    assert SECRET not in spill.read_text()
