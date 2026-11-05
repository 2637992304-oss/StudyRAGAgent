"""Required text-memory context cannot be silently dropped as optional history."""

import pytest

from app.context_budget import ContextLimit, prepare_messages
from app.core.config import Settings
from app.memory.text_graph import context_block


def test_always_memory_survives_old_history_pruning():
    memory = context_block({"items": [{"content": "先讲直觉。", "use_mode": "always"}]})
    original = [
        {"role": "system", "content": "规则"},
        memory,
        {"role": "user", "content": "旧内容" * 2000},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "当前问题"},
    ]
    settings = Settings(
        _env_file=None, context_request_tokens=1800, context_margin_tokens=100
    )
    prepared = prepare_messages(original, [], 100, settings, purpose="agent")
    assert memory in prepared.messages
    assert prepared.messages[-1] == original[-1]
    assert original[2] not in prepared.messages


def test_always_overflow_is_explicit_and_input_not_truncated():
    memory = context_block(
        {"items": [{"content": "必须保留的条件" * 1500, "use_mode": "always"}]}
    )
    original = [
        {"role": "system", "content": "规则"},
        memory,
        {"role": "user", "content": "当前问题"},
    ]
    settings = Settings(
        _env_file=None, context_request_tokens=1000, context_margin_tokens=100
    )
    with pytest.raises(ContextLimit, match="memory_capacity"):
        prepare_messages(original, [], 100, settings, purpose="agent")
    assert memory["content"].endswith("}")
