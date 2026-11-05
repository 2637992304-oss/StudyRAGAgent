"""Pure input-contract checks; no database or model calls."""

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.memory.text_schema import TextMemoryBatch, TextMemoryInput
from app.memory.text_service import effective_time


def test_manual_text_preserves_whitespace_and_conditions():
    content = "  只有数学推导详细，其他回答简短。\n"
    assert TextMemoryInput(content=content).content == content


def test_unicode_codepoint_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_content_max_chars", 1000)
    assert len(TextMemoryInput(content="😀" * 1000).content) == 1000
    with pytest.raises(ValidationError):
        TextMemoryInput(content="😀" * 1001)


@pytest.mark.parametrize("content", ["", " \n\t", "\ud800"])
def test_reject_blank_and_invalid_unicode(content):
    with pytest.raises(ValidationError):
        TextMemoryInput(content=content)


def test_no_confirmation_or_owner_forgery():
    for key in ("owner_id", "write_basis", "status", "confirmed"):
        with pytest.raises(ValidationError):
            TextMemoryInput.model_validate({"content": "目标：完成实验", key: "x"})


def test_metadata_and_time_bounds():
    for extra in (
        {"tags": ["x" * 33]},
        {"exceptions": "x" * 301},
        {"valid_from": "2026-11-04T00:00:00"},
        {"valid_from": "2026-12-01T00:00:00Z", "expires_at": "2026-11-01T00:00:00Z"},
    ):
        with pytest.raises(ValidationError):
            TextMemoryInput(content="目标：完成实验", **extra)


def test_split_keeps_conditions_as_one_item():
    batch = TextMemoryBatch(items=[{"content": "只有数学推导详细，其他回答简短。"}])
    assert len(batch.items) == 1
    with pytest.raises(ValidationError):
        TextMemoryBatch(items=[])


def test_expiration_does_not_wait_for_background_task():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    instant = datetime(2026, 11, 4, tzinfo=UTC)
    item = SimpleNamespace(status="active", valid_from=None, expires_at=instant)
    assert not effective_time(item, instant)
    item.expires_at = instant + timedelta(seconds=1)
    assert effective_time(item, instant)
    item.valid_from = instant + timedelta(seconds=1)
    assert not effective_time(item, instant)
    item.valid_from = None
    item.status = "deleted"
    assert not effective_time(item, instant)
