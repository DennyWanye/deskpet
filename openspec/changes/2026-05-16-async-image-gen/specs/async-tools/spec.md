# Spec: async-tools (NEW capability)

## ADDED Requirements

### Requirement: Long-running tools return immediately and deliver results out-of-band

A long-running tool (first adopter: `generate_image`) SHALL submit its slow work to an in-process async worker and return within ~1s, so the agent turn completes without blocking. The worker SHALL deliver the result back to the originating session's pet as an unsolicited message when the work finishes, with no further user turn required.

#### Scenario: generate_image returns immediately, work runs in background

- **GIVEN** `config.toml [image].async_enabled = true`
- **AND** the agent calls `generate_image` with a valid prompt
- **WHEN** the tool handler runs
- **THEN** it SHALL return within ~1s with `{ok:true, status:"generating", job_id:<id>, message:<"在画了，稍等"-style>}`
- **AND** it SHALL NOT perform the the relay HTTP request inside the tool handler
- **AND** the agent loop turn SHALL complete without waiting for image generation

#### Scenario: completion is pushed to the pet without a user turn

- **GIVEN** a submitted image job for session "default"
- **WHEN** the worker finishes generating + saving + opening the image
- **THEN** the worker SHALL emit a `chat_v2_final` ws event to that session's control connection with a success message naming the saved file + model + size
- **AND** the pet bubble SHALL render it via the existing chat_v2_final handler (no frontend change, `petText` cleaning applies)
- **AND** the completion message SHALL also be appended to SessionDB messages as role=assistant

#### Scenario: failure is delivered gracefully (no fabrication)

- **GIVEN** a submitted image job
- **AND** the relay returns a disconnect/4xx after the worker's retries are exhausted
- **WHEN** the worker finishes with an error
- **THEN** it SHALL emit a `chat_v2_final` event with an honest graceful error (the relay 抽风/参数/额度) — never a fabricated success
- **AND** it SHALL NOT crash the worker loop (next jobs still process)

#### Scenario: same in-flight prompt is deduplicated

- **GIVEN** an image job for (session "default", prompt P, size S) is already in flight
- **WHEN** `generate_image` is called again with the same (session, P, S)
- **THEN** no second job SHALL be enqueued
- **AND** the tool SHALL return `{ok:true, status:"already_generating", message:<"同样的图正在画"-style>}`

#### Scenario: bounded concurrency

- **GIVEN** `max_concurrent = 2`
- **AND** 3 distinct image jobs are submitted in quick succession
- **WHEN** the worker processes them
- **THEN** at most 2 SHALL run concurrently; the 3rd SHALL wait for a slot

#### Scenario: worker lifecycle is clean

- **GIVEN** the worker is running
- **WHEN** backend shutdown calls `worker.stop()`
- **THEN** in-flight jobs SHALL be cancelled and the loop task awaited
- **AND** `start()`/`stop()` SHALL be idempotent (double-call safe)

#### Scenario: async_enabled=false restores synchronous behavior

- **GIVEN** `config.toml [image].async_enabled = false`
- **WHEN** `generate_image` is called
- **THEN** it SHALL run the legacy synchronous blocking implementation (HTTP + retry + save + open in-handler, returning the final {ok,path,opened})
- **AND** the worker SHALL NOT be started (Strangler-Fig rollback)
