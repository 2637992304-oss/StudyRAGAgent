"""External tools: request-local policy, server credentials, typed evidence."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ExternalSource, Message, RepositoryGrant
from app.evaluation.telemetry import measured
from app.providers.safe_http import PublicHTTP, ToolError, public_url, response_json


def stamp():
    return datetime.now(UTC).isoformat()


def web_target(url):
    host = urlsplit(public_url(url)).hostname
    if host in {
        "github.com",
        "www.github.com",
        "api.github.com",
        "gist.github.com",
        "codeload.github.com",
    } or host.endswith(".githubusercontent.com"):
        raise ToolError("use_authorized_github_tool")


class SearchProvider(Protocol):
    def search(self, query, execution) -> list[dict]: ...


class TavilySearch:
    def __init__(self, http=None):
        self.http = http or PublicHTTP()

    def search(self, query, execution):
        settings = get_settings()
        if not settings.tavily_api_key:
            raise ToolError("not_configured")
        response = self.http.request(
            "https://api.tavily.com/search",
            execution,
            method="POST",
            headers={
                "Authorization": f"Bearer {settings.tavily_api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                }
            ).encode(),
            redirects=0,
            timeout=settings.external_timeout,
        )
        payload = response_json(response)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise ToolError("invalid_response")
        results = []
        for item in payload["results"][:5]:
            try:
                url = public_url(item["url"])
                web_target(url)
            except (KeyError, TypeError, ValueError):
                continue
            results.append(
                {
                    "url": url,
                    "title": str(item.get("title", ""))[:300],
                    "snippet": str(item.get("content", ""))[:600],
                    "read_status": "discovery_only",
                }
            )
        return results


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignore, self.in_title, self.parts, self.title = 0, False, [], []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href and len(self.links) < 100:
                self.links.append(href)
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.ignore += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.ignore = max(0, self.ignore - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.ignore:
            self.parts.append(data)


def read_page(url, execution, http=None):
    web_target(url)
    response = (http or PublicHTTP()).request(
        url, execution, timeout=get_settings().external_timeout, url_check=web_target
    )
    if response.status != 200:
        response_json(response)  # shared sanitized HTTP error classification
    mime = response.headers.get("content-type", "").lower()
    if not any(t in mime for t in ("text/html", "text/plain", "application/xhtml+xml")):
        raise ToolError("unsupported_content")
    encoding = re.search(r"charset=([\w-]+)", mime)
    try:
        raw = response.body.decode(
            encoding[1] if encoding else "utf-8", errors="replace"
        )
    except LookupError:
        raw = response.body.decode("utf-8", errors="replace")
    parser = PageText()
    if "html" in mime:
        parser.feed(raw)
        text = "\n".join(
            line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
        )
    else:
        text = raw.strip()
    if len(text.strip()) < 20:
        raise ToolError("empty_body")
    from urllib.parse import urljoin

    links = []
    for href in parser.links:
        try:
            link = public_url(urljoin(response.url, href))
        except ValueError:
            continue
        if link not in links and link != response.url:
            links.append(link)
    return {
        "kind": "web",
        "title": "".join(parser.title)[:300] or response.url,
        "url": response.url,
        "accessed_at": stamp(),
        "text": text[:12000],
        "truncated": len(text) > 12000,
        "read_status": "read",
        "links": links[:40],
    }


def repository_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ToolError("invalid_repository")
    if value.rsplit("/", 1)[1] in {".", ".."}:
        raise ToolError("invalid_repository")
    return value.lower()


def repository_path(value):
    if (
        len(value) > 600
        or value.startswith("/")
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or any(p in {".", "..", ""} for p in value.split("/"))
    ):
        if value != "":
            raise ToolError("invalid_path")
    return value


def credential(owner_id, repository):
    return get_settings().github_credentials.get(owner_id, {}).get(repository, "")


class GitHubReader:
    def __init__(self, owner_id, repository, http=None):
        self.owner_id, self.repository = owner_id, repository_name(repository)
        self.http = http or PublicHTTP()

    def get(self, suffix, execution):
        token = credential(self.owner_id, self.repository)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        result = response_json(
            self.http.request(
                f"https://api.github.com/repos/{self.repository}{suffix}",
                execution,
                headers=headers,
                redirects=0,
                max_bytes=2_000_000,
                timeout=get_settings().external_timeout,
            )
        )

        if not isinstance(result, dict):
            raise ToolError("invalid_response")
        return result

    def metadata(self, execution):
        result = self.get("", execution)
        if result.get("full_name", "").lower() != self.repository:
            raise ToolError("repository_moved")
        return result

    def commit(self, ref, execution):
        data = self.get("/commits/" + quote(ref, safe=""), execution)
        sha = data.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ToolError("invalid_commit")
        return data

    def tree(self, sha, execution):
        data = self.get("/git/trees/" + sha, execution)
        if data.get("truncated"):
            raise ToolError("tree_too_large")
        return data["tree"]

    def entry(self, sha, path, execution):
        """Traverse exact tree entries; Contents API may silently follow symlinks."""
        tree = self.tree(sha, execution)
        parts = repository_path(path).split("/") if path else []
        for i, part in enumerate(parts):
            item = next((e for e in tree if e["path"] == part), None)
            if not item:
                raise ToolError("not_found")
            if item.get("mode") not in {"100644", "100755", "040000"}:
                raise ToolError("link_or_submodule")
            if i == len(parts) - 1:
                return item
            if item["type"] != "tree":
                raise ToolError("not_directory")
            tree = self.tree(item["sha"], execution)
        return {"type": "tree", "sha": sha}

    def read(self, sha, path, start, end, execution):
        entry = self.entry(sha, path, execution)
        if entry["type"] != "blob":
            raise ToolError("not_file")
        if entry.get("size", 0) > 700_000:
            raise ToolError("file_too_large")
        blob = self.get("/git/blobs/" + entry["sha"], execution)
        if blob.get("encoding") != "base64" or blob.get("size", 0) > 700_000:
            raise ToolError("file_too_large")
        try:
            text = base64.b64decode(blob["content"]).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ToolError("binary_file") from exc
        if "\x00" in text:
            raise ToolError("binary_file")
        lines = text.splitlines()
        if start > len(lines):
            raise ToolError("invalid_lines")
        actual_end = min(end, len(lines))
        text = "\n".join(lines[start - 1 : actual_end])
        if len(text) > 12000:
            raise ToolError("line_range_too_large")
        return {
            "kind": "github",
            "title": f"{self.repository}/{path}",
            "repository": self.repository,
            "sha": sha,
            "path": path,
            "line_start": start,
            "line_end": actual_end,
            "text": text,
            "url": f"https://github.com/{self.repository}/blob/{sha}/"
            f"{quote(path, safe='/')}#L{start}-L{actual_end}",
            "accessed_at": stamp(),
            "read_status": "read",
            "truncated": False,
        }


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WebSearchArgs(Args):
    query: str = Field(min_length=1, max_length=600)


class WebReadArgs(Args):
    url: str = Field(min_length=1, max_length=2048)


class GitHubArgs(Args):
    grant_id: str = Field(min_length=1, max_length=36)
    path: str = Field(default="", max_length=600)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=160, ge=1)
    offset: int = Field(default=0, ge=0, le=100000)


def definition(name, description, args):
    schema = args.model_json_schema()
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    }


EXTERNAL_TOOLS = {
    "web_search": (
        WebSearchArgs,
        "按用户任务搜索公开信息，可改写补查；服务端隔离生成实际公开查询。摘要仅用于发现，事实需读取网页。",
    ),
    "read_web_page": (WebReadArgs, "读取本轮搜索发现的网页正文并登记可引用来源。"),
    "github_list_directory": (
        GitHubArgs,
        "读取已授权仓库固定提交的目录；每页100项，offset翻页。",
    ),
    "github_read_file": (
        GitHubArgs,
        "读取已授权仓库固定提交的UTF-8文件（README或代码），最多160行。",
    ),
    "github_read_commit": (GitHubArgs, "读取已授权仓库固定提交的实际元数据。"),
}


@dataclass(frozen=True)
class ToolPolicy:
    web_enabled: bool = False
    web_queries: tuple[str, ...] = ()
    grant_ids: tuple[str, ...] = ()
    mode: str = "legacy"
    web_blocked: bool = False
    public_context: str = ""
    targets: tuple[tuple[str, str], ...] = ()
    notice: str = ""

    def snapshot(self):
        return {
            "version": 2,
            "mode": self.mode,
            "web_enabled": self.web_enabled,
            "web_queries": list(self.web_queries),
            "grant_ids": list(self.grant_ids),
            "web_blocked": self.web_blocked,
            "public_context": self.public_context,
            "targets": [list(t) for t in self.targets],
            "notice": self.notice,
        }

    @classmethod
    def restore(cls, data):
        data = data or {}
        return cls(
            bool(data.get("web_enabled")),
            tuple(data.get("web_queries", [])),
            tuple(data.get("grant_ids", [])),
            data.get("mode", "legacy"),
            bool(data.get("web_blocked", False)),
            data.get("public_context", ""),
            tuple(tuple(t) for t in data.get("targets", [])),
            data.get("notice", ""),
        )


def live_grant(db, chat, grant_id):
    grant = db.scalar(
        select(RepositoryGrant)
        .where(
            RepositoryGrant.id == grant_id,
            RepositoryGrant.owner_id == chat.owner_id,
            RepositoryGrant.session_id == chat.id,
            RepositoryGrant.active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if not grant or (grant.private and not credential(chat.owner_id, grant.repository)):
        raise ToolError("unauthorized_repository")
    return grant


def dependencies_allowed(db, chat, stored, current=None):
    stored = ToolPolicy.restore(stored)
    if current is not None and (
        (stored.web_enabled and not current.web_enabled)
        or not set(stored.grant_ids).issubset(current.grant_ids)
    ):
        return False
    try:
        for grant_id in stored.grant_ids:
            live_grant(db, chat, grant_id)
    except ToolError:
        return False
    return True


def external_allowed(db, chat, data, policy=None):
    if data.get("kind") == "web":
        return policy is None or policy.web_enabled
    grant_id = data.get("grant_id")
    if policy is not None and grant_id not in policy.grant_ids:
        return False
    try:
        grant = live_grant(db, chat, grant_id)
        return data.get("repository") == grant.repository
    except ToolError:
        return False


def external_citation(source):
    d = source.data
    return {
        "citation_id": source.id,
        "label": d["label"],
        "kind": source.kind,
        "filename": d["title"],
        "document_type": "网页" if source.kind == "web" else "GitHub",
        "snippet": d["text"][:12000],
        "file_url": d["url"],
        **{
            k: d.get(k)
            for k in (
                "url",
                "accessed_at",
                "repository",
                "sha",
                "path",
                "line_start",
                "line_end",
                "truncated",
                "grant_id",
            )
        },
    }


class ExternalTools:
    def __init__(
        self, db, chat, policy, execution, *, search=None, http=None, query_planner=None
    ):
        self.db, self.chat, self.policy, self.execution = db, chat, policy, execution
        self.http = http or PublicHTTP()
        self.search = search or TavilySearch(self.http)
        self.query_planner = query_planner
        self.searched = set()
        self.urls, self.pins, self.results = set(), {}, {}
        if policy.web_enabled:
            # A refresh may re-read an actually fetched historical URL, but never
            # reuse its body as this round's evidence or accept a model-made URL.
            previous = db.execute(
                select(ExternalSource, Message)
                .join(Message, Message.id == ExternalSource.message_id)
                .where(
                    ExternalSource.owner_id == chat.owner_id,
                    ExternalSource.session_id == chat.id,
                    ExternalSource.kind == "web",
                    Message.owner_id == chat.owner_id,
                    Message.session_id == chat.id,
                )
                .order_by(Message.created_at.desc())
                .limit(20)
            ).all()
            self.urls.update(
                source.data["url"]
                for source, message in previous
                if dependencies_allowed(db, chat, message.tool_policy, policy)
            )

    def definitions(self):
        return [
            definition(n, d, a)
            for n, (a, d) in EXTERNAL_TOOLS.items()
            if self.permitted(n)
        ]

    def permitted(self, name):
        if name in {"web_search", "read_web_page"}:
            return self.policy.web_enabled and bool(get_settings().tavily_api_key)
        return name in EXTERNAL_TOOLS and bool(self.policy.grant_ids)

    @measured("external_tool")
    def invoke(self, name, raw):
        if not self.permitted(name):
            raise ToolError("tool_not_authorized")
        args = EXTERNAL_TOOLS[name][0].model_validate_json(raw)
        if name == "web_search":
            if self.policy.mode == "auto":
                if self.query_planner is None:
                    raise ToolError("public_query_needed")
                query = self.query_planner()
            elif args.query not in self.policy.web_queries:
                raise ToolError("query_not_authorized")
            else:
                query = args.query
            if query.casefold() in self.searched:
                raise ToolError("duplicate_query")
            self.searched.add(query.casefold())
            results = self.search.search(query, self.execution)
            self.urls.update(item["url"] for item in results)
            return {
                "status": "ok" if results else "empty",
                "query": query,
                "results": results,
            }, None
        if name == "read_web_page":
            url = public_url(args.url)
            if url not in self.urls:
                raise ToolError("url_not_discovered")
            data = read_page(url, self.execution, self.http)
            self.urls.update(data.get("links", []))
        else:
            if args.grant_id not in self.policy.grant_ids:
                raise ToolError("unauthorized_repository")
            grant = live_grant(self.db, self.chat, args.grant_id)
            reader = GitHubReader(self.chat.owner_id, grant.repository, self.http)
            if grant.id not in self.pins:
                commit = reader.commit(grant.ref, self.execution)
                self.pins[grant.id] = commit
            commit = self.pins[grant.id]
            sha = commit["sha"]
            if name == "github_read_file":
                if (
                    args.end_line < args.start_line
                    or args.end_line - args.start_line >= 160
                ):
                    raise ToolError("invalid_lines")
                data = reader.read(
                    sha, args.path, args.start_line, args.end_line, self.execution
                )
            else:
                if name == "github_list_directory":
                    entry = reader.entry(sha, args.path, self.execution)
                    if entry["type"] != "tree":
                        raise ToolError("not_directory")
                    entries = reader.tree(entry["sha"], self.execution)
                    selected = entries[args.offset : args.offset + 100]
                    text = json.dumps(
                        [
                            {"path": e["path"], "type": e["type"], "mode": e["mode"]}
                            for e in selected
                        ],
                        ensure_ascii=False,
                    )
                    next_offset = (
                        args.offset + 100 if len(entries) > args.offset + 100 else None
                    )
                    suffix = f"/tree/{sha}/{quote(args.path, safe='/')}"
                else:
                    text = json.dumps(
                        {
                            "sha": sha,
                            "message": commit["commit"]["message"][:4000],
                            "author_date": commit["commit"]["author"]["date"],
                        }
                    )
                    next_offset, suffix = None, "/commit/" + sha
                data = {
                    "kind": "github",
                    "title": f"{grant.repository} {name}",
                    "repository": grant.repository,
                    "sha": sha,
                    "path": args.path,
                    "url": "https://github.com/" + grant.repository + suffix,
                    "accessed_at": stamp(),
                    "text": text,
                    "read_status": "read",
                    "next_offset": next_offset,
                    "subtype": name,
                }
            data["grant_id"] = grant.id
            # Revocation during network work must reject the obtained result.
            live_grant(self.db, self.chat, grant.id)
        self.execution.check()
        semantic = {k: v for k, v in data.items() if k not in {"accessed_at", "label"}}
        for existing_id, existing in self.results.items():
            if semantic == {
                k: v for k, v in existing.items() if k not in {"accessed_at", "label"}
            }:
                return {"status": "ok", "source": existing}, existing_id
        key = "ext:" + str(uuid4())
        self.results[key] = data
        return {"status": "ok", "source": data}, key


def progress_key(result):
    """Successful discovery/metadata/body changes count, errors never count."""
    if result.get("status") != "ok":
        return None
    result = dict(result)
    result.pop("query", None)  # Rewording the same discovery is not new evidence.
    if "source" in result:
        result["source"] = {
            k: v
            for k, v in result["source"].items()
            if k not in {"accessed_at", "label"}
        }
    return hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def stored_source(db, chat, source_id):
    return db.scalar(
        select(ExternalSource).where(
            ExternalSource.id == source_id,
            ExternalSource.owner_id == chat.owner_id,
            ExternalSource.session_id == chat.id,
        )
    )
