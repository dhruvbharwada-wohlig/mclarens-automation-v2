"""
app/email_sender.py

Two responsibilities:
  1. send_approval_email()     → sent to Wohlig reviewer after AI processes an email
                                 contains approve / reject / resolved links
  2. send_acknowledgement_email() → sent to McLarens after successful DB execution

Uses Gmail API (same auth your gmail_reader.py already uses).
No new credentials needed.
"""

import os
import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

# Base URL where your FastAPI server is running
# In production this will be your actual domain e.g. https://api.wohlig.com
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# Who gets the approval email — the Wohlig reviewer
REVIEWER_EMAIL = os.environ.get("REVIEWER_EMAIL", "")


# ── Gmail service loader ──────────────────────────────────────────────────────

def _get_gmail_service():
    """
    Reuses the same Gmail OAuth credentials your gmail_reader.py already sets up.
    Import your existing auth function here.
    """ 
    from app.gmail_reader import GmailReader
    return GmailReader().authenticate()


# ── Send via Gmail API ────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, html_body: str) -> bool:
    """
    Sends an email using Gmail API.
    Returns True on success, False on failure.
    """
    try:
        service = _get_gmail_service()

        msg = MIMEMultipart("alternative")
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        print(f"[EMAIL] Sent to {to} — {subject}")
        return True

    except Exception as e:
        print(f"[EMAIL] Failed to send to {to} — {e}")
        return False


# ── 1. Approval email ─────────────────────────────────────────────────────────

