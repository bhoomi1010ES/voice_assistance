from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.services.audit import safe_tool_payload, safe_tool_result_content
from app.services.push_delivery import FakePushDeliveryProvider
from app.services.recurrence import (
    RecurrenceResolutionError,
    next_occurrence,
    parse_recurrence_rule,
)


def test_recurrence_rules_are_bounded_and_deterministic() -> None:
    daily = parse_recurrence_rule("FREQ=DAILY;COUNT=3")
    assert daily.frequency == "DAILY"
    assert daily.count == 3
    assert next_occurrence(
        datetime(2026, 3, 7, 14, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        rule=daily,
    ) == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)

    weekly = parse_recurrence_rule("FREQ=WEEKLY;BYDAY=MO,WE")
    assert next_occurrence(
        datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        rule=weekly,
    ) == datetime(2026, 9, 9, 13, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "rule",
    ["FREQ=MONTHLY", "FREQ=DAILY;INTERVAL=0", "FREQ=DAILY;COUNT=999", "FREQ=DAILY;BOGUS=1"],
)
def test_malformed_or_unbounded_recurrence_is_rejected(rule: str) -> None:
    with pytest.raises(RecurrenceResolutionError):
        parse_recurrence_rule(rule)


def test_tool_audit_and_fake_push_provider_never_persist_raw_credentials() -> None:
    sanitized = safe_tool_payload({"access_token": "private-token", "title": "Call Rahul"})
    assert sanitized == {"access_token": "[REDACTED]", "title": "Call Rahul"}
    assert safe_tool_result_content('{"api_key":"private-key"}') == {"api_key": "[REDACTED]"}

    provider = FakePushDeliveryProvider()
    asyncio.run(
        provider.send(
            token="physical-device-token",
            delivery_id="delivery-1",
            title="Reminder",
            body="Body",
        )
    )
    assert "physical-device-token" not in str(provider.deliveries)
