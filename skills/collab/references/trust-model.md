# How eight-eyes Keeps Reviews Honest

## Independent Reviews

Each reviewer sees only what it needs. The skeptic never sees the
implementer's explanation -- it reads the code fresh and forms its
own opinion. This is enforced at the hook level, not by asking the
model to ignore context.

## Enforced Permissions

Reviewers cannot edit code. The security auditor cannot "fix" what
it finds. Write roles can only touch the paths you approve at mission
start. Permissions are enforced by hooks before each tool call.

## Approved Commands Only

Bash commands are limited to read-only tools plus whatever you
explicitly approve at mission start. Shell pipes, redirects, chains,
and command substitution are blocked automatically.

## Structured Results

Every role must produce a result in a defined format with required
fields. Missing or invalid results are rejected -- the role must
try again. After three failures, the mission pauses for your input.

## Audit Trail

Every tool call, phase transition, and finding is logged to an
append-only ledger. The ledger deduplicates by tool call ID and
cannot be retroactively modified.

## Scope Verification at Close

When you close a mission, eight-eyes compares the git diff against
your declared allowed paths. Out-of-scope changes are flagged and
must be addressed or explicitly overridden.

## Automatic Rollback

If a read-only role somehow writes a file (e.g., due to a platform
limitation), the write is automatically reverted. Tracked files are
restored via git, untracked files are removed.

## Stale Result Detection

When a mission loops back for changes, results from the previous
iteration are automatically invalidated. Only current-iteration
results count toward mission completion.
