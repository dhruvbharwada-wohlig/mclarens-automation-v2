import json
import time  # <--- Imported for sleep delays
import google.api_core.exceptions # <--- Imported to catch Gemini exceptions

from app.gmail_reader import GmailReader
from app.tagger import Tagger
from app.dispatcher import Dispatcher
from ai_processor import process

reader     = GmailReader()
tagger     = Tagger()
dispatcher = Dispatcher()

# Phase 1 + 2 — fetch emails and reconstruct full threads
emails = reader.read_emails()

if not emails:
    print("[MAIN] No task emails found.")
else:
    for email in emails:

        # Phase 3 — auto tag from thread text
        email["tags"] = tagger.tag(email["thread_text"])

        # Phase 4 + 5 — run Gemini, get queries + steps + reply
        print(f"[MAIN] Processing: {email['subject']}")
        
        # --- ADDED: Robust Retry Loop for API Quotas ---
        result = None
        retries = 3
        delay = 22  # The error message explicitly asked for ~21.7 seconds
        
        for attempt in range(retries):
            try:
                result = process(email)
                break  # Success! Break out of the retry loop
            except google.api_core.exceptions.ResourceExhausted as e:
                print(f"[WARNING] Rate limit hit on '{email['subject']}'.")
                if attempt < retries - 1:
                    print(f"Waiting {delay} seconds before retrying (Attempt {attempt + 1}/{retries})...")
                    time.sleep(delay)
                    # Double the delay for the next attempt (exponential backoff)
                    delay *= 2 
                else:
                    print("[ERROR] Max retries reached for this email. Skipping to avoid total crash.")
                    break
            except Exception as e:
                print(f"[ERROR] Unexpected error: {e}")
                break

        # If processing failed entirely for this email, skip to the next one
        if not result:
            print(f"[MAIN] Skipping dispatcher for: {email['subject']}\n")
            continue
        # ------------------------------------------------

        # Merge AI result into email payload for dispatcher
        email["generatedSolution"] = "\n".join(
            task["description"] for task in result.get("tasks", [])
        )
        email["parsedFields"]  = [
            field
            for task in result.get("tasks", [])
            for field in task.get("parsed_fields", [])
        ]
        email["confidence"]    = result.get("confidence", "medium")
        email["ai_result"]     = result  # full result for dispatcher

        # Phase 6 — dispatch finished payload to friend's backend
        dispatcher.send(email)

        print(json.dumps(result, indent=2)) 
        
        # --- OPTIONAL: Friendly proactive delay ---
        # Keeps you under the Requests Per Minute (RPM) radar 
        time.sleep(2)