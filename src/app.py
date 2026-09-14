"""The AgentCore Runtime entrypoint: one POST /invocations, one GET /ping.

The runtime hands us the request body as `payload` and the session as `context`.
Everything else lives in `benefitline.service`, which knows nothing about AgentCore,
so the evals call the same code path the deployed runtime does.

Local: `python src/app.py`, then POST to http://127.0.0.1:8080/invocations.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:            # the container starts this file directly
    sys.path.insert(0, str(SRC))

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

from benefitline import service  # noqa: E402

# Local runs read AWS keys and model ids from .env; in the container there is no .env
# and the execution role plus the runtime environment variables supply both.
_ENV = SRC.parent / ".env"
if _ENV.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV)
    except ImportError:
        pass

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    """One action per request. Never raises: the browser gets `ok: false` and a reason."""
    try:
        body = payload if isinstance(payload, dict) else {}
        session_id = getattr(context, "session_id", None)
        if isinstance(session_id, str) and len(session_id) >= service.MIN_SESSION_ID:
            body = dict(body, session_id=session_id)
        return service.handle(body)
    except Exception as exc:  # noqa: BLE001 - a runtime 500 tells the judge nothing
        return {"ok": False, "case": None, "board": None, "gallery": None,
                "error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    app.run()
