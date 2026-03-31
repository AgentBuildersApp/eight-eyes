"""Enforcement contract loader for eight-eyes v5.0.

Loads spec/enforcement.yaml into frozen dataclasses with a compiled JSON
cache for performance.  At runtime, always loads from the compiled form
(enforcement_compiled.json) to avoid YAML parsing overhead on every hook
invocation.  Falls back to a minimal YAML subset parser when the compiled
form is missing or stale.

Stdlib-only -- no PyYAML dependency.

Usage:
    from enforcement_contract import load_contract
    contract = load_contract(project_root)
    print(contract.hard_gates())
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
FailureMode = Literal["deny", "block", "warn", "fail_open", "async_fail_open"]
GateClass = Literal["hard_gate", "recovery", "lifecycle", "observability"]
PlatformSupport = Literal["supported", "degraded", "not_available"]

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateClassSemantics:
    """Semantics for a single gate class (hard_gate, recovery, etc.)."""

    name: str
    behavior: str  # "abort_on_failure", "log_and_compensate", "best_effort", "async_only"
    blocks_mission: bool
    requires_response: bool
    description: str = ""


@dataclass(frozen=True)
class HookContract:
    """Contract for a single hook (PreToolUse, SubagentStop, etc.)."""

    name: str
    script: str
    failure_mode: str
    gate_class: str
    description: str
    platforms: Dict[str, str] = field(default_factory=dict)

    def is_hard_gate(self) -> bool:
        return self.gate_class == "hard_gate"

    def supported_on(self, platform: str) -> bool:
        return self.platforms.get(platform) in ("supported", "degraded")

    def degraded_on(self, platform: str) -> bool:
        return self.platforms.get(platform) == "degraded"


@dataclass(frozen=True)
class EnforcementContract:
    """Top-level enforcement contract loaded from spec/enforcement.yaml."""

    schema_version: int
    gate_semantics: Dict[str, GateClassSemantics]
    hooks: Dict[str, HookContract]

    def hard_gates(self) -> Tuple[HookContract, ...]:
        """Return all hooks classified as hard_gate."""
        return tuple(h for h in self.hooks.values() if h.is_hard_gate())

    def hooks_for_platform(self, platform: str) -> Dict[str, HookContract]:
        """Return hooks that are supported or degraded on the given platform."""
        return {n: h for n, h in self.hooks.items() if h.supported_on(platform)}

    def hook_names(self) -> Tuple[str, ...]:
        """Return all hook names in registration order."""
        return tuple(self.hooks.keys())


# ---------------------------------------------------------------------------
# Module-level cache (process-lifetime)
# ---------------------------------------------------------------------------
_cached_contract: Optional[EnforcementContract] = None
_cached_contract_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Minimal YAML subset parser
# ---------------------------------------------------------------------------
# Handles the subset of YAML used in enforcement.yaml:
#   - Nested mappings (key: value, key:\n  nested_key: value)
#   - Folded block scalars (>- continuation lines)
#   - Inline lists ([100, 500])
#   - YAML list items (- value) under a parent key
#   - Scalar types: string, int, bool, null
#
# Does NOT handle: anchors, aliases, flow mappings, complex keys,
# multi-document, tags, or the full YAML spec.


def _coerce_scalar(value: str) -> Union[str, int, bool, None]:
    """Convert a YAML scalar string to a Python value."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null" or value == "~":
        return None
    # Integer (including negative)
    try:
        return int(value)
    except ValueError:
        pass
    # Strip surrounding quotes
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or \
           (value[0] == "'" and value[-1] == "'"):
            return value[1:-1]
    return value


def _parse_inline_list(value: str) -> List[Any]:
    """Parse a YAML inline list like '[100, 500]' or '["a", "b"]'."""
    inner = value[1:-1].strip()
    if not inner:
        return []
    items = []
    for item in inner.split(","):
        item = item.strip()
        if item:
            items.append(_coerce_scalar(item))
    return items


