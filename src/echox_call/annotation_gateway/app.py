"""Reverse proxy that exposes only the emotion annotation console."""

from __future__ import annotations

import logging
import os
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse


DEFAULT_PREFIX = "/landa/web/biaozhu"
DEFAULT_TARGET_URL = "http://console:8001"
PROXY_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

logger = logging.getLogger(__name__)


def create_app(
    *,
    prefix: str | None = None,
    target_url: str | None = None,
    timeout_seconds: float | None = None,
) -> FastAPI:
    gateway_prefix = _normalize_prefix(
        prefix if prefix is not None else os.getenv("ANNOTATION_GATEWAY_PREFIX", DEFAULT_PREFIX)
    )
    gateway_target = (
        target_url
        if target_url is not None
        else os.getenv("ANNOTATION_GATEWAY_TARGET_URL", DEFAULT_TARGET_URL)
    ).rstrip("/")
    gateway_timeout = timeout_seconds or float(
        os.getenv("ANNOTATION_GATEWAY_TIMEOUT_SECONDS", "120")
    )

    app = FastAPI(
        title="Emotion Annotation Gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def proxy_request(request: Request, suffix: str) -> Response:
        if _has_unsafe_path_segment(suffix):
            logger.warning(
                "annotation gateway rejected unsafe path method=%s path=%s",
                request.method,
                request.url.path,
            )
            return PlainTextResponse("请求路径无效。", status_code=404)

        target_path = _target_path_for_suffix(suffix)
        target = f"{gateway_target}{target_path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"

        body = await request.body()
        if request.method == "POST" and target_path == "/console/login":
            body = _rewrite_login_body(body, gateway_prefix)

        headers = _forward_request_headers(request, gateway_prefix)
        client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(gateway_timeout),
        )
        try:
            upstream_request = client.build_request(
                request.method,
                target,
                headers=headers,
                content=body,
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning(
                "annotation gateway upstream failed method=%s path=%s target=%s error=%s",
                request.method,
                request.url.path,
                target,
                exc,
            )
            return PlainTextResponse("标注服务暂时不可用，请稍后重试。", status_code=502)

        content_type = upstream.headers.get("content-type", "")
        if "text/html" in content_type.lower():
            try:
                content = await upstream.aread()
            finally:
                await upstream.aclose()
                await client.aclose()
            encoding = upstream.encoding or "utf-8"
            html = content.decode(encoding, errors="replace")
            content = _rewrite_html(html, gateway_prefix).encode("utf-8")
            response: Response = Response(content=content, status_code=upstream.status_code)
            _copy_upstream_headers(
                response,
                upstream,
                gateway_prefix,
                gateway_target,
                decoded_body=True,
            )
        else:
            async def stream_upstream():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            response = StreamingResponse(stream_upstream(), status_code=upstream.status_code)
            _copy_upstream_headers(
                response,
                upstream,
                gateway_prefix,
                gateway_target,
                decoded_body=False,
            )

        logger.info(
            "annotation gateway forwarded method=%s path=%s targetPath=%s statusCode=%s",
            request.method,
            request.url.path,
            target_path,
            upstream.status_code,
        )
        return response

    @app.get("/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "code": 0,
            "message": "success",
            "data": {
                "status": "ok",
                "service": "annotation-gateway",
                "prefix": gateway_prefix,
            },
        }

    @app.api_route(gateway_prefix, methods=PROXY_METHODS, include_in_schema=False)
    async def prefixed_root(request: Request) -> Response:
        return await proxy_request(request, "")

    @app.api_route(
        f"{gateway_prefix}/{{proxy_path:path}}",
        methods=PROXY_METHODS,
        include_in_schema=False,
    )
    async def prefixed_path(request: Request, proxy_path: str) -> Response:
        return await proxy_request(request, f"/{proxy_path}")

    # Some upstream reverse proxies strip the configured prefix before forwarding.
    @app.api_route("/", methods=PROXY_METHODS, include_in_schema=False)
    async def stripped_root(request: Request) -> Response:
        return await proxy_request(request, "")

    @app.api_route("/{proxy_path:path}", methods=PROXY_METHODS, include_in_schema=False)
    async def stripped_path(request: Request, proxy_path: str) -> Response:
        return await proxy_request(request, f"/{proxy_path}")

    return app


def _normalize_prefix(value: str) -> str:
    normalized = f"/{value.strip().strip('/')}"
    if normalized == "/":
        raise ValueError("annotation gateway prefix must not be root")
    return normalized


def _target_path_for_suffix(suffix: str) -> str:
    normalized = "/" + suffix.lstrip("/") if suffix else ""
    if normalized in {"", "/"}:
        return "/console/annotations"
    if normalized == "/login":
        return "/console/login"
    if normalized == "/logout":
        return "/console/logout"
    if normalized == "/static" or normalized.startswith("/static/"):
        return f"/console{normalized}"
    return f"/console/annotations{normalized}"


def _has_unsafe_path_segment(suffix: str) -> bool:
    decoded = suffix
    for _ in range(2):
        decoded = unquote(decoded)
    return any(segment in {".", ".."} for segment in decoded.replace("\\", "/").split("/"))


def _forward_request_headers(request: Request, prefix: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"host", "content-length"}
    }
    headers["x-forwarded-prefix"] = prefix
    headers["x-forwarded-proto"] = request.url.scheme
    if request.headers.get("host"):
        headers["x-forwarded-host"] = request.headers["host"]
    if request.client is not None:
        existing = request.headers.get("x-forwarded-for")
        headers["x-forwarded-for"] = (
            f"{existing}, {request.client.host}" if existing else request.client.host
        )
    return headers


