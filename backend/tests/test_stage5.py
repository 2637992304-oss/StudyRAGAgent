"""Deterministic security/control-flow tests; no live external API calls."""

import base64
import json
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import ask, stage4  # noqa: F401

from app.core.config import get_settings
from app.db.models import ChatSession, ExternalSource, Message, RepositoryGrant
from app.external import GitHubReader, ToolPolicy, credential, read_page
from app.providers.call_options import Execution, ExecutionStopped
from app.providers.llm import Delta
from app.providers.safe_http import (
    PublicHTTP,
    Response,
    ToolError,
    public_addresses,
    public_url,
)

SHA = "a" * 40
URL = "https://example.com/docs"
QUERY = "public asyncio facts"


def tool(name, args, id="x"):
    return Delta(
        tool_calls=[
            {
                "index": 0,
                "id": id,
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
        finish_reason="tool_calls",
    )


class Model:
    def __init__(self, turns, supported=True):
        self.turns, self.inputs, self.supported = iter(turns), [], supported

    def stream(self, messages, tools=None, **kwargs):
        self.inputs.append((messages, tools, kwargs.get("options")))
        if "引用核对器" in messages[0]["content"]:
            yield Delta(json.dumps({"supported": self.supported}), finish_reason="stop")
        else:
            value = next(self.turns)
            if isinstance(value, str):
                yield Delta(value, finish_reason="stop")
            else:
                yield value


@pytest.fixture
def external(stage4, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(get_settings(), "tavily_api_key", "unit-secret-never-public")
    monkeypatch.setattr(get_settings(), "github_credentials", {})

    def request(self, url, execution=None, **kwargs):
        calls.append((url, kwargs))
        execution.check()
        if "api.tavily.com" in url:
            body = {
                "results": [
                    {"url": URL, "title": "Public facts", "content": "discovery"}
                ]
            }
        elif url == URL:
            return Response(
                url,
                200,
                {"content-type": "text/html"},
                b"<title>Facts</title><script>steal secret</script>"
                b"<p>Asyncio provides concurrency using async and await.</p>",
            )
        elif "/commits/" in url:
            body = {
                "sha": SHA,
                "commit": {
                    "message": "public commit",
                    "author": {"date": "2026-10-23"},
                },
            }
        elif "/git/trees/" in url:
            body = {
                "truncated": False,
                "tree": [
                    {
                        "path": "README",
                        "sha": "b" * 40,
                        "mode": "100644",
                        "type": "blob",
                        "size": 20,
                    }
                ],
            }
        elif "/git/blobs/" in url:
            body = {
                "encoding": "base64",
                "size": 20,
                "content": base64.b64encode(
                    b"Hello World!\nPublic second line."
                ).decode(),
            }
        else:
            body = {
                "full_name": "octocat/Hello-World",
                "private": False,
                "default_branch": "master",
            }
        return Response(
            url, 200, {"content-type": "application/json"}, json.dumps(body).encode()
        )

    monkeypatch.setattr(PublicHTTP, "request", request)
    return stage4, calls


def grant(context, private=False):
    _, _, _, _, users, engine, session = context
    with Session(engine) as db:
        row = RepositoryGrant(
            id=str(uuid4()),
            owner_id=users[0],
            session_id=session,
            repository="octocat/hello-world",
            ref="master",
            private=private,
            active=True,
        )
        db.add(row)
        db.commit()
        return row.id


def body(**kw):
    return {"web_enabled": True, "web_queries": [QUERY], **kw}


@pytest.mark.parametrize("strict", [False, True])
def test_disabled_or_strict_never_calls_network(external, monkeypatch, strict):
    context, calls = external
    m = Model([tool("web_search", {"query": QUERY}), "【限制】没有外部授权。"])
    result, messages = ask(
        context,
        "严格根据课程资料" if strict else "你好",
        m,
        monkeypatch,
        **(body() if strict else {"web_enabled": False}),
    )
    assert not calls
    names = [t["function"]["name"] for t in m.inputs[0][1]]
    assert names == ["search_course_materials"]
    assert result[-1][0] == "done"
    assert "unit-secret" not in json.dumps(messages)


def test_search_read_persist_and_cross_account(external, monkeypatch):
    context, calls = external
    m = Model(
        [
            tool("web_search", {"query": QUERY}, "s"),
            tool("read_web_page", {"url": URL}, "r"),
            "网页说明支持异步并发 [1]。",
        ]
    )
    ev, messages = ask(context, "搜索公开说明并读取原文", m, monkeypatch, **body())
    assert len(calls) == 2 and ev[-1][0] == "done"
    citation = messages[-1]["citations"][0]
    assert citation["kind"] == "web" and citation["url"] == URL
    assert citation["accessed_at"] and "steal" not in citation["snippet"]
    client, headers, *_, sid = context
    endpoint = "/api/v1/citations/" + citation["citation_id"]
    assert client.get(endpoint, headers=headers[0]).status_code == 200
    assert client.get(endpoint, headers=headers[1]).status_code == 404
    with Session(context[5]) as db:
        saved = db.scalar(select(Message).where(Message.id == ev[-1][1]["message_id"]))
        assert saved.protocol_state["version"] == 2
        assert saved.protocol_state["external_ids"]
        assert db.scalar(select(ExternalSource)).data["read_status"] == "read"
    assert "protocol_state" not in messages[-1]
    assert m.inputs[-1][2].thinking is False
    # The same session with Web Search off cannot replay web-derived prose/protocol.
    follow = Model(["【需补充】请启用搜索。"])
    ask(context, "继续", follow, monkeypatch, web_enabled=False)
    assert "异步并发" not in json.dumps(follow.inputs[0][0], ensure_ascii=False)
    # Re-enabling can read a previously fetched URL without a redundant search.
    refresh = Model([tool("read_web_page", {"url": URL}, "again"), "异步并发 [1]。"])
    _, refreshed = ask(context, "重新读取历史网页", refresh, monkeypatch, **body())
    assert refreshed[-1]["citations"][0]["url"] == URL


@pytest.mark.parametrize(
    "name,args",
    [
        ("web_search", {"query": "private textbook secret"}),
        ("read_web_page", {"url": "https://example.com/leak?secret=private"}),
        ("web_search", {"query": QUERY, "token": "injected"}),
        ("write_repository", {"command": "rm"}),
    ],
)
def test_untrusted_parameters_never_send(external, monkeypatch, name, args):
    context, calls = external
    ask(
        context,
        "检查",
        Model([tool(name, args), "【限制】请求被拒绝。"]),
        monkeypatch,
        **body(),
    )
    assert not calls


def test_duplicate_discovery_and_multiple_tools(external, monkeypatch):
    context, calls = external
    delta = tool("web_search", {"query": QUERY}, "s")
    delta.tool_calls.append(
        {
            "index": 1,
            "id": "c",
            "function": {
                "name": "search_course_materials",
                "arguments": '{"query":"事实"}',
            },
        }
    )
    m = Model(
        [
            delta,
            tool("read_web_page", {"url": URL}, "r"),
            "课程事实 [1]，网页的异步概念 [2]。",
        ]
    )
    _, messages = ask(context, "结合网页和课件", m, monkeypatch, **body())
    assert {c["kind"] for c in messages[-1]["citations"]} == {"course", "web"}
    assert len(calls) == 2
    duplicate = Model(
        [
            tool("web_search", {"query": QUERY}, "a"),
            tool("web_search", {"query": QUERY}, "b"),
            "【限制】无新结果。",
        ]
    )
    ask(context, "查询", duplicate, monkeypatch, **body())
    assert len(calls) == 3  # duplicate was not executed


def test_github_independent_permission_sha_lines_revocation(external, monkeypatch):
    context, calls = external
    gid = grant(context)
    m = Model(
        [
            tool("github_read_file", {"grant_id": gid, "path": "README"}),
            "仓库README写着Hello World [1]。",
        ]
    )
    _, messages = ask(
        context, "读取已授权README", m, monkeypatch, repository_grant_ids=[gid]
    )
    c = messages[-1]["citations"][0]
    assert c["kind"] == "github" and c["sha"] == SHA
    assert c["line_start"] == 1 and c["line_end"] == 2
    assert f"/blob/{SHA}/README#L1-L2" in c["url"]
    assert not any("tavily" in u for u, _ in calls)
    client, headers, *_, sid = context
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/repositories/{gid}", headers=headers[1]
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/repositories/{gid}", headers=headers[0]
        ).status_code
        == 204
    )
    history = client.get(
        f"/api/v1/chat/sessions/{sid}/messages", headers=headers[0]
    ).json()
    assert "Hello World" not in history[-1]["content"]
    assert not history[-1]["citations"]
    assert (
        client.get(
            "/api/v1/citations/" + c["citation_id"], headers=headers[0]
        ).status_code
        == 404
    )
    follow = Model(["【需补充】请重新授权。"])
    ask(context, "继续刚才的仓库解释", follow, monkeypatch)
    assert "Hello World" not in json.dumps(follow.inputs[0][0])


def test_pinned_directory_file_and_commit(external, monkeypatch):
    context, calls = external
    gid = grant(context)
    m = Model(
        [
            tool("github_list_directory", {"grant_id": gid}, "d"),
            tool("github_read_file", {"grant_id": gid, "path": "README"}, "f"),
            "README中有Hello World [2]。",
        ]
    )
    _, messages = ask(
        context, "先目录后文件", m, monkeypatch, repository_grant_ids=[gid]
    )
    assert sum("/commits/" in u for u, _ in calls) == 1
    assert all(SHA in u for u, _ in calls if "/git/trees/" in u)
    assert messages[-1]["citations"][0]["label"] == 2


def test_grant_api_forbids_tokens_and_cross_session(external, monkeypatch):
    context, _ = external
    client, headers, courses, *_, sid = context
    url = f"/api/v1/chat/sessions/{sid}/repositories"
    assert (
        client.post(
            url,
            json={"repository": "octocat/Hello-World", "token": "bad"},
            headers=headers[0],
        ).status_code
        == 422
    )
    g = client.post(url, json={"repository": "octocat/Hello-World"}, headers=headers[0])
    assert g.status_code == 201
    other = client.post(
        "/api/v1/chat/sessions", json={"course_id": courses[1]}, headers=headers[0]
    ).json()["id"]
    denied = client.post(
        f"/api/v1/chat/sessions/{other}/messages",
        json={"content": "read", "repository_grant_ids": [g.json()["id"]]},
        headers=headers[0],
    )
    assert denied.status_code == 404


def test_private_credentials_bound_to_exact_account_and_repo(external, monkeypatch):
    context, calls = external
    gid = grant(context, private=True)
    client, headers, _, _, users, _, sid = context
    url = f"/api/v1/chat/sessions/{sid}/messages"
    assert (
        client.post(
            url,
            json={"content": "read", "repository_grant_ids": [gid]},
            headers=headers[0],
        ).status_code
        == 404
    )
    monkeypatch.setattr(
        get_settings(),
        "github_credentials",
        {users[0]: {"octocat/hello-world": "private-secret"}},
    )
    assert credential(users[1], "octocat/hello-world") == ""
    assert credential(users[0], "other/repo") == ""
    _, messages = ask(
        context,
        "读取",
        Model(
            [
                tool("github_read_file", {"grant_id": gid, "path": "README"}),
                "Hello World [1]",
            ]
        ),
        monkeypatch,
        repository_grant_ids=[gid],
    )
    assert any(
        kw.get("headers", {}).get("Authorization") == "Bearer private-secret"
        for _, kw in calls
    )
    assert "private-secret" not in json.dumps(messages)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_web_switch_strict_bool(external, value):
    context, calls = external
    client, headers, *_, sid = context
    assert (
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"content": "hi", "web_enabled": value},
            headers=headers[0],
        ).status_code
        == 422
    )
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "https://example.com:8000/",
        "https://u:p@example.com/",
        "https://example.com\\@127.0.0.1/",
        "https://example.com/\r\nheader",
    ],
)
def test_ssrf_url_rejection(url):
    with pytest.raises(ToolError):
        public_url(url)