def _parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """Parse the YAML subset used by enforcement.yaml.

    Strategy:
    - Track indentation to build nested dicts.
    - When a key's value is empty or '>-', create a nested container.
    - For '>-', collect continuation lines into a single string.
    - Lines starting with '- ' under a mapping key become list items.
    - Inline lists '[...]' are parsed directly.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    root: Dict[str, Any] = {}
    # Stack entries: (indent_level, container_dict, key_for_block_scalar_or_None)
    stack: List[Tuple[int, Dict[str, Any], Optional[str]]] = [(-1, root, None)]

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        stripped = raw_line.lstrip()

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            # But if we're collecting a block scalar, a blank line ends it
            if stack[-1][2] is not None:
                # End block scalar collection
                stack[-1] = (stack[-1][0], stack[-1][1], None)
            i += 1
            continue

        indent = len(raw_line) - len(stripped)

        # --- Block scalar continuation ---
        # If we're collecting a >- block scalar, check if this line is a
        # continuation (indented further than the key that started the block).
        if stack[-1][2] is not None:
            block_key = stack[-1][2]
            block_indent = stack[-1][0]
            if indent > block_indent and ":" not in stripped.split("#")[0]:
                # Continuation line for the block scalar
                parent = stack[-1][1]
                existing = parent.get(block_key, "")
                if existing:
                    existing += " "
                parent[block_key] = existing + stripped
                i += 1
                continue
            else:
                # Block scalar ended, stop collecting
                stack[-1] = (stack[-1][0], stack[-1][1], None)
                # Fall through to process this line normally

        # --- Pop stack to correct nesting level ---
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]

        # --- List item (- value) ---
        if stripped.startswith("- "):
            item_value = stripped[2:].strip()
            # Find the parent key: the last key that was set to a list or
            # the current container. We detect this by checking if there's
            # a "pending list key" -- the key whose value is a dict that
            # should have been a list. In our YAML, list items under
            # 'failure_modes:' should produce a list.
            # Heuristic: find the most recent key in current whose value
            # is an empty dict or already a list.
            list_parent = None
            list_key = None
            for k in reversed(list(current.keys())):
                v = current[k]
                if isinstance(v, list):
                    list_parent = current
                    list_key = k
                    break
                if isinstance(v, dict) and len(v) == 0:
                    # Convert empty dict to list
                    current[k] = []
                    list_parent = current
                    list_key = k
                    break

            if list_parent is not None and list_key is not None:
                # Parse the item; handle "- key  # comment" by stripping comments
                comment_idx = item_value.find("  #")
                if comment_idx >= 0:
                    item_value = item_value[:comment_idx].strip()
                list_parent[list_key].append(_coerce_scalar(item_value))
            i += 1
            continue

        # --- Key: Value pair ---
        if ":" in stripped:
            # Handle comments at end of line (but not inside quoted values)
            work = stripped
            # Only strip trailing comments that have at least 2 spaces before #
            comment_pos = work.find("  #")
            if comment_pos >= 0:
                work = work[:comment_pos].strip()

            key, _, value = work.partition(":")
            key = key.strip()
            value = value.strip()

            if not value or value == ">-":
                # Nested dict or block scalar
                if value == ">-":
                    # Start collecting folded block scalar
                    current[key] = ""
                    stack.append((indent, current, key))
                else:
                    # Nested mapping
                    new_dict: Dict[str, Any] = {}
                    current[key] = new_dict
                    stack.append((indent, new_dict, None))
            elif value.startswith("[") and value.endswith("]"):
                # Inline list
                current[key] = _parse_inline_list(value)
            else:
                current[key] = _coerce_scalar(value)
        else:
            # Bare continuation line (part of a block scalar that we missed,
            # or a line without a colon). Check if we can attach to a block.
            if stack[-1][2] is not None:
                block_key = stack[-1][2]
                parent = stack[-1][1]
                existing = parent.get(block_key, "")
                if existing:
                    existing += " "
                parent[block_key] = existing + stripped

        i += 1

    return root


# ---------------------------------------------------------------------------
# Contract builder (from parsed dict)
# ---------------------------------------------------------------------------


def _build_contract(data: Dict[str, Any]) -> EnforcementContract:
    """Build an EnforcementContract from a parsed dict (JSON or YAML origin)."""
    gate_semantics: Dict[str, GateClassSemantics] = {}
    for name, sem in (data.get("gate_class_semantics") or {}).items():
        if not isinstance(sem, dict):
            continue
        gate_semantics[name] = GateClassSemantics(
            name=name,
            behavior=str(sem.get("behavior", "")),
            blocks_mission=bool(sem.get("blocks_mission", False)),
            requires_response=bool(sem.get("requires_response", False)),
            description=str(sem.get("description", "")),
        )

    hooks: Dict[str, HookContract] = {}
    for name, hook_data in (data.get("hooks") or {}).items():
        if not isinstance(hook_data, dict):
            continue
        # Extract platforms, handling both flat dict and nested forms
        raw_platforms = hook_data.get("platforms") or {}
        platforms: Dict[str, str] = {}
        if isinstance(raw_platforms, dict):
            for pname, pval in raw_platforms.items():
                platforms[pname] = str(pval) if pval is not None else "not_available"

        hooks[name] = HookContract(
            name=name,
            script=str(hook_data.get("script", "")),
            failure_mode=str(hook_data.get("failure_mode", "fail_open")),
            gate_class=str(hook_data.get("gate_class", "lifecycle")),
            description=str(hook_data.get("description", "")),
            platforms=platforms,
        )

    return EnforcementContract(
        schema_version=int(data.get("schema_version", 1)),
        gate_semantics=gate_semantics,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_contract(project_root: Path) -> EnforcementContract:
    """Load the enforcement contract, preferring the compiled JSON cache.

    Search order:
    1. spec/enforcement_compiled.json (fast, <1ms)
    2. spec/enforcement.yaml (slow, parsed with minimal YAML parser)

    The result is cached in module globals for the lifetime of the process.
    Staleness check: if enforcement.yaml is newer than the compiled JSON,
    falls back to parsing YAML (and the caller should recompile).
    """
    global _cached_contract, _cached_contract_path

    compiled_path = project_root / "spec" / "enforcement_compiled.json"
    yaml_path = project_root / "spec" / "enforcement.yaml"

    # Determine which source to use
    use_compiled = False
    if compiled_path.exists():
        if yaml_path.exists():
            # Staleness check: YAML newer than compiled => skip compiled
            try:
                yaml_mtime = os.path.getmtime(str(yaml_path))
                compiled_mtime = os.path.getmtime(str(compiled_path))
                use_compiled = compiled_mtime >= yaml_mtime
            except OSError:
                use_compiled = True
        else:
            use_compiled = True

    source_path = str(compiled_path) if use_compiled else str(yaml_path)

    # Return cached result if same source
    if _cached_contract is not None and _cached_contract_path == source_path:
        return _cached_contract

    data: Dict[str, Any] = {}
    if use_compiled:
        try:
            with compiled_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Compiled file is corrupt, fall back to YAML
            if yaml_path.exists():
                data = _parse_simple_yaml(yaml_path)
                source_path = str(yaml_path)
    elif yaml_path.exists():
        data = _parse_simple_yaml(yaml_path)
    else:
        # No spec files found -- return empty contract
        empty = EnforcementContract(schema_version=0, gate_semantics={}, hooks={})
        _cached_contract = empty
        _cached_contract_path = ""
        return empty

    contract = _build_contract(data)
    _cached_contract = contract
    _cached_contract_path = source_path
    return contract


def compile_contract(project_root: Path) -> None:
    """Compile enforcement.yaml to enforcement_compiled.json for fast runtime loading.

    This should be called at install time or by a build script, not on every
    hook invocation.  The compiled JSON is a faithful dict representation of
    the YAML that ``_build_contract`` can consume directly.
    """
    yaml_path = project_root / "spec" / "enforcement.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"enforcement.yaml not found at {yaml_path}")

    data = _parse_simple_yaml(yaml_path)

    # Validate that the parsed data has the expected top-level keys
    if "hooks" not in data or "gate_class_semantics" not in data:
        raise ValueError(
            "Parsed enforcement.yaml is missing required top-level keys "
            "(hooks, gate_class_semantics). Parsed keys: "
            + ", ".join(sorted(data.keys()))
        )

    compiled_path = project_root / "spec" / "enforcement_compiled.json"
    compiled_path.parent.mkdir(parents=True, exist_ok=True)

    with compiled_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")


def format_capabilities_text(contract: EnforcementContract) -> str:
    """Format the enforcement contract as human-readable capabilities text."""
    lines = [
        "eight-eyes Enforcement Contract (v{})".format(contract.schema_version),
        "=" * 50,
        "",
        "Hook Enforcement Model:",
        "{:<18} {:<14} {:<16} {:<9} {:<9} {}".format(
            "Hook", "Gate Class", "Failure Mode", "Claude", "Copilot", "Codex"
        ),
        "-" * 85,
    ]
    for name, hook in contract.hooks.items():
        lines.append(
            "{:<18} {:<14} {:<16} {:<9} {:<9} {}".format(
                name,
                hook.gate_class,
                hook.failure_mode,
                hook.platforms.get("claude_code", "?"),
                hook.platforms.get("copilot_cli", "?"),
                hook.platforms.get("codex_cli", "?"),
            )
        )

    lines.extend(["", "Gate Class Semantics:"])
    for name, sem in contract.gate_semantics.items():
        lines.append(
            "  {}: {} (blocks_mission={})".format(name, sem.behavior, sem.blocks_mission)
        )

    lines.extend(["", "Hard Gates:"])
    for gate in contract.hard_gates():
        lines.append("  - {} ({})".format(gate.name, gate.failure_mode))

    return "\n".join(lines)


def format_capabilities_json(contract: EnforcementContract) -> Dict[str, Any]:
    """Format the enforcement contract as machine-readable JSON dict."""
    return {
        "schema_version": contract.schema_version,
        "gate_class_semantics": {
            name: {
                "behavior": sem.behavior,
                "blocks_mission": sem.blocks_mission,
                "requires_response": sem.requires_response,
                "description": sem.description,
            }
            for name, sem in contract.gate_semantics.items()
        },
        "hooks": {
            name: {
                "script": hook.script,
                "failure_mode": hook.failure_mode,
                "gate_class": hook.gate_class,
                "description": hook.description,
                "platforms": dict(hook.platforms),
                "is_hard_gate": hook.is_hard_gate(),
            }
            for name, hook in contract.hooks.items()
        },
    }


def invalidate_cache() -> None:
    """Clear the module-level cache.  Useful for testing."""
    global _cached_contract, _cached_contract_path
    _cached_contract = None
    _cached_contract_path = None


__all__ = [
    "EnforcementContract",
    "FailureMode",
    "GateClass",
    "GateClassSemantics",
    "HookContract",
    "PlatformSupport",
    "compile_contract",
    "format_capabilities_json",
    "format_capabilities_text",
    "invalidate_cache",
    "load_contract",
]