def _copy_upstream_headers(
    response: Response,
    upstream: httpx.Response,
    prefix: str,
    target_url: str,
    *,
    decoded_body: bool,
) -> None:
    for name, value in upstream.headers.multi_items():
        lower_name = name.lower()
        if lower_name in HOP_BY_HOP_HEADERS:
            continue
        if decoded_body and lower_name in {"content-encoding", "content-length"}:
            continue
        if lower_name == "location":
            value = _rewrite_location(value, prefix, target_url)
        response.headers.append(name, value)


def _rewrite_html(html: str, prefix: str) -> str:
    replacements = (
        ("/console/annotations", prefix),
        ("/console/static", f"{prefix}/static"),
        ("/console/login", f"{prefix}/login"),
        ("/console/logout", f"{prefix}/logout"),
    )
    for source, destination in replacements:
        html = html.replace(source, destination)

    annotation_only_style = (
        "<style>.sidebar-nav .nav-item:not(.is-active){display:none}</style>"
    )
    if "</head>" in html and annotation_only_style not in html:
        html = html.replace("</head>", f"{annotation_only_style}</head>", 1)
    return html


def _rewrite_location(location: str, prefix: str, target_url: str) -> str:
    if location.startswith(target_url):
        location = location[len(target_url) :] or "/"

    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        return location

    path = parsed.path
    if path.startswith("/console/annotations"):
        path = f"{prefix}{path[len('/console/annotations') :]}"
    elif path == "/console/login":
        path = f"{prefix}/login"
    elif path == "/console/logout":
        path = f"{prefix}/logout"
    elif path.startswith("/console/static"):
        path = f"{prefix}/static{path[len('/console/static') :]}"
    elif path.startswith("/console"):
        path = prefix

    return urlunsplit(("", "", path, parsed.query, parsed.fragment))


def _rewrite_login_body(body: bytes, prefix: str) -> bytes:
    if not body:
        return body
    try:
        fields = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return body

    rewritten: list[tuple[str, str]] = []
    for name, value in fields:
        if name == "next":
            value = _external_annotation_path_to_internal(value, prefix)
        rewritten.append((name, value))
    return urlencode(rewritten).encode("utf-8")


def _external_annotation_path_to_internal(value: str, prefix: str) -> str:
    if value == prefix or value == f"{prefix}/":
        return "/console/annotations"
    if value.startswith(f"{prefix}/"):
        return f"/console/annotations/{value[len(prefix) + 1 :]}"
    return value


app = create_app()