def test_dns_mixed_addresses_block_before_connection(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(ToolError, match="unsafe_address"):
        public_addresses("example.com", 80)


@pytest.mark.parametrize(
    "status,code",
    [(401, "auth"), (403, "forbidden"), (429, "rate_limit"), (500, "upstream_error")],
)
def test_sanitized_upstream_failures(status, code):
    class HTTP:
        def request(self, *a, **k):
            return Response(URL, status, {}, b"private upstream error and token")

    with pytest.raises(ToolError, match=code):
        GitHubReader("u", "owner/repo", HTTP()).metadata(Execution.start(1))


def test_page_empty_and_unsupported_body():
    class HTTP:
        def __init__(self, mime, data):
            self.mime, self.data = mime, data

        def request(self, *a, **k):
            return Response(URL, 200, {"content-type": self.mime}, self.data)

    with pytest.raises(ToolError, match="empty_body"):
        read_page(
            URL, Execution.start(1), HTTP("text/html", b"<script>ignored</script>")
        )
    with pytest.raises(ToolError, match="unsupported_content"):
        read_page(URL, Execution.start(1), HTTP("application/pdf", b"pdf"))


def test_forged_or_unsupported_external_citation(external, monkeypatch):
    context, _ = external
    m = Model(
        [
            tool("web_search", {"query": QUERY}, "s"),
            tool("read_web_page", {"url": URL}, "r"),
            "不存在的断言 [99]。",
        ]
    )
    _, messages = ask(context, "查询", m, monkeypatch, **body())
    assert messages[-1]["stop_reason"] == "invalid_citation"
    assert not messages[-1]["citations"]
    m = Model(
        [
            tool("web_search", {"query": QUERY}, "s"),
            tool("read_web_page", {"url": URL}, "r"),
            "无依据事实 [1]。",
        ],
        supported=False,
    )
    _, messages = ask(context, "查询", m, monkeypatch, **body())
    assert messages[-1]["stop_reason"] == "unsupported"
    assert "无依据事实" not in messages[-1]["content"]


def test_delete_session_cleans_external_records(external, monkeypatch):
    context, _ = external
    gid = grant(context)
    ask(
        context,
        "读取",
        Model(
            [
                tool("github_read_file", {"grant_id": gid, "path": "README"}),
                "Hello World [1]",
            ]
        ),
        monkeypatch,
        repository_grant_ids=[gid],
    )
    client, headers, *_, sid = context
    assert (
        client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers[0]).status_code
        == 204
    )
    with Session(context[5]) as db:
        assert db.scalar(select(ExternalSource)) is None
        assert db.scalar(select(RepositoryGrant)) is None


