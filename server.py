"""Job Agent — FastAPI server.

Routes, session management, the onboarding chat, and the JSON API.

Sign-in model
-------------
Email and password. An account is only attached to a browser session by a path
that has actually proven identity — a correct password, or a personal sign-in
link containing a per-user secret. Typing a known email address into the
onboarding chat does NOT sign you in.

Email model
-----------
The app owns one mailbox (SMTP_USER / SMTP_PASS). Every digest is sent from it
to whatever address the user gave during onboarding, and every reply comes back
to it, matched to a user by the address it came from. Users do not connect their
own mail account.
"""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from apply.cover_letter import generate_cover_letter
from db.database import (
    attach_user_to_session, clear_session, count_users, create_session,
    create_user, finish_digest_run, get_all_users, get_criteria,
    get_digest_batch_jobs, get_latest_digest_batch, get_latest_digest_run,
    get_owned_user_job, get_session, get_session_state, get_unsent_user_jobs,
    get_user, get_user_by_email, get_user_by_invite_token, get_user_job,
    get_user_by_login_token, get_user_jobs, init_db, mark_digest_sent,
    set_session_state, start_digest_run, update_criteria, update_user,
    update_user_job, upsert_job, upsert_user_job,
)
from auth import PasswordError, hash_password, verify_password
from email_handler.digest import (
    application_subject, build_application_html, build_application_text,
    build_digest_html, build_digest_text, digest_subject,
)
from email_handler.mailbox import (
    MailboxError, extract_reply_numbers, fetch_replies, mailbox_address,
    mailbox_configured, send_email,
)
from llm import LLMError
from llm import describe as llm_describe
from matching.scorer import score_jobs_for_user
from sourcing.greenhouse import fetch_greenhouse_jobs
from sourcing.indeed import fetch_indeed_jobs
from sourcing.lever import fetch_lever_jobs
from sourcing.remotive import fetch_remotive_jobs

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
DB_PATH = os.environ.get("DB_PATH", "jobagent.db")
# Invites are required by default so a public URL isn't an open sign-up form.
# The very first account (the owner) is always allowed through.
REQUIRE_INVITE = os.environ.get("REQUIRE_INVITE", "1") not in ("0", "false", "False")
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") not in ("0", "false", "False")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "8"))
TIMEZONE = os.environ.get("TIMEZONE", "UTC")
# How many jobs go in one digest email.
DIGEST_LIMIT = int(os.environ.get("DIGEST_LIMIT", "10"))
# How often to check for replies to a digest.
REPLY_POLL_MINUTES = int(os.environ.get("REPLY_POLL_MINUTES", "15"))

# New accounts start watching these company job boards, so a friend gets real
# matches from onboarding alone without knowing what a "board slug" is. They
# can add or remove companies in Settings afterwards.
DEFAULT_GREENHOUSE_COMPANIES = [
    "stripe", "airbnb", "doordash", "coinbase", "robinhood", "instacart",
    "reddit", "dropbox", "gitlab", "databricks", "anthropic", "discord",
]
DEFAULT_LEVER_COMPANIES = ["plaid", "ramp", "attentive"]

signer = URLSafeSerializer(SECRET_KEY)
scheduler = AsyncIOScheduler(timezone=TIMEZONE)


def startup_report() -> list[str]:
    """Things that would make the app quietly do nothing. Printed at boot."""
    problems = []
    try:
        llm_provider = llm_describe()
    except Exception as exc:  # pragma: no cover - defensive
        llm_provider = f"not configured — {exc}"
    if "not configured" in llm_provider:
        problems.append(
            f"No AI key set ({llm_provider}) — jobs can't be scored and no cover "
            "letters can be written. Nothing will reach anyone's inbox."
        )
    if SECRET_KEY == "change-me-in-production":
        problems.append(
            "SECRET_KEY is still the default — anyone could forge a login. "
            "Set it to a random string."
        )
    if not mailbox_configured():
        problems.append(
            "No mailbox configured — set SMTP_USER and SMTP_PASS (a Gmail "
            "address and an App Password). Without it nobody receives a digest "
            "and replies can't be read; matches only appear on the dashboard."
        )
    if BASE_URL.startswith("http://localhost") or BASE_URL.startswith("http://127."):
        problems.append(
            f"BASE_URL is {BASE_URL} — fine locally, but invite links will be "
            "broken if this is a real deployment."
        )
    return problems


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)

    problems = startup_report()
    if problems:
        print("\n  Job Agent started, but some things won't work:")
        for problem in problems:
            print(f"   - {problem}")
        print()
    else:
        print(f"\n  Job Agent ready. AI: {llm_describe()}. Mail: "
              f"{mailbox_address()}. Digests at {DIGEST_HOUR:02d}:00 {TIMEZONE}, "
              f"replies checked every {REPLY_POLL_MINUTES} min.\n")

    if ENABLE_SCHEDULER:
        scheduler.add_job(run_all_digests, "cron", hour=DIGEST_HOUR, minute=0)
        # Without this, replying to the digest does nothing until someone opens
        # the dashboard and asks for a check.
        scheduler.add_job(poll_all_replies, "interval", minutes=REPLY_POLL_MINUTES)
        scheduler.start()
    yield
    if ENABLE_SCHEDULER and scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Job Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ── Sessions ───────────────────────────────────────────────────────────────

