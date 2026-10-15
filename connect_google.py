"""
One-time step: connect MosshyBot to your Google Calendar.

Run it in the VS Code terminal:
    .venv\\Scripts\\python connect_google.py

A browser window opens -> choose your Google account -> allow access.
It then saves your permission in your private Firestore, where both this PC
and the cloud copy of the bot can use it.
Run it again any time the bot says Google Calendar access has expired.
"""

from datetime import timedelta

from dotenv import load_dotenv

from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()
import google_calendar  # noqa: E402  (after load_dotenv, so it finds your project)
import tracker  # noqa: E402

if not google_calendar.CREDENTIALS_FILE.exists():
    raise SystemExit("credentials.json is missing from this folder.\n"
                     "Follow README.md, 'Connecting Google Calendar', steps 1-5 first.")

flow = InstalledAppFlow.from_client_secrets_file(str(google_calendar.CREDENTIALS_FILE),
                                                 google_calendar.SCOPES)
print("Opening your browser to log in to Google...")
# A tiny web page runs on your PC for a moment to receive Google's answer.
# prompt="consent" makes Google always hand over a long-lasting permission.
credentials = flow.run_local_server(port=0, prompt="consent")
google_calendar.save_token(credentials)  # stored in your Firestore

# Quick test: read the next 7 days of your calendar.
today = tracker.now().date()
events = google_calendar.list_calendar_events(today.isoformat(),
                                              (today + timedelta(days=7)).isoformat())
print(f"\nConnected! You have {len(events)} event(s) in the next 7 days:")
for event in events[:10]:
    print(f"  - {event['start'][:16].replace('T', ' ')}  {event['title']}")
print("\nNow restart the bot so it picks up your calendar.")
