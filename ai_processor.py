import json
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Load mappings and sample document once at startup
with open("mappings.json", "r") as f:
    MAPPINGS = json.load(f)

with open("sample_document.json", "r") as f:
    SAMPLE_DOCUMENT = json.load(f)

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
- confidence must be: "high" if all fields are clearly present, "medium" if some are inferred, "low" if uncertain.
- Respond ONLY with a valid JSON object. No explanation, no markdown, no backticks.
"""


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
      "human_steps": [
        "1. Open the invoices collection in MongoDB Compass",
        "2. Paste the filter query to find the document",
        "3. Switch to the update tab and paste the update query",
        "4. Click Update to apply changes",
        "5. Verify the updated fields match the email values"
      ],
      "reply_email": "Dear Team,\\n\\nThe requested corrections for invoice <invoiceNumber> have been completed successfully.\\n\\nKindly verify the updated details and revert on this thread if any further changes are required.\\n\\nThanks & Regards,\\nWohlig Support Team"
    }}
  ]
}}
"""


def process(email):
    """
    Run Groq on a single email dict (must have thread_text key).
    Returns parsed JSON result.
    """

    user_prompt = build_prompt(email["thread_text"])

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content.strip()

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        return result

    except json.JSONDecodeError as je:
        print(f"[ERROR] Failed to parse JSON from Groq. Error: {je}")
        return {
            "intent": "TASK",
            "confidence": "low",
            "tasks": [],
            "error": f"Invalid JSON generation: {str(je)}"
        }