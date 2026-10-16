"""Claude Sonnet 5.5 (the workstation's default model) has an official price, so its session cost is measured (status bar cost)."""

from decimal import Decimal

from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

# 216k input, 756 output, no cache: $2 in / $10 out per 1M (anthropic.com/claude-sonnet-5-5).
USAGE = CanonicalUsage(input_tokens=216_000, output_tokens=756)


def test_sonnet_55_is_priced_from_the_official_rates():
    result = estimate_usage_cost("claude-sonnet-5-5", USAGE, provider="anthropic")
    assert result.status == "estimated", result
    assert result.amount_usd == Decimal("0.43956")


def test_sonnet_55_cache_reads_use_the_official_rate():
    usage = CanonicalUsage(input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000)
    result = estimate_usage_cost("claude-sonnet-5-5", usage, provider="anthropic")
    assert result.amount_usd == Decimal("0.10")
