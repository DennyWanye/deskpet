import pytest
from providers.base import LLMProvider
from providers.ollama_llm import OllamaLLM


def test_ollama_llm_implements_protocol():
    provider = OllamaLLM(model="qwen2.5:14b")
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_ollama_llm_health_check_fails_when_offline():
    provider = OllamaLLM(model="nonexistent", base_url="http://localhost:19999")
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_ollama_llm_chat_stream_integration():
    provider = OllamaLLM(model="qwen2.5:14b")
    if not await provider.health_check():
        pytest.skip("Ollama not running or model not available")
    tokens = []
    async for token in provider.chat_stream(
        [{"role": "user", "content": "Say 'hello' and nothing else."}],
        max_tokens=20,
    ):
        tokens.append(token)
    result = "".join(tokens).lower()
    assert "hello" in result
