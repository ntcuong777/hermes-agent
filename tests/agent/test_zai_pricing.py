"""Z.AI models have official prices, so a Z.AI session's cost is measured, not left unknown (status bar cost)."""

from decimal import Decimal

import pytest

from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

# 100k input, 10k output, no cache: GLM-5.3-Flash $0.15 in / $0.50 out per 1M (docs.z.ai pricing).
USAGE = CanonicalUsage(input_tokens=100_000, output_tokens=10_000)


@pytest.mark.parametrize("model", ["glm-5.3-flash", "zai/glm-5.3-flash"])
def test_a_zai_flash_call_is_priced_from_the_official_rates(model):
    result = estimate_usage_cost(model, USAGE, provider="zai")
    assert result.status == "estimated", result
    assert result.amount_usd == Decimal("0.02")


def test_an_unpriced_model_still_reports_unknown_not_zero():
    result = estimate_usage_cost("glm-not-a-real-model", USAGE, provider="zai")
    assert result.status == "unknown" and result.amount_usd is None
