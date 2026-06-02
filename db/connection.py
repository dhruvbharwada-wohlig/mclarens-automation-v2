"""
db/connection.py

Two separate MongoDB connections:

  get_db()          → company cluster → wohlig_automation
                      used for: automation_tasks (tracking, tokens, logs)

  get_mclarens_db() → friend's cluster (prod: client cluster) → mclarenc
                      used for: invoices (actual invoice updates)

To go to production, only change MCLARENS_MONGO_URI in .env.
No code changes needed anywhere.
"""

import os
import certifi
from pymongo import MongoClient
from pymongo.database import Database
from dotenv import load_dotenv

load_dotenv()

# Two separate clients — one per cluster
_automation_client: MongoClient = None
_mclarens_client:   MongoClient = None


# ── Company cluster — automation tracking ─────────────────────────────────────

def get_automation_client() -> MongoClient:
    global _automation_client
    if _automation_client is None:
        uri = os.environ.get("MONGO_URI")
        if not uri:
            raise ValueError("MONGO_URI is not set in .env")
        _automation_client = MongoClient(uri, tlsCAFile=certifi.where())
    return _automation_client


def get_db() -> Database:
    """
    Returns wohlig_automation database.
    Used by task_store.py for automation_tasks collection.
    """
    db_name = os.environ.get("MONGO_DB_NAME", "wohlig_automation")
    return get_automation_client()[db_name]


# ── Friend's cluster (later: client cluster) — invoice operations ─────────────

def get_mclarens_client() -> MongoClient:
    global _mclarens_client
    if _mclarens_client is None:
        uri = os.environ.get("MCLARENS_MONGO_URI")
        if not uri:
            raise ValueError("MCLARENS_MONGO_URI is not set in .env")
        _mclarens_client = MongoClient(uri, tlsCAFile=certifi.where())
    return _mclarens_client


def get_mclarens_db() -> Database:
    """
    Returns mclarenc database.
    Used by routes.py for invoice updates.
    """
    db_name = os.environ.get("MCLARENS_DB_NAME", "mclarenc")
    return get_mclarens_client()[db_name]


def get_invoices_collection():
    """
    Shortcut — returns the invoices collection directly.
    This is what routes.py uses to execute updates.
    """
    collection_name = os.environ.get("MCLARENS_COLLECTION", "invoices")
    return get_mclarens_db()[collection_name]


# ── Index setup ───────────────────────────────────────────────────────────────

def setup_indexes():
    """
    Run once when setting up the project.
    Creates indexes on automation_tasks only — never touches mclarenc.
    Safe to re-run.
    """
    col = get_db()["automation_tasks"]

    col.create_index("gmailMessageId", unique=True)  # dedup guard
    col.create_index("approvalToken",  unique=True)  # token lookups
    col.create_index("status")                        # filter by state
    col.create_index("createdAt")                     # time-based queries

    print("[DB] Indexes created on automation_tasks")


if __name__ == "__main__":
    # Run once to set up indexes:
    # python -m db.connection
    setup_indexes()
    print("[DB] Automation DB OK:", get_db().name)
    print("[DB] McLarens DB OK:  ", get_mclarens_db().name)