import pytest
from context import ServiceContext
from providers.base import LLMProvider


class FakeLLM:
    def __init__(self, name: str = "fake"):
        self.name = name

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        yield "hello"

    async def health_check(self) -> bool:
        return True


def test_service_context_creation():
    ctx = ServiceContext()
    assert ctx.llm_engine is None
    assert ctx.asr_engine is None
    assert ctx.tts_engine is None


def test_service_context_register_and_get():
    ctx = ServiceContext()
    fake_llm = FakeLLM("test")
    ctx.register("llm_engine", fake_llm)
    assert ctx.llm_engine is fake_llm
    assert ctx.llm_engine.name == "test"


def test_service_context_deep_copy_isolation():
    ctx = ServiceContext()
    fake_llm = FakeLLM("original")
    ctx.register("llm_engine", fake_llm)

    ctx_copy = ctx.create_session()
    ctx_copy.llm_engine.name = "modified"

    assert ctx.llm_engine.name == "original"
    assert ctx_copy.llm_engine.name == "modified"


def test_service_context_register_unknown_raises():
    ctx = ServiceContext()
    with pytest.raises(ValueError, match="Unknown service"):
        ctx.register("unknown_engine", object())
