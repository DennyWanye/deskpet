---
description: "进入 brainstorming 模式 — sp-brainstorming skill 包装"
allowed-tools: ["Skill"]
---

User just invoked `/sp:brainstorm` with arguments: $ARGUMENTS

Your job: invoke the `sp-brainstorming` skill via the Skill tool. If
`$ARGUMENTS` is non-empty, treat it as the initial idea/topic to
brainstorm and feed into the skill's first step. If empty, let the skill
itself prompt the user.

Do NOT do the brainstorming yourself — let the skill drive (it has its
own hard gates: explore project → clarifying questions one at a time →
2-3 approaches → design sections with user approval → write spec doc).