@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/", "http://[::1]/", "https://github.com/unapproved/repo"],
)
def test_redirect_target_rechecked_before_connect(monkeypatch, target):
    from app.external import web_target
    from app.providers import safe_http

    connected = []
    monkeypatch.setattr(safe_http, "public_addresses", lambda *a: ["93.184.216.34"])

    class Sock:
        def getpeername(self):
            return ("93.184.216.34", 80)

        def settimeout(self, n):
            pass

    class Reply:
        status = 302

        def getheaders(self):
            return [("Location", target)]

    class Connection:
        sock = None

        def __init__(self, *a, **kw):
            self.timeout = kw["timeout"]

        def request(self, *a):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(safe_http.http.client, "HTTPConnection", Connection)

    def connect(address, timeout):
        connected.append(address)
        return Sock()

    monkeypatch.setattr(safe_http.socket, "create_connection", connect)
    with pytest.raises(ToolError, match="unsafe_url|use_authorized_github_tool"):
        PublicHTTP().request(
            "http://example.com", Execution.start(2), url_check=web_target
        )
    assert connected == [("93.184.216.34", 80)]


def test_dns_pinned_tls_host_and_body_limit(monkeypatch):
    from app.providers import safe_http

    connected, tls_hosts, lookups = [], [], []

    def addresses(host, port):
        lookups.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(safe_http, "public_addresses", addresses)

    class Sock:
        def getpeername(self):
            return ("93.184.216.34", 443)

        def settimeout(self, n):
            pass

    class TLS:
        def wrap_socket(self, sock, server_hostname):
            tls_hosts.append(server_hostname)
            return sock

    class Reply:
        status = 200

        def getheaders(self):
            return []

        def read1(self, size):
            return b"x" * size

    class Connection:
        sock = None

        def __init__(self, *a, **kw):
            self.timeout = kw["timeout"]

        def request(self, *a):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    def connect(address, timeout):
        connected.append(address)
        return Sock()

    monkeypatch.setattr(safe_http.socket, "create_connection", connect)
    monkeypatch.setattr(safe_http.ssl, "create_default_context", TLS)
    monkeypatch.setattr(safe_http.http.client, "HTTPConnection", Connection)
    with pytest.raises(ToolError, match="response_too_large"):
        PublicHTTP().request("https://example.com", Execution.start(2), max_bytes=20)
    assert lookups == ["example.com"] and tls_hosts == ["example.com"]
    assert connected == [("93.184.216.34", 443)]


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_silent_external_transport_cancellation(monkeypatch, kind):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered, release = threading.Event(), threading.Event()
    now = [0.0]
    execution = Execution(20, clock=lambda: now[0])

    def blocked(*args):
        entered.set()
        release.wait(2)
        return "late private result"

    monkeypatch.setattr(PublicHTTP, "_request", blocked)
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(PublicHTTP().request, URL, execution)
        assert entered.wait(1)
        if kind == "cancel":
            execution.cancelled.set()
        else:
            now[0] = 21
        try:
            with pytest.raises(ExecutionStopped):
                task.result(timeout=1)
        finally:
            release.set()


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_github_rejects_symlink_and_submodule(mode):
    class Reader(GitHubReader):
        def tree(self, sha, execution):
            return [{"path": "link", "mode": mode, "type": "blob", "sha": SHA}]

    with pytest.raises(ToolError, match="link_or_submodule"):
        Reader("u", "owner/repo").entry(SHA, "link", Execution.start(1))


