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
python -m unittest discover -s tests -v
```

Before opening a change, also run:

```bash
python scripts/collabctl.py --cwd . verify
```

## Code Quality Standards

- Every function has a docstring.
- Every audit finding gets fixed or explicitly blocks advancement. There are no "nice to have" failures in the mission path.
- Tests come before advancement. If a phase cannot be verified, it does not pass.
- Python stays 100% stdlib. Do not add pip dependencies.
- Docs and metadata must describe the behavior that ships, not the behavior a future wave might add.
