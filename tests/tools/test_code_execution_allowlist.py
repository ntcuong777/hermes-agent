"""The tools execute_code's ``hermes_tools`` offers are configurable, and a refused import says how to reach the tool.

Before: a hard-coded frozenset of seven tools (web_search, web_extract, read_file, write_file,
search_files, patch, terminal). A model that tried ``from hermes_tools import vision_analyze`` got
"use the normal tool call instead", which does not say that a deferred tool needs ``tool_call``.
Now ``code_execution.allowed_tools`` in config.yaml names the tools (unset or empty: the seven), a
tool without a hand-written stub gets a generic ``name(**kwargs)`` one, ``execute_code`` is never
offered to itself, and the import hint names ``tool_call`` and the config key.
"""

from __future__ import annotations

import pytest

import model_tools  # noqa: F401  (registers the built-in tools the hint looks up)
from tools import code_execution_tool as cet

DEFAULT = {
    "web_search",
    "web_extract",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
}


def configure(monkeypatch, **section):
    monkeypatch.setattr(cet, "_load_config", lambda: dict(section))


def test_unset_means_the_seven_built_in_tools(monkeypatch):
    configure(monkeypatch)
    assert cet.sandbox_allowed_tools() == DEFAULT


@pytest.mark.parametrize("raw", [[], None, "read_file", 5, {"a": 1}])
def test_empty_or_malformed_falls_back_to_the_seven(monkeypatch, raw):
    configure(monkeypatch, allowed_tools=raw)
    assert cet.sandbox_allowed_tools() == DEFAULT


def test_the_configured_list_replaces_the_default(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file", " vision_analyze ", "read_file"])
    assert cet.sandbox_allowed_tools() == {"read_file", "vision_analyze"}


def test_execute_code_is_never_offered_to_itself(monkeypatch):
    configure(monkeypatch, allowed_tools=["execute_code", "read_file"])
    assert cet.sandbox_allowed_tools() == {"read_file"}


def test_only_configured_tools_that_are_enabled_are_importable(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file", "vision_analyze"])
    assert cet._sandbox_tools_for(["read_file", "vision_analyze", "terminal"]) == {
        "read_file",
        "vision_analyze",
    }
    assert "vision_analyze" not in cet._sandbox_tools_for(["read_file"])


def test_a_tool_without_a_stub_template_gets_a_generic_one(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file", "vision_analyze"])
    source = cet.generate_hermes_tools_module(["read_file", "vision_analyze"])
    assert "def vision_analyze(**kwargs):" in source
    assert "_call('vision_analyze', kwargs)" in source
    assert "def read_file(path: str" in source  # the hand-written stub is kept


def test_a_tool_not_on_the_list_gets_no_stub_even_when_passed(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file"])
    source = cet.generate_hermes_tools_module(
        ["read_file", "vision_analyze", "web_search"]
    )
    assert "def vision_analyze" not in source and "def web_search" not in source


def test_a_name_that_is_not_a_python_identifier_gets_no_stub(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file", "mcp-linear-get"])
    source = cet.generate_hermes_tools_module(["read_file", "mcp-linear-get"])
    assert "mcp-linear-get" not in source


def test_the_schema_lists_a_configured_tool_without_a_template(monkeypatch):
    configure(monkeypatch, allowed_tools=["read_file", "vision_analyze"])
    description = cet.build_execute_code_schema({"read_file", "vision_analyze"})[
        "description"
    ]
    assert "vision_analyze(**kwargs)" in description


def test_the_default_schema_is_unchanged(monkeypatch):
    configure(monkeypatch)
    description = cet.build_execute_code_schema()["description"]
    assert "web_search(query: str" in description and "(**kwargs)" not in description


def test_an_import_hint_for_a_real_tool_names_tool_call_and_the_config_key(monkeypatch):
    configure(monkeypatch)
    hint = cet._sandbox_failure_hint(
        "ImportError: cannot import name 'vision_analyze' from 'hermes_tools'",
        sorted(DEFAULT),
    )
    assert "tool_call" in hint and "code_execution.allowed_tools" in hint
    assert "vision_analyze" in hint


def test_an_import_hint_for_a_name_no_tool_has_keeps_the_old_text(monkeypatch):
    configure(monkeypatch)
    hint = cet._sandbox_failure_hint(
        "ImportError: cannot import name 'no_such_tool' from 'hermes_tools'",
        sorted(DEFAULT),
    )
    assert "is not available inside the execute_code sandbox" in hint
    assert "code_execution.allowed_tools" not in hint
