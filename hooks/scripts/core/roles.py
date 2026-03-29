"""Role validation and subagent context building for the collab-plugin.

Provides result schema validation for all 8 built-in roles plus custom roles,
and builds role-specific context strings injected into subagents at startup.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, Iterable, List, Optional

from .engine import (
    COLLAB_PREFIX,
    MissionContext,
    RESULT_BEGIN,
    RESULT_END,
    changed_paths_from_summary,
    load_role_result,
    recent_progress,
    spec_hash,
)

BUILTIN_ROLE_NAMES = frozenset({
    "implementer", "test-writer", "skeptic", "security",
    "performance", "accessibility", "docs", "verifier",
})
ALL_COLLAB_ROLES = BUILTIN_ROLE_NAMES
CUSTOM_ROLE_SCOPE_TYPES = frozenset({"read_only", "write_allowed", "write_test", "write_doc"})
WRITE_ROLES = frozenset({"implementer", "test-writer", "docs"})
READ_ONLY_ROLES = frozenset({"skeptic", "security", "performance", "accessibility", "verifier"})
NO_BASH_ROLES = frozenset({"implementer", "test-writer", "docs"})
DEFAULT_DETAIL_LEVELS = {
    "implementer": 3,
    "test-writer": 3,
    "docs": 3,
    "skeptic": 2,
    "security": 2,
    "performance": 2,
    "accessibility": 2,
    "verifier": 3,
}


def _truncate_text(text: str, limit: int) -> str:
    """Return *text* truncated to *limit* characters with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _format_scope_list(items: Iterable[str]) -> str:
    """Format a path or command list for compact single-line display."""
    values = [str(item) for item in items if str(item)]
    return ", ".join(values) or "(none)"


def _append_acceptance_criteria(lines: List[str], criteria: List[Any]) -> None:
    """Append acceptance criteria lines when present."""
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend([f"{idx + 1}. {criterion}" for idx, criterion in enumerate(criteria)])


def _append_changed_paths(lines: List[str], changed: List[str], label: str = "Changed paths:") -> None:
    """Append changed-path lines when present."""
    if changed:
        lines.append(label)
        lines.extend([f"- {path}" for path in changed])


def _append_scope_rules(lines: List[str], manifest: Dict[str, Any]) -> None:
    """Append the mission scope rules."""
    lines.append("Scope rules:")
    lines.append(f"- allowed_paths: {_format_scope_list(manifest.get('allowed_paths', []))}")
    lines.append(f"- denied_paths: {_format_scope_list(manifest.get('denied_paths', []))}")
    lines.append(f"- test_paths: {_format_scope_list(manifest.get('test_paths', []))}")
    lines.append(f"- doc_paths: {_format_scope_list(manifest.get('doc_paths', []))}")


def _append_spec_anchor(lines: List[str], spec: Dict[str, Any]) -> None:
    """Append the anchored spec reference."""
    if spec:
        lines.append(
            f"Spec anchor: {spec.get('path', '')} sha256={spec.get('sha256', '')}"
        )


def _append_result_reference(lines: List[str]) -> None:
    """Append the shared result-schema reference."""
    lines.append(
        f"Result format: Use {RESULT_BEGIN}/{RESULT_END} markers. "
        "See references/result-schemas.md for your role's schema."
    )


def validate_custom_role_name(name: str) -> tuple[bool, str]:
    """Validate a custom role name against built-ins and allowed characters.

    Rules: non-empty, 1-64 characters, alphanumeric plus hyphens/underscores,
    must not collide with a built-in role name.
    """
    value = str(name or "").strip()
    if not value:
        return False, "custom role name is required"
    if len(value) > 64:
        return False, f"custom role name exceeds 64 characters (got {len(value)})"
    if value in BUILTIN_ROLE_NAMES:
        return False, f"'{value}' conflicts with built-in role"
    if not value.replace("-", "").replace("_", "").isalnum():
        return False, f"'{value}' contains invalid characters"
    return True, ""


