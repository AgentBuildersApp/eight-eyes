from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def normalize_path(candidate: str, base: Path) -> Path:
    path = Path(candidate)
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def path_is_allowed(
    candidate: Path,
    manifest: Dict[str, Any],
    project_root: Path,
) -> tuple[bool, str]:
    candidate = normalize_path(str(candidate), project_root)
    if not is_relative_to(candidate, project_root):
        return False, f"path escapes repository root: {candidate}"
    denied = [normalize_path(item, project_root) for item in manifest.get("denied_paths", [])]
    for item in denied:
        if is_relative_to(candidate, item):
            return False, f"path is under denied path {safe_rel(item, project_root)}"
    allowed = [normalize_path(item, project_root) for item in manifest.get("allowed_paths", [])]
    if allowed and not any(is_relative_to(candidate, item) for item in allowed):
        return False, f"path is outside allowed_paths: {safe_rel(candidate, project_root)}"
    return True, ""


def path_is_in_test_paths(
    candidate: str,
    manifest: Dict[str, Any],
    project_root: Path,
) -> tuple[bool, str]:
    test_paths = manifest.get("test_paths", [])
    if not test_paths:
        return False, "no test_paths defined in manifest"
    resolved = normalize_path(candidate, project_root)
    if not is_relative_to(resolved, project_root):
        return False, f"path escapes repository root: {resolved}"
    for denied_path in manifest.get("denied_paths", []):
        if is_relative_to(resolved, normalize_path(denied_path, project_root)):
            return False, f"path is under denied path {denied_path}"
    bases = [normalize_path(path, project_root) for path in test_paths]
    if any(is_relative_to(resolved, base) for base in bases):
        return True, ""
    return False, f"path is outside test_paths: {safe_rel(resolved, project_root)}"


def path_is_in_doc_paths(
    candidate: str,
    manifest: Dict[str, Any],
    project_root: Path,
) -> tuple[bool, str]:
    doc_paths = manifest.get("doc_paths", [])
    if not doc_paths:
        return False, "no doc_paths defined in manifest"
    resolved = normalize_path(candidate, project_root)
    if not is_relative_to(resolved, project_root):
        return False, f"path escapes repository root: {resolved}"
    for denied_path in manifest.get("denied_paths", []):
        if is_relative_to(resolved, normalize_path(denied_path, project_root)):
            return False, f"path is under denied path {denied_path}"
    bases = [normalize_path(path, project_root) for path in doc_paths]
    if any(is_relative_to(resolved, base) for base in bases):
        return True, ""
    return False, f"path is outside doc_paths: {safe_rel(resolved, project_root)}"


__all__ = [
    "is_relative_to",
    "normalize_path",
    "path_is_allowed",
    "path_is_in_doc_paths",
    "path_is_in_test_paths",
    "safe_rel",
]

