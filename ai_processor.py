import json
import os
from datetime import date

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# =========================
# API CALL PROTECTION
# =========================

# Max AI calls allowed per day
MAX_DAILY_CALLS = 50

# Local file to track usage
_CALL_COUNT_FILE = ".api_call_count"


def _check_and_increment_call_count() -> bool:
    """
    Returns True if API call is allowed.
    Returns False if daily limit reached.

    Automatically resets every new day.
    """

    today = str(date.today())

    # Read existing count
    if os.path.exists(_CALL_COUNT_FILE):
        try:
            with open(_CALL_COUNT_FILE, "r") as f:
                data = f.read().strip().split(",")

                saved_date = data[0] if len(data) > 0 else ""
                saved_count = int(data[1]) if len(data) > 1 else 0

        except Exception:
            saved_date = ""
            saved_count = 0
    else:
        saved_date = ""
        saved_count = 0

    # Reset if date changed
    if saved_date != today:
        saved_count = 0

    # Block if limit reached
    if saved_count >= MAX_DAILY_CALLS:
        print(f"[AI] Daily API limit of {MAX_DAILY_CALLS} reached")
        return False

    # Increment count
    new_count = saved_count + 1

    with open(_CALL_COUNT_FILE, "w") as f:
        f.write(f"{today},{new_count}")

    print(f"[AI] API call {new_count}/{MAX_DAILY_CALLS} today")

    return True


# =========================
# OLLAMA CLIENT
# =========================

client = OpenAI(
    api_key=os.environ.get("OLLAMA_API_KEY"),
    base_url="https://ollama.com/v1"
)

# =========================
# LOAD STATIC FILES
# =========================

with open("mappings.json", "r") as f:
    MAPPINGS = json.load(f)

with open("sample_document.json", "r") as f:
    SAMPLE_DOCUMENT = json.load(f)

# =========================
# SYSTEM PROMPT
# =========================

SYSTEM_PROMPT = """
You are an AI assistant for a software company called Wohlig that manages invoice data for McLarens Insurance in MongoDB.

You will receive a full email thread where McLarens has reported one or more invoice correction tasks.

Your job is to:
1. Read the LATEST message in the thread — that is the actual task.
2. Use the older messages only as context.
3. Extract all fields that need to be updated.
4. Map client terminology to MongoDB field names using the provided mappings.
5. Generate a MongoDB filter query and update query.
6. Generate clear human steps for executing the update in MongoDB Compass.
7. Generate a professional reply email confirming the changes.

RULES:
- NEVER invent values. Only use values explicitly mentioned in the email.
- ALWAYS use invoiceNumber to filter — never use _id.
- Use $set for all updates.
- If multiple tasks exist in the latest message, return all of them in the tasks array.
- Dates must be in ISO format: YYYY-MM-DDTHH:mm:ss.000Z
- Reply email must be professional, short, and ask client to verify on same thread.
- confidence must be:
  - "high" if all fields are clearly present
  - "medium" if some are inferred
  - "low" if uncertain
- Respond ONLY with a valid JSON object.
- No explanation.
- No markdown.
- No backticks.
"""

# =========================
# PROMPT BUILDER
# =========================


def build_prompt(thread_text):
    return f"""
EMAIL THREAD (oldest to newest):
{thread_text}

FIELD MAPPINGS (client term -> MongoDB field):
{json.dumps(MAPPINGS, indent=2)}

MONGODB DOCUMENT SCHEMA (for reference):
{json.dumps(SAMPLE_DOCUMENT, indent=2)}

Based on the LATEST message in the thread above, extract all correction tasks and return this exact JSON structure:

{{
  "intent": "TASK",
  "confidence": "high | medium | low",
  "tasks": [
    {{
      "description": "short human readable summary of what needs to be done",

      "filter_query": {{
        "invoiceNumber": "<value from email>"
      }},

      "update_query": {{
        "$set": {{
          "<mongoField>": "<value>"
        }}
      }},

      "parsed_fields": [
        {{
          "clientTerm": "<term used in email>",
          "mongoField": "<mapped mongo field>",
          "newValue": "<value to set>",
          "confidence": "high | medium | low"
        }}
      ],

      "reply_email": "Dear Team,\\n\\nThe requested corrections for invoice <invoiceNumber> have been completed successfully.\\n\\nKindly verify the updated details and revert on this thread if any further changes are required.\\n\\nThanks & Regards,\\nWohlig Support Team"
    }}
  ]
}}
"""


# =========================
# MAIN PROCESS FUNCTION
# =========================

def process(email):
    """
    Run AI processing on a single email dict.
    Requires:
        email["thread_text"]

    Returns parsed JSON result.
    """

    # =========================
    # DAILY LIMIT PROTECTION
    # =========================

    if not _check_and_increment_call_count():
        return {
            "intent": "TASK",
            "confidence": "low",
            "tasks": [],
            "error": f"Daily API call limit of {MAX_DAILY_CALLS} reached"
        }

    # =========================
    # BUILD PROMPT
    # =========================

    user_prompt = build_prompt(email["thread_text"])

    try:
        response = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content.strip()

        # Remove accidental markdown wrapping
        if raw.startswith("```"):
            raw = raw.split("```")[1]

            if raw.startswith("json"):
                raw = raw[4:]

            raw = raw.strip()

        result = json.loads(raw)

        return result

    except json.JSONDecodeError as je:
        print(f"[ERROR] Failed to parse JSON: {je}")

        return {
            "intent": "TASK",
            "confidence": "low",
            "tasks": [],
            "error": f"Invalid JSON generation: {str(je)}"
        }

    except Exception as e:
        print(f"[ERROR] AI processing failed: {e}")

        return {
            "intent": "TASK",
            "confidence": "low",
            "tasks": [],
            "error": str(e)
        }