def custom_role_scope_type(role_config: Optional[Dict[str, Any]]) -> str:
    """Return the normalized custom-role scope type, failing closed."""
    if not isinstance(role_config, dict):
        return "read_only"
    raw = role_config.get("scope_type", role_config.get("scope", "read_only"))
    value = str(raw or "").strip().lower().replace("-", "_")
    if value in CUSTOM_ROLE_SCOPE_TYPES:
        return value
    return "read_only"


def _custom_role_commands(role_config: Dict[str, Any]) -> List[str]:
    """Extract approved command strings from a custom-role config."""
    commands: List[str] = []
    for item in role_config.get("approved_commands", []):
        if isinstance(item, dict) and isinstance(item.get("command"), str):
            commands.append(item["command"].strip())
        elif isinstance(item, str):
            commands.append(item.strip())
    return [command for command in commands if command]


def _commands_for_role(role: str, manifest: Dict[str, Any]) -> List[str]:
    """Return the approved command list relevant to *role*."""
    if role == "verifier":
        return approved_verification_commands(manifest)
    if role == "security":
        return approved_security_commands(manifest)
    if role == "performance":
        return approved_benchmark_commands(manifest)
    if role == "accessibility":
        return approved_a11y_commands(manifest)
    return []


def _append_approved_commands(lines: List[str], commands: List[str]) -> None:
    """Append approved commands when provided."""
    if commands:
        lines.append("Approved commands:")
        lines.extend([f"- {command}" for command in commands])


def role_from_agent_type(agent_type: Optional[str]) -> Optional[str]:
    """Extract the role name from a collab agent type string.

    Returns the portion after the ``collab-`` prefix, or ``None`` if
    *agent_type* is falsy or does not carry the prefix.
    """
    if not agent_type:
        return None
    if agent_type.startswith(COLLAB_PREFIX):
        return agent_type[len(COLLAB_PREFIX):]
    return None


def custom_role_config(
    manifest: Dict[str, Any],
    role: str,
) -> Optional[Dict[str, Any]]:
    """Return the custom-role configuration dict for *role*, or ``None``.

    Searches ``manifest["custom_roles"]`` for an entry whose ``name``
    matches *role*.  Non-dict entries in the list are silently skipped.
    """
    for item in manifest.get("custom_roles", []):
        if isinstance(item, dict) and item.get("name") == role:
            return item
    return None


def approved_verification_commands(manifest: Dict[str, Any]) -> List[str]:
    """Return the allow-listed verification commands from *manifest*."""
    return _extract_command_list(manifest, "verification_commands")


def approved_security_commands(manifest: Dict[str, Any]) -> List[str]:
    """Return the allow-listed security scan commands from *manifest*."""
    return _extract_command_list(manifest, "security_scan_commands")


def approved_benchmark_commands(manifest: Dict[str, Any]) -> List[str]:
    """Return the allow-listed benchmark commands from *manifest*."""
    return _extract_command_list(manifest, "benchmark_commands")


def approved_a11y_commands(manifest: Dict[str, Any]) -> List[str]:
    """Return the allow-listed accessibility audit commands from *manifest*."""
    return _extract_command_list(manifest, "a11y_commands")


def _extract_command_list(manifest: Dict[str, Any], key: str) -> List[str]:
    """Extract a flat list of non-empty command strings from *manifest[key]*.

    Each entry may be a plain string or a ``{"command": "..."}`` dict.
    Empty / whitespace-only entries are silently dropped.
    """
    commands: List[str] = []
    for item in manifest.get(key, []):
        if isinstance(item, dict) and isinstance(item.get("command"), str):
            commands.append(item["command"].strip())
        elif isinstance(item, str):
            commands.append(item.strip())
    return [command for command in commands if command]


