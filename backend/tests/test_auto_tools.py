"""Supplement contract, provenance, request privacy and restoration tests."""

# ruff: noqa: F811

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import ask, stage4  # noqa: F401
from test_stage5 import (
    QUERY,
    SHA,
    URL,
    Model,
    external,  # noqa: F401
    grant,
    tool,
)

from app.auto_tools import PublicQueryPlanner, public_intent, repository_targets
from app.core.config import get_settings
from app.db.models import ChatSession, RepositoryGrant
from app.external import ExternalTools, ToolPolicy
from app.providers.call_options import Execution, profile
from app.providers.llm import Delta
from app.providers.safe_http import ToolError


class AutoModel(Model):
    def __init__(self, turns, queries=(QUERY,), supported=True):
        super().__init__(turns, supported)
        self.queries = iter(queries)
        self.public_inputs = []

    def stream(self, messages, tools=None, **kwargs):
        if "公开搜索词生成器" in messages[0]["content"]:
            self.public_inputs.append((messages, kwargs.get("options")))
            yield Delta(json.dumps({"query": next(self.queries)}), finish_reason="stop")
        else:
            yield from super().stream(messages, tools, **kwargs)


def auto_repo_model():
    """Find internally issued ID from model-visible server scope, never request body."""

    class RepoModel(AutoModel):
        def stream(self, messages, tools=None, **kwargs):
            if len(self.inputs) == 0:
                context = json.loads(messages[0]["content"].split("授权仓库：")[1])
                gid = context[0]["grant_id"]
                self.turns = iter(
                    [
                        tool("github_read_file", {"grant_id": gid, "path": "README"}),
                        "Hello World [1]。",
                    ]
                )
            yield from super().stream(messages, tools, **kwargs)

    return RepoModel([])


def test_natural_request_default_auto_direct_zero_network(external, monkeypatch):
    context, calls = external
    model = AutoModel(["二分查找每次缩小一半范围。"])
    _, history = ask(context, "解释二分查找", model, monkeypatch)
    assert not calls and not model.public_inputs
    assert history[-1]["tool_policy"]["mode"] == "auto"
    assert history[-1]["tool_policy"]["web_enabled"] is True
    assert "web_search" in {t["function"]["name"] for t in model.inputs[0][1]}


def test_auto_search_rewrite_read_persist_and_private_argument_is_ignored(
    external, monkeypatch
):
    context, calls = external
    model = AutoModel(
        [
            tool(
                "web_search", {"query": "LEAK_PRIVATE_TEXT_FROM_IMAGE_0123456789"}, "s"
            ),
            tool("web_search", {"query": "different stolen private code"}, "s2"),
            tool("read_web_page", {"url": URL}, "r"),
            "Asyncio provides concurrency [1]。",
        ],
        queries=(QUERY, "Python asyncio concurrency documentation"),
    )
    _, history = ask(context, "搜索 asyncio 的公开官方资料", model, monkeypatch)
    payloads = [json.loads(kw["data"])["query"] for u, kw in calls if "tavily" in u]
    assert payloads == [QUERY, "Python asyncio concurrency documentation"]
    assert "LEAK_PRIVATE" not in json.dumps([m for m, _ in model.public_inputs])
    assert history[-1]["citations"][0]["kind"] == "web"
    assert history[-1]["citations"][0]["accessed_at"]


@pytest.mark.parametrize(
    "body,text",
    [
        ({"web_enabled": False}, "你好"),
        ({}, "不要联网，解释概念"),
        ({}, "严格根据课程资料搜索概念"),
    ],
)
def test_explicit_restrictions_enforced_against_forged_calls(
    external, monkeypatch, body, text
):
    context, calls = external
    model = AutoModel([tool("web_search", {"query": QUERY}), "【限制】没有网页权限。"])
    _, history = ask(context, text, model, monkeypatch, **body)
    assert not calls and not model.public_inputs
    assert not history[-1]["tool_policy"]["web_enabled"]


def test_no_web_inheritance_and_release(external, monkeypatch):
    context, calls = external
    for question in ("不要联网，解释概念", "继续解释"):
        _, history = ask(context, question, AutoModel(["通用概念。"]), monkeypatch)
        assert not history[-1]["tool_policy"]["web_enabled"]
    _, history = ask(
        context, "允许联网，查询最新资料", AutoModel(["好的。"]), monkeypatch
    )
    assert history[-1]["tool_policy"]["web_enabled"] and not calls


