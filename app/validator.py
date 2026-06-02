"""
app/validator.py

Runs safety checks on every AI-generated MongoDB query before execution.
Called after DeepSeek returns output, before storing the task or sending
the approval email.

If validation fails, the task is NOT stored and NOT sent for approval.
The failure is logged and the email is flagged for manual review.
"""

import json

# Fields that are allowed to be modified via $set
# Add to this list as your schema grows — anything NOT here is blocked
ALLOWED_FIELDS = {
    "invoiceNumber",
    "approvalStatus",
    "approvalTime",
    "irnNumber",
    "acknowledgementNumber",
    "acknowledgementDate",
    "grandTotal",
    "subTotal",
    "currency",
    "conversionRate",
    "convertedAmount",
    "roundOff",
    "roundOffPlus",
    "GSTType",
    "fees",
    "expense",
    "outstanding",
    "isTallySuccess",
    "failureReason",
    "isHardcopySent",
    "isSoftcopySent",
    "submitType",
    "isReportSync",
    "status",
    "timestamp",
    "reqtimestamp",
}

# Only these MongoDB operators are allowed in update queries
ALLOWED_OPERATORS = {"$set"}

# Operators that must never appear anywhere in the query
DANGEROUS_OPERATORS = {
    "$unset",
    "$rename",
    "$drop",
    "$delete",
    "$where",
    "$expr",
    "$function",
    "$accumulator",
    "$out",
    "$merge",
}


# ── Main entry point ──────────────────────────────────────────────────────────

def validate_tasks(tasks: list[dict]) -> dict:
    """
    Validates all tasks from DeepSeek output.

    Args:
        tasks: the tasks array from ai_result["tasks"]

    Returns:
        {
            "passed": True/False,
            "issues": []           — empty if passed, list of strings if failed
        }
    """
    if not tasks:
        return _fail(["No tasks found in AI output"])

    all_issues = []

    for i, task in enumerate(tasks):
        prefix = f"Task {i + 1}"
        issues = _validate_single_task(task, prefix)
        all_issues.extend(issues)

    if all_issues:
        return _fail(all_issues)

    return {"passed": True, "issues": []}


# ── Single task validation ────────────────────────────────────────────────────

def _validate_single_task(task: dict, prefix: str) -> list[str]:
    issues = []

    filter_query = task.get("filter_query", {})
    update_query = task.get("update_query", {})

    # 1. filter_query must exist and not be empty
    if not filter_query:
        issues.append(f"{prefix}: filter_query is empty or missing")

    # 2. invoiceNumber must be in filter_query
    elif "invoiceNumber" not in filter_query:
        issues.append(f"{prefix}: filter_query must contain invoiceNumber")

    # 3. invoiceNumber value must not be empty
    elif not filter_query.get("invoiceNumber"):
        issues.append(f"{prefix}: invoiceNumber in filter_query is empty")

    # 4. update_query must exist and not be empty
    if not update_query:
        issues.append(f"{prefix}: update_query is empty or missing")
        return issues  # no point checking further

    # 5. only $set is allowed as top-level operator
    top_level_keys = set(update_query.keys())
    disallowed_operators = top_level_keys - ALLOWED_OPERATORS
    if disallowed_operators:
        issues.append(
            f"{prefix}: disallowed operators in update_query: {disallowed_operators}"
        )

    # 6. $set must exist and not be empty
    set_clause = update_query.get("$set", {})
    if not set_clause:
        issues.append(f"{prefix}: $set clause is empty or missing")
        return issues

    # 7. all fields in $set must be in ALLOWED_FIELDS
    unknown_fields = set(set_clause.keys()) - ALLOWED_FIELDS
    if unknown_fields:
        issues.append(
            f"{prefix}: unknown fields in $set (not in schema): {unknown_fields}"
        )

    # 8. no field value should be None or empty string
    for field, value in set_clause.items():
        if value is None or value == "":
            issues.append(f"{prefix}: field '{field}' has empty/null value in $set")

    # 9. scan entire update_query for dangerous operators (nested too)
    dangerous_found = _scan_for_dangerous(update_query)
    if dangerous_found:
        issues.append(
            f"{prefix}: dangerous operators found in update_query: {dangerous_found}"
        )

    # 10. scan filter_query for dangerous operators
    dangerous_in_filter = _scan_for_dangerous(filter_query)
    if dangerous_in_filter:
        issues.append(
            f"{prefix}: dangerous operators found in filter_query: {dangerous_in_filter}"
        )

    return issues


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scan_for_dangerous(obj: dict, found: set = None) -> set:
    """Recursively scans a dict for any dangerous operator keys."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in DANGEROUS_OPERATORS:
                found.add(key)
            _scan_for_dangerous(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _scan_for_dangerous(item, found)
    return found


def _fail(issues: list[str]) -> dict:
    print(f"[VALIDATOR] Validation failed:")
    for issue in issues:
        print(f"  - {issue}")
    return {"passed": False, "issues": issues}


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Good task — should pass
    good = [
        {
            "description": "Update IRN number",
            "filter_query": {"invoiceNumber": "INV-001"},
            "update_query": {"$set": {"irnNumber": "ABC123456"}},
        }
    ]

    # Bad task — should fail
    bad = [
        {
            "description": "Missing invoiceNumber",
            "filter_query": {"_id": "some_id"},
            "update_query": {"$set": {"irnNumber": "ABC123456"}},
        },
        {
            "description": "Dangerous operator",
            "filter_query": {"invoiceNumber": "INV-002"},
            "update_query": {"$set": {"irnNumber": ""}, "$unset": {"totalAmount": ""}},
        },
    ]

    print("=== Good tasks ===")
    print(json.dumps(validate_tasks(good), indent=2))

    print("\n=== Bad tasks ===")
    print(json.dumps(validate_tasks(bad), indent=2))