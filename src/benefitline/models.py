"""Model factories. Nothing here holds state; every call returns a fresh model object.

Region is passed explicitly on every model: Bedrock resolves region as
explicit `region_name` > boto3 session > `AWS_REGION` > `us-west-2`, and a configured
AWS profile beats `AWS_REGION`. Only `us-east-1` has every AgentCore component.
Model ids come from `.env` (`MODEL_PRIMARY`, `MODEL_FALLBACK`); no key is ever printed.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from strands.models import BedrockModel, ModelRouter

REGION = "us-east-1"
_DEFAULT_PRIMARY = "us.anthropic.claude-sonnet-4-6"
# The same Sonnet 4.6 through the other inference profile. Bedrock meters the daily token
# quota per profile, and one profile hit "Too many tokens per day" twice on 2026-09-14
# while the other answered; same model, same bar, so it is tried before Haiku.
_DEFAULT_PRIMARY_ALT = "global.anthropic.claude-sonnet-4-6"
_DEFAULT_FALLBACK = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_loaded = False


def _env() -> None:
    """Load `.env` from the project root once per process."""
    global _loaded
    if _loaded:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", ".."))
    load_dotenv(os.path.join(root, ".env"))
    _loaded = True


def primary_id() -> str:
    _env()
    return os.environ.get("MODEL_PRIMARY", _DEFAULT_PRIMARY)


def primary_alt_id() -> str:
    _env()
    return os.environ.get("MODEL_PRIMARY_ALT", _DEFAULT_PRIMARY_ALT)


def fallback_id() -> str:
    _env()
    return os.environ.get("MODEL_FALLBACK", _DEFAULT_FALLBACK)


def primary(**kw) -> BedrockModel:
    """Sonnet 4.6. The reasoning tier."""
    return BedrockModel(model_id=primary_id(), region_name=REGION, **kw)


def fallback(**kw) -> BedrockModel:
    """Haiku 4.5. Same bar, cheaper; used when the primary cannot serve."""
    return BedrockModel(model_id=fallback_id(), region_name=REGION, **kw)


def primary_alt(**kw) -> BedrockModel:
    """Sonnet 4.6 on the other inference profile: the same bar when one profile's daily quota is out."""
    return BedrockModel(model_id=primary_alt_id(), region_name=REGION, **kw)


def router(**kw) -> ModelRouter:
    """Sonnet first, Sonnet on the other profile next, Haiku last. `max_switches=2` lets one
    invocation walk the whole list; the Haiku step is still a single downgrade."""
    return ModelRouter(models=[primary(**kw), primary_alt(**kw), fallback(**kw)], max_switches=2)


def cheap() -> BedrockModel:
    """Haiku at temperature 0 with a 600-token ceiling: the tier-1 short-message reader.

    A tier-1 turn returns a full IntakeTurn (facts plus a one-line reply); 64 tokens could not hold
    it and every tier-1 turn escalated to Sonnet (probe in _runs/2026-09-13_phase2_intake/probe_tier1.py).
    """
    return BedrockModel(model_id=fallback_id(), region_name=REGION, temperature=0, max_tokens=600)
