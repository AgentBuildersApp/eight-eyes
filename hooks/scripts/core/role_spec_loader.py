"""Load role specifications from spec/roles/builtin_roles.yaml into frozen dataclasses.

Uses a compiled-cache strategy identical to the enforcement_contract pattern:
at **compile time** the YAML source is parsed once and written as
``roles_compiled.json``; at **runtime** the module loads the pre-compiled
JSON (fast, stdlib-only) and falls back to YAML parsing only when the
compiled cache is missing or stale.

The public entry point is :func:`load_role_registry` which returns a
:class:`RoleRegistry` instance cached in a module-level global for the
lifetime of the process.

Backward-compatibility invariant
--------------------------------
The frozen sets exposed by :class:`RoleRegistry` helper methods MUST
produce identical values to the hardcoded constants in ``roles.py``:

- ``BUILTIN_ROLE_NAMES == registry.builtin_names()``
- ``WRITE_ROLES       == registry.write_roles()``
- ``READ_ONLY_ROLES   == registry.read_only_roles()``
- ``NO_BASH_ROLES     == registry.no_bash_roles()``
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleSpec:
    """Immutable specification for a single built-in review role."""

    name: str
    purpose: str
    scope_mode: str  # read_only, write_allowed, write_test, write_doc, verify_only
    write_paths_key: Optional[str]  # manifest key for write paths
    bash_policy: str  # denied, read_only, read_only_plus_approved
    approved_commands_key: Optional[str]  # manifest key for approved commands
    blind_from: tuple[str, ...]
    review_md_visibility: bool
    result_schema: str  # path to JSON schema
    result_required_fields: tuple[str, ...]
    result_status_values: tuple[str, ...]
    supports_parallel_phase: bool
    default_enabled: bool
    phase: str
    host_support: Dict[str, str]


@dataclass(frozen=True)
class RoleRegistry:
    """Immutable registry of all built-in role specifications.

    Provides convenience methods that mirror the legacy ``frozenset``
    constants in ``roles.py`` for backward compatibility.
    """

    schema_version: int
    roles: Dict[str, RoleSpec]

    def builtin_names(self) -> frozenset[str]:
        """Return the set of all built-in role names."""
        return frozenset(self.roles.keys())

    def write_roles(self) -> frozenset[str]:
        """Return roles whose ``scope_mode`` starts with ``write``."""
        return frozenset(
            n for n, r in self.roles.items()
            if r.scope_mode.startswith("write")
        )

    def read_only_roles(self) -> frozenset[str]:
        """Return roles with ``read_only`` or ``verify_only`` scope."""
        return frozenset(
            n for n, r in self.roles.items()
            if r.scope_mode == "read_only" or r.scope_mode == "verify_only"
        )

    def no_bash_roles(self) -> frozenset[str]:
        """Return roles whose ``bash_policy`` is ``denied``."""
        return frozenset(
            n for n, r in self.roles.items()
            if r.bash_policy == "denied"
        )

    def roles_for_phase(self, phase: str) -> list[RoleSpec]:
        """Return all roles assigned to *phase*."""
        return [r for r in self.roles.values() if r.phase == phase]


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_CACHED_REGISTRY: Optional[RoleRegistry] = None

# Relative to project root
_YAML_REL = os.path.join("spec", "roles", "builtin_roles.yaml")
_JSON_REL = os.path.join("spec", "roles", "roles_compiled.json")


# ---------------------------------------------------------------------------
# Minimal YAML parser  (compile-time only, stdlib-only)
# ---------------------------------------------------------------------------
# The YAML files we need to parse are simple: mappings, sequences, scalars.
# No anchors, aliases, multi-line blocks with special indicators, or tags.
# This keeps the module dependency-free.

def _yaml_loads(text: str) -> Any:
    """Parse a *simple* YAML document into Python objects.

    Supports: nested mappings, sequences (``- item``), quoted and unquoted
    scalars, ``true``/``false``/``null``, integers.  Does **not** support
    anchors, aliases, multi-line block scalars (``|``, ``>``), flow
    collections on a single line, or tags.

    This is intentionally limited -- it only runs at compile time when the
    full PyYAML library is not available.
    """
    lines = text.splitlines()
    result, _ = _yaml_parse_value(lines, 0, -1)
    return result


def _yaml_scalar(raw: str) -> Any:
    """Convert a raw YAML scalar string to a Python value."""
    stripped = raw.strip()
    if not stripped or stripped == "null" or stripped == "~":
        return None
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    # Quoted strings
    if (stripped.startswith('"') and stripped.endswith('"')) or \
       (stripped.startswith("'") and stripped.endswith("'")):
        return stripped[1:-1]
    # Integer
    try:
        return int(stripped)
    except ValueError:
        pass
    # Float
    try:
        return float(stripped)
    except ValueError:
        pass
    return stripped


def _indent_level(line: str) -> int:
    """Return the number of leading spaces in *line*."""
    return len(line) - len(line.lstrip(" "))


def _is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def _yaml_parse_value(lines: list[str], idx: int, parent_indent: int) -> tuple[Any, int]:
    """Parse a YAML value starting at *idx*.

    Returns ``(parsed_value, next_idx)`` where *next_idx* is the first
    line index NOT consumed by this value.
    """
    # Skip blanks/comments
    while idx < len(lines) and _is_blank_or_comment(lines[idx]):
        idx += 1
    if idx >= len(lines):
        return None, idx

    line = lines[idx]
    indent = _indent_level(line)
    stripped = line.strip()

    # Sequence item?
    if stripped.startswith("- "):
        return _yaml_parse_sequence(lines, idx, indent)

    # Mapping key?
    colon_match = re.match(r'^( *)([^#:]+?):\s*(.*)', line)
    if colon_match:
        return _yaml_parse_mapping(lines, idx, indent)

    # Plain scalar
    return _yaml_scalar(stripped), idx + 1


def _yaml_parse_sequence(lines: list[str], idx: int, base_indent: int) -> tuple[list, int]:
    """Parse a YAML sequence starting at *idx*."""
    result: list[Any] = []
    while idx < len(lines):
        if _is_blank_or_comment(lines[idx]):
            idx += 1
            continue
        indent = _indent_level(lines[idx])
        stripped = lines[idx].strip()
        if indent < base_indent:
            break
        if indent == base_indent and not stripped.startswith("- "):
            break
        if indent == base_indent and stripped.startswith("- "):
            # Value after "- "
            after_dash = stripped[2:]
            if not after_dash or after_dash.startswith("#"):
                # Nested structure under the dash
                result.append(None)
                idx += 1
                # Check if next lines are indented further (nested mapping/sequence)
                if idx < len(lines) and not _is_blank_or_comment(lines[idx]):
                    next_indent = _indent_level(lines[idx])
                    if next_indent > base_indent:
                        val, idx = _yaml_parse_value(lines, idx, base_indent)
                        result[-1] = val
            elif ":" in after_dash and not after_dash.startswith("{"):
                # Inline mapping start: "- key: value"
                # Reconstruct as indented mapping for sub-parser
                sub_indent = base_indent + 2
                reconstructed = [" " * sub_indent + after_dash]
                temp_idx = idx + 1
                while temp_idx < len(lines):
                    if _is_blank_or_comment(lines[temp_idx]):
                        temp_idx += 1
                        continue
                    ni = _indent_level(lines[temp_idx])
                    if ni > base_indent + 1:
                        reconstructed.append(lines[temp_idx])
                        temp_idx += 1
                    else:
                        break
                val, _ = _yaml_parse_mapping(reconstructed, 0, sub_indent)
                result.append(val)
                idx = temp_idx
            else:
                result.append(_yaml_scalar(after_dash))
                idx += 1
        else:
            # Indented continuation -- shouldn't normally reach here
            idx += 1
    return result, idx


def _yaml_parse_mapping(lines: list[str], idx: int, base_indent: int) -> tuple[dict, int]:
    """Parse a YAML mapping starting at *idx*."""
    result: dict[str, Any] = {}
    while idx < len(lines):
        if _is_blank_or_comment(lines[idx]):
            idx += 1
            continue
        indent = _indent_level(lines[idx])
        if indent < base_indent:
            break
        if indent > base_indent:
            # Belongs to a sub-structure of a previous key -- skip
            idx += 1
            continue

        line = lines[idx]
        colon_match = re.match(r'^( *)([^#:]+?):\s*(.*)', line)
        if not colon_match:
            break

        key = colon_match.group(2).strip()
        value_part = colon_match.group(3).strip()
        # Strip trailing comments from value
        if value_part and "#" in value_part:
            # Naive: only strip if # is preceded by space and not inside quotes
            for ci, ch in enumerate(value_part):
                if ch == "#" and ci > 0 and value_part[ci - 1] == " ":
                    # Make sure we're not inside quotes
                    before = value_part[:ci].strip()
                    if not (before.startswith('"') and not before.endswith('"')):
                        value_part = before
                        break

        if value_part:
            result[key] = _yaml_scalar(value_part)
            idx += 1
        else:
            # Value is on subsequent indented lines
            idx += 1
            # Find next non-blank line's indent
            peek = idx
            while peek < len(lines) and _is_blank_or_comment(lines[peek]):
                peek += 1
            if peek < len(lines) and _indent_level(lines[peek]) > base_indent:
                val, idx = _yaml_parse_value(lines, peek, base_indent)
                result[key] = val
            else:
                result[key] = None
    return result, idx


# ---------------------------------------------------------------------------
# Deserialization from compiled JSON
# ---------------------------------------------------------------------------

def _role_spec_from_dict(d: Dict[str, Any]) -> RoleSpec:
    """Construct a :class:`RoleSpec` from a plain dict (JSON-decoded)."""
    return RoleSpec(
        name=str(d.get("name", "")),
        purpose=str(d.get("purpose", "")),
        scope_mode=str(d.get("scope_mode", "read_only")),
        write_paths_key=d.get("write_paths_key"),
        bash_policy=str(d.get("bash_policy", "denied")),
        approved_commands_key=d.get("approved_commands_key"),
        blind_from=tuple(d.get("blind_from") or ()),
        review_md_visibility=bool(d.get("review_md_visibility", True)),
        result_schema=str(d.get("result_schema", "")),
        result_required_fields=tuple(d.get("result_required_fields") or ()),
        result_status_values=tuple(d.get("result_status_values") or ()),
        supports_parallel_phase=bool(d.get("supports_parallel_phase", False)),
        default_enabled=bool(d.get("default_enabled", True)),
        phase=str(d.get("phase", "review")),
        host_support=dict(d.get("host_support") or {}),
    )


def _registry_from_dict(d: Dict[str, Any]) -> RoleRegistry:
    """Construct a :class:`RoleRegistry` from a plain dict (JSON-decoded)."""
    roles_raw = d.get("roles", {})
    roles: Dict[str, RoleSpec] = {}
    for name, spec_dict in roles_raw.items():
        spec_dict["name"] = name  # ensure name is set
        roles[name] = _role_spec_from_dict(spec_dict)
    return RoleRegistry(
        schema_version=int(d.get("schema_version", 1)),
        roles=roles,
    )


def _role_spec_to_dict(spec: RoleSpec) -> Dict[str, Any]:
    """Serialize a :class:`RoleSpec` to a plain dict for JSON output."""
    return {
        "name": spec.name,
        "purpose": spec.purpose,
        "scope_mode": spec.scope_mode,
        "write_paths_key": spec.write_paths_key,
        "bash_policy": spec.bash_policy,
        "approved_commands_key": spec.approved_commands_key,
        "blind_from": list(spec.blind_from),
        "review_md_visibility": spec.review_md_visibility,
        "result_schema": spec.result_schema,
        "result_required_fields": list(spec.result_required_fields),
        "result_status_values": list(spec.result_status_values),
        "supports_parallel_phase": spec.supports_parallel_phase,
        "default_enabled": spec.default_enabled,
        "phase": spec.phase,
        "host_support": dict(spec.host_support),
    }


def _registry_to_dict(registry: RoleRegistry) -> Dict[str, Any]:
    """Serialize a :class:`RoleRegistry` for JSON output."""
    return {
        "schema_version": registry.schema_version,
        "roles": {
            name: _role_spec_to_dict(spec)
            for name, spec in registry.roles.items()
        },
    }


# ---------------------------------------------------------------------------
# Source hash for staleness detection
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> Optional[str]:
    """Return the hex SHA-256 of *path*, or ``None`` if unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, IOError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_role_registry(project_root: Path) -> RoleRegistry:
    """Load the role registry, preferring compiled JSON over YAML source.

    Resolution order:

    1. Return the module-level cache if already loaded.
    2. Load ``spec/roles/roles_compiled.json`` if it exists **and** its
       embedded ``source_sha256`` matches the current YAML source hash.
    3. Fall back to parsing ``spec/roles/builtin_roles.yaml`` directly.
    4. If neither file exists, raise :class:`FileNotFoundError`.

    The loaded registry is cached in a module global so that repeated
    calls within the same process are free.
    """
    global _CACHED_REGISTRY
    if _CACHED_REGISTRY is not None:
        return _CACHED_REGISTRY

    json_path = project_root / _JSON_REL
    yaml_path = project_root / _YAML_REL

    # --- Fast path: compiled JSON ---
    if json_path.is_file():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            # Staleness check: compare source hash
            yaml_hash = _file_sha256(yaml_path)
            compiled_hash = raw.get("source_sha256")
            if yaml_hash is not None and compiled_hash == yaml_hash:
                registry = _registry_from_dict(raw)
                _CACHED_REGISTRY = registry
                return registry
            # Stale cache or missing YAML -- try loading anyway if YAML
            # is gone (user may only ship the compiled file)
            if yaml_hash is None:
                registry = _registry_from_dict(raw)
                _CACHED_REGISTRY = registry
                return registry
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(
                f"[role_spec_loader] warning: compiled cache corrupt "
                f"({exc}), falling back to YAML",
                file=sys.stderr,
            )

    # --- Slow path: parse YAML source ---
    if yaml_path.is_file():
        text = yaml_path.read_text(encoding="utf-8")
        data = _yaml_loads(text)
        if not isinstance(data, dict):
            raise ValueError(
                f"builtin_roles.yaml root must be a mapping, got {type(data).__name__}"
            )
        registry = _registry_from_dict(data)
        _CACHED_REGISTRY = registry
        return registry

    raise FileNotFoundError(
        f"Neither compiled cache ({json_path}) nor YAML source ({yaml_path}) found. "
        f"Run compile_role_registry() first or create the YAML source."
    )


