"""
main.py
Full pipeline — fetch → dedup → tag → AI → validate → store → send approval email
"""

import json
from datetime import datetime, timezone

from app.gmail_reader import GmailReader
from app.tagger import Tagger
from app.deduplicator import should_process
from app.validator import validate_tasks
from app.task_store import create_task
from app.email_sender import send_approval_email
from ai_processor import process

reader = GmailReader()
tagger = Tagger()

# ── Step 1: Fetch emails ──────────────────────────────────────────────────────

emails = reader.read_emails()

if not emails:
    print("[MAIN] No task emails found.")
else:
    for email in emails:

        subject    = email.get("subject", "")
        sender     = email.get("sender", "")
        message_id = email.get("id", "")
        thread_id  = email.get("thread_id", "")

        print(f"\n[MAIN] Processing: {subject}")

        # ── Step 2: Dedup check ───────────────────────────────────────────────
        # If this gmailMessageId is already in automation_tasks → skip entirely
        proceed, reason = should_process(email)
        if not proceed:
            print(f"[MAIN] Skipping — {reason}")
            continue

        # ── Step 3: Tag ───────────────────────────────────────────────────────
        tags = tagger.tag(email["thread_text"])
        email["tags"] = tags

        # ── Step 4: DeepSeek ──────────────────────────────────────────────────
        try:
            ai_result = process(email)
        except Exception as e:
            print(f"[MAIN] AI processing failed for '{subject}': {e}")
            continue

        if not ai_result or not ai_result.get("tasks"):
            print(f"[MAIN] AI returned empty result for '{subject}' — skipping")
            continue

        print(json.dumps(ai_result, indent=2))

        # ── Step 5: Validate ──────────────────────────────────────────────────
        validation = validate_tasks(ai_result.get("tasks", []))

        if not validation["passed"]:
            print(f"[MAIN] Validation failed for '{subject}' — not storing task")
            for issue in validation["issues"]:
                print(f"  - {issue}")
            # TODO: flag for manual review (future improvement)
            continue

        print(f"[MAIN] Validation passed for '{subject}'")

        # ── Step 6: Store task in automation_tasks ────────────────────────────
        task = create_task(
            gmail_message_id  = message_id,
            gmail_thread_id   = thread_id,
            subject           = subject,
            sender_email      = sender,
            received_at       = datetime.now(timezone.utc),
            tags              = tags,
            ai_result         = ai_result,
            validation_result = validation,
        )

        if not task:
            # create_task returns None if gmailMessageId already exists
            # This is the DB-level dedup catching anything that slipped through
            print(f"[MAIN] Task already exists in DB for '{subject}' — skipping")
            continue

        # Store thread_text on the task document for retry flow
        # (needed by routes.py _run_retry() if reviewer rejects)
        from db.connection import get_db
        get_db()["automation_tasks"].update_one(
            {"_id": task["_id"]},
            {"$set": {"threadText": email.get("thread_text", "")}}
        )

        print(f"[MAIN] Task stored — ID: {task['_id']}")

        # ── Step 7: Send approval email to reviewer ───────────────────────────
        sent = send_approval_email(task)

        if sent:
            print(f"[MAIN] Approval email sent for '{subject}'")
        else:
            print(f"[MAIN] Failed to send approval email for '{subject}'")

        print(f"[MAIN] Done — task {task['_id']} is pending approval\n")