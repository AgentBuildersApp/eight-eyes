---
name: 8eyes-copilot
description: Start a multi-agent review mission (Copilot CLI adapter notes)
---

# /8eyes for Copilot CLI

Future adapter note for `adapters/copilot_cli/plugin.json`:

```json
"commands": [{"name": "8eyes", "description": "Start multi-agent review"}]
```

When the Copilot CLI adapter is added, `/8eyes <objective>` should initialize a collab mission through `scripts/collabctl.py`, present the mission plan for approval, and then follow the `skills/collab/SKILL.md` phase workflow.