def set_session_cookie(response: Response, session_id: str):
    response.set_cookie(
        "session",
        signer.dumps(session_id),
        httponly=True,
        samesite="lax",
        secure=BASE_URL.startswith("https://"),
        max_age=60 * 60 * 24 * 30,
    )


def read_session_id(request: Request) -> Optional[str]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return signer.loads(token)
    except BadSignature:
        return None


def ensure_session(request: Request) -> str:
    """Return this browser's session id, creating one if needed."""
    sid = read_session_id(request)
    if not sid:
        sid = str(uuid.uuid4())
    create_session(sid)
    return sid


def get_current_user(request: Request) -> Optional[dict]:
    sid = read_session_id(request)
    if not sid:
        return None
    sess = get_session(sid)
    if not sess or not sess.get("user_id"):
        return None
    return get_user(sess["user_id"])


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def sign_in(response: Response, session_id: str, user_id: int):
    attach_user_to_session(session_id, user_id)
    set_session_cookie(response, session_id)


# ── Sign-up gating ─────────────────────────────────────────────────────────

def signup_check(state: dict) -> tuple[bool, str]:
    """Can the person holding this session create an account?"""
    if count_users() == 0:
        return True, ""
    if not REQUIRE_INVITE:
        return True, ""
    inviter = get_user_by_invite_token(state.get("invite_token", ""))
    if inviter:
        return True, ""
    return False, (
        "This Job Agent is invite-only. Ask whoever runs it for their invite "
        "link — it looks like /onboard?invite=…"
    )


# ── Root ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse("/dashboard" if get_current_user(request) else "/onboard")


# ── Onboarding ─────────────────────────────────────────────────────────────

ONBOARD_STEPS = [
    {"key": "name",        "prompt": "Hi! I'm your job agent. What's your name?"},
    {"key": "email",       "prompt": "Nice to meet you, {name}! What email should I send your job digest to?"},
    {"key": "job_titles",  "prompt": "What job titles are you looking for? (e.g. Product Manager, Senior PM)"},
    {"key": "locations",   "prompt": "Where would you like to work? List cities, or type 'remote' for remote-only."},
    {"key": "remote_pref", "prompt": "Remote preference — any / remote / hybrid / onsite?"},
    {"key": "salary",      "prompt": "What's your salary range? (e.g. $120k-$160k, or skip)"},
    {"key": "seniority",   "prompt": "Seniority levels? (e.g. Senior, Staff, Lead — or skip)"},
    {"key": "resume",      "prompt": "Upload your resume (PDF) or paste the text below so I can tailor cover letters."},
    {"key": "password",    "prompt": "Last step — pick a password so you can sign back in later (at least 8 characters)."},
]
TOTAL_STEPS = len(ONBOARD_STEPS)


@app.get("/onboard", response_class=HTMLResponse)
async def onboard_page(request: Request, invite: Optional[str] = None):
    if get_current_user(request):
        return RedirectResponse("/dashboard")

    sid = ensure_session(request)
    state = get_session_state(sid)
    if invite:
        state["invite_token"] = invite
        set_session_state(sid, state)

    allowed, reason = signup_check(state)
    response = templates.TemplateResponse(
        request,
        "onboard.html",
        {
            "total_steps": TOTAL_STEPS,
            "first_prompt": ONBOARD_STEPS[0]["prompt"],
            "mail_ready": mailbox_configured(),
            "signup_blocked": None if allowed else reason,
        },
    )
    # Set the cookie on the template response directly. Copying headers across
    # from a throwaway Response() also copies its content-length: 0, which
    # truncates the page to nothing.
    set_session_cookie(response, sid)
    return response


