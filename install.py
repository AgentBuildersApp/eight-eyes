#!/usr/bin/env python3
"""Eight-eyes cross-platform installer.

Detects Claude Code, Copilot CLI, and Codex CLI installations
and sets up the appropriate adapter for each.

Usage:
    python3 install.py              # Auto-detect and install
    python3 install.py --platform claude_code
    python3 install.py --platform copilot_cli
    python3 install.py --platform codex_cli
    python3 install.py --uninstall  # Remove installed adapters
    python3 install.py --verify     # Check installation
    python3 install.py --add-to-path  # Also create a CLI shim on PATH
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
HOME = Path.home()

CLAUDE_TARGET = HOME / ".claude" / "plugins" / "eight-eyes"
COPILOT_TARGET = HOME / ".config" / "github-copilot" / "plugins" / "eight-eyes"
CODEX_TARGET = HOME / ".codex" / "plugins" / "eight-eyes"

PLATFORM_TARGETS = {
    "claude_code": CLAUDE_TARGET,
    "copilot_cli": COPILOT_TARGET,
    "codex_cli": CODEX_TARGET,
}


def detect_claude_code() -> bool:
    return (HOME / ".claude").exists()


def detect_copilot_cli() -> bool:
    config_dir = HOME / ".config" / "github-copilot"
    if config_dir.exists():
        return True
    try:
        proc = subprocess.run(
            ["gh", "copilot", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def detect_codex_cli() -> bool:
    return (HOME / ".codex").exists()


PLATFORM_DETECTORS = {
    "claude_code": detect_claude_code,
    "copilot_cli": detect_copilot_cli,
    "codex_cli": detect_codex_cli,
}

INSTALL_EXPECTATIONS = {
    "claude_code": [
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
        "skills/collab/SKILL.md",
        "scripts/collabctl.py",
    ],
    "copilot_cli": [
        "plugin.json",
        "hooks.json",
        "agents",
        "skills/collab/SKILL.md",
        "hooks/scripts/collab_pre_tool.py",
        "scripts/collabctl.py",
    ],
    "codex_cli": [
        "AGENTS.md",
        "hooks.json",
        "agents",
        "hooks/scripts/collab_pre_tool.py",
        "scripts/collabctl.py",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install eight-eyes adapters for supported CLIs.")
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(PLATFORM_TARGETS),
        help="Install, uninstall, or verify only the named platform. Repeat to handle multiple platforms.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove installed adapters for the selected or auto-detected platforms.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the repo assets and any installed adapters for the selected or auto-detected platforms.",
    )
    parser.add_argument(
        "--add-to-path",
        action="store_true",
        help="Create a CLI shim in ~/.local/bin/ (or equivalent) for PATH discoverability.",
    )
    return parser.parse_args()


def selected_platforms(requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    return [name for name, detector in PLATFORM_DETECTORS.items() if detector()]


def _on_rm_error(_func: object, path: str, _exc_info: object) -> None:
    """Handle permission errors during rmtree on Windows (locked .git objects)."""
    import os, stat
    try:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)
    except OSError:
        pass  # Best-effort: skip files that truly cannot be removed


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path, onexc=_on_rm_error)


def reset_directory(path: Path) -> None:
    remove_path(path)
    path.mkdir(parents=True, exist_ok=True)


def link_or_copy(src: Path, dst: Path) -> str:
    if not src.exists():
        raise FileNotFoundError(src)
    remove_path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
        return "symlinked"
    except OSError:
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".tmp-tests", "__pycache__"),
            )
        else:
            shutil.copy2(src, dst)
        return "copied"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def repo_verify() -> bool:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "collabctl.py"), "--cwd", str(REPO_ROOT), "verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode == 0


def verify_target(platform: str) -> tuple[bool, list[str]]:
    target = PLATFORM_TARGETS[platform]
    errors: list[str] = []
    for relative_path in INSTALL_EXPECTATIONS[platform]:
        if not (target / relative_path).exists():
            errors.append(f"Missing {platform} file: {target / relative_path}")
    return not errors, errors


def installed_copilot_hooks(target: Path) -> dict:
    scripts_root = target / "hooks" / "scripts"
    def bash_script(name: str) -> str:
        return f'python3 "{(scripts_root / name).as_posix()}"'

    def powershell_script(name: str) -> str:
        return f'python3 "{scripts_root / name}"'

    return {
        "version": 1,
        "hooks": {
            "preToolUse": [
                {
                    "type": "command",
                    "bash": bash_script("collab_pre_tool.py"),
                    "powershell": powershell_script("collab_pre_tool.py"),
                }
            ],
            "postToolUse": [
                {
                    "type": "command",
                    "bash": bash_script("collab_post_tool.py"),
                    "powershell": powershell_script("collab_post_tool.py"),
                }
            ],
            "sessionStart": [
                {
                    "type": "command",
                    "bash": bash_script("collab_session_start.py"),
                    "powershell": powershell_script("collab_session_start.py"),
                }
            ],
            "subagentStart": [
                {
                    "type": "command",
                    "bash": bash_script("collab_subagent_start.py"),
                    "powershell": powershell_script("collab_subagent_start.py"),
                }
            ],
            "subagentStop": [
                {
                    "type": "command",
                    "bash": bash_script("collab_subagent_stop.py"),
                    "powershell": powershell_script("collab_subagent_stop.py"),
                }
            ],
            "stop": [
                {
                    "type": "command",
                    "bash": bash_script("collab_stop.py"),
                    "powershell": powershell_script("collab_stop.py"),
                }
            ],
        },
    }


def installed_codex_hooks(target: Path) -> dict:
    scripts_root = target / "hooks" / "scripts"
    return {
        "hooks": [
            {
                "event": "PreToolUse",
                "command": ["python3", str(scripts_root / "collab_pre_tool.py")],
                "timeout_ms": 30000,
            },
            {
                "event": "PostToolUse",
                "command": ["python3", str(scripts_root / "collab_post_tool.py")],
                "timeout_ms": 30000,
            },
            {
                "event": "SessionStart",
                "command": ["python3", str(scripts_root / "collab_session_start.py")],
                "timeout_ms": 30000,
            },
            {
                "event": "Stop",
                "command": ["python3", str(scripts_root / "collab_stop.py")],
                "timeout_ms": 30000,
            },
        ]
    }


def install_claude_code(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = link_or_copy(REPO_ROOT, target)
    print(f"[OK] claude_code {mode}: {target}")


def install_copilot_cli(target: Path) -> None:
    reset_directory(target)
    mode_agents = link_or_copy(REPO_ROOT / "adapters" / "copilot_cli" / "agents", target / "agents")
    mode_skill = link_or_copy(
        REPO_ROOT / "adapters" / "copilot_cli" / "skills" / "collab" / "SKILL.md",
        target / "skills" / "collab" / "SKILL.md",
    )
    mode_refs = link_or_copy(
        REPO_ROOT / "skills" / "collab" / "references",
        target / "skills" / "collab" / "references",
    )
    mode_hooks = link_or_copy(REPO_ROOT / "hooks", target / "hooks")
    mode_scripts = link_or_copy(REPO_ROOT / "scripts", target / "scripts")
    adapter_plugin = json.loads((REPO_ROOT / "adapters" / "copilot_cli" / "plugin.json").read_text(encoding="utf-8"))
    write_json(target / "plugin.json", adapter_plugin)
    write_json(target / "hooks.json", installed_copilot_hooks(target))
    print(
        "[OK] copilot_cli installed: "
        f"plugin=generated, agents={mode_agents}, skill={mode_skill}, "
        f"references={mode_refs}, hooks={mode_hooks}, scripts={mode_scripts}"
    )


def install_codex_cli(target: Path) -> None:
    reset_directory(target)
    mode_agents = link_or_copy(REPO_ROOT / "adapters" / "codex_cli" / "agents", target / "agents")
    mode_instructions = link_or_copy(REPO_ROOT / "adapters" / "codex_cli" / "AGENTS.md", target / "AGENTS.md")
    mode_hooks = link_or_copy(REPO_ROOT / "hooks", target / "hooks")
    mode_scripts = link_or_copy(REPO_ROOT / "scripts", target / "scripts")
    write_json(target / "hooks.json", installed_codex_hooks(target))
    print(
        "[OK] codex_cli installed: "
        f"agents={mode_agents}, AGENTS.md={mode_instructions}, hooks={mode_hooks}, scripts={mode_scripts}"
    )


INSTALLERS = {
    "claude_code": install_claude_code,
    "copilot_cli": install_copilot_cli,
    "codex_cli": install_codex_cli,
}


def _print_post_install_guidance(platform: str, target: Path) -> None:
    """Print user guidance after a successful install."""
    print()
    print("  eight-eyes installed successfully!")
    print()
    print(f"  Verify:  python3 {target}/scripts/collabctl.py --cwd <your-repo> verify")
    print(f"  Version: python3 {target}/scripts/collabctl.py --version")
    print(f"  Usage:   From a git repo, use /8eyes:collab <objective>")
    print(f"  Locate:  python3 {target}/scripts/collabctl.py locate")
    print()
    if platform == "copilot_cli":
        print("  Note: Restart your Copilot CLI session to pick up the new hooks.")
    elif platform == "codex_cli":
        print("  Note: Restart your Codex CLI session to pick up the new hooks.")
    print()


def _install_cli_shim() -> None:
    """Create a cross-platform CLI shim in ~/.local/bin/ for PATH discoverability."""
    import os
    bin_dir = HOME / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    collabctl = REPO_ROOT / "scripts" / "collabctl.py"

    if sys.platform == "win32":
        shim_path = bin_dir / "eight-eyes.cmd"
        shim_content = f'@echo off\r\npython3 "{collabctl}" %*\r\n'
        shim_path.write_text(shim_content, encoding="utf-8")
    else:
        shim_path = bin_dir / "eight-eyes"
        shim_content = (
            '#!/usr/bin/env bash\n'
            f'exec python3 "{collabctl}" "$@"\n'
        )
        shim_path.write_text(shim_content, encoding="utf-8")
        os.chmod(str(shim_path), 0o755)

    print(f"[OK] CLI shim installed: {shim_path}")
    if str(bin_dir) not in os.environ.get("PATH", ""):
        print(f"  Add {bin_dir} to your PATH if not already present.")


def _cleanup_marketplace_registry() -> None:
    """Remove eight-eyes entries from Claude Code plugin registry files."""
    home = Path.home()
    # Clean known_marketplaces.json
    km_path = home / ".claude" / "plugins" / "known_marketplaces.json"
    if km_path.exists():
        try:
            import json as _json
            data = _json.loads(km_path.read_text(encoding="utf-8"))
            if "8eyes-marketplace" in data:
                del data["8eyes-marketplace"]
                km_path.write_text(
                    _json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                print("[OK] removed 8eyes-marketplace from known_marketplaces.json")
        except Exception as exc:
            print(f"[WARN] could not clean known_marketplaces.json: {exc}")
    # Clean installed_plugins.json
    ip_path = home / ".claude" / "plugins" / "installed_plugins.json"
    if ip_path.exists():
        try:
            import json as _json
            data = _json.loads(ip_path.read_text(encoding="utf-8"))
            plugins = data.get("plugins", {})
            if "8eyes@8eyes-marketplace" in plugins:
                del plugins["8eyes@8eyes-marketplace"]
                ip_path.write_text(
                    _json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                print("[OK] removed 8eyes@8eyes-marketplace from installed_plugins.json")
        except Exception as exc:
            print(f"[WARN] could not clean installed_plugins.json: {exc}")
    # Clean marketplace cache directory
    cache_dir = home / ".claude" / "plugins" / "cache" / "8eyes-marketplace"
    if cache_dir.exists():
        remove_path(cache_dir)
        print(f"[OK] removed marketplace cache: {cache_dir}")
    mp_dir = home / ".claude" / "plugins" / "marketplaces" / "8eyes-marketplace"
    if mp_dir.exists():
        remove_path(mp_dir)
        print(f"[OK] removed marketplace directory: {mp_dir}")
    # Copilot CLI marketplace (Windows)
    copilot_marketplace = home / "AppData" / "Local" / "copilot" / "marketplaces" / "AgentBuildersApp-eight-eyes"
    if copilot_marketplace.exists():
        remove_path(copilot_marketplace)
        print(f"[OK] removed Copilot marketplace cache: {copilot_marketplace}")
    # Copilot CLI marketplace (macOS/Linux)
    copilot_marketplace_unix = home / ".local" / "share" / "copilot" / "marketplaces" / "AgentBuildersApp-eight-eyes"
    if copilot_marketplace_unix.exists():
        remove_path(copilot_marketplace_unix)
        print(f"[OK] removed Copilot marketplace cache: {copilot_marketplace_unix}")


def uninstall_platform(platform: str) -> None:
    target = PLATFORM_TARGETS[platform]
    if target.exists() or target.is_symlink():
        remove_path(target)
        print(f"[OK] removed {platform}: {target}")
    else:
        print(f"[OK] {platform} not installed: {target}")
    # Copilot CLI: also clean marketplace caches
    if platform == "copilot_cli":
        home = Path.home()
        copilot_mp_win = home / "AppData" / "Local" / "copilot" / "marketplaces" / "AgentBuildersApp-eight-eyes"
        if copilot_mp_win.exists():
            remove_path(copilot_mp_win)
            print(f"[OK] removed Copilot marketplace cache: {copilot_mp_win}")
        copilot_mp_unix = home / ".local" / "share" / "copilot" / "marketplaces" / "AgentBuildersApp-eight-eyes"
        if copilot_mp_unix.exists():
            remove_path(copilot_mp_unix)
            print(f"[OK] removed Copilot marketplace cache: {copilot_mp_unix}")


def verify_platforms(platforms: list[str]) -> int:
    ok = repo_verify()
    failures = 0
    for platform in platforms:
        platform_ok, errors = verify_target(platform)
        if platform_ok:
            print(f"[OK] {platform} install verified: {PLATFORM_TARGETS[platform]}")
        else:
            failures += 1
            for error in errors:
                print(f"[FAIL] {error}")
    if not platforms:
        print("No supported platform installations detected.")
    return 0 if ok and failures == 0 else 1


def main() -> int:
    args = parse_args()
    platforms = selected_platforms(args.platform)

    if args.uninstall:
        for platform in platforms:
            uninstall_platform(platform)
        _cleanup_marketplace_registry()
        if not platforms:
            print("No supported platform installations detected.")
        return 0

    if args.verify:
        return verify_platforms(platforms)

    if not platforms:
        print("No supported platform installations detected.")
        return 1

    for platform in platforms:
        INSTALLERS[platform](PLATFORM_TARGETS[platform])
        _print_post_install_guidance(platform, PLATFORM_TARGETS[platform])

    if args.add_to_path:
        _install_cli_shim()

    return 0 if repo_verify() else 1


if __name__ == "__main__":
    raise SystemExit(main())