def compile_role_registry(project_root: Path) -> None:
    """Parse ``builtin_roles.yaml`` and write ``roles_compiled.json``.

    The compiled file embeds a ``source_sha256`` of the YAML source for
    staleness detection at load time.  This function is intended to be
    called during build/CI, not at runtime.
    """
    yaml_path = project_root / _YAML_REL
    json_path = project_root / _JSON_REL

    if not yaml_path.is_file():
        raise FileNotFoundError(f"YAML source not found: {yaml_path}")

    text = yaml_path.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    data = _yaml_loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"builtin_roles.yaml root must be a mapping, got {type(data).__name__}"
        )

    registry = _registry_from_dict(data)
    out = _registry_to_dict(registry)
    out["source_sha256"] = source_hash

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(out, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def role_spec(registry: RoleRegistry, role_name: str) -> Optional[RoleSpec]:
    """Look up a single role by name, returning ``None`` if not found."""
    return registry.roles.get(role_name)


def reset_cache() -> None:
    """Clear the module-level registry cache (useful in tests)."""
    global _CACHED_REGISTRY
    _CACHED_REGISTRY = None


__all__ = [
    "RoleSpec",
    "RoleRegistry",
    "compile_role_registry",
    "load_role_registry",
    "reset_cache",
    "role_spec",
]
