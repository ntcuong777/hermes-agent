"""Agent-visible redaction never leaves a fragment of a credential: no head/tail characters of the secret
(only a vendor prefix such as ``sk-`` / ``pa-``), the case of ``cat .envrc`` in a terminal."""

from agent.redact import redact_terminal_output

ENVRC = """export LANGFUSE_CS_SECRET_KEY="sk-lf-1234567890abcdef1234567890abcdef" LANGFUSE_CS_PUBLIC_KEY="pk-lf-abcdef1234567890abcdef1234567890"
export LANGFUSE_SECRET_KEY='sk-lf-zzzzzzzz-1111-2222-3333-444444444444'
export VOYAGE_API_KEY=pa-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop
export LABEL_STUDIO_URL="https://label-studio.pageai-test.co"
export LABEL_STUDIO_API_KEY=0123456789abcdef0123456789abcdef01234567
"""


def test_cat_envrc_keeps_no_characters_of_any_key():
    out = redact_terminal_output(ENVRC, "cat .envrc")
    for fragment in ("1234567890ab", "abcdef1234", "zzzzzzzz", "ABCDEF", "mnop", "012345", "4567"):
        assert fragment not in out, (fragment, out)
    assert out.splitlines()[2].startswith("export VOYAGE_API_KEY=pa-***")
    assert "LABEL_STUDIO_API_KEY=***" in out
    assert 'LABEL_STUDIO_URL="https://label-studio.pageai-test.co"' in out  # a URL is not a secret


def test_each_export_stays_on_its_own_line():
    out = redact_terminal_output(ENVRC, "cat .envrc")
    assert len(out.splitlines()) == len(ENVRC.splitlines())
    assert all(line.startswith("export ") for line in out.splitlines())


def test_a_short_value_is_fully_masked():
    assert redact_terminal_output("export DB_PASSWORD=hunter2hunter2\n", "cat .env").strip() == "export DB_PASSWORD=***"