def _validate_reviewer_role(
    role: str,
    result: Dict[str, Any],
    required_list_field: str,
    category_values: Optional[Iterable[str]] = None,
) -> tuple[bool, str]:
    """Validate a reviewer-type role result (security/performance/accessibility)."""
    allowed_categories = set(category_values) if category_values is not None else None
    if result.get("recommendation") not in {"approve", "needs_changes", "abort"}:
        return False, f"{role} recommendation must be approve, needs_changes, or abort"
    if not isinstance(result.get("findings"), list):
        return False, f"{role} findings must be a list"
    _VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
    for idx, finding in enumerate(result["findings"]):
        if not isinstance(finding, dict):
            return False, f"{role} findings[{idx}] must be an object"
        category = finding.get("category")
        if not isinstance(category, str) or not category.strip():
            return False, f"{role} findings[{idx}] must include a non-empty category field"
        if allowed_categories is not None and category not in allowed_categories:
            return False, f"{role} findings[{idx}] category must be one of {sorted(allowed_categories)}"
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity.strip() not in _VALID_SEVERITIES:
            return False, f"{role} findings[{idx}] must include severity (critical/high/medium/low/info)"
    if not isinstance(result.get(required_list_field), list):
        return False, f"{role} {required_list_field} must be a list"
    return True, ""


def validate_custom_role_result(
    role: str,
    result: Any,
) -> tuple[bool, str]:
    """Validate the generic result schema used by manifest-defined custom roles."""
    if not isinstance(result, dict):
        return False, f"result block must be a JSON object, got {type(result).__name__}"
    if result.get("role") != role:
        return False, f"result block role must be '{role}'"
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        return False, "result block requires a non-empty summary"

    has_status = isinstance(result.get("status"), str) and bool(result["status"].strip())
    has_recommendation = isinstance(result.get("recommendation"), str) and bool(result["recommendation"].strip())
    if not (has_status or has_recommendation):
        return False, "custom role result must include status or recommendation"
    return True, ""