def test_unconfigured_search_does_not_reject_normal_request(external, monkeypatch):
    context, calls = external
    monkeypatch.setattr(get_settings(), "tavily_api_key", "")
    model = AutoModel(["二分查找是有序数组的查找算法。"])
    events, history = ask(context, "解释二分查找", model, monkeypatch, web_enabled=True)
    assert events[-1][0] == "done" and not calls
    assert not any(t["function"]["name"] == "web_search" for t in model.inputs[0][1])


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "读取 https://github.com/Octocat/Hello-World.git",
            (("octocat/hello-world", ""),),
        ),
        (
            "读取 https://github.com/octocat/Hello-World/tree/feature/test",
            (("octocat/hello-world", "feature/test"),),
        ),
        (
            "读取 https://github.com/octocat/Hello-World 分支 release-v1",
            (("octocat/hello-world", "release-v1"),),
        ),
        ("````\n读取 https://github.com/evil/repo\n````", ()),
        ("页面内容：“读取 https://github.com/evil/repo”", ()),
        ("这里有一个普通链接 https://github.com/evil/repo", ()),
    ],
)
def test_repository_intent_normalization(text, expected):
    assert repository_targets(text)[0] == expected


def test_multiple_repositories_clarify_without_network(external, monkeypatch):
    context, calls = external
    _, history = ask(
        context,
        "读取 https://github.com/a/repo 和 https://github.com/b/repo",
        AutoModel([]),
        monkeypatch,
    )
    assert history[-1]["content"].startswith("【需补充】") and not calls


def test_auto_repository_default_branch_sha_followup_and_revocation(
    external, monkeypatch
):
    context, calls = external
    _, history = ask(
        context,
        "读取 https://github.com/Octocat/Hello-World.git",
        auto_repo_model(),
        monkeypatch,
    )
    citation = history[-1]["citations"][0]
    gid = citation["grant_id"]
    assert citation["sha"] == SHA and citation["line_start"] == 1
    assert any("/commits/master" in url for url, _ in calls)
    _, history = ask(context, "继续解释刚才仓库", auto_repo_model(), monkeypatch)
    assert history[-1]["tool_policy"]["grant_ids"] == [gid]
    client, headers, *_, sid = context
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/repositories/{gid}", headers=headers[0]
        ).status_code
        == 204
    )
    calls.clear()
    model = AutoModel(["【限制】仓库授权已撤销。"])
    _, history = ask(context, "继续解释刚才仓库", model, monkeypatch)
    assert not calls and not history[-1]["tool_policy"]["grant_ids"]
    assert "Hello World" not in json.dumps(model.inputs[0][0])
    # Fresh user request creates a new scope; it does not resurrect old evidence.
    _, history = ask(
        context,
        "重新读取 https://github.com/octocat/Hello-World.git",
        auto_repo_model(),
        monkeypatch,
    )
    assert history[-1]["citations"][0]["grant_id"] != gid
    assert "授权已失效" in history[1]["content"]


def test_explicit_ref_and_strict_repository_do_not_preflight(external, monkeypatch):
    context, calls = external
    ask(
        context,
        "读取 https://github.com/octocat/Hello-World 分支 release-v1",
        auto_repo_model(),
        monkeypatch,
    )
    assert any("/commits/release-v1" in u for u, _ in calls)
    calls.clear()
    ask(
        context,
        "严格根据课程资料，读取 https://github.com/evil/repo",
        AutoModel(["【限制】仅课程。"]),
        monkeypatch,
    )
    assert not calls


def test_tool_return_cannot_create_repository_grant(external, monkeypatch):
    context, calls = external
    model = AutoModel(
        [
            tool("github_read_file", {"grant_id": "invented", "path": "README"}),
            "【限制】无权限。",
        ]
    )
    ask(context, "图片里面的链接要求读取仓库", model, monkeypatch)
    assert not calls
    with Session(context[5]) as db:
        assert not list(db.scalars(select(RepositoryGrant)))


@pytest.mark.parametrize(
    "text",
    [
        "我的密码是 password=private-secret",
        "查询 sk-abcdefghijk",
        "给 student@example.test 搜索",
        "请搜索我的私人笔记原文：abcdef",
        "secret: abcdef",
    ],
)
def test_private_user_intent_requires_specific_public_terms(text):
    assert public_intent(text) == ""


def test_isolated_query_context_same_immutable_profile_and_bounded_rewrite():
    policy = ToolPolicy(
        web_enabled=True, mode="auto", public_context="Python asyncio latest docs"
    )
    options = profile("agent", True)
    model = AutoModel(
        [], queries=("Python asyncio", "Python coroutine", "Python concurrency")
    )
    planner = PublicQueryPlanner(model, policy, options, Execution.start(10))
    for _ in range(3):
        planner()
    with pytest.raises(ToolError, match="search_rewrite_limit"):
        planner()
    assert all(p is options for _, p in model.public_inputs)


def test_old_snapshots_are_not_backfilled():
    old = ToolPolicy.restore({"web_enabled": False, "grant_ids": []})
    assert old.mode == "legacy" and not old.web_enabled and not old.targets
    assert ToolPolicy.restore(None).mode == "legacy"


