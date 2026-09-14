"""The single public door: one Lambda Function URL that serves the page and the API.

GET  /                     -> web/index.html, with window.BENEFITLINE_API pointing at
                              this same Function URL, so the page needs no query string.
GET  /gallery/scripts.json -> the demo scripts the page fetches on boot.
POST /                     -> the JSON body forwarded to the AgentCore runtime named by
                              the AGENT_ARN environment variable, answer passed straight
                              back.

No credentials live here: the function's execution role carries the one permission it
needs, `bedrock-agentcore:InvokeAgentRuntime` on that one runtime ARN.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
from botocore.config import Config

HERE = Path(__file__).resolve().parent
PAGE = HERE / "index.html"
SCRIPTS = HERE / "scripts.json"
AGENT_ARN = os.environ.get("AGENT_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
MIN_SESSION_ID = 33

# botocore defaults to a 60 s read timeout, which is inside the runtime's cold start plus
# a Sonnet turn. The Lambda timeout is 120 s (deploy.PROXY_TIMEOUT); this sits under it so
# a slow runtime comes back as JSON from the catch-all instead of a Lambda timeout, and the
# call is never retried, because a retry doubles the wait on an already slow model turn.
_client = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(read_timeout=110, connect_timeout=10, retries={"max_attempts": 1}),
)

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def _reply(status: int, body: str, content_type: str) -> dict:
    return {
        "statusCode": status,
        "headers": dict(CORS, **{"Content-Type": content_type, "Cache-Control": "no-store"}),
        "body": body,
    }


def _error(status: int, message: str) -> dict:
    return _reply(status, json.dumps(
        {"ok": False, "case": None, "board": None, "gallery": None, "error": message},
    ), "application/json")


def _self_url(event: dict) -> str:
    domain = event.get("requestContext", {}).get("domainName", "")
    return f"https://{domain}/" if domain else ""


def _page(event: dict) -> dict:
    if not PAGE.exists():
        return _error(500, "index.html was not bundled into the proxy zip")
    html = PAGE.read_text(encoding="utf-8")
    inject = f'<script>window.BENEFITLINE_API = "{_self_url(event)}";</script>'
    marker = "<head>"
    html = html.replace(marker, marker + inject, 1) if marker in html else inject + html
    return _reply(200, html, "text/html; charset=utf-8")


def _invoke(body: str) -> dict:
    if not AGENT_ARN:
        return _error(500, "AGENT_ARN is not set on the proxy function")
    try:
        payload = json.loads(body or "{}")
    except ValueError as exc:
        return _error(400, f"the request body is not JSON: {exc}")
    if not isinstance(payload, dict):
        return _error(400, "the request body must be a JSON object")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or len(session_id) < MIN_SESSION_ID:
        return _error(400, f"session_id must be at least {MIN_SESSION_ID} characters")

    answer = _client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
        qualifier="DEFAULT",
    )
    stream = answer.get("response")
    raw = stream.read() if hasattr(stream, "read") else stream or b""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return _reply(200, text, "application/json")


def handler(event, context=None):
    http = event.get("requestContext", {}).get("http", {})
    method = (http.get("method") or "GET").upper()
    path = http.get("path") or "/"

    if method == "OPTIONS":
        return _reply(204, "", "text/plain")
    if method == "GET":
        if path.endswith("scripts.json"):
            if not SCRIPTS.exists():
                return _error(404, "scripts.json was not bundled into the proxy zip")
            return _reply(200, SCRIPTS.read_text(encoding="utf-8"), "application/json")
        return _page(event)
    if method != "POST":
        return _error(405, f"{method} is not allowed here")

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")
    try:
        return _invoke(body)
    except Exception as exc:  # noqa: BLE001 - the browser gets the reason, never a 502
        return _error(502, f"{type(exc).__name__}: {exc}")
