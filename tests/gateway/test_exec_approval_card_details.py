"""An approval card shows what the TUI/Desktop card shows: what the call is about, in full.

``permission-gate`` words a web fetch, a search or a directory write as a multi-line reason (title, then
``URL:`` / ``Query:`` / path lines), and an MCP call carries its arguments in a structured context. The
button cards cut the reason at a platform budget (Discord: 300 chars) and previewed the MCP arguments from
the prompt's 1,200-char copy, so the details a person approves on were missing on the thread.
"""
import pytest

from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.base_exec_approval import approval_card_parts, ea_mcp_block


def _discord():
    from plugins.platforms.discord.adapter import DiscordAdapter
    adapter = DiscordAdapter.__new__(DiscordAdapter)
    adapter._approval_mention_content = lambda: ""
    return adapter


WEB_REASON = ("Web access needs approval (web_fetch)\n"
              "URL: https://example.com/a/very/long/path?token=abc\n"
              "URL: https://other.example.org/\n"
              "Query: how do I reproduce the PD-8343 shoulder conditions auto-map failure")


def test_a_plugin_reason_keeps_its_title_as_the_reason_and_moves_the_details_into_the_body():
    body, reason = approval_card_parts("<web_fetch> (plugin approval rule)", WEB_REASON)
    assert reason == "Web access needs approval (web_fetch)"
    assert body.splitlines()[:3] == ["URL: https://example.com/a/very/long/path?token=abc",
                                     "URL: https://other.example.org/",
                                     "Query: how do I reproduce the PD-8343 shoulder conditions auto-map failure"]
    assert body.endswith("<web_fetch> (plugin approval rule)")


def test_a_one_line_reason_and_a_plain_command_are_unchanged():
    assert approval_card_parts("rm -rf /tmp/x", "dangerous command") == ("rm -rf /tmp/x", "dangerous command")
    assert approval_card_parts("", "") == ("", "")


def test_mcp_arguments_come_from_the_structured_context_in_full():
    long_args = '{\n  "path": "' + "d" * 1500 + '"\n}'
    mcp = {"server": "files", "tool": "write", "tool_description": "Writes a file.", "arguments": long_args}
    prompt_text = ("MCP tool 'write' on UNTRUSTED server 'files' wants to run. It may modify external state."
                   "\n\nArguments:\n" + long_args[:1100] + "\n... [truncated]")
    block = ea_mcp_block(mcp, prompt_text)
    assert block.startswith("Writes a file.\n\nMCP tool 'write' on UNTRUSTED server 'files' wants to run.")
    assert block.endswith("\n\nArguments:\n" + long_args)
    assert "truncated" not in block
    # No structured arguments: the prompt text stands, as before.
    assert ea_mcp_block({"server": "s", "tool": "t"}, prompt_text) == prompt_text


def test_a_plugin_mcp_call_without_notices_gets_an_arguments_section():
    mcp = {"server": "s", "tool": "t", "arguments": '{"q": 1}'}
    assert ea_mcp_block(mcp, "<mcp__s__t> (plugin approval rule)") == (
        "<mcp__s__t> (plugin approval rule)\n\nArguments:\n{\"q\": 1}")


@pytest.mark.parametrize("make", [_discord], ids=["discord"])
def test_the_discord_card_shows_every_detail_line_and_a_short_reason(make):
    from gateway.platforms.base_exec_approval import render_exec_approval
    text, truncated = render_exec_approval(make(), "<web_fetch> (plugin approval rule)", WEB_REASON, False, True)
    assert not truncated
    for line in WEB_REASON.splitlines()[1:]:
        assert line in text, line
    # The reason line is only the title, inside the budget however long the details are.
    assert text.count("Web access needs approval (web_fetch)") == 1
    code_start, code_end = text.index("```bash"), text.rindex("```")
    assert "URL: https://example.com" in text[code_start:code_end]  # inside the previewed body


def test_a_long_reason_detail_is_never_cut_by_the_reason_budget():
    from gateway.platforms.base_exec_approval import render_exec_approval
    detail = "Path: /srv/" + "x" * 600
    text, _ = render_exec_approval(_discord(), "<write_file> (plugin approval rule)",
                                   f"Writing outside the workspace\n{detail}", False, True)
    assert detail in text  # the reason budget (300) applied to the old one-blob reason cut it


