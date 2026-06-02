"""
app/deduplicator.py

The gate between Gmail and the rest of the pipeline.
Called before intent classification, thread reconstruction, or DeepSeek.

Two-layer dedup:
  Layer 1 — Gmail unread flag (already in your gmail_reader.py)
  Layer 2 — MongoDB gmailMessageId check (this file)

Layer 2 is the hard guard. Even if Gmail marks something unread again,
or the poller picks up the same email twice in the same run, this stops it.
"""

from app.task_store import is_already_processed


def should_process(email: dict) -> tuple[bool, str]:
    """
    Decides whether an email should enter the pipeline.

    Args:
        email: dict with at least { "id": gmailMessageId, ... }
               This is the email dict your gmail_reader.py already builds.

    Returns:
        (True,  "ok")                   — proceed with processing
        (False, "already_processed")    — gmailMessageId in automation_tasks
        (False, "missing_message_id")   — email dict has no id field
    """
    message_id = email.get("id")

    if not message_id:
        print(f"[DEDUP] Email has no message id — skipping")
        return False, "missing_message_id"

    if is_already_processed(message_id):
        print(f"[DEDUP] {message_id} already in automation_tasks — skipping")
        return False, "already_processed"

    print(f"[DEDUP] {message_id} is new — proceeding")
    return True, "ok"


def filter_new_emails(emails: list[dict]) -> list[dict]:
    """
    Filters a list of emails down to only ones not yet processed.
    Use this if your gmail_reader ever returns a batch.

    Args:
        emails: list of email dicts from gmail_reader

    Returns:
        list of email dicts that are safe to process
    """
    new_emails = []

    for email in emails:
        proceed, reason = should_process(email)
        if proceed:
            new_emails.append(email)
        else:
            print(f"[DEDUP] Dropped {email.get('id', 'unknown')} — {reason}")

    print(f"[DEDUP] {len(new_emails)}/{len(emails)} emails are new")
    return new_emails