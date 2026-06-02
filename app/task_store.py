"""
app/task_store.py
All MongoDB operations for the automation_tasks collection.
No other file should query automation_tasks directly —
everything goes through these functions.
"""

import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from db.connection import get_db


# ── Status enum ───────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"     # AI processed, approval email sent
    APPROVED  = "approved"    # human approved
    REJECTED  = "rejected"    # human rejected — retry flow
    EXECUTING = "executing"   # pymongo update running
    COMPLETED = "completed"   # done — ack email sent
    FAILED    = "failed"      # execution error — needs manual check


# Only these transitions are allowed
VALID_TRANSITIONS = {
    TaskStatus.PENDING:   {TaskStatus.APPROVED, TaskStatus.REJECTED},
    TaskStatus.APPROVED:  {TaskStatus.EXECUTING},
    TaskStatus.REJECTED:  {TaskStatus.PENDING},   # after AI retry
    TaskStatus.EXECUTING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),                   # terminal
    TaskStatus.FAILED:    set(),                   # terminal
}

APPROVAL_TOKEN_TTL_HOURS = 72


# ── Internal helpers ──────────────────────────────────────────────────────────

def _col():
    return get_db()["automation_tasks"]


def _now():
    return datetime.now(timezone.utc)


def _token_expiry():
    return _now() + timedelta(hours=APPROVAL_TOKEN_TTL_HOURS)


def _log_entry(action: str, result: str, detail: dict = None) -> dict:
    entry = {
        "timestamp": _now(),
        "action":    action,
        "result":    result,
    }
    if detail:
        entry["detail"] = detail
    return entry


# ── Create ────────────────────────────────────────────────────────────────────

def create_task(
    gmail_message_id: str,
    gmail_thread_id:  str,
    subject:          str,
    sender_email:     str,
    received_at:      datetime,
    tags:             list,
    ai_result:        dict,
    validation_result: dict,
) -> dict | None:
    """
    Builds and inserts a new task document.
    Returns the inserted document (with _id) or None if it already exists
    (duplicate gmailMessageId — dedup guard).
    """
    token = str(uuid.uuid4())

    doc = {
        # Identity
        "gmailMessageId":      gmail_message_id,
        "gmailThreadId":       gmail_thread_id,

        # Status
        "status":              TaskStatus.PENDING,
        "confidence":          ai_result.get("confidence", "low"),

        # Email metadata
        "subject":             subject,
        "senderEmail":         sender_email,
        "receivedAt":          received_at,
        "tags":                tags,

        # AI output
        "tasks":               ai_result.get("tasks", []),
        "validationResult":    validation_result,

        # Approval
        "approvalToken":       token,
        "approvalTokenExpiry": _token_expiry(),
        "approvedBy":          None,
        "rejectionReason":     None,
        "retryCount":          0,

        # Execution + Audit
        "executionLog":        [],
        "completedAt":         None,
        "createdAt":           _now(),
    }

    try:
        result = _col().insert_one(doc)
        doc["_id"] = result.inserted_id
        print(f"[TASK] Created task {result.inserted_id} for message {gmail_message_id}")
        return doc
    except DuplicateKeyError:
        print(f"[TASK] Already processed message {gmail_message_id} — skipping")
        return None


# ── Read ──────────────────────────────────────────────────────────────────────

def get_task_by_token(token: str) -> dict | None:
    return _col().find_one({"approvalToken": token})


def get_task_by_message_id(gmail_message_id: str) -> dict | None:
    return _col().find_one({"gmailMessageId": gmail_message_id})


def get_task_by_id(task_id: str) -> dict | None:
    return _col().find_one({"_id": ObjectId(task_id)})


def is_already_processed(gmail_message_id: str) -> bool:
    """
    Dedup check — call this before doing anything with an email.
    Returns True if this gmailMessageId is already in the collection.
    """
    return _col().find_one(
        {"gmailMessageId": gmail_message_id},
        {"_id": 1}       # only fetch _id — faster
    ) is not None


# ── Status transitions ────────────────────────────────────────────────────────