@app.get("/signin", response_class=HTMLResponse)
async def signin_page(request: Request, error: Optional[str] = None):
    # A signed-in visitor has nothing to do here — unless we were sent here to
    # explain something, in which case bouncing them back is an invisible loop.
    if get_current_user(request) and not error:
        return RedirectResponse("/dashboard")
    sid = ensure_session(request)
    response = templates.TemplateResponse(
        request,
        "signin.html",
        {
            "mail_ready": mailbox_configured(),
            "error": error,
        },
    )
    set_session_cookie(response, sid)
    return response


def _chat_reply(sid: str, reply: str, step_num: int, action: str | None = None):
    response = JSONResponse({"reply": reply, "step_num": step_num, "action": action})
    set_session_cookie(response, sid)
    return response


def _parse_step(key: str, message: str, data: dict) -> str | None:
    """Record one onboarding answer. Returns an error message to show, if any."""
    if key == "name":
        data["name"] = message.strip().title()

    elif key == "email":
        if "@" not in message:
            return "That doesn't look like an email address. Try again."
        data["email"] = message.strip().lower()

    elif key == "job_titles":
        data["job_titles"] = [t.strip() for t in message.split(",") if t.strip()]

    elif key == "locations":
        if message.lower() in ("remote", "skip", ""):
            data["locations"] = []
            data["remote_preference"] = "remote"
        else:
            data["locations"] = [l.strip() for l in message.split(",") if l.strip()]

    elif key == "remote_pref":
        pref = message.lower().strip().rstrip(".")
        data["remote_preference"] = pref if pref in ("any", "remote", "hybrid", "onsite") else "any"

    elif key == "salary":
        if message.lower() not in ("skip", ""):
            import re
            nums = re.findall(r"\d+", message.replace("k", "000").replace("K", "000"))
            if len(nums) >= 2:
                data["min_salary"], data["max_salary"] = int(nums[0]), int(nums[1])
            elif len(nums) == 1:
                data["min_salary"] = int(nums[0])

    elif key == "seniority":
        if message.lower() not in ("skip", ""):
            data["seniority_levels"] = [s.strip() for s in message.split(",") if s.strip()]

    elif key == "resume":
        if len(message.strip()) > 50:
            data["resume_text"] = message.strip()[:8000]

    elif key == "password":
        try:
            data["password_hash"] = hash_password(message)
        except PasswordError as exc:
            return str(exc)

    return None


CRITERIA_KEYS = {
    "job_titles", "locations", "remote_preference", "min_salary", "max_salary",
    "seniority_levels",
}


def create_account_from_state(state: dict, email: str) -> int:
    """Create the user + criteria rows from collected onboarding answers."""
    data = state.get("data", {})
    inviter = get_user_by_invite_token(state.get("invite_token", ""))
    user_id = create_user(data.get("name") or "There", email)

    criteria = {k: v for k, v in data.items() if k in CRITERIA_KEYS}
    # Seed the job boards so the first digest has somewhere to look.
    criteria["greenhouse_companies"] = list(DEFAULT_GREENHOUSE_COMPANIES)
    criteria["lever_companies"] = list(DEFAULT_LEVER_COMPANIES)
    update_criteria(user_id, criteria)
    if data.get("resume_text"):
        update_user(user_id, {"resume_text": data["resume_text"]})
    if inviter:
        print(f"[signup] user {user_id} joined via invite from user {inviter['id']}")
    return user_id


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()

    sid = ensure_session(request)
    state = get_session_state(sid)
    step_num = state.get("step_num", 0)
    data = state.setdefault("data", {})

    if step_num >= len(ONBOARD_STEPS):
        return _chat_reply(sid, "You're all set — head to your dashboard.", step_num,
                           "redirect:/dashboard")

    step = ONBOARD_STEPS[step_num]
    error = _parse_step(step["key"], message, data)
    if error:
        return _chat_reply(sid, error, step_num)

    # An email that already has an account is a sign-in, not a sign-up — and it
    # has to go through a path that proves identity.
    if step["key"] == "email" and get_user_by_email(data["email"]):
        return _chat_reply(
            sid,
            "You already have an account with that email. Taking you to sign in...",
            step_num,
            "redirect:/signin",
        )

    if step["key"] == "name":
        allowed, reason = signup_check(state)
        if not allowed:
            return _chat_reply(sid, reason, step_num)

    state["step_num"] = step_num + 1
    state["data"] = data
    set_session_state(sid, state)

    # Answering the last question is the sign-up.
    if step["key"] == "password":
        return _create_account(sid, state)

    return _chat_reply(sid, *_next_prompt(state))


