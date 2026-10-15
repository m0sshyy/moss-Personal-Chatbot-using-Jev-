"""
One-time move: copy everything from the old mosshy.db file into Firestore.

Run it in the VS Code terminal (with the bot stopped):
    .venv\\Scripts\\python migrate_to_firestore.py

Safe to run more than once: each old row becomes a Firestore document with a
fixed name ("sqlite-12" for row 12), so running it again overwrites instead of
making duplicates. mosshy.db itself is only read, never changed. Keep it as a backup.
"""

import sqlite3
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
import tracker  # noqa: E402  (imported after load_dotenv so it sees GOOGLE_CLOUD_PROJECT)

OLD_DB = Path(__file__).with_name("mosshy.db")
if not OLD_DB.exists():
    raise SystemExit("mosshy.db not found - nothing to copy.")

old = sqlite3.connect(OLD_DB)
old.row_factory = sqlite3.Row
started = tracker.now()


def rows(table: str) -> list[dict]:
    try:
        return [dict(row) for row in old.execute(f"SELECT * FROM {table}")]
    except sqlite3.OperationalError:  # table didn't exist in older versions
        return []


def copy(collection: str, documents: dict[str, dict]) -> None:
    """Write documents in batches of up to 500 (Firestore's limit per batch)."""
    items = list(documents.items())
    for start in range(0, len(items), 500):
        batch = tracker.db().batch()
        for doc_id, data in items[start:start + 500]:
            batch.set(tracker._collection(collection).document(doc_id), data)
        batch.commit()
    print(f"  {collection}: {len(items)} copied")


print(f"Copying {OLD_DB.name} -> Firestore ({tracker.db().project})...")
copy("entries", {
    f"sqlite-{r['id']}": {
        "logged_at": r["logged_at"], "category": r["category"], "item": r["item"],
        "amount": r["amount"] or 0, "unit": r["unit"] or "", "calories": r["calories"] or 0,
        "notes": r["notes"] or "", "tag": r.get("tag") or "",
    } for r in rows("entries")
})
copy("goals", {r["name"]: {"name": r["name"], "target": r["target"], "unit": r["unit"]}
               for r in rows("goals")})
copy("quick_buttons", {
    f"sqlite-{r['id']}": {
        "category": r["category"], "item": r["item"], "amount": r["amount"] or 0,
        "unit": r["unit"] or "", "calories": r["calories"] or 0, "tag": r["tag"] or "",
        # Keeps the buttons in their old order (they're sorted by "created").
        "created": (started + timedelta(milliseconds=r["id"])).isoformat(),
    } for r in rows("quick_buttons")
})
print("Done! Your data is in Firestore. mosshy.db was not changed - keep it as a backup.")
