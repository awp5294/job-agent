"""
Job Agent - FastAPI server
All routes, session management, onboarding chat, and API endpoints.
"""
import os
import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from itsdangerous import URLSafeSerializer, BadSignature

from db.database import (
    init_db, create_user, get_user_by_email, get_user,
    update_user, get_criteria, update_criteria,
    upsert_session, get_session, upsert_job,
    get_user_jobs, upsert_user_job, update_user_job,
    get_user_job_by_id, get_unsent_user_jobs, mark_digest_sent,
)
from sourcing.greenhouse import fetch_greenhouse_jobs
from sourcing.lever import fetch_lever_jobs
from sourcing.indeed import fetch_indeed_jobs
from matching.scorer import score_jobs_for_user
from email_handler.gmail import (
    get_oauth_flow, get_gmail_service,
    send_digest_email, poll_for_replies,
)
from apply.cover_letter import generate_cover_letter
from apply.browser import open_job_for_review

# ── Config ─────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DB_PATH = os.environ.get("DB_PATH", "jobagent.db")

signer = URLSafeSerializer(SECRET_KEY)

app = FastAPI(title="Job Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

scheduler = AsyncIOScheduler()

# ── Helpers ────────────────────────────────────────────────────────────────────

def set_session(response: Response, session_id: str):
    token = signer.dumps(session_id)
    response.set_cookie("session", token, httponly=True, max_age=60 * 60 * 24 * 30)


def get_session_id(request: Request) -> Optional[str]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return signer.loads(token)
    except BadSignature:
        return None


def get_current_user(request: Request):
    sid = get_session_id(request)
    if not sid:
        return None
    sess = get_session(sid)
    if not sess or not sess.get("user_id"):
        return None
    return get_user(sess["user_id"])


@app.on_event("startup")
async def startup():
    init_db(DB_PATH)
    scheduler.add_job(run_all_digests, "cron", hour=8, minute=0)
    scheduler.start()


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return RedirectResponse("/onboard")


ONBOARD_STEPS = [
    {"key": "name", "prompt": "Hi! I'm your job agent. What's your name?"},
    {"key": "email", "prompt": "Nice to meet you, {name}! What's your email address?"},
    {"key": "job_titles", "prompt": "What job titles are you looking for? (e.g. Product Manager, Senior PM)"},
    {"key": "locations", "prompt": "Where would you like to work? List cities or type 'remote'."},
    {"key": "remote_pref", "prompt": "Remote preference: any / remote / hybrid / onsite?"},
    {"key": "salary", "prompt": "What's your salary range? (e.g. $120k-$160k, or skip)"},
    {"key": "seniority", "prompt": "Seniority levels? (e.g. Senior, Staff, Lead or skip)"},
    {"key": "resume", "prompt": "Upload your resume (PDF) or paste the text below."},
    {"key": "gmail", "prompt": "Last step - connect your Gmail so I can send your daily digest."},
]


def get_or_create_session(request, response):
    sid = get_session_id(request)
    if not sid:
        sid = str(uuid.uuid4())
        upsert_session(sid, {})
    set_session(response, sid)
    return sid


@app.get("/onboard", response_class=HTMLResponse)
async def onboard_page(request: Request, invite: Optional[str] = None):
    response = Response()
    sid = get_or_create_session(request, response)
    sess = get_session(sid) or {}
    if invite:
        sess["invite_token"] = invite
        upsert_session(sid, sess)
    html = templates.TemplateResponse("onboard.html", {"request": request})
    for key, val in response.headers.items():
        html.headers[key] = val
    return html


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    response = Response()
    sid = get_or_create_session(request, response)
    sess = get_session(sid) or {}
    step_num = sess.get("step_num", 0)
    data = sess.get("data", {})
    if step_num < len(ONBOARD_STEPS):
        step = ONBOARD_STEPS[step_num]
        key = step["key"]
        if key == "email":
            if "@" not in message:
                return JSONResponse({"reply": "That doesn't look like an email. Try again.", "step_num": step_num, "action": null})
            existing = get_user_by_email(message.lower())
            if existing:
                sess["user_id"] = existing["id"]
                upsert_session(sid, sess)
                resp = JSONResponse({"reply": f"Welcome back, {existing['name']}! Redirecting...", "step_num": step_num, "action": "redirect:/dashboard"})
                set_session(resp, sid)
                return resp
            data["email"] = message.lower()
        elif key == "name":
            data["name"] = message.strip().title()
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
            if pref not in ("any", "remote", "hybrid", "onsite"):
                pref = "any"
            data["remote_preference"] = pref
        elif key == "salary":
            if message.lower() not in ("skip", ""):
                import re
                nums = re.findall(r"\d+", message.replace("k", "000").replace("K", "000"))
                if len(nums) >= 2:
                    data["min_salary"] = int(nums[0])
                    data["max_salary"] = int(nums[1])
                elif len(nums) == 1:
                    data["min_salary"] = int(nums[0])
        elif key == "seniority":
            if message.lower() not in ("skip", ""):
                data["seniority_levels"] = [s.strip() for s in message.split(",") if s.strip()]
        elif key == "resume":
            if len(message) > 50:
                data["resume_text"] = message
        sess["data"] = data
        sess["step_num"] = step_num + 1
        upsert_session(sid, sess)
        if key == "resume":
            return JSONResponse({"reply": ONBOARD_STEPS[-1]["prompt"], "step_num": step_num + 1, "action": "show_gmail_button"})
        next_step_num = step_num + 1
        if next_step_num >= len(ONBOARD_STEPS):
            user_id = create_user(data["name"], data["email"])
            criteria = {k: v for k, v in data.items() if k not in ("name", "email", "resume_text")}
            update_criteria(user_id, criteria)
            if data.get("resume_text"):
                update_user(user_id, {"resume_text": data["resume_text"]})
            sess["user_id"] = user_id
            upsert_session(sid, sess)
            resp = JSONResponse({"reply": "You're all set! Redirecting...", "step_num": next_step_num, "action": "redirect:/dashboard"})
            set_session(resp, sid)
            return resp
        next_step = ONBOARD_STEPS[next_step_num]
        reply = next_step["prompt"].format(**data)
        action = None
        if next_step["key"] == "resume":
            action = "show_resume_upload"
        elif next_step["key"] == "gmail":
            action = "show_gmail_button"
        resp = JSONResponse({"reply": reply, "step_num": next_step_num, "action": action})
        set_session(resp, sid)
        return resp
    return JSONResponse({"reply": "Onboarding complete!", "step_num": step_num, "action": "redirect:/dashboard"})


@app.post("/api/resume-upload")
async def resume_upload(request: Request, file: UploadFile = File(...)):
    response = Response()
    sid = get_or_create_session(request, response)
    sess = get_session(sid) or {}
    data = sess.get("data", {})
    content = await file.read()
    try:
        import io, pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        text = content.decode("utf-8", errors="ignore")
    data["resume_text"] = text[:8000]
    sess["data"] = data
    step_num = sess.get("step_num", 7)
    sess["step_num"] = step_num + 1
    upsert_session(sid, sess)
    resp = JSONResponse({"reply": "Resume uploaded! Last step - connect your Gmail.", "step_num": step_num + 1, "action": "show_gmail_button"})
    set_session(resp, sid)
    return resp


@app.get("/auth/gmail")
async def auth_gmail(request: Request):
    sid = get_session_id(request)
    flow = get_oauth_flow(f"{BASE_URL}/auth/gmail/callback", state=sid or "")
    auth_url, _ = flow.authorization_url(prompt="consent")
    return RedirectResponse(auth_url)


@app.get("/auth/gmail/callback")
async def auth_gmail_callback(request: Request, code: str, state: str = ""):
    flow = get_oauth_flow(f"{BASE_URL}/auth/gmail/callback", state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    sid = state or get_session_id(request)
    sess = get_session(sid) or {}
    data = sess.get("data", {})
    if not sess.get("user_id"):
        user_id = create_user(data.get("name", "User"), data.get("email", ""))
        update_criteria(user_id, {k: v for k, v in data.items() if k not in ("name", "email", "resume_text")})
        if data.get("resume_text"):
            update_user(user_id, {"resume_text": data["resume_text"]})
        sess["user_id"] = user_id
    user_id = sess["user_id"]
    gmail_creds = {"token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri, "client_id": creds.client_id, "client_secret": creds.client_secret, "scopes": list(creds.scopes or* [])}
    update_user(user_id, {"gmail_credentials": json.dumps(gmail_creds)})
    upsert_session(sid, sess)
    resp = RedirectResponse("/dashboard")
    set_session(resp, sid)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    criteria = get_criteria(user["id"]) or {}
    invite_token = user.get("invite_token") or str(uuid.uuid4())
    if not user.get("invite_token"):
        update_user(user["id"], {"invite_token": invite_token})
    invite_url = f"{BASE_URL}/onboard?invite={invite_token}"
    return templates.TemplateResponse("settings.html", {"request": request, "user": user, "criteria": criteria, "invite_url": invite_url})


@app.post("/settings")
async def settings_post(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/onboard")
    form = await request.form()
    def csv(key):
        val = form.get(key, "")
        return [v.strip() for v in val.split(",") if v.strip()]
    criteria = {"job_titles": csv("job_titles"), "locations": csv("locations"), "remote_preference": form.get("remote_preference", "any"), "min_salary": int(form.get("min_salary") or 0) or None, "max_salary": int(form.get("max_salary") or 0) or None, "seniority_levels": csv("seniority_levels"), "greenhouse_companies": csv("greenhouse_companies"), "lever_companies": csv("lever_companies")}
    update_criteria(user["id"], {k: v for k, v in criteria.items() if v is not None})
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    resp = RedirectResponse("/onboard")
    resp.delete_cookie("session")
    return resp


@app.get("/api/jobs")
async def api_jobs(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    return JSONResponse(get_user_jobs(user["id"]))


@app.post("/api/jobs/{uj_id}/select")
async def api_select_job(uj_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    uj = get_user_job_by_id(uj_id)
    if not uj or uj["user_id"] != user["id"]:
        raise HTTPException(status_code=404)
    cover_letter = uj.get("cover_letter_text")
    if not cover_letter:
        criteria = get_criteria(user["id"]) or {}
        cover_letter = await asyncio.to_thread(generate_cover_letter, job_title=uj["title"], company=uj.get("company", ""), job_description=uj.get("description", ""), resume_text=user.get("resume_text", ""), criteria=criteria)
        update_user_job(uj_id, {"status": "selected", "cover_letter_text": cover_letter})
    else:
        update_user_job(uj_id, {"status": "selected"})
    return JSONResponse({"cover_letter": cover_letter, "apply_url": uj.get("apply_url", "")})


@app.post("/api/jobs/{uj_id}/ignore")
async def api_ignore_job(uj_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    update_user_job(uj_id, {"status": "ignored"})
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{uj_id}/update-cover-letter")
async def api_update_cover_letter(uj_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    update_user_job(uj_id, {"cover_letter_text": body.get("cover_letter", "")})
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{uj_id}/mark-applied")
async def api_mark_applied(uj_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    update_user_job(uj_id, {"status": "applied", "cover_letter_text": body.get("cover_letter", ""), "applied_at": datetime.utcnow().isoformat()})
    return JSONResponse({"ok": True})


@app.post("/api/run-digest")
async def api_run_digest(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    asyncio.create_task(run_digest_for_user(user["id"]))
    return JSONResponse({"ok": True, "message": "Digest started in background"})


async def run_digest_for_user(user_id: int):
    user = get_user(user_id)
    if not user:
        return
    criteria = get_criteria(user_id) or {}
    all_jobs = []
    for company in criteria.get("greenhouse_companies", []):
        try:
            all_jobs.extend(await asyncio.to_thread(fetch_greenhouse_jobs, company))
        except: pass
    for company in criteria.get("lever_companies", []):
        try:
            all_jobs.extend(await asyncio.to_thread(fetch_lever_jobs, company))
        except: pass
    if criteria.get("job_titles"):
        try:
            all_jobs.extend(await asyncio.to_thread(fetch_indeed_jobs, criteria["job_titles"], criteria.get("locations", [])))
        except: pass
    for job in all_jobs:
        job_id = upsert_job(source=job.get("source", "unknown"), external_id=job.get("external_id", ""), title=job.get("title", ""), company=job.get("company", ""), location=job.get("location", ""), remote_type=job.get("remote_type", ""), salary_min=job.get("salary_min"), salary_max=job.get("salary_max"), apply_url=job.get("apply_url", ""), description=job.get("description", ""), company_domain=job.get("company_domain", ""))
        if job_id: job["id"] = job_id
    scored = await asyncio.to_thread(score_jobs_for_user, all_jobs, user_id, criteria)
    for job, score, reason in scored:
        upsert_user_job(user_id=user_id, job_id=job["id"], score=score, score_reason=reason)
    unsent = get_unsent_user_jobs(user_id)
    if not unsent: return
    gmail_creds_json = user.get("gmail_credentials")
    if gmail_creds_json:
        try:
            service = get_gmail_service(json.loads(gmail_creds_json))
            send_digest_email(service, user["email"], user["name"], unsent)
            mark_digest_sent(user_id, [uj["id"] for uj in unsent])
        except: pass


async def run_all_digests():
    from db.database import get_conn
    conn = get_conn()
    users = conn.execute("SELECT id FROM users").fetchall()
    conn.close()
    for (user_id,) in users:
        try:
            await run_digest_for_user(user_id)
        except: pass


@app.post("/api/poll-replies")
async def api_poll_replies(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    gmail_creds_json = user.get("gmail_credentials")
    if not gmail_creds_json:
        return JSONResponse({"ok": False, "message": "No Gmail connected"})
    service = get_gmail_service(json.loads(gmail_creds_json))
    job_numbers = poll_for_replies(service, user["email"])
    unsent = get_unsent_user_jobs(user["id"])
    selected = []
    for num in job_numbers:
        idx = num - 1
        if 0 <= idx < len(unsent):
            uj = unsent[idx]
            update_user_job(uj["id"], {"status": "selected"})
            selected.append(uj["id"])
    return JSONResponse({"ok": True, "selected": selected})