def validate_role_result(
    role: str,
    result: Any,
    manifest: Dict[str, Any],
    custom_role: Optional[Dict[str, Any]] = None,
) -> tuple[bool, str]:
    """Validate a role's JSON result block against its expected schema.

    Returns ``(True, "")`` on success, or ``(False, reason)`` describing
    the first validation failure found. Each built-in role has bespoke
    field requirements; custom roles registered in the manifest use a
    generic schema requiring role, summary, and status/recommendation.
    """
    if not isinstance(result, dict):
        return False, f"result block must be a JSON object, got {type(result).__name__}"
    if result.get("role") != role:
        return False, f"result block role must be '{role}'"
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        return False, "result block requires a non-empty summary"

    if role == "implementer":
        if result.get("status") not in {"complete", "blocked"}:
            return False, "implementer status must be 'complete' or 'blocked'"
        if not isinstance(result.get("changed_paths"), list):
            return False, "implementer changed_paths must be a list"
        if "artifacts" not in result:
            return False, "implementer result must include artifacts"
        if "tests_run" not in result:
            return False, "implementer result must include tests_run"
        return True, ""

    if role == "test-writer":
        if result.get("status") not in {"complete", "blocked"}:
            return False, "test-writer status must be 'complete' or 'blocked'"
        if not isinstance(result.get("test_files_created"), list):
            return False, "test-writer test_files_created must be a list"
        if not isinstance(result.get("coverage_targets"), list):
            return False, "test-writer coverage_targets must be a list"
        if not isinstance(result.get("test_count"), int):
            return False, "test-writer test_count must be an integer"
        if not isinstance(result.get("edge_cases_covered"), list):
            return False, "test-writer edge_cases_covered must be a list"
        return True, ""

    if role == "skeptic":
        if result.get("recommendation") not in {"approve", "needs_changes", "abort"}:
            return False, "skeptic recommendation must be approve, needs_changes, or abort"
        if not isinstance(result.get("findings"), list):
            return False, "skeptic findings must be a list"
        for idx, finding in enumerate(result["findings"]):
            if not isinstance(finding, dict):
                return False, f"skeptic findings[{idx}] must be an object"
            for field in ("severity", "issue"):
                if not isinstance(finding.get(field), str) or not finding[field].strip():
                    return False, f"skeptic findings[{idx}] must include a non-empty {field}"
        return True, ""

    # Security, performance, and accessibility share a common reviewer schema:
    # recommendation + findings (with category) + a role-specific commands list.
    _REVIEWER_COMMANDS_FIELD = {
        "security": "scan_commands_run",
        "performance": "benchmarks_run",
        "accessibility": "a11y_commands_run",
    }
    if role in _REVIEWER_COMMANDS_FIELD:
        return _validate_reviewer_role(role, result, _REVIEWER_COMMANDS_FIELD[role])

    if role == "docs":
        if result.get("status") not in {"complete", "blocked"}:
            return False, "docs status must be 'complete' or 'blocked'"
        if not isinstance(result.get("docs_updated"), list):
            return False, "docs docs_updated must be a list"
        if not isinstance(result.get("docs_created"), list):
            return False, "docs docs_created must be a list"
        return True, ""

    if role == "verifier":
        if result.get("recommendation") not in {"pass", "fail", "needs_changes"}:
            return False, "verifier recommendation must be pass, fail, or needs_changes"
        criteria_results = result.get("criteria_results")
        criteria = manifest.get("acceptance_criteria", [])
        if not isinstance(criteria_results, list):
            return False, "verifier criteria_results must be a list"
        if len(criteria_results) != len(criteria):
            return False, (
                f"verifier must report exactly {len(criteria)} criteria_results "
                f"entries (got {len(criteria_results)})"
            )
        expected = {str(item) for item in criteria}
        seen: set[str] = set()
        for item in criteria_results:
            if not isinstance(item, dict):
                return False, "each criteria_results entry must be an object"
            criterion = item.get("criterion")
            status = item.get("status")
            if criterion not in expected:
                return False, "criteria_results contains a criterion not present in manifest"
            if criterion in seen:
                return False, "criteria_results contains duplicate criteria"
            seen.add(criterion)
            if status not in {"pass", "fail", "not-run"}:
                return False, "criterion status must be pass, fail, or not-run"
            evidence = item.get("evidence")
            if status in ("pass", "fail") and not isinstance(evidence, (str, list)):
                return False, f"criterion '{criterion}' with status '{status}' must include evidence"
        return True, ""

    if custom_role or custom_role_config(manifest, role):
        return validate_custom_role_result(role, result)

    return False, f"unknown role '{role}'"


READ_ONLY_BASH_PATTERNS = [
    re.compile(r"^\s*git\s+(status(\s+--short)?|diff\b.*|show\b.*|log\b.*)\s*$"),
    re.compile(r"^\s*(rg|grep|cat|head|tail|ls)\b.*$"),
    # sed -n: only allow print-line forms; deny w/e/r/R commands inside expressions
    re.compile(r"^\s*sed\s+-n\s+'[0-9]+p'\s*$"),
    re.compile(r"^\s*sed\s+-n\s+'/[^']*(?<!/)/p'\s*$"),
    # awk removed entirely -- system(), getline, print-to-file make safe allowlisting infeasible
    re.compile(r"^\s*find\b(?!.*(-exec|-execdir|-delete|-ok)\b).*$"),
]

# NOTE: These patterns intentionally over-reject rather than under-reject.
# A "|" inside a quoted grep argument (e.g. grep "a|b") will trigger a
# false-positive denial.  This is the safe direction -- fail-closed.
_OUTPUT_REDIRECT = re.compile(r">{1,2}\s*\S")
_PIPE_OPERATOR = re.compile(r"\|")
_COMMAND_CHAIN = re.compile(r"[;&]|&&|\|\|")
_COMMAND_SUBST = re.compile(r"`|\$\(")