def send_approval_email(task: dict) -> bool:
    """
    Sends approval email to the Wohlig reviewer.
    Called after AI processing + validation passes.

    Args:
        task: the full task document from automation_tasks

    Returns:
        True if sent successfully, False otherwise
    """
    token   = task["approvalToken"]
    subject = task.get("subject", "Invoice Correction Request")
    sender  = task.get("senderEmail", "McLarens")
    tasks   = task.get("tasks", [])

    approve_url  = f"{API_BASE_URL}/task/approve?token={token}&by=reviewer"
    reject_url   = f"{API_BASE_URL}/task/reject?token={token}&by=reviewer&reason=Incorrect+details"
    resolved_url = f"{API_BASE_URL}/task/resolved?token={token}&by=reviewer"

    # Build task summary rows
    task_rows = ""
    for i, t in enumerate(tasks):
        description  = t.get("description", "No description")
        filter_query = json.dumps(t.get("filter_query", {}), indent=2)
        update_query = json.dumps(t.get("update_query", {}), indent=2)

        task_rows += f"""
        <div style="background:#f9f9f9;border:1px solid #e0e0e0;border-radius:8px;
                    padding:16px;margin-bottom:16px;">
            <p style="margin:0 0 8px;font-weight:bold;color:#333;">
                Task {i + 1}: {description}
            </p>
            <p style="margin:0 0 4px;font-size:13px;color:#666;">Filter query:</p>
            <pre style="background:#fff;border:1px solid #ddd;border-radius:4px;
                        padding:10px;font-size:12px;overflow-x:auto;
                        margin:0 0 10px;">{filter_query}</pre>
            <p style="margin:0 0 4px;font-size:13px;color:#666;">Update query:</p>
            <pre style="background:#fff;border:1px solid #ddd;border-radius:4px;
                        padding:10px;font-size:12px;overflow-x:auto;
                        margin:0 0 0;">{update_query}</pre>
        </div>
        """

    confidence = task.get("confidence", "unknown")
    conf_color = {"high": "#27ae60", "medium": "#e67e22", "low": "#c0392b"}.get(
        confidence, "#888"
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;
                 padding:24px;color:#333;">

        <h2 style="color:#1a1a1a;border-bottom:2px solid #e0e0e0;padding-bottom:12px;">
            Invoice Correction Request — Approval Required
        </h2>

        <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
            <tr>
                <td style="padding:6px 0;color:#666;width:140px;">From</td>
                <td style="padding:6px 0;font-weight:bold;">{sender}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;color:#666;">Subject</td>
                <td style="padding:6px 0;">{subject}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;color:#666;">AI Confidence</td>
                <td style="padding:6px 0;">
                    <span style="background:{conf_color};color:#fff;padding:2px 10px;
                                 border-radius:12px;font-size:13px;">{confidence}</span>
                </td>
            </tr>
            <tr>
                <td style="padding:6px 0;color:#666;">Tasks found</td>
                <td style="padding:6px 0;">{len(tasks)}</td>
            </tr>
        </table>

        <h3 style="color:#333;margin-bottom:12px;">Generated Queries</h3>
        {task_rows}

        <h3 style="color:#333;margin:24px 0 12px;">Action Required</h3>
        <p style="color:#555;margin-bottom:20px;">
            Please review the queries above and take one of the following actions:
        </p>

        <table style="width:100%;border-collapse:separate;border-spacing:8px;">
            <tr>
                <td style="text-align:center;">
                    <a href="{approve_url}"
                       style="display:block;background:#27ae60;color:#fff;
                              text-decoration:none;padding:14px 0;border-radius:8px;
                              font-size:16px;font-weight:bold;">
                        ✓ Approve &amp; Execute
                    </a>
                </td>
                <td style="text-align:center;">
                    <a href="{reject_url}"
                       style="display:block;background:#c0392b;color:#fff;
                              text-decoration:none;padding:14px 0;border-radius:8px;
                              font-size:16px;font-weight:bold;">
                        ✗ Reject
                    </a>
                </td>
                <td style="text-align:center;">
                    <a href="{resolved_url}"
                       style="display:block;background:#7f8c8d;color:#fff;
                              text-decoration:none;padding:14px 0;border-radius:8px;
                              font-size:16px;font-weight:bold;">
                        ✓ Already Resolved
                    </a>
                </td>
            </tr>
        </table>

        <p style="margin-top:24px;font-size:12px;color:#999;">
            This link expires in 72 hours. Clicking Approve will immediately
            execute the MongoDB update on the invoices collection.
        </p>

    </body>
    </html>
    """

    to = REVIEWER_EMAIL
    if not to:
        print("[EMAIL] REVIEWER_EMAIL not set in .env — cannot send approval email")
        return False

    return _send_email(to, f"[ACTION REQUIRED] {subject}", html)


# ── 2. Acknowledgement email ──────────────────────────────────────────────────

def send_acknowledgement_email(task: dict) -> bool:
    """
    Sends acknowledgement email to McLarens after successful DB execution.
    Called from routes.py after mark_completed().

    Args:
        task: the full task document from automation_tasks

    Returns:
        True if sent successfully, False otherwise
    """
    sender_email = task.get("senderEmail", "")
    subject      = task.get("subject", "Invoice Correction")
    tasks        = task.get("tasks", [])

    if not sender_email:
        print("[EMAIL] No senderEmail on task — cannot send acknowledgement")
        return False

    # Build a summary of what was done
    summary_rows = ""
    for i, t in enumerate(tasks):
        description = t.get("description", "Update applied")
        set_fields  = t.get("update_query", {}).get("$set", {})
        fields_text = "<br>".join(
            f"<b>{k}:</b> {v}" for k, v in set_fields.items()
        )
        summary_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">
                {i + 1}. {description}
            </td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;
                    color:#555;font-size:13px;line-height:1.8;">
                {fields_text}
            </td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
                 padding:24px;color:#333;">

        <h2 style="color:#1a1a1a;border-bottom:2px solid #e0e0e0;padding-bottom:12px;">
            Invoice Correction — Completed
        </h2>

        <p style="color:#555;line-height:1.6;">
            Dear Team,<br><br>
            The requested invoice corrections have been completed successfully.
            Please find a summary of the updates applied below.
        </p>

        <table style="width:100%;border-collapse:collapse;margin:20px 0;
                      border:1px solid #e0e0e0;border-radius:8px;">
            <thead>
                <tr style="background:#f5f5f5;">
                    <th style="padding:10px 12px;text-align:left;
                               font-size:13px;color:#666;">Description</th>
                    <th style="padding:10px 12px;text-align:left;
                               font-size:13px;color:#666;">Fields Updated</th>
                </tr>
            </thead>
            <tbody>
                {summary_rows}
            </tbody>
        </table>

        <p style="color:#555;line-height:1.6;">
            Kindly verify the updated details on your end and revert on this
            thread if any further corrections are required.
        </p>

        <p style="color:#555;line-height:1.6;">
            Thanks &amp; Regards,<br>
            <strong>Wohlig Support Team</strong>
        </p>

    </body>
    </html>
    """

    return _send_email(sender_email, f"Re: {subject}", html)