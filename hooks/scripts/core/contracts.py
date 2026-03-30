from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HookRequest:
    cwd: str = "."
    agent_type: Optional[str] = None
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "HookRequest":
        return cls(
            cwd=str(payload.get("cwd") or "."),
            agent_type=payload.get("agent_type"),
            tool_name=str(payload.get("tool_name") or ""),
            tool_input=dict(payload.get("tool_input") or {}),
            payload=dict(payload),
        )


@dataclass
class HookResponse:
    decision: Optional[str] = None
    reason: Optional[str] = None
    hookSpecificOutput: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        response: Dict[str, Any] = {}
        if self.decision is not None:
            response["decision"] = self.decision
        if self.reason is not None:
            response["reason"] = self.reason
        if self.hookSpecificOutput:
            response["hookSpecificOutput"] = self.hookSpecificOutput
        return response


__all__ = ["HookRequest", "HookResponse"]

