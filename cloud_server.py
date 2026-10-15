"""
MosshyBot's web server for Google Cloud Run (the 24/7 cloud version).

On your PC the bot keeps asking Telegram "any new messages?" (polling). In the
cloud it's the other way round: Telegram sends each new message to this
server's web address (a "webhook"). The addresses:

    POST /telegram     Telegram delivers each message or button tap here
    POST /shortcut     your iPhone's Siri Shortcuts (siri.py)
    GET  /dashboard    the web dashboard (dashboard.py; log in with /dashboard in Telegram)
    POST /jobs/<name>  Google Cloud Scheduler triggers scheduled messages here
                       ("monthly-report", "weekly-news", and "tick" every 15
                       minutes for reminders)
    GET  /             a simple "I'm alive" page

Every POST address checks a secret, so only Telegram and your Cloud Scheduler
can use them, even though the address itself is public.

Cloud Run starts this with:  uvicorn cloud_server:web
"""

import contextlib
import logging
import os
import secrets
from datetime import timedelta

from google.api_core.exceptions import AlreadyExists
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from telegram import Update

import MosshyBot as bot
import dashboard
import reminders
import siri
import tracker

log = logging.getLogger("server")
# Telegram sends this secret with every message (we set it with telegram_webhook.py).
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
# Cloud Scheduler sends this one with every scheduled job.
JOB_SECRET = os.environ["JOB_SECRET"]

application = bot.build_app()
SCHEDULED = {**bot.SCHEDULED, "tick": reminders.tick}


def _has_secret(request: Request, header: str, expected: str) -> bool:
    # compare_digest takes the same time whether the guess is close or not,
    # so nobody can work the secret out by timing the answers.
    return secrets.compare_digest(request.headers.get(header, ""), expected)


def first_time_seen(update_id: int) -> bool:
    """If we're slow to answer (e.g. a 40-second news search), Telegram may send
    the same message again. Each update_id is remembered for 2 days, so a repeat
    is ignored instead of being answered twice. Firestore deletes the old ones."""
    try:
        tracker._collection("processed_updates").document(str(update_id)).create(
            {"expire_at": tracker.now() + timedelta(days=2)})
        return True
    except AlreadyExists:
        return False


async def telegram_webhook(request: Request) -> Response:
    if not _has_secret(request, "X-Telegram-Bot-Api-Secret-Token", WEBHOOK_SECRET):
        return Response(status_code=403)
    update = Update.de_json(await request.json(), application.bot)
    if first_time_seen(update.update_id):
        # Finish handling the message *before* answering Telegram: Cloud Run
        # only gives the bot CPU while a request is still in progress.
        await application.process_update(update)
    return Response()  # "200 OK" tells Telegram the message was delivered


async def scheduled_job(request: Request) -> Response:
    if not _has_secret(request, "X-Job-Secret", JOB_SECRET):
        return Response(status_code=403)
    job = SCHEDULED.get(request.path_params["name"])
    if job is None:
        return PlainTextResponse("No such job", status_code=404)
    await job(application.bot)
    return PlainTextResponse("Done")


async def siri_shortcut(request: Request) -> Response:
    """Your iPhone's Shortcuts (Siri commands, see siri.py) post here."""
    if not siri.secret() or not _has_secret(request, "X-Shortcut-Secret", siri.secret()):
        return Response(status_code=403)
    try:
        data = await request.json()
    except ValueError:  # Shortcuts can also send a form instead of JSON
        data = dict(await request.form())
    return PlainTextResponse(await siri.handle(application.bot, data if isinstance(data, dict) else {}))


PAGE_HEADERS = {  # browser safety settings for the dashboard pages
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
                                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"),
}


def _login_page(message: str) -> HTMLResponse:
    return HTMLResponse(dashboard.LOGIN_NEEDED.replace("{message}", message), status_code=401, headers=PAGE_HEADERS)


async def dashboard_page(request: Request) -> Response:
    if not dashboard.valid_session(request.cookies.get(dashboard.COOKIE)):
        return _login_page("Send <b>/dashboard</b> to Moss in Telegram to get your login link ♡")
    return HTMLResponse(dashboard.PAGE, headers=PAGE_HEADERS)


async def dashboard_login(request: Request) -> Response:
    """The one-time link from Telegram: swaps it for a 30-day login cookie."""
    if not dashboard.use_login_token(request.query_params.get("t", "")):
        return _login_page("That link has expired or was already used. Send <b>/dashboard</b> again ♡")
    response = RedirectResponse("/dashboard", status_code=303, headers=PAGE_HEADERS)
    response.set_cookie(dashboard.COOKIE, dashboard.new_session(), max_age=dashboard.SESSION_DAYS * 86400,
                        httponly=True, secure=True, samesite="lax", path="/dashboard")
    return response


async def dashboard_data(request: Request) -> Response:
    if not dashboard.valid_session(request.cookies.get(dashboard.COOKIE)):
        return JSONResponse({"error": "login needed"}, status_code=401)
    days = {"7": 7, "90": 90}.get(request.query_params.get("days", ""), 30)
    return JSONResponse(dashboard.data(days), headers={"Cache-Control": "no-store"})


async def alive(request: Request) -> Response:
    return PlainTextResponse("MosshyBot is running")


@contextlib.asynccontextmanager
async def lifespan(app):
    """Runs once when Cloud Run starts the bot, and once when it shuts it down."""
    tracker.setup_database()
    await application.initialize()
    log.info("MosshyBot web server ready (Google Calendar: %s)",
             "connected" if bot.CALENDAR_ON else "not connected")
    yield
    await application.shutdown()


web = Starlette(
    routes=[
        Route("/", alive),
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/jobs/{name}", scheduled_job, methods=["POST"]),
        Route("/shortcut", siri_shortcut, methods=["POST"]),
        Route("/dashboard", dashboard_page),
        Route("/dashboard/login", dashboard_login),
        Route("/dashboard/data", dashboard_data),
    ],
    lifespan=lifespan,
)
