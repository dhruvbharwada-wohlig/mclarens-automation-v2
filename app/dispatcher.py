import json
import requests

# Good practice to externalize configuration or keep it cleanly at the top
# WEBHOOK_URL = "http://localhost:3000/api/webhook/new-email"
WEBHOOK_URL = "http://192.168.1.127:3000/api/webhook/new-email"

class Dispatcher:

    def send(self, email):
        """
        Send fully processed email with AI result to friend's webhook.
        """
        ai_result = email.get("ai_result", {})
        tasks     = ai_result.get("tasks", [])

        # Build human readable solution from all tasks
        solution_lines = []
        for i, task in enumerate(tasks, start=1):
            solution_lines.append(f"Task {i}: {task.get('description', 'No description provided')}")
            solution_lines.append("Steps:")
            # Use list comprehensions or extend safely
            for step in task.get("human_steps", []):
                solution_lines.append(f"  {step}")
            solution_lines.append(f"Filter Query: {json.dumps(task.get('filter_query', {}))}")
            solution_lines.append(f"Update Query: {json.dumps(task.get('update_query', {}))}")
            solution_lines.append("")

        # Safely extract values using .get() to avoid sudden KeyError failures
        payload = {
            "clientName":        "McLarens Insurance",
            "subject":           email.get("subject", "No Subject"),
            "emailBody":         email.get("thread_text", email.get("body", "")), # Fallback safety
            "priority":          "normal",
            "tags":              email.get("tags", []),
            "generatedSolution": "\n".join(solution_lines).strip(),
            "parsedFields":      email.get("parsedFields", []),
            "confidence":        email.get("confidence", "medium")
        }

        try:
            # CRITICAL: Added a 10-second timeout so a stuck server won't hang your pipeline
            response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
            response.raise_for_status()

            print(f"[DISPATCHER] ✓ Sent     : {payload['subject']}")
            print(f"[DISPATCHER] ✓ Status   : {response.status_code}")
            print(f"[DISPATCHER] ✓ Tasks    : {len(tasks)}")

            # Handle endpoints that return empty responses (like 204 No Content) gracefully
            if response.status_code == 204 or not response.text:
                return {"status": "success"}
            return response.json()

        except requests.exceptions.Timeout:
            print(f"[DISPATCHER] ✗ Timeout  : {payload['subject']} (Server took too long to respond)")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[DISPATCHER] ✗ Failed   : {payload['subject']}")
            print(f"[DISPATCHER] ✗ Error    : {e}")
            return None

    def send_all(self, emails):
        """
        Optional helper if you decide to offload the loop from main.py
        """
        results = []
        for email in emails:
            result = self.send(email)
            if result:
                results.append(result)
        return results