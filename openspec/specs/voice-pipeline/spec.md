# voice-pipeline Specification

## Purpose
TBD - created by archiving change p4-s21-msi-polish-pack. Update Purpose after archive.
## Requirements
### Requirement: VoicePipeline MUST fall back to legacy chat_stream when tool stack is absent
For backwards compatibility (older tests, dev configs) the pipeline
SHALL retain the legacy `agent.chat_stream` code path. When any of
`tool_registry_v2`, `permission_gate`, or `local_llm` is None, the
pipeline routes to `_run_legacy_chat_stream` and behavior matches the
P3-era pipeline exactly.

#### Scenario: Legacy ctor still works
- **WHEN** `VoicePipeline(vad=v, asr=a, agent=ag, tts=t)` is constructed without v2 params
- **THEN** voice utterances stream tokens via `agent.chat_stream` and text → TTS
- **AND** no AgentLoop import or invocation happens

