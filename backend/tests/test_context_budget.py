import json

import pytest

from app.context_budget import ContextLimit, prepare_messages
from app.core.config import Settings


def messages(*texts):
    rows = [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": "当前问题"},
    ]
    for index, body in enumerate(texts):
        rows += [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "opaque-reasoning",
                "tool_calls": [
                    {
                        "id": str(index),
                        "type": "function",
                        "function": {
                            "name": "search_course_materials",
                            "arguments": '{"query":"x"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": str(index),
                "content": json.dumps(
                    {
                        "sources": [
                            {
                                "label": 1,
                                "chunk_id": "chunk-a",
                                "document_id": "doc-a",
                                "text": body,
                            }
                        ]
                    }
                ),
            },
        ]
    return rows


def settings(**kwargs):
    return Settings(_env_file=None, context_budget_enabled=True, **kwargs)


def test_repeated_source_keeps_one_body_and_valid_reference():
    original = messages("定义：不得超过17。" * 40, "定义：不得超过17。" * 40)
    prepared = prepare_messages(original, [], 100, settings(), purpose="agent")
    first = json.loads(prepared.messages[3]["content"])["sources"][0]
    repeated = json.loads(prepared.messages[5]["content"])["sources"][0]
    assert first["text"] == "定义：不得超过17。" * 40
    assert "text" not in repeated
    assert repeated["text_ref"]["label"] == first["label"] == 1
    assert (
        original[5]["content"]
        == messages("定义：不得超过17。" * 40, "定义：不得超过17。" * 40)[5]["content"]
    )
    assert prepared.deduplicated == 1


def test_same_text_different_source_is_not_collapsed():
    rows = messages("same", "same")
    data = json.loads(rows[5]["content"])
    data["sources"][0]["chunk_id"] = "chunk-b"
    rows[5]["content"] = json.dumps(data)
    prepared = prepare_messages(rows, [], 100, settings(), purpose="agent")
    assert prepared.deduplicated == 0


def test_history_is_removed_as_whole_protocol_group_current_question_survives():
    previous = messages("旧正文" * 1000)[1:]
    rows = [
        {"role": "system", "content": "规则"},
        *previous,
        {"role": "assistant", "content": "旧答案"},
        {"role": "user", "content": "必须保留的当前问题"},
    ]
    prepared = prepare_messages(
        rows,
        [],
        100,
        settings(context_request_tokens=1800, context_margin_tokens=100),
        purpose="agent",
    )
    assert prepared.messages == [rows[0], rows[-1]]
    assert prepared.removed_groups == 1


def test_necessary_source_or_tool_schema_too_big_is_rejected_atomically():
    with pytest.raises(ContextLimit):
        prepare_messages(
            messages("表格完整证明" * 2000),
            [],
            100,
            settings(context_request_tokens=1500, context_margin_tokens=100),
            purpose="agent",
        )
    with pytest.raises(ContextLimit):
        prepare_messages(
            messages(),
            [{"description": "工具定义" * 2000}],
            100,
            settings(context_request_tokens=1500, context_margin_tokens=100),
            purpose="review",
        )


def test_image_unknown_is_not_url_character_estimate():
    rows = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,eA=="},
                }
            ],
        }
    ]
    with pytest.raises(ContextLimit, match="image_budget_unknown"):
        prepare_messages(rows, [], 100, settings(), purpose="agent")


def test_orphan_tool_response_is_rejected():
    with pytest.raises(ContextLimit, match="invalid_tool_protocol"):
        prepare_messages(
            [
                {"role": "user", "content": "question"},
                {"role": "tool", "tool_call_id": "missing", "content": "{}"},
            ],
            [],
            100,
            settings(),
            purpose="agent",
        )


def test_external_versions_and_authorizations_are_not_text_duplicates():
    rows = messages()
    for index, sha in enumerate(["commit-a", "commit-b"]):
        rows += [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": str(index),
                        "function": {"name": "read_repository_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": str(index),
                "content": json.dumps(
                    {
                        "source": {
                            "kind": "github",
                            "label": index + 1,
                            "repository": "self/authored",
                            "sha": sha,
                            "grant_id": "grant-a",
                            "text": "same text",
                        }
                    }
                ),
            },
        ]
    result = prepare_messages(rows, [], 100, settings(), purpose="agent")
    assert result.deduplicated == 0


def test_configured_visual_estimate_excludes_base64_transport_length():
    from app.context_budget import estimate

    small = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,eA=="},
                }
            ],
        }
    ]
    large = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + "eA==" * 1000},
                }
            ],
        }
    ]
    assert estimate(small, [], settings(context_image_tokens=4000)) == estimate(
        large, [], settings(context_image_tokens=4000)
    )


def test_reserved_capacity_is_not_available_to_exploration():
    from app.context_budget import BudgetLedger

    ledger = BudgetLedger(
        settings(
            context_request_tokens=2000,
            context_turn_tokens=20000,
            context_turn_calls=12,
        )
    )
    ledger.reserve(2000, "agent")
    ledger.reserve(2000, "query_rewrite")
    with pytest.raises(ContextLimit, match="turn_budget_exhausted"):
        ledger.reserve(1, "agent")
    for purpose in ["finalize", "review", "repair", "recheck"]:
        ledger.reserve(2000, purpose)
    assert ledger.snapshot()["estimated_reserved_units"] == 12000


@pytest.mark.parametrize(
    "purpose", ["agent", "query_rewrite", "review", "repair", "recheck", "finalize"]
)
def test_every_model_purpose_is_guarded_before_provider(monkeypatch, purpose):
    from app.core.config import get_settings
    from app.providers.call_options import profile
    from app.providers.llm import complete_turn

    monkeypatch.setattr(get_settings(), "context_budget_enabled", True)
    monkeypatch.setattr(get_settings(), "context_request_tokens", 6000)

    class Forbidden:
        def stream(self, *args, **kwargs):
            pytest.fail("budget must reject before provider side effects")

    with pytest.raises(ContextLimit, match="context_limit"):
        complete_turn(
            Forbidden(),
            [{"role": "user", "content": "必要来源" * 3000}],
            options=profile("agent"),
            measurement_purpose=purpose,
        )
