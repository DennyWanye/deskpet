from __future__ import annotations
import tomli
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class BackendConfig:
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "INFO"

@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 2048

@dataclass
class ASRConfig:
    provider: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"

@dataclass
class TTSConfig:
    provider: str = "edge-tts"
    voice: str = "zh-CN-XiaoyiNeural"
    model_dir: str = "./assets/cosyvoice2"

@dataclass
class VADConfig:
    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 500

@dataclass
class MemoryConfig:
    db_path: str = "./data/memory.db"
    embedding_model: str = "bge-m3"

@dataclass
class AppConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

def load_config(path: str | Path = "config.toml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    with open(path, "rb") as f:
        raw = tomli.load(f)
    config = AppConfig()
    if "backend" in raw:
        config.backend = BackendConfig(**raw["backend"])
    if "llm" in raw:
        config.llm = LLMConfig(**raw["llm"])
    if "asr" in raw:
        config.asr = ASRConfig(**raw["asr"])
    if "tts" in raw:
        config.tts = TTSConfig(**raw["tts"])
    if "vad" in raw:
        config.vad = VADConfig(**raw["vad"])
    if "memory" in raw:
        config.memory = MemoryConfig(**raw["memory"])
    return config
