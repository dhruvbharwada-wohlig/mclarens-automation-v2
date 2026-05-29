import base64


class ThreadReader:

    def extract_body(self, payload):

        body = ""

        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data")
            if data:
                body += base64.urlsafe_b64decode(
                    data
                ).decode("utf-8", errors="ignore")

        for part in payload.get("parts", []):
            body += self.extract_body(part)

        return body

    def get_header(self, headers, name):

        for header in headers:
            if header["name"].lower() == name.lower():
                return header["value"]

        return ""

    def reconstruct(self, service, thread_id):
        """
        Fetch full thread and return messages oldest to newest.

        Returns list of:
        {
            "sender":  str,
            "date":    str,
            "subject": str,
            "body":    str
        }
        """

        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full"
        ).execute()

        messages = thread.get("messages", [])

        reconstructed = []

        for message in messages:

            headers = message["payload"]["headers"]

            reconstructed.append({
                "sender":  self.get_header(headers, "From"),
                "date":    self.get_header(headers, "Date"),
                "subject": self.get_header(headers, "Subject"),
                "body":    self.extract_body(message["payload"]).strip()
            })

        return reconstructed

    def to_text(self, thread_messages):
        """
        Convert reconstructed thread into a clean string for the LLM.
        """

        lines = []

        for i, msg in enumerate(thread_messages, start=1):
            lines.append(f"--- Message {i} ---")
            lines.append(f"From    : {msg['sender']}")
            lines.append(f"Date    : {msg['date']}")
            lines.append(f"Subject : {msg['subject']}")
            lines.append(f"Body    :\n{msg['body']}")
            lines.append("")

        return "\n".join(lines)