def test_github_binary_and_invalid_line_bounds(external, monkeypatch):
    from app.external import ExternalTools

    context, _ = external
    gid = grant(context)
    with Session(context[5]) as db:
        chat = db.get(ChatSession, context[-1])
        service = ExternalTools(
            db, chat, ToolPolicy(grant_ids=(gid,)), Execution.start(2)
        )
        with pytest.raises(ToolError, match="invalid_lines"):
            service.invoke(
                "github_read_file",
                json.dumps({"grant_id": gid, "path": "README", "end_line": 200}),
            )
        with pytest.raises(ToolError, match="invalid_path"):
            service.invoke(
                "github_read_file", json.dumps({"grant_id": gid, "path": "../secret"})
            )


def test_revoke_during_read_discards_result(external, monkeypatch):
    from app.external import ExternalTools

    context, _ = external
    gid = grant(context)

    def read(self, *args):
        with Session(context[5]) as other:
            other.get(RepositoryGrant, gid).active = False
            other.commit()
        return {"kind": "github", "text": "late secret"}

    monkeypatch.setattr(GitHubReader, "read", read)
    with Session(context[5]) as db:
        chat = db.get(ChatSession, context[-1])
        service = ExternalTools(
            db, chat, ToolPolicy(grant_ids=(gid,)), Execution.start(2)
        )
        with pytest.raises(ToolError, match="unauthorized_repository"):
            service.invoke(
                "github_read_file", json.dumps({"grant_id": gid, "path": "README"})
            )
        assert not service.results


