# /collab Result Schemas

Use these schemas inside `COLLAB_RESULT_JSON_BEGIN` / `COLLAB_RESULT_JSON_END` blocks.

## implementer

```json
{
  "role": "implementer",
  "status": "complete",
  "summary": "One paragraph summary of what changed and why.",
  "changed_paths": ["src/example.py"],
  "artifacts": ["brief evidence item"],
  "tests_run": []
}
```

## test-writer

```json
{
  "role": "test-writer",
  "status": "complete",
  "summary": "One paragraph summary.",
  "test_files_created": ["tests/test_example.py"],
  "coverage_targets": ["src/example.py::Example.method"],
  "test_count": 3,
  "edge_cases_covered": ["empty input", "invalid input"]
}
```

## skeptic

```json
{
  "role": "skeptic",
  "summary": "One paragraph review summary.",
  "recommendation": "approve",
  "findings": [
    {
      "severity": "medium",
      "path": "src/example.py",
      "line": 42,
      "issue": "Describe the issue",
      "evidence": "Concrete evidence from the code or diff"
    }
  ]
}
```

## security

```json
{
  "role": "security",
  "summary": "One paragraph assessment.",
  "recommendation": "approve",
  "findings": [
    {
      "severity": "high",
      "category": "injection",
      "path": "src/auth.py",
      "line": 42,
      "issue": "SQL injection via unsanitized input",
      "evidence": "cursor.execute(f'SELECT * FROM users WHERE id={user_id}')",
      "cwe": "CWE-89"
    }
  ],
  "scan_commands_run": ["bandit -r src/"]
}
```

## performance

```json
{
  "role": "performance",
  "summary": "One paragraph assessment.",
  "recommendation": "approve",
  "findings": [
    {
      "severity": "high",
      "category": "algorithmic",
      "path": "src/query.py",
      "issue": "Nested loop O(n*m)",
      "evidence": "Lines 42-58: for user in users: for order in orders:"
    }
  ],
  "benchmarks_run": ["pytest --benchmark-only"]
}
```

## accessibility

```json
{
  "role": "accessibility",
  "summary": "One paragraph assessment.",
  "recommendation": "approve",
  "findings": [
    {
      "severity": "high",
      "category": "aria",
      "path": "src/components/Login.tsx",
      "issue": "Button missing accessible label",
      "evidence": "<button onClick={handleLogin}><Icon /></button> at line 42",
      "wcag": "2.1-AA-4.1.2"
    }
  ],
  "a11y_commands_run": []
}
```

## docs

```json
{
  "role": "docs",
  "status": "complete",
  "summary": "One paragraph summary.",
  "docs_updated": ["README.md", "docs/api.md"],
  "docs_created": ["docs/migration-guide.md"]
}
```

## verifier

```json
{
  "role": "verifier",
  "summary": "One paragraph verification summary.",
  "recommendation": "pass",
  "criteria_results": [
    {
      "criterion": "<exact acceptance criterion text>",
      "status": "pass",
      "evidence": ["pytest -q output: 12 passed", "src/auth.py line 42: JWT validation present"]
    }
  ]
}
```