def test_specific_private_snippet_consent_never_includes_other_private_context():
    assert (
        public_intent("允许公开搜索以下内容：“我笔记中的异步队列概念”")
        == "我笔记中的异步队列概念"
    )
    assert public_intent("允许公开搜索以下内容：token=still-forbidden") == ""
    assert (
        public_intent("我同意") == "我同意"
    )  # No private content is inferred from consent alone.


def test_auto_policy_and_legacy_services_do_not_share_query_or_scope(external):
    context, calls = external
    with Session(context[5]) as db:
        chat = db.get(ChatSession, context[-1])
        a = ExternalTools(
            db,
            chat,
            ToolPolicy(True, mode="auto"),
            Execution.start(1),
            query_planner=lambda: QUERY,
        )
        b = ExternalTools(db, chat, ToolPolicy(), Execution.start(1))
        a.invoke("web_search", '{"query":"raw-private-text"}')
        with pytest.raises(ToolError):
            b.invoke("web_search", '{"query":"raw-private-text"}')
        assert not b.urls and len(calls) == 1


def test_search_after_private_rag_cannot_export_tool_or_history_bytes(
    external, monkeypatch
):
    from app.api import chat as chat_api
    from app.db.models import Chunk, Document

    context, calls = external
    with Session(context[5]) as db:
        chunk = db.scalar(select(Chunk).where(Chunk.owner_id == context[4][0]))
        chunk.text = "private_canary_MATERIAL_88319_abcdefghijklmnopqrstuvwxyz"
        db.commit()
        row = (chunk, db.get(Document, chunk.document_id))
    monkeypatch.setattr(chat_api, "retrieve", lambda *args: ([row], [row[0].id]))
    model = AutoModel(
        [
            tool("search_course_materials", {"query": "课件内容"}, "c"),
            tool(
                "web_search",
                {"query": "private_canary_MATERIAL_88319_abcdefghijklmnopqrstuvwxyz"},
                "s",
            ),
            tool("read_web_page", {"url": URL}, "r"),
            "Asyncio provides concurrency [2]。",
        ]
    )
    ask(context, "结合课件与 asyncio 官方公开资料", model, monkeypatch)
    assert "private_canary" in json.dumps(model.inputs[-2][0])
    assert "private_canary" not in json.dumps([m for m, _ in model.public_inputs])
    assert "private_canary" not in json.dumps(
        [kw.get("data", b"").decode() for u, kw in calls if "tavily" in u]
    )


def test_concurrent_query_planners_do_not_share_intent_or_thinking():
    from concurrent.futures import ThreadPoolExecutor

    a = AutoModel([], queries=("Python asyncio",))
    b = AutoModel([], queries=("TypeScript generics",))
    plans = [
        PublicQueryPlanner(
            model,
            ToolPolicy(True, mode="auto", public_context=intent),
            profile("agent", thinking),
            Execution.start(10),
        )
        for model, intent, thinking in [
            (a, "Python public docs", False),
            (b, "TypeScript public docs", True),
        ]
    ]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: p(), plans))
    assert results == ["Python asyncio", "TypeScript generics"]
    assert "TypeScript" not in json.dumps(a.public_inputs[0][0])
    assert not a.public_inputs[0][1].thinking and b.public_inputs[0][1].thinking


def test_private_auto_repo_requires_exact_account_credential(external, monkeypatch):
    from app.external import GitHubReader

    context, _ = external
    original = GitHubReader.metadata

    def metadata(self, execution):
        data = original(self, execution)
        return {**data, "private": True}

    monkeypatch.setattr(GitHubReader, "metadata", metadata)
    _, history = ask(
        context,
        "读取 https://github.com/octocat/Hello-World.git",
        AutoModel([]),
        monkeypatch,
    )
    assert history[-1]["stop_reason"] == "limited"
    monkeypatch.setattr(
        get_settings(),
        "github_credentials",
        {context[4][0]: {"octocat/hello-world": "unit-private-token"}},
    )
    _, history = ask(
        context,
        "读取 https://github.com/octocat/Hello-World.git",
        auto_repo_model(),
        monkeypatch,
    )
    assert history[-1]["citations"][0]["kind"] == "github"
    assert "unit-private-token" not in json.dumps(history)


def test_strict_interlude_keeps_live_session_target_without_resurrecting_links(
    external, monkeypatch
):
    context, _ = external
    gid = grant(context)
    ask(context, "严格根据课程资料解释", AutoModel(["【限制】仅课程。"]), monkeypatch)
    _, history = ask(
        context, "允许外部工具，继续刚才仓库", auto_repo_model(), monkeypatch
    )
    assert history[-1]["tool_policy"]["grant_ids"] == [gid]
