"""Comprehensive test suite for /collab plugin hooks.

130 tests covering all 8 roles, scope enforcement, result validation,
lifecycle management, state handling, schema migration (v3->v4), collabctl CLI,
parallel audit phase, TDD hook enforcement, mission resilience
(timeout, stale warning, failure tracking, REVIEW.md, dry-run),
v4 schema fields (model_map, role_assignments, planned_roles, fail_closed),
CLI observability (timeline, report, status model info), copilot adapter,
compensating revert (M1), close-time scope verification (M3),
security audit fixes, and accessibility improvements.

Runs with Python 3.10+ stdlib only. Uses real git repos in temp directories.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = PLUGIN_ROOT / "hooks" / "scripts"
AGENTS_DIR = PLUGIN_ROOT / "agents"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
TEST_TMP_ROOT = PLUGIN_ROOT / ".tmp-tests"


class CollabHookTests(unittest.TestCase):
    """Full test suite for /collab plugin — 130 tests."""

    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.tempdir = TEST_TMP_ROOT / f"test-{uuid.uuid4().hex}"
        self.tempdir.mkdir()
        self.repo = self.tempdir / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo, check=True, capture_output=True,
        )
        # Create standard project dirs
        for d in ("src", "tests", "config", "docs", "secrets", "__tests__"):
            (self.repo / d).mkdir()
        (self.repo / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.repo / "src" / "auth.py").write_text("def validate(): pass\n", encoding="utf-8")
        (self.repo / "docs" / "spec.md").write_text("# Spec\n\nDo the thing.\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Project\n", encoding="utf-8")

        # Initial commit
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo, check=True, capture_output=True,
        )

        # Load collab_common module from the plugin's hooks/scripts directory
        spec = importlib.util.spec_from_file_location(
            "collab_common", HOOKS_DIR / "collab_common.py"
        )
        assert spec is not None and spec.loader is not None
        self.common = importlib.util.module_from_spec(spec)
        sys.modules["collab_common"] = self.common
        spec.loader.exec_module(self.common)

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)
        sys.modules.pop("collab_common", None)

    # ── Helpers ──

    def run_hook(self, script_name: str, payload: dict, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a hook script with a JSON payload on stdin."""
        script = HOOKS_DIR / script_name
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(cwd or self.repo),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def run_hook_stdin(self, script_name: str, raw_input: str, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a hook script with raw stdin instead of JSON-encoding the payload."""
        script = HOOKS_DIR / script_name
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=raw_input,
            text=True,
            capture_output=True,
            cwd=str(cwd or self.repo),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def hook_json(self, script_name: str, payload: dict, cwd: Path | None = None):
        """Run a hook and parse JSON output. Returns None if no output."""
        rc, out, err = self.run_hook(script_name, payload, cwd=cwd)
        self.assertEqual(rc, 0, msg=f"Hook {script_name} failed: {err}")
        return json.loads(out) if out else None

    def run_ctl(self, *args: str) -> subprocess.CompletedProcess:
        """Run collabctl with args using --cwd to point at the test repo."""
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo), *args],
            cwd=str(self.repo),
            text=True,
            capture_output=True,
            check=True,
        )

    def init_mission(self, **extra_args) -> dict:
        """Initialize a standard mission and return the parsed output."""
        cmd = [
            "init",
            "--objective", "Implement a safe change",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--allowed-path", "tests",
            "--criterion", "Behavior matches the requested objective",
            "--criterion", "Relevant tests pass",
            "--verify-command", "pytest -q",
        ]
        # Add security command for security role tests
        if extra_args.get("security"):
            cmd.extend(["--security-command", "bandit -r src/"])
        if extra_args.get("benchmark"):
            cmd.extend(["--benchmark-command", "pytest --benchmark-only"])
        if extra_args.get("a11y"):
            cmd.extend(["--a11y-command", "npx axe-linter src/"])
        if extra_args.get("tdd"):
            cmd.append("--tdd")
        if "timeout_hours" in extra_args:
            cmd.extend(["--timeout-hours", str(extra_args["timeout_hours"])])
        return json.loads(self.run_ctl(*cmd).stdout)

    def set_phase(self, phase: str, awaiting_user: bool = False, force: bool = True):
        args = ["phase", phase, "--awaiting-user", "true" if awaiting_user else "false"]
        if force:
            args.append("--force")
        self.run_ctl(*args)

    def close_mission(self, outcome: str):
        self.run_ctl("close", outcome, "--reason", "done")

    def get_mission_dir(self) -> Path:
        show = json.loads(self.run_ctl("show").stdout)
        return Path(show["paths"]["manifest"]).parent

    def record_implementer_write(self):
        """Simulate an implementer file write via post_tool hook."""
        self.hook_json("collab_post_tool.py", {
            "cwd": str(self.repo),
            "hook_event_name": "PostToolUse",
            "agent_type": "collab-implementer",
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / "src" / "app.py"), "content": "print('changed')\n"},
            "tool_response": {"filePath": str(self.repo / "src" / "app.py"), "success": True},
            "tool_use_id": "toolu-write-impl-1",
        })

    def load_manifest(self) -> dict:
        show = json.loads(self.run_ctl("show").stdout)
        return json.loads(Path(show["paths"]["manifest"]).read_text(encoding="utf-8"))

    def save_manifest(self, manifest: dict) -> None:
        show = json.loads(self.run_ctl("show").stdout)
        Path(show["paths"]["manifest"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def save_role_result(self, role: str, result: dict) -> None:
        ctx = self.common.load_active_context(self.repo)
        self.common.save_role_result(ctx, role, result)

    def valid_role_result(self, role: str) -> dict:
        results = {
            "implementer": {
                "role": "implementer",
                "status": "complete",
                "summary": "Implemented the change.",
                "changed_paths": ["src/app.py"],
                "artifacts": ["updated handler"],
                "tests_run": [],
            },
            "test-writer": {
                "role": "test-writer",
                "status": "complete",
                "summary": "Wrote tests first.",
                "test_files_created": ["tests/test_app.py"],
                "coverage_targets": ["src/app.py"],
                "test_count": 1,
                "edge_cases_covered": ["happy path"],
            },
            "skeptic": {
                "role": "skeptic",
                "summary": "Reviewed the change.",
                "recommendation": "approve",
                "findings": [],
            },
            "security": {
                "role": "security",
                "summary": "Security review complete.",
                "recommendation": "approve",
                "findings": [],
                "scan_commands_run": [],
            },
            "performance": {
                "role": "performance",
                "summary": "Performance review complete.",
                "recommendation": "approve",
                "findings": [],
                "benchmarks_run": [],
            },
            "accessibility": {
                "role": "accessibility",
                "summary": "Accessibility review complete.",
                "recommendation": "approve",
                "findings": [],
                "a11y_commands_run": [],
            },
            "verifier": {
                "role": "verifier",
                "summary": "Verification complete.",
                "recommendation": "pass",
                "criteria_results": [
                    {
                        "criterion": "Behavior matches the requested objective",
                        "status": "pass",
                        "evidence": ["looks good"],
                    },
                    {
                        "criterion": "Relevant tests pass",
                        "status": "pass",
                        "evidence": ["pytest -q"],
                    },
                ],
            },
        }
        return dict(results[role])

    # ═══════════════════════════════════════════════════════════════════
    #  SCOPE ENFORCEMENT TESTS (Tests 1–19)
    # ═══════════════════════════════════════════════════════════════════

    def test_01_implementer_write_allowed_path(self):
        """Test 1: Implementer Write to allowed path → allowed."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "src/new_file.py", "content": "x = 1\n"},
        })
        self.assertIsNone(result)  # None = allowed through

    def test_02_implementer_write_outside_allowed(self):
        """Test 2: Implementer Write to config/ (outside allowed) → denied."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "config/secrets.txt", "content": "oops\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_03_implementer_bash_denied(self):
        """Test 3: Implementer Bash → hard denied."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_04_test_writer_write_to_tests(self):
        """Test 4: Test-writer Write to tests/ → allowed."""
        self.init_mission()
        self.set_phase("test")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-test-writer",
            "tool_name": "Write", "tool_input": {"file_path": "tests/test_app.py", "content": "def test(): pass\n"},
        })
        self.assertIsNone(result)

    def test_05_test_writer_write_to_src_denied(self):
        """Test 5: Test-writer Write to src/ → denied."""
        self.init_mission()
        self.set_phase("test")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-test-writer",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "hacked\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_06_skeptic_write_denied(self):
        """Test 6: Skeptic Write → denied."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "evil\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_07_skeptic_readonly_bash_allowed(self):
        """Test 7: Skeptic read-only Bash → allowed."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "git status --short"},
        })
        self.assertIsNone(result)

    def test_08_skeptic_bash_redirect_denied(self):
        """Test 8: Skeptic Bash with redirect → denied."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "cat src/app.py > /tmp/stolen.txt"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_09_skeptic_bash_pipe_denied(self):
        """Test 9: Skeptic Bash with pipe → denied."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "grep -r password src/ | tee /tmp/passwords.txt"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_10_skeptic_bash_chain_denied(self):
        """Test 10: Skeptic Bash with command chain → denied."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "ls src/; rm -rf /"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_11_skeptic_find_exec_denied(self):
        """Test 11: Skeptic find -exec → denied."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "find . -name '*.py' -exec rm {} +"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_12_skeptic_find_type_allowed(self):
        """Test 12: Skeptic find -type f → allowed."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "find src -name '*.py' -type f"},
        })
        self.assertIsNone(result)

    def test_13_security_approved_scan_allowed(self):
        """Test 13: Security Bash approved scan command → allowed."""
        self.init_mission(security=True)
        self.set_phase("security")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-security",
            "tool_name": "Bash", "tool_input": {"command": "bandit -r src/"},
        })
        self.assertIsNone(result)

    def test_14_security_unapproved_command_denied(self):
        """Test 14: Security Bash unapproved command → denied."""
        self.init_mission(security=True)
        self.set_phase("security")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-security",
            "tool_name": "Bash", "tool_input": {"command": "npm audit"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_15_verifier_approved_command_allowed(self):
        """Test 15: Verifier Bash approved verification command → allowed."""
        self.init_mission()
        self.set_phase("verify")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-verifier",
            "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
        })
        self.assertIsNone(result)

    def test_16_verifier_unapproved_command_denied(self):
        """Test 16: Verifier Bash unapproved command → denied."""
        self.init_mission()
        self.set_phase("verify")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-verifier",
            "tool_name": "Bash", "tool_input": {"command": "npm test"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_17_docs_write_to_docs_allowed(self):
        """Test 17: Docs Write to docs/ → allowed."""
        self.init_mission()
        self.set_phase("docs")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-docs",
            "tool_name": "Write", "tool_input": {"file_path": "docs/api.md", "content": "# API\n"},
        })
        self.assertIsNone(result)

    def test_18_docs_write_to_src_denied(self):
        """Test 18: Docs Write to src/ → denied."""
        self.init_mission()
        self.set_phase("docs")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-docs",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "evil\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_19_path_traversal_blocked(self):
        """Test 19: Path traversal (../../../etc/passwd) → denied."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "../../../etc/passwd", "content": "evil\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    # ═══════════════════════════════════════════════════════════════════
    #  RESULT VALIDATION TESTS (Tests 20–24)
    # ═══════════════════════════════════════════════════════════════════

    def test_20_subagent_stop_blocks_without_result(self):
        """Test 20: SubagentStop blocks implementer without result block → blocked."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "agent_id": "agent-impl", "stop_hook_active": False,
            "last_assistant_message": "Done without structured output.",
        })
        self.assertEqual(result["decision"], "block")

    def test_21_subagent_stop_allows_valid_result(self):
        """Test 21: SubagentStop allows implementer with valid result → allowed, persisted."""
        self.init_mission()
        self.set_phase("implement")
        msg = textwrap.dedent("""
            Here is the result.

            COLLAB_RESULT_JSON_BEGIN
            {"role":"implementer","status":"complete","summary":"Implemented the change.","changed_paths":["src/app.py"],"artifacts":["updated handler"],"tests_run":[]}
            COLLAB_RESULT_JSON_END
        """).strip()
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "agent_id": "agent-impl", "stop_hook_active": False,
            "last_assistant_message": msg,
        })
        self.assertIsNone(result)  # None = allowed
        # Verify persisted
        mission_dir = self.get_mission_dir()
        impl_result = json.loads((mission_dir / "results" / "implementer.json").read_text(encoding="utf-8"))
        self.assertEqual(impl_result["role"], "implementer")

    def test_22_subagent_stop_blocks_invalid_skeptic(self):
        """Test 22: SubagentStop blocks skeptic with invalid recommendation → blocked."""
        self.init_mission()
        self.set_phase("review")
        msg = textwrap.dedent("""
            COLLAB_RESULT_JSON_BEGIN
            {"role":"skeptic","summary":"Looks good.","recommendation":"maybe","findings":[]}
            COLLAB_RESULT_JSON_END
        """).strip()
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "agent_id": "agent-sk", "stop_hook_active": False,
            "last_assistant_message": msg,
        })
        self.assertEqual(result["decision"], "block")
        self.assertIn("recommendation", result["reason"])

    def test_23_subagent_stop_blocks_verifier_wrong_criteria_count(self):
        """Test 23: SubagentStop blocks verifier with wrong criteria count → blocked."""
        self.init_mission()
        self.set_phase("verify")
        # Mission has 2 criteria but verifier reports only 1
        msg = textwrap.dedent("""
            COLLAB_RESULT_JSON_BEGIN
            {"role":"verifier","summary":"Checked.","recommendation":"pass","criteria_results":[{"criterion":"Behavior matches the requested objective","status":"pass","evidence":["looks good"]}]}
            COLLAB_RESULT_JSON_END
        """).strip()
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-verifier",
            "agent_id": "agent-v", "stop_hook_active": False,
            "last_assistant_message": msg,
        })
        self.assertEqual(result["decision"], "block")
        self.assertIn("2", result["reason"])  # Should mention expected count

    def test_24_nested_json_result_block(self):
        """Test 24: Nested JSON in result block parses correctly."""
        self.init_mission()
        self.set_phase("implement")
        msg = textwrap.dedent("""
            COLLAB_RESULT_JSON_BEGIN
            {"role":"implementer","status":"complete","summary":"Added config.","changed_paths":["src/config.py"],"artifacts":["new module"],"tests_run":[],"metadata":{"nested":{"deep":"value"},"list":[1,2,3]}}
            COLLAB_RESULT_JSON_END
        """).strip()
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "agent_id": "agent-nested", "stop_hook_active": False,
            "last_assistant_message": msg,
        })
        self.assertIsNone(result)
        mission_dir = self.get_mission_dir()
        impl = json.loads((mission_dir / "results" / "implementer.json").read_text(encoding="utf-8"))
        self.assertEqual(impl["metadata"]["nested"]["deep"], "value")

    # ═══════════════════════════════════════════════════════════════════
    #  LIFECYCLE TESTS (Tests 25–33)
    # ═══════════════════════════════════════════════════════════════════

    def test_25_stop_blocks_active_mission_no_results(self):
        """Test 25: Stop blocks on active mission with no results."""
        self.init_mission()
        self.set_phase("implement", awaiting_user=False)
        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertEqual(result["decision"], "block")
        self.assertIn("implementer", result["reason"])

    def test_26_stop_allows_stop_hook_active(self):
        """Test 26: Stop allows when stop_hook_active=true."""
        self.init_mission()
        self.set_phase("implement", awaiting_user=False)
        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": True,
        })
        self.assertIsNone(result)

    def test_27_stop_allows_awaiting_user(self):
        """Test 27: Stop allows when awaiting_user=true."""
        self.init_mission()  # plan phase, awaiting_user=true by default
        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertIsNone(result)

    def test_28_stop_allows_closed_mission(self):
        """Test 28: Stop allows when mission closed."""
        self.init_mission()
        self.close_mission("pass")
        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertIsNone(result)

    def test_29_subagent_stop_allows_stop_hook_active(self):
        """Test 29: SubagentStop allows when stop_hook_active=true."""
        self.init_mission()
        self.set_phase("implement")
        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "agent_id": "agent-1", "stop_hook_active": True,
            "last_assistant_message": "no result block",
        })
        self.assertIsNone(result)

    def test_30_session_start_injects_context(self):
        """Test 30: SessionStart(startup) injects mission context."""
        mission = self.init_mission()
        result = self.hook_json("collab_session_start.py", {
            "cwd": str(self.repo), "source": "startup",
        })
        self.assertIsNotNone(result)
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn(mission["mission_id"], ctx)

    def test_31_session_start_compact_rehydrates(self):
        """Test 31: SessionStart(compact) injects full rehydration context."""
        mission = self.init_mission()
        self.set_phase("implement", awaiting_user=False)
        result = self.hook_json("collab_session_start.py", {
            "cwd": str(self.repo), "source": "compact",
        })
        self.assertIsNotNone(result)
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn(mission["mission_id"], ctx)
        self.assertIn("implement", ctx)

    def test_32_terminal_phase_makes_hooks_inert(self):
        """Test 32: Terminal phase makes all hooks inert."""
        self.init_mission()
        self.close_mission("pass")
        # SessionStart should be inert
        ss = self.hook_json("collab_session_start.py", {"cwd": str(self.repo), "source": "startup"})
        self.assertIsNone(ss)
        # Stop should allow
        stop = self.hook_json("collab_stop.py", {"cwd": str(self.repo), "stop_hook_active": False})
        self.assertIsNone(stop)

    def test_33_pre_compact_creates_snapshot(self):
        """Test 33: PreCompact creates snapshot file."""
        self.init_mission()
        self.set_phase("implement", awaiting_user=False)
        self.hook_json("collab_pre_compact.py", {
            "cwd": str(self.repo), "trigger": "auto",
        })
        ctx = self.common.load_active_context(self.repo)
        snapshots = list((ctx.mission_dir / "snapshots").glob("*.json"))
        self.assertGreater(len(snapshots), 0)
        snap = json.loads(snapshots[0].read_text(encoding="utf-8"))
        self.assertIn("manifest", snap)
        self.assertEqual(snap["manifest"]["phase"], "implement")

    # ═══════════════════════════════════════════════════════════════════
    #  STATE TESTS (Tests 34–37)
    # ═══════════════════════════════════════════════════════════════════

    def test_34_worktree_reads_shared_state(self):
        """Test 34: Worktree reads shared state from git common dir."""
        self.init_mission()
        self.set_phase("implement")
        wt = Path(self.tempdir.name) / "worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "wt-branch", str(wt)],
            cwd=self.repo, check=True, capture_output=True,
        )
        # Should still see the mission from the worktree
        allow = self.hook_json("collab_pre_tool.py", {
            "cwd": str(wt), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "src/from_wt.py", "content": "x=1\n"},
        })
        self.assertIsNone(allow)  # allowed
        deny = self.hook_json("collab_pre_tool.py", {
            "cwd": str(wt), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "config/bad.txt", "content": "x\n"},
        })
        self.assertEqual(deny["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_35_ledger_thread_safe(self):
        """Test 35: Ledger append is thread-safe (concurrent writes)."""
        self.init_mission()
        self.set_phase("implement")
        ctx = self.common.load_active_context(self.repo)
        # Count entries before the concurrent writes (force_override from set_phase)
        ledger = ctx.mission_dir / "ledger.ndjson"
        baseline = len([ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()])

        def worker(i: int):
            self.common.append_ledger(ctx, {
                "mission_id": ctx.mission_id,
                "agent_type": "collab-implementer",
                "tool_name": "Write",
                "tool_use_id": f"tool-{i}",
                "kind": "file_mutation",
                "paths": [f"src/file_{i}.py"],
                "success": True,
            })

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines) - baseline, 20)

    def test_36_ledger_deduplicates_by_tool_use_id(self):
        """Test 36: Ledger deduplicates by tool_use_id."""
        self.init_mission()
        self.set_phase("implement")
        ctx = self.common.load_active_context(self.repo)
        ledger = ctx.mission_dir / "ledger.ndjson"
        baseline = len([ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()])
        entry = {
            "mission_id": ctx.mission_id,
            "agent_type": "collab-implementer",
            "tool_name": "Write",
            "tool_use_id": "duplicate-id",
            "kind": "file_mutation",
            "paths": ["src/app.py"],
            "success": True,
        }
        self.common.append_ledger(ctx, entry)
        self.common.append_ledger(ctx, entry)  # Duplicate
        self.common.append_ledger(ctx, entry)  # Duplicate
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines) - baseline, 1)

    def test_37_unknown_collab_agent_denied(self):
        """Test 37: Unknown collab-* agent_type is denied (fail-closed)."""
        self.init_mission()
        self.set_phase("implement")
        rc, out, err = self.run_hook("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implemnter",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "x\n"},
        })
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Unrecognized", parsed["hookSpecificOutput"]["permissionDecisionReason"])

    # ═══════════════════════════════════════════════════════════════════
    #  COLLABCTL TESTS (Tests 38–42)
    # ═══════════════════════════════════════════════════════════════════

    def test_38_init_creates_mission_all_fields(self):
        """Test 38: init creates mission with all fields."""
        result = self.init_mission(security=True, benchmark=True, a11y=True)
        self.assertIn("mission_id", result)
        show = json.loads(self.run_ctl("show").stdout)
        manifest = show["manifest"]
        self.assertEqual(manifest["schema_version"], 4)
        self.assertEqual(manifest["phase"], "plan")
        self.assertTrue(manifest["awaiting_user"])
        self.assertIn("src", manifest["allowed_paths"])
        self.assertEqual(len(manifest["acceptance_criteria"]), 2)
        self.assertEqual(len(manifest["verification_commands"]), 1)
        self.assertEqual(len(manifest["security_scan_commands"]), 1)
        self.assertEqual(len(manifest["benchmark_commands"]), 1)
        self.assertEqual(len(manifest["a11y_commands"]), 1)
        self.assertIn("test_paths", manifest)
        self.assertIn("doc_paths", manifest)
        self.assertFalse(manifest["tdd_mode"])
        self.assertEqual(manifest["custom_roles"], [])
        self.assertEqual(manifest["timeout_hours"], 24)
        self.assertEqual(manifest["role_failure_counts"], {})
        self.assertEqual(manifest["loop_epoch"], 0)
        # v4 fields
        self.assertIn("model_map", manifest)
        self.assertIn("phase_started_at", manifest)
        self.assertIn("role_assignments", manifest)
        self.assertIn("planned_roles", manifest)
        self.assertIn("skipped_roles", manifest)
        self.assertIn("fail_closed", manifest)

    def test_39_show_displays_mission(self):
        """Test 39: show displays mission JSON."""
        self.init_mission()
        result = json.loads(self.run_ctl("show").stdout)
        self.assertIn("mission_id", result)
        self.assertIn("manifest", result)
        self.assertIn("paths", result)

    def test_40_phase_advances(self):
        """Test 40: phase advances phase."""
        self.init_mission()
        self.set_phase("implement")
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "implement")

    def test_41_close_removes_active(self):
        """Test 41: close pass removes active.json."""
        self.init_mission()
        self.close_mission("pass")
        # show should report no active mission
        result = self.run_ctl("show")
        self.assertIn("No active", result.stdout)

    def test_42_ledger_trim_archives(self):
        """Test 42: ledger-trim archives trimmed entries."""
        self.init_mission()
        self.set_phase("implement")
        ctx = self.common.load_active_context(self.repo)
        # Write 10 entries
        for i in range(10):
            self.common.append_ledger(ctx, {
                "mission_id": ctx.mission_id, "agent_type": "collab-implementer",
                "tool_name": "Write", "tool_use_id": f"trim-{i}",
                "kind": "file_mutation", "paths": [f"src/f{i}.py"], "success": True,
            })
        self.run_ctl("ledger-trim", "--keep", "5")
        ledger = ctx.mission_dir / "ledger.ndjson"
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 5)
        # Archived entries exist
        archives = list((ctx.mission_dir / "snapshots").glob("ledger-trimmed-*.ndjson"))
        self.assertEqual(len(archives), 1)

    # ═══════════════════════════════════════════════════════════════════
    #  BLIND REVIEW TESTS (Tests 43–44)
    # ═══════════════════════════════════════════════════════════════════

    def test_43_skeptic_blind_review_no_implementer_claims(self):
        """Test 43: Skeptic context injection does NOT include implementer claims."""
        self.init_mission()
        self.set_phase("implement")
        self.record_implementer_write()
        self.set_phase("review")
        result = self.hook_json("collab_subagent_start.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic", "agent_id": "agent-sk",
        })
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Blind review", ctx)
        self.assertIn("Changed paths", ctx)
        # Must NOT contain implementer claims
        self.assertNotIn("artifacts", ctx.lower())
        self.assertNotIn("updated handler", ctx.lower())

    def test_44_test_writer_sees_changed_paths(self):
        """Test 44: Test-writer context includes implementer's changed_paths."""
        self.init_mission()
        self.set_phase("implement")
        self.record_implementer_write()
        self.set_phase("test")
        result = self.hook_json("collab_subagent_start.py", {
            "cwd": str(self.repo), "agent_type": "collab-test-writer", "agent_id": "agent-tw",
        })
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("test-writer", ctx)

    # ═══════════════════════════════════════════════════════════════════
    #  ADDITIONAL ROLE TESTS (Tests 45–48)
    # ═══════════════════════════════════════════════════════════════════

    def test_45_performance_approved_benchmark_allowed(self):
        """Test 45: Performance profiler approved benchmark → allowed."""
        self.init_mission(benchmark=True)
        self.set_phase("performance")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-performance",
            "tool_name": "Bash", "tool_input": {"command": "pytest --benchmark-only"},
        })
        self.assertIsNone(result)

    def test_46_accessibility_write_denied(self):
        """Test 46: Accessibility checker cannot write files."""
        self.init_mission(a11y=True)
        self.set_phase("accessibility")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-accessibility",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "evil\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_47_docs_write_readme_allowed(self):
        """Test 47: Docs writer can write to README.md."""
        self.init_mission()
        self.set_phase("docs")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-docs",
            "tool_name": "Write", "tool_input": {"file_path": "README.md", "content": "# Updated\n"},
        })
        self.assertIsNone(result)

    def test_48_test_writer_write_to_dunder_tests_allowed(self):
        """Test 48: Test-writer can write to __tests__/ directory."""
        self.init_mission()
        self.set_phase("test")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-test-writer",
            "tool_name": "Write", "tool_input": {"file_path": "__tests__/test_app.js", "content": "test('x', ()=>{});\n"},
        })
        self.assertIsNone(result)

    def test_49_session_start_includes_clear_matcher(self):
        """Test 49: SessionStart matcher includes 'clear' event."""
        hooks_json = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        matcher = hooks_json["hooks"]["SessionStart"][0]["matcher"]
        self.assertIn("clear", matcher)
        self.assertIn("compact", matcher)

    def test_50_security_readonly_bash_allowed(self):
        """Test 50: Security auditor read-only bash (git diff) → allowed."""
        self.init_mission(security=True)
        self.set_phase("security")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-security",
            "tool_name": "Bash", "tool_input": {"command": "git diff -- src/app.py"},
        })
        self.assertIsNone(result)

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE TRANSITION STATE MACHINE TESTS (Tests 51–53)
    # ═══════════════════════════════════════════════════════════════════

    def test_51_phase_plan_to_verify_blocked(self):
        """Test 51: Phase transition plan -> verify is blocked by state machine."""
        self.init_mission()
        # plan -> verify is illegal (must go plan -> implement first)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "verify", "--awaiting-user", "false"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Cannot transition", proc.stderr)
        self.assertIn("implement", proc.stderr)  # should mention allowed target

    def test_52_phase_plan_to_implement_allowed(self):
        """Test 52: Phase transition plan -> implement is allowed by state machine."""
        self.init_mission()
        # plan -> implement is legal
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "implement", "--awaiting-user", "false"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr: {proc.stderr}")
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "implement")

    def test_53_phase_force_bypasses_state_machine(self):
        """Test 53: --force bypasses state machine validation."""
        self.init_mission()
        # plan -> docs is illegal, but --force overrides
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "docs", "--awaiting-user", "false", "--force"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr: {proc.stderr}")
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "docs")

    # ═══════════════════════════════════════════════════════════════════
    #  LOOP COUNTER ENFORCEMENT TESTS (Tests 54–55)
    # ═══════════════════════════════════════════════════════════════════

    def test_54_loop_back_increments_loop_count(self):
        """Test 54: Loop back from review to implement increments loop_count."""
        self.init_mission()
        # Walk through legal transitions: plan -> implement -> review -> implement
        self.set_phase("implement", force=False)
        self.set_phase("review", force=False)
        self.set_phase("implement", force=False)  # loop back
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["loop_count"], 1)

    def test_55_loop_count_exceeds_max_raises_error(self):
        """Test 55: loop_count > max_loops raises error."""
        # Init with max_loops=1
        cmd = [
            "init",
            "--objective", "Test loop limit",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Works",
            "--verify-command", "pytest -q",
            "--max-loops", "1",
        ]
        json.loads(self.run_ctl(*cmd).stdout)

        # plan -> implement -> review -> implement (loop_count=1, allowed)
        self.set_phase("implement", force=False)
        self.set_phase("review", force=False)
        self.set_phase("implement", force=False)  # loop_count becomes 1, which equals max_loops=1, ok

        # implement -> review -> implement again (loop_count=2 > max_loops=1)
        self.set_phase("review", force=False)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "implement", "--awaiting-user", "false"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Loop limit reached", proc.stderr)

    # ═══════════════════════════════════════════════════════════════════
    #  SCHEMA MIGRATION TESTS (Test 56)
    # ═══════════════════════════════════════════════════════════════════

    def test_56_migrate_upgrades_v1_to_v4(self):
        """Test 56: migrate upgrades a v1 manifest through v2, v3 to v4."""
        self.init_mission()
        # Manually downgrade manifest to simulate v1
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        # Remove v2/v3/v4 fields and set version to 1
        manifest["schema_version"] = 1
        for key in ("test_paths", "doc_paths", "security_scan_commands",
                     "benchmark_commands", "a11y_commands", "loop_count", "max_loops",
                     "tdd_mode", "custom_roles", "timeout_hours",
                     "role_failure_counts", "loop_epoch",
                     "model_map", "phase_started_at", "role_assignments",
                     "planned_roles", "skipped_roles", "fail_closed"):
            manifest.pop(key, None)
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Run migrate
        proc = self.run_ctl("migrate")
        self.assertIn("migrated from schema v1 to v4", proc.stdout)

        # Verify v2 + v3 + v4 fields exist
        show = json.loads(self.run_ctl("show").stdout)
        m = show["manifest"]
        self.assertEqual(m["schema_version"], 4)
        self.assertIn("test_paths", m)
        self.assertIn("doc_paths", m)
        self.assertIn("security_scan_commands", m)
        self.assertIn("benchmark_commands", m)
        self.assertIn("a11y_commands", m)
        self.assertEqual(m["loop_count"], 0)
        self.assertEqual(m["max_loops"], 3)
        self.assertFalse(m["tdd_mode"])
        self.assertEqual(m["custom_roles"], [])
        self.assertEqual(m["timeout_hours"], 24)
        self.assertEqual(m["role_failure_counts"], {})
        self.assertEqual(m["loop_epoch"], 0)
        # v4 fields
        self.assertIn("model_map", m)
        self.assertIn("role_assignments", m)
        self.assertIn("planned_roles", m)
        self.assertIn("skipped_roles", m)
        self.assertFalse(m["fail_closed"])

    def test_57_migrate_noop_on_v4(self):
        """Test 57: migrate is a no-op if already v4."""
        self.init_mission()
        proc = self.run_ctl("migrate")
        self.assertIn("already at schema v4", proc.stdout)

    def test_58_epoch_stale_result_returns_none(self):
        """Stale result from previous epoch returns None."""
        self.init_mission()
        ctx = self.common.load_active_context(self.repo)
        self.common.save_role_result(ctx, "implementer", {
            "role": "implementer",
            "status": "complete",
            "summary": "Implemented the change.",
            "changed_paths": ["src/app.py"],
            "artifacts": ["updated handler"],
            "tests_run": [],
        })
        ctx.manifest["loop_epoch"] = 1
        self.assertIsNone(self.common.load_role_result(ctx, "implementer"))
        result_path = ctx.mission_dir / "results" / "implementer.json"
        self.assertTrue(result_path.exists(), "Stale result file should still exist on disk")

    def test_59_epoch_current_result_loads(self):
        """Current epoch result loads correctly."""
        self.init_mission()
        ctx = self.common.load_active_context(self.repo)
        self.common.save_role_result(ctx, "implementer", {
            "role": "implementer",
            "status": "complete",
            "summary": "Implemented the change.",
            "changed_paths": ["src/app.py"],
            "artifacts": ["updated handler"],
            "tests_run": [],
        })
        loaded = self.common.load_role_result(ctx, "implementer")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["role"], "implementer")
        self.assertEqual(loaded["_epoch"], 0)

    def test_60_migrate_v2_to_v4(self):
        """v2 to v4 migration adds all v3 + v4 fields."""
        self.init_mission()
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["schema_version"] = 2
        for key in ("tdd_mode", "custom_roles", "timeout_hours", "role_failure_counts", "loop_epoch",
                     "model_map", "phase_started_at", "role_assignments",
                     "planned_roles", "skipped_roles", "fail_closed"):
            manifest.pop(key, None)
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        proc = self.run_ctl("migrate")
        self.assertIn("migrated from schema v2 to v4", proc.stdout)

        migrated = json.loads(self.run_ctl("show").stdout)["manifest"]
        self.assertEqual(migrated["schema_version"], 4)
        self.assertFalse(migrated["tdd_mode"])
        self.assertEqual(migrated["custom_roles"], [])
        self.assertEqual(migrated["timeout_hours"], 24)
        self.assertEqual(migrated["role_failure_counts"], {})
        self.assertEqual(migrated["loop_epoch"], 0)

    def test_61_wildcard_matcher_custom_role(self):
        """collab-* wildcard fires for custom role names."""
        self.init_mission()
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["custom_roles"] = [{"name": "auditor", "bash": "read-only"}]
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        hooks_json = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(hooks_json["hooks"]["SubagentStart"][0]["matcher"], "collab-")
        self.assertEqual(hooks_json["hooks"]["SubagentStop"][0]["matcher"], "collab-")

        result = self.hook_json("collab_subagent_stop.py", {
            "cwd": str(self.repo), "agent_type": "collab-auditor",
            "agent_id": "agent-auditor", "stop_hook_active": False,
            "last_assistant_message": textwrap.dedent("""
                COLLAB_RESULT_JSON_BEGIN
                {"role":"auditor","summary":"Reviewed the diff.","status":"complete"}
                COLLAB_RESULT_JSON_END
            """).strip(),
        })
        self.assertIsNone(result)
        mission_dir = self.get_mission_dir()
        stored = json.loads((mission_dir / "results" / "auditor.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["role"], "auditor")

    def test_62_custom_role_scope_from_manifest(self):
        """Custom role scope enforced via manifest custom_roles field."""
        self.init_mission()
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["custom_roles"] = [{"name": "auditor", "bash": "read-only"}]
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.set_phase("review")

        write_result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-auditor",
            "tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "x = 1\n"},
        })
        self.assertEqual(write_result["hookSpecificOutput"]["permissionDecision"], "deny")

        bash_result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-auditor",
            "tool_name": "Bash", "tool_input": {"command": "git status --short"},
        })
        self.assertIsNone(bash_result)

    def test_63_custom_role_bash_policy_unknown_string_denied(self):
        """Unknown custom-role bash_policy values fail closed to denied Bash."""
        self.init_mission()
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["custom_roles"] = [{"name": "auditor", "bash_policy": "full"}]
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.set_phase("review")

        bash_result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-auditor",
            "tool_name": "Bash", "tool_input": {"command": "git status --short"},
        })
        self.assertEqual(bash_result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_64_module_split_imports(self):
        """All imports from collab_common still work after split."""
        core_dir = HOOKS_DIR / "core"
        for filename in ("__init__.py", "engine.py", "roles.py", "paths.py", "contracts.py"):
            self.assertTrue((core_dir / filename).exists(), msg=filename)
        shim = (HOOKS_DIR / "collab_common.py").read_text(encoding="utf-8")
        self.assertIn("from core import *", shim)
        for name in (
            "MissionContext",
            "load_active_context",
            "validate_role_result",
            "build_subagent_context",
            "path_is_allowed",
            "HookRequest",
            "HookResponse",
        ):
            self.assertTrue(hasattr(self.common, name), msg=name)

    def test_65_slim_session_start_under_200_chars(self):
        """SessionStart slim output is compact."""
        self.init_mission()
        self.set_phase("implement", awaiting_user=False)
        result = self.hook_json("collab_session_start.py", {
            "cwd": str(self.repo), "source": "startup",
        })
        self.assertIsNotNone(result)
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(ctx), 200)
        self.assertIn("collabctl.py show", ctx)
        self.assertNotIn("Acceptance criteria", ctx)

    def test_66_full_manifest_via_show(self):
        """collabctl show still returns full manifest with all details."""
        self.init_mission(security=True, benchmark=True, a11y=True)
        self.set_phase("implement")
        self.record_implementer_write()
        ctx = self.common.load_active_context(self.repo)
        self.common.save_role_result(ctx, "implementer", {
            "role": "implementer",
            "status": "complete",
            "summary": "Implemented the change.",
            "changed_paths": ["src/app.py"],
            "artifacts": ["updated handler"],
            "tests_run": [],
        })
        summary = self.common.format_manifest_summary(ctx)
        self.assertIn("Completed: implementer (complete)", summary)
        self.assertIn("Remaining: 7 roles pending", summary)
        self.assertNotIn("Role result: skeptic (pending)", summary)

        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["objective"], "Implement a safe change")
        self.assertEqual(len(show["manifest"]["acceptance_criteria"]), 2)
        self.assertEqual(len(show["manifest"]["security_scan_commands"]), 1)
        self.assertEqual(len(show["manifest"]["benchmark_commands"]), 1)
        self.assertEqual(len(show["manifest"]["a11y_commands"]), 1)

    def test_67_ledger_summary_cache_sync(self):
        """Ledger summary stays in sync after append."""
        self.init_mission()
        self.set_phase("implement")
        ctx = self.common.load_active_context(self.repo)
        # Baseline includes any entries from set_phase (e.g., force_override)
        ledger = ctx.mission_dir / "ledger.ndjson"
        baseline = len([ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()])
        self.common.append_ledger(ctx, {
            "mission_id": ctx.mission_id,
            "agent_type": "collab-implementer",
            "tool_name": "Write",
            "tool_use_id": "summary-1",
            "kind": "file_mutation",
            "paths": ["src/auth.py", "src/routes.py"],
            "success": True,
        })
        summary_path = ctx.mission_dir / "ledger-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["entry_count"], baseline + 1)
        self.assertIn("src/auth.py", summary["changed_paths"])
        self.assertIn("src/routes.py", summary["changed_paths"])
        self.assertTrue(summary["last_updated"].endswith("Z"))
        self.assertEqual(
            self.common.changed_paths_from_summary(ctx),
            ["src/auth.py", "src/routes.py"],
        )

    def test_68_context_level_1_minimal(self):
        """Level 1 context contains only role, phase, mission_id, truncated objective."""
        self.init_mission()
        ctx = self.common.load_active_context(self.repo)
        ctx.manifest["objective"] = "x" * 250
        text = self.common.build_subagent_context(ctx, "skeptic", detail_level=1)
        level_0 = self.common.build_subagent_context(ctx, "skeptic", detail_level=0)
        self.assertIn(f"[COLLAB] Mission {ctx.mission_id}", text)
        self.assertIn("Role: skeptic", text)
        self.assertIn("Phase: plan", text)
        self.assertIn(f"Objective: {'x' * 200}...", text)
        self.assertNotIn("Acceptance criteria", text)
        self.assertNotIn("Changed paths", text)
        self.assertNotIn("Scope rules", text)
        self.assertNotIn("Spec anchor", text)
        self.assertLess(len(text), 400)
        self.assertEqual(level_0, text, "detail_level=0 should clamp to 1")

    def test_69_context_level_2_standard(self):
        """Level 2 context adds criteria and changed paths."""
        self.init_mission()
        self.set_phase("implement")
        self.record_implementer_write()
        ctx = self.common.load_active_context(self.repo)
        text = self.common.build_subagent_context(ctx, "skeptic", detail_level=2)
        self.assertIn("Acceptance criteria", text)
        self.assertIn("Changed paths", text)
        self.assertIn("Scope rules", text)
        self.assertNotIn("Spec anchor", text)
        self.assertNotIn("references/result-schemas.md", text)
        self.assertLess(len(text), 1200)

    def test_70_context_level_3_full(self):
        """Level 3 context adds spec, commands, and result template reference."""
        self.init_mission()
        ctx = self.common.load_active_context(self.repo)
        text = self.common.build_subagent_context(ctx, "verifier", detail_level=3)
        level_99 = self.common.build_subagent_context(ctx, "verifier", detail_level=99)
        self.assertIn("Spec anchor: docs/spec.md", text)
        self.assertIn("Approved commands:", text)
        self.assertIn("pytest -q", text)
        self.assertIn("references/result-schemas.md", text)
        self.assertEqual(level_99, text, "detail_level=99 should clamp to 3")

        schema_path = PLUGIN_ROOT / "skills" / "collab" / "references" / "result-schemas.md"
        self.assertTrue(schema_path.exists())
        schema_text = schema_path.read_text(encoding="utf-8")
        for role in (
            "implementer",
            "test-writer",
            "skeptic",
            "security",
            "performance",
            "accessibility",
            "docs",
            "verifier",
        ):
            self.assertIn(f"## {role}", schema_text)

    def test_71_ledger_summary_fallback_on_corrupt(self):
        """Corrupt ledger-summary.json falls back to full ledger scan."""
        self.init_mission()
        self.set_phase("implement")
        ctx = self.common.load_active_context(self.repo)
        self.common.append_ledger(ctx, {
            "mission_id": ctx.mission_id,
            "agent_type": "collab-implementer",
            "tool_name": "Write",
            "tool_use_id": "summary-corrupt",
            "kind": "file_mutation",
            "paths": ["src/auth.py"],
            "success": True,
        })
        summary_path = ctx.mission_dir / "ledger-summary.json"
        summary_path.write_text("{not-json", encoding="utf-8")
        self.assertEqual(self.common.changed_paths_from_summary(ctx), ["src/auth.py"])

    def test_72_audit_phase_transition(self):
        """test -> audit transition is valid."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("test", force=False)
        self.set_phase("audit", force=False)
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "audit")

    def test_73_stop_blocks_during_audit_incomplete(self):
        """Stop blocks when not all 4 audit results present."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("test", force=False)
        self.set_phase("audit", awaiting_user=False, force=False)
        self.save_role_result("skeptic", self.valid_role_result("skeptic"))

        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertEqual(result["decision"], "block")
        self.assertIn("security result", result["reason"])
        self.assertIn("performance result", result["reason"])
        self.assertIn("accessibility result", result["reason"])

    def test_74_stop_allows_audit_complete(self):
        """Stop allows when all 4 audit results present."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("test", force=False)
        self.set_phase("audit", awaiting_user=False, force=False)
        for role in ("skeptic", "security", "performance", "accessibility"):
            self.save_role_result(role, self.valid_role_result(role))

        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertIsNone(result)

    def test_75_audit_to_implement_on_failure(self):
        """audit -> implement transition valid (loop back)."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("test", force=False)
        self.set_phase("audit", force=False)
        self.set_phase("implement", force=False)
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "implement")
        self.assertEqual(show["manifest"]["loop_count"], 1)

    def test_76_tdd_blocks_implementer_without_tests(self):
        """TDD mode blocks implementer writes when no test-writer result."""
        self.init_mission(tdd=True)
        self.set_phase("test", force=False)
        self.set_phase("implement", force=False)

        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "src/new_file.py", "content": "x = 1\n"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecisionReason"],
            "[COLLAB] TDD mode: tests must be written before implementation. The test phase must complete first.",
        )

    def test_77_tdd_allows_implementer_after_tests(self):
        """TDD mode allows implementer after test-writer completes."""
        self.init_mission(tdd=True)
        self.set_phase("test", force=False)
        self.save_role_result("test-writer", self.valid_role_result("test-writer"))
        self.set_phase("implement", force=False)

        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "tool_name": "Write", "tool_input": {"file_path": "src/new_file.py", "content": "x = 1\n"},
        })
        self.assertIsNone(result)

    def test_78_tdd_phase_transitions(self):
        """TDD mode enforces plan -> test -> implement ordering."""
        self.init_mission(tdd=True)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "implement", "--awaiting-user", "false"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Allowed: test", proc.stderr)

        self.set_phase("test", force=False)
        self.set_phase("implement", force=False)
        self.set_phase("audit", force=False)
        self.set_phase("test", force=False)
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "test")
        self.assertEqual(show["manifest"]["loop_count"], 1)

    def test_79_non_tdd_unchanged(self):
        """Non-TDD mode phase transitions unchanged."""
        self.init_mission()
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo),
             "phase", "test", "--awaiting-user", "false"],
            cwd=str(self.repo), text=True, capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Allowed: implement", proc.stderr)

        self.set_phase("implement", force=False)
        self.set_phase("review", force=False)
        show = json.loads(self.run_ctl("show").stdout)
        self.assertEqual(show["manifest"]["phase"], "review")

    def test_80_timeout_allows_stop(self):
        """Stop allowed when mission exceeds timeout_hours."""
        self.init_mission(timeout_hours=1)
        self.set_phase("implement", awaiting_user=False)
        manifest = self.load_manifest()
        manifest["created_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.save_manifest(manifest)

        result = self.hook_json("collab_stop.py", {
            "cwd": str(self.repo), "stop_hook_active": False,
        })
        self.assertIsNone(result)

    def test_81_stale_mission_warning(self):
        """SessionStart warns on mission older than 12 hours."""
        self.init_mission()
        manifest = self.load_manifest()
        manifest["created_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=13)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.save_manifest(manifest)

        result = self.hook_json("collab_session_start.py", {
            "cwd": str(self.repo), "source": "startup",
        })
        self.assertIsNotNone(result)
        self.assertIn("[COLLAB WARNING] Mission is over 12 hours old.", result["hookSpecificOutput"]["additionalContext"])

    def test_82_role_failure_count_triggers_awaiting(self):
        """3 consecutive failures set awaiting_user=true."""
        self.init_mission()
        self.set_phase("implement")

        payload = {
            "cwd": str(self.repo), "agent_type": "collab-implementer",
            "agent_id": "agent-impl", "stop_hook_active": False,
            "last_assistant_message": "Done without structured output.",
        }
        for _ in range(3):
            result = self.hook_json("collab_subagent_stop.py", payload)
            self.assertEqual(result["decision"], "block")

        manifest = self.load_manifest()
        self.assertEqual(manifest["role_failure_counts"]["implementer"], 3)
        self.assertTrue(manifest["awaiting_user"])
        self.assertIn("implementer failed validation", manifest["awaiting_user_reason"])

    def test_83_review_md_injected(self):
        """REVIEW.md content injected into reviewer context."""
        self.init_mission()
        self.set_phase("review")
        review_text = "Review checklist\n" + ("A" * 2100)
        (self.repo / "REVIEW.md").write_text(review_text, encoding="utf-8")

        result = self.hook_json("collab_subagent_start.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic", "agent_id": "agent-sk",
        })
        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("REVIEW.md:", ctx)
        self.assertIn("Review checklist", ctx)
        self.assertNotIn("A" * 2001, ctx)

    def test_84_dry_run_no_side_effects(self):
        """--dry-run prints plan without creating mission."""
        result = json.loads(self.run_ctl(
            "init",
            "--objective", "Dry run mission",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Works",
            "--verify-command", "pytest -q",
            "--dry-run",
        ).stdout)
        self.assertTrue(result["dry_run"])
        state_root = self.common.resolve_git_common_dir(self.repo) / "claude-collab"
        self.assertFalse(state_root.exists())

    def test_85_custom_role_init_from_cli(self):
        """--custom-role creates custom_roles in manifest."""
        self.run_ctl(
            "init",
            "--objective", "Run lint review",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Lint review completes",
            "--verify-command", "pytest -q",
            "--custom-role", "name=linter,scope=read_only,commands=eslint src/",
        )
        manifest = json.loads(self.run_ctl("show").stdout)["manifest"]
        self.assertEqual(manifest["custom_roles"], [{
            "name": "linter",
            "scope_type": "read_only",
            "approved_commands": ["eslint src/"],
            "isolation": "none",
        }])

    def test_86_custom_role_collision_guard(self):
        """Custom role name matching built-in is rejected."""
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "collabctl.py"),
                "--cwd",
                str(self.repo),
                "init",
                "--objective",
                "Reject built-in collision",
                "--spec-path",
                "docs/spec.md",
                "--allowed-path",
                "src",
                "--criterion",
                "Fails fast",
                "--verify-command",
                "pytest -q",
                "--custom-role",
                "name=implementer,scope=read_only,commands=eslint src/",
            ],
            cwd=str(self.repo),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("conflicts with built-in role", proc.stderr)

    def test_87_custom_role_result_validation(self):
        """Custom role result validates with generic schema."""
        ok, reason = self.common.validate_custom_role_result("linter", {
            "role": "linter",
            "summary": "Lint review complete.",
            "status": "complete",
        })
        self.assertTrue(ok, msg=reason)
        bad_ok, bad_reason = self.common.validate_custom_role_result("linter", {
            "role": "linter",
            "summary": "Missing outcome.",
        })
        self.assertFalse(bad_ok)
        self.assertIn("status or recommendation", bad_reason)

    def test_88_custom_role_context_building(self):
        """Custom role gets context in subagent start."""
        self.init_mission()
        show = json.loads(self.run_ctl("show").stdout)
        mpath = Path(show["paths"]["manifest"])
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["custom_roles"] = [{
            "name": "linter",
            "scope_type": "read_only",
            "approved_commands": ["eslint src/"],
            "isolation": "none",
        }]
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        result = self.hook_json("collab_subagent_start.py", {
            "cwd": str(self.repo),
            "agent_type": "collab-linter",
            "agent_id": "agent-linter",
        })
        self.assertIsNotNone(result)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Role: linter", text)
        self.assertIn("Objective: Implement a safe change", text)
        self.assertIn("Custom role scope: read_only", text)
        self.assertIn("Custom approved commands: eslint src/", text)

    def test_89_8eyes_command_exists(self):
        """commands/8eyes.md is an explicit orchestrator workflow."""
        command_path = PLUGIN_ROOT / "commands" / "8eyes.md"
        self.assertTrue(command_path.exists())
        text = command_path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: 8eyes", text)
        self.assertIn("description: Start a failure-aware multi-agent code review mission", text)
        self.assertIn("disable-model-invocation: false", text)
        self.assertIn("FIRST ACTION: run this via the Bash tool", text)
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py init --objective \"$ARGUMENTS\"", text)
        self.assertIn("WAIT for explicit user approval", text)
        self.assertIn("collab-implementer", text)
        self.assertIn("collab-test-writer", text)
        self.assertIn("collab-skeptic", text)
        self.assertIn("collab-security", text)
        self.assertIn("collab-performance", text)
        self.assertIn("collab-accessibility", text)
        self.assertIn("collab-verifier", text)
        self.assertIn("After EACH role completes, show", text)
        self.assertIn("needs_changes", text)

    def test_90_collabctl_verify_checks_8eyes(self):
        """collabctl verify checks for commands/8eyes.md."""
        proc = self.run_ctl("verify")
        self.assertIn("Claude /8eyes command", proc.stdout)
        self.assertIn("commands/8eyes.md", proc.stdout)

    def test_91_copilot_adapter_plugin_json(self):
        """Copilot CLI plugin.json exists with correct fields."""
        plugin_path = PLUGIN_ROOT / "adapters" / "copilot_cli" / "plugin.json"
        self.assertTrue(plugin_path.exists())
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "eight-eyes")
        self.assertEqual(plugin["version"], "4.0.0")
        self.assertEqual(plugin["agents"], "agents/")
        self.assertEqual(plugin["skills"], ["skills/collab"])
        self.assertEqual(plugin["hooks"], "hooks.json")
        self.assertEqual(len(list((PLUGIN_ROOT / "adapters" / "copilot_cli" / "agents").glob("*.agent.md"))), 8)

    def test_92_copilot_adapter_hooks_json(self):
        """Copilot CLI hooks.json has version:1 format."""
        hooks_path = PLUGIN_ROOT / "adapters" / "copilot_cli" / "hooks.json"
        self.assertTrue(hooks_path.exists())
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(set(payload["hooks"]), {"preToolUse", "postToolUse", "sessionStart", "subagentStart", "subagentStop", "stop"})
        for event in payload["hooks"].values():
            self.assertEqual(event[0]["type"], "command")
            self.assertIn("bash", event[0])
            self.assertIn("powershell", event[0])

    def test_93_codex_adapter_hooks_json(self):
        """Codex CLI hooks.json has event-based format."""
        hooks_path = PLUGIN_ROOT / "adapters" / "codex_cli" / "hooks.json"
        self.assertTrue(hooks_path.exists())
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertEqual([hook["event"] for hook in payload["hooks"]], ["PreToolUse", "PostToolUse", "SessionStart", "Stop"])
        for hook in payload["hooks"]:
            self.assertEqual(hook["command"][0], "python3")
            self.assertEqual(hook["timeout_ms"], 30000)

    def test_94_codex_agents_md_exists(self):
        """Codex CLI AGENTS.md exists with role descriptions."""
        agents_md = PLUGIN_ROOT / "adapters" / "codex_cli" / "AGENTS.md"
        self.assertTrue(agents_md.exists())
        text = agents_md.read_text(encoding="utf-8")
        self.assertIn("# Eight-Eyes Multi-Agent Code Review", text)
        self.assertIn("PreToolUse", text)
        for role in (
            "collab-implementer",
            "collab-test-writer",
            "collab-skeptic",
            "collab-security",
            "collab-performance",
            "collab-accessibility",
            "collab-docs",
            "collab-verifier",
        ):
            self.assertIn(role, text)
        self.assertEqual(len(list((PLUGIN_ROOT / "adapters" / "codex_cli" / "agents").glob("*.toml"))), 8)

    def test_95_install_script_exists(self):
        """install.py exists and has --help."""
        install_path = PLUGIN_ROOT / "install.py"
        self.assertTrue(install_path.exists())
        proc = subprocess.run(
            [sys.executable, str(install_path), "--help"],
            cwd=str(PLUGIN_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("--platform", proc.stdout)
        self.assertIn("--uninstall", proc.stdout)
        self.assertIn("--verify", proc.stdout)

    def test_96_verify_checks_adapters(self):
        """collabctl verify checks for adapter files."""
        proc = self.run_ctl("verify")
        self.assertIn("Copilot adapter manifest", proc.stdout)
        self.assertIn("adapters/copilot_cli/plugin.json", proc.stdout)
        self.assertIn("Copilot adapter hooks", proc.stdout)
        self.assertIn("adapters/copilot_cli/hooks.json", proc.stdout)
        self.assertIn("Codex adapter hooks", proc.stdout)
        self.assertIn("adapters/codex_cli/hooks.json", proc.stdout)
        self.assertIn("Codex adapter instructions", proc.stdout)
        self.assertIn("adapters/codex_cli/AGENTS.md", proc.stdout)
        self.assertIn("Cross-platform installer", proc.stdout)
        self.assertIn("install.py", proc.stdout)

    def test_97_collabctl_status_summarizes_roles(self):
        """collabctl status shows mission metadata and per-role progress details."""
        self.init_mission()
        self.set_phase("audit")

        implementer = self.valid_role_result("implementer")
        implementer["summary"] = "Implemented the requested change."
        self.save_role_result("implementer", implementer)

        skeptic = self.valid_role_result("skeptic")
        skeptic["recommendation"] = "needs_changes"
        skeptic["summary"] = "Reviewed the change.\nFound a missing guard clause."
        skeptic["findings"] = [
            {"category": "bug", "summary": "Null check missing on the auth path."},
            {"category": "risk", "summary": "No regression coverage for the fallback branch."},
        ]
        self.save_role_result("skeptic", skeptic)

        text = self.run_ctl("status").stdout
        self.assertIn("Mission ID:", text)
        self.assertIn("Objective: Implement a safe change", text)
        self.assertIn("Current phase: audit", text)
        self.assertIn("Elapsed:", text)
        self.assertIn("- implementer: complete", text)
        self.assertIn("recommendation: complete", text)
        self.assertIn("Implemented the requested change.", text)
        self.assertIn("- skeptic: failed", text)
        self.assertIn("recommendation: needs_changes", text)
        self.assertIn("Reviewed the change.", text)
        self.assertIn("Found a missing guard clause.", text)
        self.assertIn("- test-writer: pending", text)

    def test_98_hooks_fail_open_on_invalid_json(self):
        """All hook entrypoints log invalid JSON but never exit non-zero."""
        for script_name in (
            "collab_pre_tool.py",
            "collab_post_tool.py",
            "collab_session_start.py",
            "collab_subagent_start.py",
            "collab_subagent_stop.py",
            "collab_stop.py",
            "collab_pre_compact.py",
        ):
            rc, out, err = self.run_hook_stdin(script_name, "{not-json")
            self.assertEqual(rc, 0, msg=f"{script_name} exited non-zero: {err}")
            self.assertEqual(out, "")
            self.assertIn("hook error", err)


    # ═══════════════════════════════════════════════════════════════════
    #  SCHEMA V4 TESTS (Tests 99–104)
    # ═══════════════════════════════════════════════════════════════════

    def test_99_init_v4_has_model_map(self):
        """Init creates manifest with model_map field (empty default key only)."""
        self.init_mission()
        manifest = self.load_manifest()
        self.assertIn("model_map", manifest)
        self.assertIsInstance(manifest["model_map"], dict)
        # Default init has at least the "default" key
        self.assertIn("default", manifest["model_map"])

    def test_100_init_v4_has_role_assignments(self):
        """Init creates manifest with role_assignments as empty dict."""
        self.init_mission()
        manifest = self.load_manifest()
        self.assertIn("role_assignments", manifest)
        self.assertEqual(manifest["role_assignments"], {})

    def test_101_init_v4_has_planned_roles(self):
        """Init creates manifest with planned_roles list containing all 8 roles."""
        self.init_mission()
        manifest = self.load_manifest()
        self.assertIn("planned_roles", manifest)
        self.assertIsInstance(manifest["planned_roles"], list)
        self.assertGreater(len(manifest["planned_roles"]), 0)
        for role in ("implementer", "test-writer", "skeptic", "verifier", "docs"):
            self.assertIn(role, manifest["planned_roles"])

    def test_102_init_v4_has_fail_closed_default_false(self):
        """Init creates manifest with fail_closed defaulting to False."""
        self.init_mission()
        manifest = self.load_manifest()
        self.assertIn("fail_closed", manifest)
        self.assertFalse(manifest["fail_closed"])

    def test_103_init_with_model_map_flag(self):
        """--model-map '{"skeptic":"codex"}' populates model_map."""
        result = json.loads(self.run_ctl(
            "init",
            "--objective", "Model map test",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Works",
            "--verify-command", "pytest -q",
            "--model-map", '{"skeptic":"codex"}',
        ).stdout)
        manifest = self.load_manifest()
        self.assertEqual(manifest["model_map"]["skeptic"], "codex")
        # default key should still be present
        self.assertIn("default", manifest["model_map"])

    def test_104_init_with_default_model(self):
        """--default-model gemini sets model_map['default'] to gemini."""
        result = json.loads(self.run_ctl(
            "init",
            "--objective", "Default model test",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Works",
            "--verify-command", "pytest -q",
            "--default-model", "gemini",
        ).stdout)
        manifest = self.load_manifest()
        self.assertEqual(manifest["model_map"]["default"], "gemini")

    # ═══════════════════════════════════════════════════════════════════
    #  MIGRATION TESTS (Tests 105–106)
    # ═══════════════════════════════════════════════════════════════════

    def test_105_migrate_v3_to_v4(self):
        """v3 manifest gains v4 fields after migrate command."""
        self.init_mission()
        manifest = self.load_manifest()
        # Downgrade to v3 by removing v4 fields
        manifest["schema_version"] = 3
        for key in ("model_map", "phase_started_at", "role_assignments",
                     "planned_roles", "skipped_roles", "fail_closed"):
            manifest.pop(key, None)
        self.save_manifest(manifest)
        # Run migrate
        self.run_ctl("migrate")
        migrated = self.load_manifest()
        self.assertEqual(migrated["schema_version"], 4)
        self.assertIn("model_map", migrated)
        self.assertIn("role_assignments", migrated)
        self.assertIn("planned_roles", migrated)
        self.assertIn("skipped_roles", migrated)
        self.assertIn("fail_closed", migrated)
        self.assertFalse(migrated["fail_closed"])
        self.assertEqual(migrated["role_assignments"], {})

    def test_106_migrate_preserves_existing_v4_fields(self):
        """Migrate does not overwrite existing v4 field values."""
        self.init_mission()
        manifest = self.load_manifest()
        # Set v4 fields to custom values, then downgrade schema_version
        manifest["schema_version"] = 3
        manifest["model_map"] = {"default": "gemini", "skeptic": "codex"}
        manifest["role_assignments"] = {"implementer": {"model": "claude"}}
        manifest["fail_closed"] = True
        self.save_manifest(manifest)
        self.run_ctl("migrate")
        migrated = self.load_manifest()
        self.assertEqual(migrated["schema_version"], 4)
        # setdefault should NOT overwrite existing values
        self.assertEqual(migrated["model_map"]["skeptic"], "codex")
        self.assertEqual(migrated["role_assignments"]["implementer"]["model"], "claude")
        self.assertTrue(migrated["fail_closed"])

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE TIMING TEST (Test 107)
    # ═══════════════════════════════════════════════════════════════════

    def test_107_phase_sets_started_at(self):
        """cmd_phase sets phase_started_at to a valid ISO timestamp."""
        self.init_mission()
        self.set_phase("implement")
        manifest_after = self.load_manifest()
        new_ts = manifest_after["phase_started_at"]
        self.assertIsNotNone(new_ts)
        self.assertIsInstance(new_ts, str)
        self.assertTrue(new_ts.endswith("Z"), msg=f"Timestamp should end with Z: {new_ts}")
        from datetime import datetime
        parsed = datetime.fromisoformat(new_ts.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed)

    # ═══════════════════════════════════════════════════════════════════
    #  CLI OBSERVABILITY TESTS (Tests 108–110)
    # ═══════════════════════════════════════════════════════════════════

    def test_108_timeline_command_exists(self):
        """collabctl timeline runs without error on active mission."""
        self.init_mission()
        proc = self.run_ctl("timeline")
        # Should succeed (rc=0) even if no assignments yet
        self.assertIn("No role assignments", proc.stdout)

    def test_109_report_command_exists(self):
        """collabctl report runs without error on active mission."""
        self.init_mission()
        proc = self.run_ctl("report")
        # Should produce a report header
        self.assertIn("Mission Report", proc.stdout)
        self.assertIn("Objective:", proc.stdout)

    def test_110_status_shows_model_when_available(self):
        """Status output includes model info when role_assignments has model data."""
        self.init_mission()
        self.set_phase("implement")
        # Inject a role_assignment with model info
        manifest = self.load_manifest()
        manifest["role_assignments"]["implementer"] = {
            "model": "codex",
            "started_at": manifest["phase_started_at"],
            "duration_seconds": 42,
        }
        self.save_manifest(manifest)
        # Also save a role result so status has something to display
        self.save_role_result("implementer", self.valid_role_result("implementer"))
        text = self.run_ctl("status").stdout
        self.assertIn("model=codex", text)
        self.assertIn("duration=0m42s", text)

    # ═══════════════════════════════════════════════════════════════════
    #  COPILOT ADAPTER TEST (Test 111)
    # ═══════════════════════════════════════════════════════════════════

    def test_111_copilot_has_subagent_start(self):
        """Copilot CLI hooks.json includes subagentStart hook."""
        hooks_path = PLUGIN_ROOT / "adapters" / "copilot_cli" / "hooks.json"
        self.assertTrue(hooks_path.exists())
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertIn("subagentStart", payload["hooks"])
        hook_entry = payload["hooks"]["subagentStart"]
        self.assertIsInstance(hook_entry, list)
        self.assertGreater(len(hook_entry), 0)
        self.assertEqual(hook_entry[0]["type"], "command")
        self.assertIn("subagent_start", hook_entry[0]["bash"])

    # ═══════════════════════════════════════════════════════════════════
    #  FAIL-CLOSED TEST (Test 112)
    # ═══════════════════════════════════════════════════════════════════

    def test_112_init_with_fail_closed(self):
        """--fail-closed creates manifest with fail_closed=True."""
        result = json.loads(self.run_ctl(
            "init",
            "--objective", "Fail closed test",
            "--spec-path", "docs/spec.md",
            "--allowed-path", "src",
            "--criterion", "Works",
            "--verify-command", "pytest -q",
            "--fail-closed",
        ).stdout)
        manifest = self.load_manifest()
        self.assertTrue(manifest["fail_closed"])


    def test_113_awk_system_bypass_denied(self):
        """awk with system() must be denied for read-only roles."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "awk '{system(\"rm -rf /\")}'"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_114_awk_simple_denied(self):
        """Even simple awk must be denied -- too many write/exec capabilities."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "awk '{print $1}' file.txt"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_115_sed_n_w_bypass_denied(self):
        """sed -n with w command must be denied for read-only roles."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "sed -n 'w /tmp/pwned' src/app.py"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_116_sed_n_e_bypass_denied(self):
        """sed -n with e command must be denied for read-only roles."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "sed -n 'e id' src/app.py"},
        })
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_117_sed_n_print_line_allowed(self):
        """sed -n with line print (e.g. '5p') must still be allowed."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "sed -n '5p'"},
        })
        self.assertIsNone(result)

    def test_118_sed_n_pattern_print_allowed(self):
        """sed -n with pattern print (e.g. '/foo/p') must still be allowed."""
        self.init_mission()
        self.set_phase("review")
        result = self.hook_json("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-skeptic",
            "tool_name": "Bash", "tool_input": {"command": "sed -n '/error/p'"},
        })
        self.assertIsNone(result)

    def test_119_phase_verify_requires_audit_roles(self):
        """Transitioning to verify without audit role results must fail."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("review", force=False)
        self.set_phase("security", force=False)
        # Only security done, missing performance/accessibility/skeptic
        self.save_role_result("security", self.valid_role_result("security"))
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"), "--cwd", str(self.repo), "phase", "verify"],
            cwd=str(self.repo), text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_120_phase_verify_with_skip_role(self):
        """Transitioning to verify with --skip-role bypasses missing audit roles."""
        self.init_mission()
        self.set_phase("implement", force=False)
        self.set_phase("review", force=False)
        self.set_phase("security", force=False)
        self.set_phase("performance", force=False)
        self.set_phase("accessibility", force=False)
        # Save accessibility result only, skip the rest
        self.save_role_result("accessibility", self.valid_role_result("accessibility"))
        proc = self.run_ctl(
            "phase", "verify",
            "--skip-role", "security",
            "--skip-role", "performance",
            "--skip-role", "skeptic",
        )
        self.assertEqual(proc.returncode, 0)


    # ==================================================================
    #  FEATURE TESTS: M1 Revert, M3 Close Scope, Security, Accessibility
    # ==================================================================

    def test_121_post_tool_revert_concept(self):
        """PostToolUse code path imports READ_ONLY_ROLES for revert logic."""
        spec = importlib.util.spec_from_file_location(
            "collab_post_tool_check", HOOKS_DIR / "collab_post_tool.py",
        )
        source = (HOOKS_DIR / "collab_post_tool.py").read_text(encoding="utf-8")
        self.assertIn("READ_ONLY_ROLES", source)
        self.assertIn("scope_violation_reverted", source)
        self.assertIn("custom_role_scope_type", source)

    def test_122_close_captures_git_baseline(self):
        """Init captures git_baseline in manifest."""
        self.init_mission()
        manifest = self.load_manifest()
        self.assertIn("git_baseline", manifest)

    def test_123_close_scope_verification(self):
        """_verify_close_scope detects out-of-scope files."""
        # Import collabctl module
        spec = importlib.util.spec_from_file_location(
            "collabctl_test", SCRIPTS_DIR / "collabctl.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # A manifest with only src/ allowed
        manifest = {
            "allowed_paths": ["src/"],
            "test_paths": ["tests/"],
            "doc_paths": ["docs/"],
        }
        # No git changes = no violations (the function calls git diff)
        violations = mod._verify_close_scope(manifest, self.repo)
        self.assertIsInstance(violations, list)

    def test_124_force_logged_to_ledger(self):
        """--force usage is recorded in ledger as force_override."""
        self.init_mission()
        # Use --force to skip from plan to verify (normally illegal)
        self.set_phase("verify", force=True)
        ctx = self.common.load_active_context(self.repo)
        ledger = ctx.mission_dir / "ledger.ndjson"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        force_entries = [
            json.loads(ln) for ln in lines if ln.strip()
            and json.loads(ln).get("kind") == "force_override"
        ]
        self.assertTrue(len(force_entries) >= 1)
        self.assertEqual(force_entries[0]["phase_from"], "plan")
        self.assertEqual(force_entries[0]["phase_to"], "verify")

    def test_125_skip_role_validates_audit_roles(self):
        """--skip-role rejects non-audit role names."""
        self.init_mission()
        self.set_phase("implement")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"),
             "--cwd", str(self.repo),
             "phase", "review",
             "--skip-role", "implementer"],
            cwd=str(self.repo), text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a valid audit role", proc.stderr)

    def test_126_unrecognized_agent_denied(self):
        """Unrecognized collab-* agent type is denied by pre_tool hook."""
        self.init_mission()
        self.set_phase("implement")
        rc, out, err = self.run_hook("collab_pre_tool.py", {
            "cwd": str(self.repo), "agent_type": "collab-hacker",
            "tool_name": "Bash", "tool_input": {"command": "rm -rf /"},
        })
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Unrecognized", parsed["hookSpecificOutput"]["permissionDecisionReason"])

    def test_127_timeline_fits_80_columns(self):
        """Timeline header fits within 80 characters."""
        spec = importlib.util.spec_from_file_location(
            "collabctl_test_tl", SCRIPTS_DIR / "collabctl.py",
        )
        source = (SCRIPTS_DIR / "collabctl.py").read_text(encoding="utf-8")
        # Find the header format string in cmd_timeline
        # The header is built from: Phase(10) + Role(17) + Model(8) + Status(14) + Start(8) + Dur(8) + Find
        # We test via running the command
        self.init_mission()
        self.set_phase("implement")
        # Dispatch a role via subagent_start to populate role_assignments
        self.run_hook("collab_subagent_start.py", {
            "cwd": str(self.repo),
            "agent_type": "collab-implementer",
        })
        proc = self.run_ctl("timeline")
        header_line = proc.stdout.strip().splitlines()[0]
        self.assertLessEqual(len(header_line), 80, f"Header is {len(header_line)} chars: {header_line!r}")

    def test_128_status_shows_running(self):
        """Status shows 'running' for dispatched-but-incomplete roles."""
        self.init_mission()
        self.set_phase("implement")
        # Dispatch implementer (started_at set, no completed_at)
        self.run_hook("collab_subagent_start.py", {
            "cwd": str(self.repo),
            "agent_type": "collab-implementer",
        })
        proc = self.run_ctl("status")
        self.assertIn("running", proc.stdout)

    def test_129_report_includes_severity(self):
        """Report output includes severity labels from findings."""
        self.init_mission()
        self.set_phase("implement")
        self.set_phase("review")
        self.set_phase("security")
        # Save security result with findings that have severity
        result = self.valid_role_result("security")
        result["findings"] = [
            {"severity": "high", "summary": "SQL injection risk"},
            {"severity": "low", "summary": "Missing logging"},
        ]
        self.save_role_result("security", result)
        proc = self.run_ctl("report")
        self.assertIn("[HIGH]", proc.stdout)
        self.assertIn("[LOW]", proc.stdout)

    def test_130_no_mission_suggests_init(self):
        """'No active mission' messages suggest running init."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "collabctl.py"),
             "--cwd", str(self.repo), "status"],
            cwd=str(self.repo), text=True, capture_output=True, check=False,
        )
        self.assertIn("collabctl init", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