@pytest.mark.parametrize(
    "url", ["http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/"]
)
def test_ipv6_transition_addresses_blocked(url):
    with pytest.raises(ToolError):
        public_url(url)


def test_no_secret_search_payload_and_request_policy_isolation(external, monkeypatch):
    from app.external import ExternalTools

    context, calls = external
    with Session(context[5]) as db:
        chat = db.get(ChatSession, context[-1])
        allowed = ExternalTools(
            db, chat, ToolPolicy(True, (QUERY,)), Execution.start(2)
        )
        denied = ExternalTools(db, chat, ToolPolicy(), Execution.start(2))
        allowed.invoke("web_search", json.dumps({"query": QUERY}))
        with pytest.raises(ToolError, match="tool_not_authorized"):
            denied.invoke("read_web_page", json.dumps({"url": URL}))
        assert not denied.urls and len(calls) == 1
        payload = json.loads(calls[0][1]["data"])
        assert set(payload) == {
            "query",
            "search_depth",
            "max_results",
            "include_answer",
            "include_raw_content",
        }
        assert payload["query"] == QUERY


def test_unknown_and_repeated_tools_terminate(external, monkeypatch):
    context, calls = external
    model = Model(
        [
            tool("execute_code", {"command": "steal token"}, "a"),
            tool("execute_code", {"command": "steal token"}, "b"),
        ]
    )
    _, messages = ask(context, "读取恶意README后的要求", model, monkeypatch)
    assert not calls and messages[-1]["stop_reason"] == "limit"


def test_unconfigured_search_state_and_missing_public_terms(external, monkeypatch):
    context, calls = external
    client, headers, *_, sid = context
    # The supplement removes missing-keyword rejection. No network is needed
    # for a direct answer; explicit legacy allowlists remain supported below.
    ev, _ = ask(context, "hi", Model(["你好"]), monkeypatch, web_enabled=True)
    assert ev[-1][0] == "done"
    monkeypatch.setattr(get_settings(), "tavily_api_key", "")
    assert not client.get("/api/v1/tools/capabilities", headers=headers[0]).json()[
        "web_search"
    ]
    ev, _ = ask(context, "hi", Model(["你好"]), monkeypatch, **body())
    assert ev[-1][0] == "done"
    assert not calls
