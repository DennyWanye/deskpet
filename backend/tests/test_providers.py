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


from providers.faster_whisper_asr import FasterWhisperASR
from providers.base import ASRProvider


def test_faster_whisper_implements_protocol():
    asr = FasterWhisperASR(model="large-v3-turbo")
    assert isinstance(asr, ASRProvider)


from providers.edge_tts_provider import EdgeTTSProvider
from providers.base import TTSProvider


def test_edge_tts_implements_protocol():
    tts = EdgeTTSProvider(voice="zh-CN-XiaoyiNeural")
    assert isinstance(tts, TTSProvider)


from providers.silero_vad import SileroVAD
from providers.base import VADProvider


def test_silero_vad_implements_protocol():
    vad = SileroVAD(threshold=0.5)
    assert isinstance(vad, VADProvider)


def test_silero_vad_process_silence():
    """Silent frames should produce no events (without model loaded, we test the structure)."""
    vad = SileroVAD()
    # We can't test without model loaded, but we verify the interface exists
    assert hasattr(vad, "process_chunk")
    assert hasattr(vad, "reset")
    assert callable(vad.process_chunk)
    assert callable(vad.reset)