def _create_account(sid: str, state: dict):
    """Final onboarding step: make the account and sign the browser in."""
    data = state.get("data", {})

    allowed, reason = signup_check(state)
    if not allowed:
        return _chat_reply(sid, reason, state["step_num"])
    if not data.get("email") or not data.get("password_hash"):
        return _chat_reply(
            sid, "Something went missing — let's start over.", 0, "redirect:/onboard"
        )
    if get_user_by_email(data["email"]):
        return _chat_reply(
            sid,
            "That email already has an account. Taking you to sign in...",
            state["step_num"], "redirect:/signin",
        )

    user_id = create_account_from_state(state, data["email"])
    update_user(user_id, {"password_hash": data["password_hash"]})

    where = (
        f"I'll email your first digest to {data['email']}."
        if mailbox_configured() else
        "Heads up: email isn't set up on this deployment yet, so your matches "
        "will only appear on your dashboard for now."
    )
    response = _chat_reply(
        sid, f"You're all set. {where} Taking you to your dashboard...",
        state["step_num"], "redirect:/dashboard",
    )
    sign_in(response, sid, user_id)
    return response


def _next_prompt(state: dict) -> tuple[str, int, str | None]:
    """(reply, step_num, action) for whatever step the session is now on."""
    step_num = state["step_num"]
    step = ONBOARD_STEPS[step_num]
    if step["key"] == "resume":
        return step["prompt"], step_num, "show_resume_upload"
    if step["key"] == "password":
        return step["prompt"], step_num, "show_password"
    return step["prompt"].format(**state.get("data", {})), step_num, None


@app.post("/api/resume-upload")
async def resume_upload(request: Request, file: UploadFile = File(...)):
    sid = ensure_session(request)
    state = get_session_state(sid)
    data = state.setdefault("data", {})

    content = await file.read()
    try:
        import io

        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        text = content.decode("utf-8", errors="ignore")

    data["resume_text"] = text[:8000]
    # Jump to the final step regardless of where they were — the upload IS the
    # answer to the resume question.
    state["step_num"] = len(ONBOARD_STEPS) - 1
    state["data"] = data
    set_session_state(sid, state)

    return _chat_reply(
        sid,
        f"Got it — {len(text.split())} words. {ONBOARD_STEPS[-1]['prompt']}",
        state["step_num"], "show_password",
    )


# ── Sign in ────────────────────────────────────────────────────────────────

@app.post("/api/signin")
async def api_signin(request: Request):
    """Email + password. Deliberately vague about which half was wrong."""
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    user = get_user_by_email(email) if email else None
    if not user or not verify_password(password, user.get("password_hash")):
        return JSONResponse(
            {"ok": False, "message": "That email and password don't match an account."},
            status_code=401,
        )

    sid = ensure_session(request)
    response = JSONResponse({"ok": True})
    sign_in(response, sid, user["id"])
    return response


@app.post("/api/change-password")
async def api_change_password(request: Request):
    user = require_user(request)
    body = await request.json()

    if not verify_password(body.get("current_password") or "", user.get("password_hash")):
        return JSONResponse(
            {"ok": False, "message": "Your current password isn't right."},
            status_code=403,
        )
    try:
        new_hash = hash_password(body.get("new_password") or "")
    except PasswordError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    update_user(user["id"], {"password_hash": new_hash})
    return JSONResponse({"ok": True, "message": "Password updated."})


@app.get("/auth/token")
async def auth_token(request: Request, t: str = ""):
    """Sign in with a personal link. The token is the secret."""
    user = get_user_by_login_token(t)
    if not user:
        return RedirectResponse("/signin?error=bad-link")
    sid = ensure_session(request)
    response = RedirectResponse("/dashboard")
    sign_in(response, sid, user["id"])
    return response


