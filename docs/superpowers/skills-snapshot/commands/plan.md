---
description: "spec → 实施 plan — sp-writing-plans skill 包装"
allowed-tools: ["Skill", "Read"]
---

User just invoked `/sp:plan` with arguments: $ARGUMENTS

`$ARGUMENTS` should be the path to a spec markdown file (e.g.
`docs/superpowers/specs/2026-05-28-X-design.md`). If empty, ask the
user for the spec path ONCE then proceed.

Invoke the `sp-writing-plans` skill via the Skill tool. Pass the spec
path as initial context so the skill can Read it.
