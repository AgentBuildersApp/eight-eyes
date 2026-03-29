---
name: 8eyes
description: Start a failure-aware multi-agent code review mission
---

Start an eight-eyes mission. Parse the user's objective and run:

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py init --objective "<objective>" --allowed-path src --criterion "<inferred criteria>"`
2. Present the plan to the user for approval
3. Follow the phase workflow in `skills/collab/SKILL.md`

Usage: `/8eyes <objective>`
Example: `/8eyes Refactor auth to use JWT tokens`