@app.get("/logout")
async def logout(request: Request):
    sid = read_session_id(request)
    if sid:
        clear_session(sid)
    response = RedirectResponse("/onboard")
    response.delete_cookie("session")
    return response


# ── Dashboard & settings ───────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "mail_ready": mailbox_configured(),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "criteria": get_criteria(user["id"]) or {},
            "invite_url": f"{BASE_URL}/onboard?invite={user['invite_token']}",
            "signin_url": f"{BASE_URL}/auth/token?t={user['login_token']}",
            "mailbox_address": mailbox_address(),
            "require_invite": REQUIRE_INVITE,
            "mail_ready": mailbox_configured(),
        },
    )


@app.post("/settings")
async def settings_post(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    form = await request.form()

    def csv(key):
        return [v.strip() for v in (form.get(key) or "").split(",") if v.strip()]

    def money(key):
        raw = (form.get(key) or "").strip()
        return int(raw) if raw.isdigit() else None

    update_criteria(user["id"], {
        "job_titles": csv("job_titles"),
        "locations": csv("locations"),
        "remote_preference": form.get("remote_preference") or "any",
        "min_salary": money("min_salary"),
        "max_salary": money("max_salary"),
        "seniority_levels": csv("seniority_levels"),
        "greenhouse_companies": csv("greenhouse_companies"),
        "lever_companies": csv("lever_companies"),
    })
    return RedirectResponse("/settings?saved=1", status_code=303)


# ── Jobs API ───────────────────────────────────────────────────────────────
# Every mutation resolves the row through get_owned_user_job so one signed-in
# user can never touch another user's rows by guessing an id.

@app.get("/api/jobs")
async def api_jobs(request: Request):
    user = require_user(request)
    return JSONResponse(get_user_jobs(user["id"]))


@app.post("/api/jobs/{uj_id}/select")
async def api_select_job(uj_id: int, request: Request):
    user = require_user(request)
    uj = get_owned_user_job(uj_id, user["id"])
    if not uj:
        raise HTTPException(status_code=404, detail="Job not found")

    cover_letter = uj.get("cover_letter_text")
    if not cover_letter:
        try:
            cover_letter = await asyncio.to_thread(
                generate_cover_letter,
                job_title=uj["title"],
                company=uj.get("company", ""),
                job_description=uj.get("description", ""),
                resume_text=user.get("resume_text") or "",
                criteria=get_criteria(user["id"]) or {},
            )
        except LLMError as exc:
            # Leave the job where it was so the user can retry from the same
            # place, rather than stranding it in Selected with no letter.
            raise HTTPException(status_code=502, detail=str(exc))

    update_user_job(uj_id, {
        "status": "selected",
        "cover_letter_text": cover_letter,
        "selected_at": datetime.utcnow().isoformat(),
    })
    return JSONResponse({"cover_letter": cover_letter, "apply_url": uj.get("apply_url", "")})


@app.post("/api/jobs/{uj_id}/ignore")
async def api_ignore_job(uj_id: int, request: Request):
    user = require_user(request)
    if not get_owned_user_job(uj_id, user["id"]):
        raise HTTPException(status_code=404, detail="Job not found")
    update_user_job(uj_id, {"status": "ignored"})
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{uj_id}/update-cover-letter")
async def api_update_cover_letter(uj_id: int, request: Request):
    user = require_user(request)
    if not get_owned_user_job(uj_id, user["id"]):
        raise HTTPException(status_code=404, detail="Job not found")
    body = await request.json()
    update_user_job(uj_id, {"cover_letter_text": body.get("cover_letter", "")})
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{uj_id}/mark-applied")
async def api_mark_applied(uj_id: int, request: Request):
    user = require_user(request)
    if not get_owned_user_job(uj_id, user["id"]):
        raise HTTPException(status_code=404, detail="Job not found")
    body = await request.json()
    fields = {"status": "applied", "applied_at": datetime.utcnow().isoformat()}
    if body.get("cover_letter"):
        fields["cover_letter_text"] = body["cover_letter"]
    update_user_job(uj_id, fields)
    return JSONResponse({"ok": True})


# ── Digest ─────────────────────────────────────────────────────────────────

@app.post("/api/run-digest")
async def api_run_digest(request: Request):
    user = require_user(request)
    asyncio.create_task(run_digest_for_user(user["id"]))
    return JSONResponse({"ok": True, "message": "Digest started"})


@app.get("/api/digest-status")
async def api_digest_status(request: Request):
    user = require_user(request)
    run = get_latest_digest_run(user["id"])
    if not run:
        return JSONResponse({"status": "none"})
    return JSONResponse({
        "status": run["status"],
        "jobs_found": run["jobs_found"],
        "jobs_matched": run["jobs_matched"],
        "email_sent": bool(run["email_sent"]),
        "message": run["message"] or "",
        "finished_at": run["finished_at"],
    })


def _collect_jobs(criteria: dict) -> tuple[list[dict], list[str]]:
    """Pull postings from every configured source. Returns (jobs, problems)."""
    jobs: list[dict] = []
    problems: list[str] = []

    for slug in criteria.get("greenhouse_companies", []):
        try:
            jobs.extend(fetch_greenhouse_jobs(slug))
        except Exception as exc:
            problems.append(f"greenhouse/{slug}: {exc}")

    for slug in criteria.get("lever_companies", []):
        try:
            jobs.extend(fetch_lever_jobs(slug))
        except Exception as exc:
            problems.append(f"lever/{slug}: {exc}")

    titles = criteria.get("job_titles", [])
    if titles:
        # Keyword search across thousands of companies, so a new account gets
        # real matches without naming any company.
        try:
            jobs.extend(fetch_remotive_jobs(titles))
        except Exception as exc:
            problems.append(f"remotive: {exc}")

        try:
            found = fetch_indeed_jobs(titles, criteria.get("locations", []))
            if not found:
                problems.append("indeed: returned no results (it blocks scrapers often)")
            jobs.extend(found)
        except Exception as exc:
            problems.append(f"indeed: {exc}")

    return jobs, problems


async def run_digest_for_user(user_id: int) -> dict:
    """Source, score, store, and email. Records what happened either way."""
    run_id = start_digest_run(user_id)
    user = get_user(user_id)
    if not user:
        finish_digest_run(run_id, "error", message="User not found")
        return {"status": "error"}

    criteria = get_criteria(user_id) or {}
    try:
        all_jobs, problems = await asyncio.to_thread(_collect_jobs, criteria)
    except Exception as exc:
        finish_digest_run(run_id, "error", message=f"Sourcing failed: {exc}")
        return {"status": "error"}

    for job in all_jobs:
        try:
            job["id"] = await asyncio.to_thread(
                upsert_job,
                source=job.get("source", "unknown"),
                external_id=job.get("external_id", ""),
                title=job.get("title", ""),
                company=job.get("company", ""),
                apply_url=job.get("apply_url", ""),
                location=job.get("location", ""),
                remote_type=job.get("remote_type"),
                salary_min=job.get("salary_min"),
                salary_max=job.get("salary_max"),
                description=job.get("description", ""),
                company_domain=job.get("company_domain", ""),
            )
        except Exception as exc:
            problems.append(f"store {job.get('title')!r}: {exc}")

    try:
        scored = await asyncio.to_thread(score_jobs_for_user, all_jobs, user_id, criteria)
    except Exception as exc:
        finish_digest_run(run_id, "error", len(all_jobs),
                          message=f"Scoring failed: {exc}")
        return {"status": "error"}

    for job, score, reason in scored:
        upsert_user_job(user_id=user_id, job_id=job["id"], score=score, score_reason=reason)

    # Send the best DIGEST_LIMIT matches. Anything below the cut stays 'new'
    # and goes out in a later digest, so nothing is lost.
    unsent = get_unsent_user_jobs(user_id)[:DIGEST_LIMIT]
    if not unsent:
        finish_digest_run(
            run_id, "ok", len(all_jobs), len(scored),
            message="; ".join(problems) or "No new matches to send.",
        )
        return {"status": "ok", "sent": 0}

    email_sent, email_note = await asyncio.to_thread(_send_digest, user, unsent)
    if email_sent:
        mark_digest_sent(user_id, [uj["id"] for uj in unsent], batch=str(uuid.uuid4()))
    if email_note:
        problems.append(email_note)

    finish_digest_run(run_id, "ok", len(all_jobs), len(scored), email_sent,
                      "; ".join(problems))
    return {"status": "ok", "sent": len(unsent) if email_sent else 0}


def _send_digest(user: dict, jobs: list[dict]) -> tuple[bool, str]:
    """Returns (sent, note). Never raises — a send failure isn't fatal."""
    if not mailbox_configured():
        return False, "email not configured — matches are on your dashboard instead"
    try:
        send_email(
            user["email"],
            digest_subject(jobs),
            build_digest_text(user["name"], jobs),
            build_digest_html(user["name"], jobs),
        )
        return True, ""
    except MailboxError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"email send: {exc}"


async def run_all_digests():
    for user in get_all_users():
        try:
            await run_digest_for_user(user["id"])
        except Exception as exc:
            print(f"[digest] user {user['id']} failed: {exc}")


# ── Replies ────────────────────────────────────────────────────────────────
# The whole point of the digest is that you reply to it from your phone and the
# agent takes it from there, so this runs on a schedule — not only when someone
# happens to have the dashboard open.

async def apply_reply(user: dict, numbers: list[int]) -> dict:
    """Act on the numbers one person replied with.

    Selects each job, writes its cover letter, and emails the letters back with
    apply links.
    """
    user_id = user["id"]
    batch = get_latest_digest_batch(user_id)
    if not batch:
        return {"selected": [], "message": "No digest sent yet"}

    by_position = {
        uj["digest_position"]: uj for uj in get_digest_batch_jobs(user_id, batch)
    }
    criteria = get_criteria(user_id) or {}
    selected, prepared = [], []

    for number in numbers:
        uj = by_position.get(number)
        # Only act on jobs still sitting in the digest — replying twice, or a
        # number that also appears in quoted text, shouldn't undo later work.
        if not uj or uj["status"] != "sent":
            continue

        full = get_user_job(uj["id"])
        cover_letter, note = "", ""
        try:
            cover_letter = await asyncio.to_thread(
                generate_cover_letter,
                job_title=full["title"],
                company=full.get("company", ""),
                job_description=full.get("description", ""),
                resume_text=user.get("resume_text") or "",
                criteria=criteria,
            )
        except LLMError as exc:
            # Still select the job — the person asked for it. They just get the
            # link without a letter, and can retry from the dashboard.
            note = f"Cover letter failed: {exc}"
            print(f"[replies] user {user_id} job {uj['id']}: {note}")

        update_user_job(uj["id"], {
            "status": "selected",
            "selected_at": datetime.utcnow().isoformat(),
            "cover_letter_text": cover_letter,
        })
        selected.append(uj["id"])
        prepared.append({
            "title": full["title"],
            "company": full.get("company", ""),
            "apply_url": full.get("apply_url", ""),
            "cover_letter": cover_letter,
            "note": note,
        })

    if prepared:
        try:
            await asyncio.to_thread(
                send_email,
                user["email"],
                application_subject(prepared),
                build_application_text(user["name"], prepared),
                build_application_html(user["name"], prepared),
            )
        except Exception as exc:
            print(f"[replies] user {user_id}: could not send the letters: {exc}")

    return {"selected": selected, "prepared": len(prepared)}


async def poll_all_replies() -> dict:
    """Read the app mailbox and route each reply to the account that sent it.

    One inbox for everyone: a reply is matched to a user by its From address,
    so nobody has to connect their own mail account.
    """
    if not mailbox_configured():
        return {"ok": False, "message": "No mailbox configured", "handled": 0}

    try:
        replies = await asyncio.to_thread(fetch_replies)
    except MailboxError as exc:
        print(f"[replies] {exc}")
        return {"ok": False, "message": str(exc), "handled": 0}

    handled, selected_total = 0, 0
    for reply in replies:
        user = get_user_by_email(reply["from_email"])
        if not user:
            print(f"[replies] ignoring mail from unknown address {reply['from_email']!r}")
            continue

        batch = get_latest_digest_batch(user["id"])
        sent_count = len(get_digest_batch_jobs(user["id"], batch)) if batch else 0
        if not sent_count:
            continue

        numbers = extract_reply_numbers(reply["body"], sent_count)
        if not numbers:
            continue

        try:
            result = await apply_reply(user, numbers)
        except Exception as exc:
            print(f"[replies] user {user['id']} failed: {exc}")
            continue
        handled += 1
        selected_total += len(result["selected"])

    return {"ok": True, "handled": handled, "selected": selected_total}


@app.post("/api/poll-replies")
async def api_poll_replies(request: Request):
    """Check for replies now, rather than waiting for the next scheduled run."""
    require_user(request)
    return JSONResponse(await poll_all_replies())
