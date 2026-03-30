# Contributing to eight-eyes

`eight-eyes` stays intentionally small: stdlib-only Python, explicit hook boundaries, and adapter files that are easy to inspect. Keep changes readable and grounded in shipped behavior.

## Adding a Custom Role

1. Decide whether the role is one-off or reusable. A one-off role only needs `--custom-role`; a reusable role should also get a prompt file in the relevant adapter format.
2. If you want a reusable Claude/Copilot prompt, add `agents/collab-<name>.md` and, if needed, `adapters/copilot_cli/agents/collab-<name>.agent.md`. For Codex, add `adapters/codex_cli/agents/collab-<name>.toml`.
3. Choose a scope type: `read_only`, `write_allowed`, `write_test`, or `write_doc`.
4. Add the role to a mission with `--custom-role`. Example:

```bash
python3 scripts/collabctl.py init \
  --objective "Run lint review" \
  --allowed-path src \
  --criterion "Lint review completes" \
  --verify-command "pytest -q" \
  --custom-role "name=linter,scope=read_only,commands=eslint src/"
```

5. Make sure the role emits a valid result block. Custom roles must include `role`, `summary`, and either `status` or `recommendation`.
6. Test the change with the full unit suite and `collabctl verify`.

## Code Organization

Shared hook logic lives in `hooks/scripts/core/`.  The four modules there
handle the bulk of cross-platform enforcement:

- `engine.py` -- manifest loading, phase validation, and lifecycle management
- `roles.py` -- role definitions, scope rules, and permission checks
- `paths.py` -- Git common directory resolution and state path helpers
- `contracts.py` -- result schema validation used by the SubagentStop hook

Platform adapters import from `core/` rather than duplicating logic.

## Writing a Platform Adapter

1. Create `adapters/<platform>/`.
2. Add the platform-native manifest and hook file in the host format.
3. Reuse the shared root `hooks/`, `scripts/`, and `skills/collab/references/` assets instead of copying logic unless the platform format forces it.
4. Add role prompt files in the host format. Use the existing Copilot and Codex adapters as the reference layout.
5. If the platform has a distinct command surface, add a command file under `commands/`. See `commands/8eyes.md` (Claude Code) and `commands/8eyes-copilot.md` (Copilot CLI) as examples.
6. Extend `install.py` so the adapter can be installed, verified, and removed cleanly.
7. Extend `python3 scripts/collabctl.py --cwd . verify` so the new adapter is checked in CI and local validation.
8. Add or update tests that assert the adapter files exist and match the expected format.

## Running Tests

```bash
python3 -m pytest tests/ -q
```

Before opening a change, also run:

```bash
python3 scripts/collabctl.py --cwd . verify
```

## Error Handling in Hooks

All hooks use a default-allow pattern so that a bug in the hook does not brick the session. If your hook raises an exception, the tool call is allowed by default (or denied if `--fail-closed` is set). The exception is logged to the ledger.

When contributing a hook, wrap your enforcement logic in the error handler and add tests for both the success path and the error path.

## Security Considerations

- **Fail-closed mode**: When `manifest.fail_closed` is `true`, the `PreToolUse` hook denies on error instead of allowing. Use this for security-critical missions where a hook failure should halt work rather than permit unscoped access.
- **PostToolUse revert**: Read-only roles have a automatic revert. If a write slips past `PreToolUse`, `PostToolUse` restores tracked files via `git checkout` and removes untracked files. The revert is recorded as a `scope_violation_reverted` ledger event.
- **Close-time scope verification**: `collabctl close` compares the current `git diff` against `allowed_paths` and blocks if out-of-scope files were modified. `--force-close` overrides with a logged reason.

## Code Quality Standards

- Every function has a docstring.
- Every test failure or audit finding must be resolved before the phase can advance.
- Tests come before advancement. If a phase cannot be verified, it does not pass.
- Python stays 100% stdlib. Do not add pip dependencies.
- Docs and metadata must describe the behavior that ships, not planned features.