def _has_shell_operator(command: str) -> bool:
    """Return True if *command* contains pipes, redirects, chains, or substitutions."""
    if _OUTPUT_REDIRECT.search(command):
        return True
    if _PIPE_OPERATOR.search(command):
        return True
    if _COMMAND_CHAIN.search(command):
        return True
    if _COMMAND_SUBST.search(command):
        return True
    return False


def command_matches_any(
    command: str,
    patterns: Iterable[re.Pattern[str]],
) -> bool:
    """Return True if *command* matches any regex in *patterns*.

    Returns False immediately if the command contains shell operators
    (pipes, redirects, chains, or command substitution) to prevent
    bypassing read-only restrictions via chaining.
    """
    if _has_shell_operator(command):
        return False
    return any(pattern.match(command) for pattern in patterns)


def command_is_approved_extra(command: str, approved: List[str]) -> bool:
    """Return True if *command* is an exact match in the *approved* list.

    Shell operators cause an immediate False to prevent injection via
    approved command strings concatenated with destructive suffixes.
    """
    if _has_shell_operator(command):
        return False
    return command in approved


def format_manifest_summary(ctx: MissionContext) -> str:
    """Build a human-readable multi-line summary of the active mission.

    Includes phase, spec anchor status, path scopes, acceptance criteria,
    approved commands, observed changed paths, role result statuses, and
    recent progress entries.
    """
    manifest = ctx.manifest
    lines = [
        f"[COLLAB] Active mission {ctx.mission_id}",
        f"Phase: {manifest.get('phase', 'unknown')}",
        f"Awaiting user: {'yes' if manifest.get('awaiting_user') else 'no'}",
        f"Objective: {manifest.get('objective', '')}",
    ]

    spec = manifest.get("spec") or {}
    if spec:
        anchored = spec.get("sha256", "unknown")
        current = spec_hash(ctx.project_root, spec.get("path", "")) or "missing"
        state = "MATCH" if anchored == current else "MISMATCH"
        lines.append(f"Spec: {spec.get('path', '')} [{state}]")
        lines.append(f"Spec SHA (anchored/current): {anchored} / {current}")

    lines.append(f"Allowed paths: {', '.join(manifest.get('allowed_paths', [])) or '(none)'}")
    lines.append(f"Denied paths: {', '.join(manifest.get('denied_paths', [])) or '(none)'}")
    lines.append(f"Test paths: {', '.join(manifest.get('test_paths', [])) or '(none)'}")
    lines.append(f"Doc paths: {', '.join(manifest.get('doc_paths', [])) or '(none)'}")

    criteria = manifest.get("acceptance_criteria", [])
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend([f"{idx + 1}. {criterion}" for idx, criterion in enumerate(criteria)])

    for label, key in [
        ("verification", "verification_commands"),
        ("security scan", "security_scan_commands"),
        ("benchmark", "benchmark_commands"),
        ("a11y", "a11y_commands"),
    ]:
        commands = _extract_command_list(manifest, key)
        if commands:
            lines.append(f"Approved {label} commands:")
            lines.extend([f"- {command}" for command in commands])

    changed = changed_paths_from_summary(ctx)
    if changed:
        lines.append("Observed changed paths:")
        lines.extend([f"- {item}" for item in changed])

    completed_roles: List[str] = []
    for role in sorted(ALL_COLLAB_ROLES):
        role_result = load_role_result(ctx, role)
        if role_result:
            outcome = role_result.get("recommendation") or role_result.get("status") or "present"
            completed_roles.append(f"{role} ({outcome})")
    lines.append(f"Completed: {', '.join(completed_roles) if completed_roles else 'none'}")
    remaining = len(ALL_COLLAB_ROLES) - len(completed_roles)
    lines.append(f"Remaining: {remaining} role{'s' if remaining != 1 else ''} pending")

    progress = recent_progress(ctx)
    if progress:
        lines.append("Recent progress:")
        lines.extend(progress)

    lines.append("Use python3 .claude/tools/collabctl.py show for full state.")
    return "\n".join(lines)


