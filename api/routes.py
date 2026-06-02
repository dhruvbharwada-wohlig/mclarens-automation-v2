"""
api/routes.py

FastAPI app with three endpoints:
  GET /task/approve?token=xxx&by=xxx
  GET /task/reject?token=xxx&by=xxx&reason=xxx
  GET /task/resolved?token=xxx&by=xxx

To run:
  uvicorn api.routes:app --host 0.0.0.0 --port 8000 --reload
"""

import uuid

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from app.task_store import (
    TaskStatus,
    approve_task,
    reject_task,
    get_task_by_token,
    mark_executing,
    mark_completed,
    mark_failed,
    append_log,
    increment_retry,
)
from app.validator import validate_tasks
from app.email_sender import send_approval_email, send_acknowledgement_email
from db.connection import get_invoices_collection

app = FastAPI(title="Wohlig AI Automation API")


# ── HTML response page ────────────────────────────────────────────────────────

def _html_page(title: str, message: str, color: str = "#1a1a1a") -> HTMLResponse:
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
                background: #f5f5f5;
            }}
            .card {{
                background: white;
                padding: 40px 60px;
                border-radius: 12px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.1);
                text-align: center;
                max-width: 500px;
            }}
            h1 {{ color: {color}; font-size: 28px; margin-bottom: 12px; }}
            p  {{ color: #555; font-size: 16px; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>{title}</h1>
            <p>{message}</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# ── GET /task/approve ─────────────────────────────────────────────────────────

@app.get("/task/approve")
def approve(
    token: str = Query(..., description="Approval token from email"),
    by:    str = Query("reviewer", description="Who approved"),
):
    # 1 — verify token + mark approved
    task = approve_task(token=token, approved_by=by)

    if not task:
        return _html_page(
            "Invalid or Expired Link",
            "This approval link is invalid or has already been used.",
            color="#c0392b"
        )

    task_id = str(task["_id"])
    tasks   = task.get("tasks", [])

    # 2 — re-validate before execution
    validation = validate_tasks(tasks)
    if not validation["passed"]:
        append_log(task_id, "pre_execution_validation", "failed", {
            "issues": validation["issues"]
        })
        mark_failed(task_id, f"Pre-execution validation failed: {validation['issues']}")
        return _html_page(
            "Validation Failed",
            "The stored queries failed safety checks and were not executed. "
            "The team has been notified.",
            color="#c0392b"
        )

    # 3 — execute all tasks
    mark_executing(task_id)
    invoices       = get_invoices_collection()
    execution_errors = []

    for i, task_item in enumerate(tasks):
        filter_query = task_item.get("filter_query", {})
        update_query = task_item.get("update_query", {})

        try:
            result = invoices.update_one(filter_query, update_query)

            if result.matched_count == 0:
                msg = f"Task {i+1}: no document matched filter {filter_query}"
                execution_errors.append(msg)
                append_log(task_id, f"execute_task_{i+1}", "no_match", {
                    "filter": filter_query
                })
            else:
                append_log(task_id, f"execute_task_{i+1}", "success", {
                    "filter":   filter_query,
                    "update":   update_query,
                    "matched":  result.matched_count,
                    "modified": result.modified_count,
                })
                print(f"[EXECUTE] Task {i+1} done — matched: {result.matched_count}, modified: {result.modified_count}")

        except Exception as e:
            msg = f"Task {i+1}: execution error — {str(e)}"
            execution_errors.append(msg)
            append_log(task_id, f"execute_task_{i+1}", "error", {"error": str(e)})

    # 4 — mark final status
    if execution_errors:
        mark_failed(task_id, "; ".join(execution_errors))
        return _html_page(
            "Execution Failed",
            f"Some tasks could not be executed: {'; '.join(execution_errors)}",
            color="#c0392b"
        )

    mark_completed(task_id)

    # 5 — send acknowledgement email to McLarens
    ack_sent = send_acknowledgement_email(task)
    append_log(task_id, "ack_email", "sent" if ack_sent else "failed")

    return _html_page(
        "✓ Approved & Executed",
        "All invoice updates have been applied successfully. "
        "An acknowledgement has been sent to McLarens.",
        color="#27ae60"
    )


# ── GET /task/reject ──────────────────────────────────────────────────────────

@app.get("/task/reject")
def reject(
    token:  str = Query(..., description="Approval token from email"),
    by:     str = Query("reviewer", description="Who rejected"),
    reason: str = Query("No reason provided", description="Rejection reason"),
):
    # 1 — verify token + mark rejected
    task = reject_task(token=token, rejected_by=by, reason=reason)

    if not task:
        return _html_page(
            "Invalid or Expired Link",
            "This rejection link is invalid or has already been used.",
            color="#c0392b"
        )

    task_id = str(task["_id"])
    append_log(task_id, "rejected", "triggering_retry", {"reason": reason})

    # 2 — feed rejection back to DeepSeek and get corrected solution
    retry_result = _run_retry(task, reason)

    if not retry_result:
        return _html_page(
            "Rejected — Retry Limit Reached",
            f"This task has been rejected {task.get('retryCount', 0)} times "
            "and has reached the retry limit. Please handle manually.",
            color="#c0392b"
        )

    return _html_page(
        "Rejected — New Solution Sent",
        f"Reason noted: {reason}. "
        "The AI has generated a corrected solution and a new approval email has been sent.",
        color="#e67e22"
    )


# ── GET /task/resolved ────────────────────────────────────────────────────────

@app.get("/task/resolved")
def resolved(
    token: str = Query(..., description="Approval token from email"),
    by:    str = Query("reviewer", description="Who resolved"),
):
    task = get_task_by_token(token)

    if not task:
        return _html_page(
            "Invalid or Expired Link",
            "This link is invalid or has already been used.",
            color="#c0392b"
        )

    task_id = str(task["_id"])
    mark_completed(task_id)
    append_log(task_id, "manually_resolved", "completed", {"by": by})

    return _html_page(
        "✓ Marked as Resolved",
        "This task has been marked as manually resolved and closed.",
        color="#27ae60"
    )


# ── Retry flow ────────────────────────────────────────────────────────────────

def _run_retry(task: dict, rejection_reason: str) -> bool:
    """
    Called when reviewer rejects a task.

    1. Rebuilds the prompt with the original thread + rejection reason
    2. Calls DeepSeek again
    3. Validates new output
    4. Updates task document with new queries + new token
    5. Sends new approval email

    Returns True if retry succeeded, False if retry limit reached.
    """
    from ai_processor import process
    from app.validator import validate_tasks

    task_id      = str(task["_id"])
    thread_text  = task.get("threadText", "")
    retry_count  = task.get("retryCount", 0)

    if retry_count >= 3:
        mark_failed(task_id, "Retry limit of 3 reached")
        append_log(task_id, "retry", "limit_reached")
        return False

    # Build a modified email dict for re-processing
    # Include the rejection reason so DeepSeek knows what was wrong
    email_for_retry = {
        "thread_text": _build_retry_thread(thread_text, task, rejection_reason)
    }

    try:
        new_ai_result = process(email_for_retry)
    except Exception as e:
        append_log(task_id, "retry_ai_call", "error", {"error": str(e)})
        mark_failed(task_id, f"AI retry failed: {str(e)}")
        return False

    # Validate new output before sending for approval again
    validation = validate_tasks(new_ai_result.get("tasks", []))
    if not validation["passed"]:
        append_log(task_id, "retry_validation", "failed", {
            "issues": validation["issues"]
        })
        mark_failed(task_id, f"Retry validation failed: {validation['issues']}")
        return False

    # Generate new token for the new approval email
    new_token = str(uuid.uuid4())

    # Update task document — back to pending with new queries + new token
    success = increment_retry(task_id, new_ai_result, new_token)
    if not success:
        return False

    # Fetch updated task to send approval email with new token
    from app.task_store import get_task_by_id
    updated_task = get_task_by_id(task_id)

    if updated_task:
        sent = send_approval_email(updated_task)
        append_log(task_id, "retry_approval_email", "sent" if sent else "failed")

    return True


def _build_retry_thread(original_thread: str, task: dict, rejection_reason: str) -> str:
    """
    Builds the prompt context for a retry — appends rejection feedback
    so DeepSeek knows exactly what was wrong with its previous answer.
    """
    previous_tasks = task.get("tasks", [])

    previous_summary = ""
    for i, t in enumerate(previous_tasks):
        previous_summary += f"\nTask {i+1}: {t.get('description', '')}"
        previous_summary += f"\nPrevious filter: {t.get('filter_query', {})}"
        previous_summary += f"\nPrevious update: {t.get('update_query', {})}\n"

    return f"""
{original_thread}

---
IMPORTANT — PREVIOUS ATTEMPT WAS REJECTED:
Rejection reason: {rejection_reason}

Your previous solution was:
{previous_summary}

Please carefully re-read the email thread above and generate a corrected solution.
Address the rejection reason specifically.
---
"""


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Wohlig AI Automation API"}