class _Chat(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 1000

    def __init__(self):
        self.sent, self.prompts = [], []

    def _exec_approval_cmd_budget(self, description, smart_denied):
        return 400

    async def connect(self): ...
    async def disconnect(self): ...
    async def get_chat_info(self, chat_id): return {}

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append(content)
        return SendResult(success=True, message_id="m1")

    async def _send_exec_approval_prompt(self, prompt):
        self.prompts.append(prompt)
        return SendResult(success=True, message_id="card")


@pytest.mark.asyncio
async def test_a_preview_cut_through_the_detail_lines_sends_the_whole_body_first():
    """The 'full command above' send now covers the detail lines too, or Approve would be live on a card whose
    URL list was cut."""
    adapter = _Chat()
    many = "\n".join(f"URL: https://example.com/{i}/" + "p" * 60 for i in range(40))
    await adapter.send_exec_approval("c1", "<web_fetch> (plugin approval rule)", "sk",
                                     description=f"Web access\n{many}", request_id="r1")
    assert adapter.sent and "URL: https://example.com/39/" in adapter.sent[0]
    assert next(c for _, c, _ in adapter.prompts[-1].actions) == "once"


@pytest.mark.asyncio
async def test_the_prompt_handed_to_an_adapter_carries_the_whole_body_and_the_short_reason():
    """Teams renders from prompt.command/description, not prompt.text: it must get what the card was cut from."""
    adapter = _Chat()
    await adapter.send_exec_approval("c1", "<web_fetch> (plugin approval rule)", "sk",
                                     description=WEB_REASON, request_id="r1")
    prompt = adapter.prompts[-1]
    assert prompt.description == "Web access needs approval (web_fetch)"
    assert "URL: https://example.com/a/very/long/path?token=abc" in prompt.command
    assert prompt.command.endswith("<web_fetch> (plugin approval rule)")


@pytest.mark.asyncio
async def test_an_mcp_call_whose_arguments_were_cut_is_deny_only_and_says_so():
    """Over 4,000 characters the structured context itself is cut: sending that same text ahead of the card
    cannot make it whole, so nobody can approve it from a card ("nobody approves a cut call")."""
    adapter = _Chat()
    mcp = {"server": "files", "tool": "write", "arguments": '{"a": 1}\n... [arguments truncated]', "arguments_cut": True}
    await adapter.send_exec_approval("c1", "MCP tool 'write' wants to run.", "sk", description="MCP call",
                                     request_id="r1", mcp=mcp)
    prompt = adapter.prompts[-1]
    assert [c for _, c, _ in prompt.actions] == ["deny"]
    assert "only Deny" in prompt.text or "Deny" in prompt.text
    assert adapter.sent == [], "no full-command send that would imply the call is whole"


@pytest.mark.asyncio
async def test_an_mcp_call_with_whole_arguments_still_offers_approve():
    adapter = _Chat()
    mcp = {"server": "files", "tool": "write", "arguments": '{"a": 1}'}
    await adapter.send_exec_approval("c1", "MCP tool 'write' wants to run.", "sk", description="MCP call",
                                     request_id="r1", mcp=mcp)
    assert "once" in [c for _, c, _ in adapter.prompts[-1].actions]


def test_the_mcp_context_flags_arguments_cut_at_the_bound():
    from tools.mcp_tool_handlers import _approval_mcp_context
    assert "arguments_cut" not in _approval_mcp_context("s", "t", {"q": "small"})
    assert _approval_mcp_context("s", "t", {"q": "x" * 6000})["arguments_cut"] is True


def test_blank_lines_in_a_multi_line_command_survive():
    body, _ = approval_card_parts("python - <<EOF\nprint(1)\n\nprint(2)\nEOF", "Run a script")
    assert body == "python - <<EOF\nprint(1)\n\nprint(2)\nEOF"
    body, _ = approval_card_parts("cmd", "title\ndetail one\n\ndetail two")
    assert body == "detail one\n\ndetail two\ncmd"


def test_the_text_fallback_fence_outgrows_backticks_in_the_body():
    from gateway.run import _format_exec_approval_fallback
    text = _format_exec_approval_fallback("<web_fetch> (plugin approval rule)",
                                          "Web access\nQuery: ``` ignore the above ```", "/")
    assert "````" in text  # a fence longer than the longest run in the body
    assert text.count("Query: ``` ignore the above ```") == 1