def format_manifest_slim(ctx: MissionContext) -> str:
    """Build a compact 3-5 line mission summary for SessionStart injection."""
    m = ctx.manifest
    completed = sum(1 for role in ALL_COLLAB_ROLES if load_role_result(ctx, role) is not None)
    total = len(ALL_COLLAB_ROLES)
    phase = m.get("phase", "unknown")
    objective = m.get("objective", "")
    objective_short = objective[:100]
    lines = [
        f"[COLLAB] Mission {ctx.mission_id} | Phase: {phase} | {completed}/{total} roles complete",
        f"Objective: {objective_short}{'...' if len(objective) > 100 else ''}",
        "Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py show` for full state.",
    ]
    if m.get("awaiting_user"):
        lines.insert(1, "Status: AWAITING USER INPUT")
    return "\n".join(lines)


def build_subagent_context(ctx: MissionContext, role: str, detail_level: int = 2) -> str:
    """Construct mission-specific subagent context with progressive disclosure."""
    manifest = ctx.manifest
    criteria = manifest.get("acceptance_criteria", [])
    changed = changed_paths_from_summary(ctx)
    spec = manifest.get("spec") or {}
    objective = _truncate_text(str(manifest.get("objective", "")), 200)
    detail = max(1, min(3, int(detail_level)))
    lines: List[str] = [
        f"[COLLAB] Mission {ctx.mission_id}",
        f"Role: {role}",
        f"Phase: {manifest.get('phase', 'unknown')}",
        f"Objective: {objective}",
    ]

    custom = custom_role_config(manifest, role)
    if role not in ALL_COLLAB_ROLES and not custom:
        print(f"[collab] warning: unknown role '{role}' in build_subagent_context", file=sys.stderr)
        return "\n".join(lines)

    if custom:
        lines.append(f"Custom role scope: {custom_role_scope_type(custom)}")
        lines.append(f"Custom approved commands: {_format_scope_list(_custom_role_commands(custom))}")
        lines.append(f"Custom isolation: {str(custom.get('isolation') or 'none')}")

    if detail >= 2:
        if role == "skeptic":
            lines.append("Blind review inputs: implementer claims are intentionally omitted.")
        _append_acceptance_criteria(lines, criteria)
        _append_changed_paths(lines, changed)
        _append_scope_rules(lines, manifest)
        if custom:
            instructions = custom.get("instructions")
            if isinstance(instructions, str) and instructions.strip():
                lines.append(f"Custom instructions: {_truncate_text(instructions.strip(), 500)}")

    if detail >= 3:
        _append_spec_anchor(lines, spec)
        if not custom:
            _append_approved_commands(lines, _commands_for_role(role, manifest))
        _append_result_reference(lines)

    return "\n".join(lines)


__all__ = [
    "ALL_COLLAB_ROLES",
    "BUILTIN_ROLE_NAMES",
    "CUSTOM_ROLE_SCOPE_TYPES",
    "NO_BASH_ROLES",
    "READ_ONLY_BASH_PATTERNS",
    "READ_ONLY_ROLES",
    "WRITE_ROLES",
    "DEFAULT_DETAIL_LEVELS",
    "approved_a11y_commands",
    "approved_benchmark_commands",
    "approved_security_commands",
    "approved_verification_commands",
    "build_subagent_context",
    "command_is_approved_extra",
    "command_matches_any",
    "custom_role_config",
    "custom_role_scope_type",
    "format_manifest_slim",
    "format_manifest_summary",
    "role_from_agent_type",
    "validate_custom_role_name",
    "validate_custom_role_result",
    "validate_role_result",
]
