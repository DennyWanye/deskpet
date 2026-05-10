# voice-pipeline — P4-S21 delta

## ADDED Requirements

### Requirement: VoicePipeline MUST route through AgentLoop when tool stack is wired
The voice pipeline SHALL accept optional `tool_registry_v2`,
`permission_gate`, and `local_llm` constructor params. When all three
are provided, `_handle_user_said` SHALL run the user transcript
through `AgentLoop.run()` (the same tool-use loop used by the text
chat handler) instead of `agent.chat_stream`. This unifies the
behavior between voice and text input — voice utterances can invoke
tools (write files, run shell commands, etc).

#### Scenario: Voice request to create a file triggers tool invocation
- **WHEN** user says "帮我在桌面生成一个 todo.txt 内容是吃饭买菜" via mic
- **THEN** ASR produces the transcript
- **AND** AgentLoop runs and emits a `desktop_create_file` tool call
- **AND** the file appears on the user's desktop after permission is granted

### Requirement: VoicePipeline MUST persist user + assistant messages to SessionDB
When the tool-use path runs, the voice pipeline SHALL write both the
user transcript and the assistant's final response to SessionDB and
enqueue them for vector indexing — matching the text chat handler.
Without this, voice-only conversations are invisible to L2/L3 recall.

#### Scenario: Voice turn ends with both rows in SessionDB
- **WHEN** voice turn completes with response "好啦，已生成笑话.txt"
- **THEN** `messages` table has one new row with `role="user"` (the transcript)
- **AND** one row with `role="assistant"` (the final response)
- **AND** the vector_worker queue contains both message ids

## ADDED Requirements

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