def transition_status(
    task_id:    str,
    new_status: TaskStatus,
    extra_fields: dict = None,
) -> bool:
    """
    Moves a task to a new status.
    Validates the transition is legal before writing.
    Returns True on success, False if transition is invalid or task not found.
    """
    task = get_task_by_id(task_id)
    if not task:
        print(f"[TASK] transition_status: task {task_id} not found")
        return False

    current = TaskStatus(task["status"])
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        print(f"[TASK] Invalid transition: {current} → {new_status}")
        return False

    update = {
        "$set":  {"status": new_status, **(extra_fields or {})},
        "$push": {"executionLog": _log_entry(
            action=f"status_change",
            result=f"{current} → {new_status}",
        )},
    }

    # Set completedAt if terminal
    if new_status == TaskStatus.COMPLETED:
        update["$set"]["completedAt"] = _now()

    _col().update_one({"_id": ObjectId(task_id)}, update)
    print(f"[TASK] {task_id}: {current} → {new_status}")
    return True


# ── Approval helpers ──────────────────────────────────────────────────────────

def approve_task(token: str, approved_by: str) -> dict | None:
    """
    Marks task as approved.
    Returns the updated task or None if token invalid/expired.
    """
    task = get_task_by_token(token)
    if not task:
        print(f"[TASK] approve_task: token not found")
        return None

    expiry = task["approvalTokenExpiry"]
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < _now():
        print(f"[TASK] approve_task: token expired")
        return None

    task_id = str(task["_id"])
    success = transition_status(
        task_id,
        TaskStatus.APPROVED,
        extra_fields={"approvedBy": approved_by}
    )
    return get_task_by_id(task_id) if success else None


def reject_task(token: str, rejected_by: str, reason: str) -> dict | None:
    """
    Marks task as rejected with a reason.
    The rejection reason gets fed back to DeepSeek in the retry flow.
    Returns the updated task or None if token invalid/expired.
    """
    task = get_task_by_token(token)
    if not task:
        print(f"[TASK] reject_task: token not found")
        return None

    expiry = task["approvalTokenExpiry"]
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < _now():
        print(f"[TASK] reject_task: token expired")
        return None

    task_id = str(task["_id"])
    success = transition_status(
        task_id,
        TaskStatus.REJECTED,
        extra_fields={
            "approvedBy":      rejected_by,
            "rejectionReason": reason,
        }
    )
    return get_task_by_id(task_id) if success else None


# ── Execution helpers ─────────────────────────────────────────────────────────

def mark_executing(task_id: str) -> bool:
    return transition_status(task_id, TaskStatus.EXECUTING)


def mark_completed(task_id: str) -> bool:
    return transition_status(task_id, TaskStatus.COMPLETED)


def mark_failed(task_id: str, error: str) -> bool:
    return transition_status(
        task_id,
        TaskStatus.FAILED,
        extra_fields={"failureReason": error}
    )


def append_log(task_id: str, action: str, result: str, detail: dict = None):
    """Push a log entry into executionLog without changing status."""
    _col().update_one(
        {"_id": ObjectId(task_id)},
        {"$push": {"executionLog": _log_entry(action, result, detail)}}
    )


# ── Retry ─────────────────────────────────────────────────────────────────────

def increment_retry(task_id: str, new_ai_result: dict, new_token: str) -> bool:
    """
    Called after AI produces a corrected solution on rejection.
    Resets to pending with new tasks + new approval token.
    Returns False if retry limit (3) reached.
    """
    task = get_task_by_id(task_id)
    if not task:
        return False

    if task.get("retryCount", 0) >= 3:
        print(f"[TASK] {task_id}: retry limit reached")
        mark_failed(task_id, "Retry limit of 3 reached")
        return False

    _col().update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": {
                "status":              TaskStatus.PENDING,
                "tasks":               new_ai_result.get("tasks", []),
                "confidence":          new_ai_result.get("confidence", "low"),
                "approvalToken":       new_token,
                "approvalTokenExpiry": _token_expiry(),
                "rejectionReason":     None,
            },
            "$inc":  {"retryCount": 1},
            "$push": {"executionLog": _log_entry(
                "retry",
                f"Retried — attempt {task['retryCount'] + 1}"
            )},
        }
    )
    return True