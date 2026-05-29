from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import os
import base64

from app.thread_reader import ThreadReader

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Accepted sender domains — tasks come from client (mclarens.in) or forwarded by wohlig senior
TARGET_DOMAINS = ["mclarens.in", "wohlig.com"]


class GmailReader:

    def authenticate(self):

        creds = None

        if os.path.exists("credentials/token.json"):
            creds = Credentials.from_authorized_user_file(
                "credentials/token.json",
                SCOPES
            )

        if not creds or not creds.valid:

            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())

            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    "credentials/credentials.json",
                    SCOPES
                )

                creds = flow.run_local_server(port=0)

            with open("credentials/token.json", "w") as token:
                token.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)

        return service

    def classify_intent(self, text):

        text = text.lower()

        # Check TASK first — forwarded threads may contain both task and ack keywords
        task_keywords = [
            "irn details not updated",
            "irn number was not updated",
            "not updated in",
            "not captured in",
            "kindly update",
            "kindly update the irn",
            "please do the needful",
            "pls do the needful",
            "do the needful",
            "details to be updated",
            "need attention",
            "need a attention",
            "kindly arrange",
            "invoice date to be changed",
            "ack date to be changed",
            "irn no",
            "ack no",
            "ack date",
            "file no",
            "invoice no",
            "invoice date",
        ]

        acknowledgement_keywords = [
            "both the issues have been resolved",
            "issue has been resolved",
            "the issue has been resolved",
            "please verify and revert",
            "kindly check now and revert",
            "updated. thanks",
            "updated thanks",
            "issue resolved",
        ]

        # TASK check runs first — if any task keyword found, classify as TASK
        for keyword in task_keywords:
            if keyword in text:
                return "TASK"

        # Only check acknowledgement if no task keyword matched
        for keyword in acknowledgement_keywords:
            if keyword in text:
                return "ACKNOWLEDGEMENT"

        return "UNKNOWN"

    def extract_body(self, payload):
        """Recursively extract plain text body from potentially nested MIME parts."""

        body = ""

        # Direct body on this payload
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data")
            if data:
                body += base64.urlsafe_b64decode(
                    data
                ).decode("utf-8", errors="ignore")

        # Recurse into parts
        for part in payload.get("parts", []):
            body += self.extract_body(part)

        return body

    def read_emails(self):

        service = self.authenticate()

        results = service.users().messages().list(
            userId="me",
            maxResults=30
        ).execute()

        messages = results.get("messages", [])

        relevant_messages = []

        thread_reader = ThreadReader()

        for msg in messages:

            message = service.users().messages().get(
                userId="me",
                id=msg["id"]
            ).execute()

            headers = message["payload"]["headers"]

            subject = ""
            sender = ""

            for header in headers:

                if header["name"] == "Subject":
                    subject = header["value"]

                if header["name"] == "From":
                    sender = header["value"]

            snippet = message.get("snippet", "")
            thread_id = message.get("threadId", "")

            # Recursively extract full body (handles nested forwarded MIME parts)
            body = self.extract_body(message["payload"])

            sender_lower = sender.lower()
            body_lower = body.lower()

            # Accept if sender is from any target domain OR body mentions any target domain
            domain_match = any(
                domain in sender_lower or domain in body_lower
                for domain in TARGET_DOMAINS
            )

            if not domain_match:
                continue

            email_content = f"{subject} {snippet} {body}".lower()

            intent = self.classify_intent(email_content)

            if intent != "TASK":
                continue

            # Reconstruct full thread for LLM context
            thread_messages = thread_reader.reconstruct(service, thread_id)
            thread_text     = thread_reader.to_text(thread_messages)

            relevant_messages.append({
                "sender":         sender,
                "subject":        subject,
                "thread_id":      thread_id,
                "snippet":        snippet,
                "body":           body,
                "intent":         intent,
                "thread_messages": thread_messages,   # structured list for LLM
                "thread_text":    thread_text          # readable string for LLM
            })

            print("=" * 60)
            print(f"FROM      : {sender}")
            print(f"SUBJECT   : {subject}")
            print(f"THREAD ID : {thread_id}")
            print(f"SNIPPET   : {snippet}")
            print(f"INTENT    : {intent}")
            print(f"THREAD MESSAGES: {len(thread_messages)}+ messages in thread")
            print("=" * 60)

            if len(relevant_messages) == 2:
                break

        return relevant_messages