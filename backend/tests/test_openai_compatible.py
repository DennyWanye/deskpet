"""Unit tests for OpenAICompatibleProvider (P2-1-S1).

Covers:
- Protocol conformance (runtime-checkable LLMProvider)
- chat_stream against a mocked SSE transport (no network)
- health_check against mocked /models endpoints
- Two integration tests (skip if endpoints offline / no api key)
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_implements_protocol():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4:e4b",
    )
    assert isinstance(provider, LLMProvider)
