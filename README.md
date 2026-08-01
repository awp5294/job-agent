# Job Agent

An AI-powered job sourcing and application agent. It sends you a daily email digest of
matched jobs. You reply with the numbers you want. It writes a tailored cover letter for
each and opens the application form.

**No terminal required for end users.** Everything happens through a web UI, so you can
share it with friends and each of them runs their own search.

## Features

- Chat-based onboarding — set your criteria in the browser
- Daily email digest with AI-scored matches (only ≥70% shown)
- Reply to the email with job numbers → those jobs move to Selected
- A tailored cover letter per job, with a stop-slop filter over the output
- Dashboard: New Matches / Selected / Applied
- Multi-user: share an invite link; each person gets their own account, criteria and jobs
- Sources: Greenhouse API, Lever API, Indeed

## Quick Start (local)

```bash
git clone https://github.com/awp5294/job-agent
cd job-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add GEMINI_API_KEY (or ANTHROPIC_API_KEY) and a SECRET_KEY
uvicorn server:app --reload
```

Open http://localhost:8000 — the chat onboarding starts automatically. The first account
created becomes the owner and doesn't need an invite.

Gmail is optional to get started: without it, matches still appear on your dashboard and
you sign in with the private link shown at the end of onboarding.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | Yes | One AI key. Either provider works — see below. |
| `SECRET_KEY` | Yes | Random string for session signing — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Yes | Your public URL, e.g. `https://yourapp.railway.app`. Must match the Gmail redirect URI. |
| `GMAIL_CLIENT_ID` | For email | From Google Cloud Console |
| `GMAIL_CLIENT_SECRET` | For email | From Google Cloud Console |
| `SMTP_USER` / `SMTP_PASS` | Fallback | Gmail address + app password, if you'd rather not set up OAuth |
| `REQUIRE_INVITE` | No | Defaults to `1`. Set to `0` to let anyone with the URL sign up. |
| `LLM_PROVIDER` | No | `anthropic` or `gemini`. Only needed if both keys are set. |
| `ANTHROPIC_MODEL` / `GEMINI_MODEL` | No | Defaults: `claude-opus-5` / `gemini-2.5-flash` |
| `DIGEST_HOUR` / `TIMEZONE` | No | When the daily digest runs. Defaults to 8am UTC. |
| `DIGEST_LIMIT` | No | Jobs per digest email. Defaults to 10. |
| `REPLY_POLL_MINUTES` | No | How often to check for replies. Defaults to 15. |
| `DB_PATH` | No | SQLite file location. Defaults to `jobagent.db`. |

## Which AI Provider

The app runs on **Claude or Gemini** — it uses whichever API key it finds:

- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) → Gemini, default model `gemini-2.5-flash`
- `ANTHROPIC_API_KEY` → Claude, default model `claude-opus-5`

If both are set, Claude is used; set `LLM_PROVIDER=gemini` to override. The startup log
prints which provider and model are active, so you can confirm at a glance.

If your key doesn't have access to the default model, the error message says so and tells
you to set `GEMINI_MODEL` / `ANTHROPIC_MODEL`.

## How Sign-In Works

There are exactly two ways into an account:

1. **Sign in with Google** — the account is keyed to the email Google reports for the
   connected mailbox, not to anything typed into the chat. This is the normal path once
   `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` are configured.
2. **A private sign-in link** — `/auth/token?t=…`, issued at the end of onboarding when
   Gmail OAuth isn't configured. Treat it like a password. You can re-copy it any time
   from Settings.

Typing someone else's email address into the onboarding chat does **not** sign you in as
them — it sends you to the sign-in page.

## Sharing With Friends

1. Go to **Settings** → copy your invite link (`/onboard?invite=…`) → send it to a friend.
2. They open it, go through onboarding with their own criteria, resume and Gmail, and get
   their own dashboard.
3. Everyone gets their own invite link, so they can pass it on.

Sign-up requires an invite link by default (`REQUIRE_INVITE=1`), so the URL is safe to
leave public — the only exception is the very first account, which bootstraps the owner.
Each user's jobs, criteria, resume and cover letters are private to them; the API refuses
any request for a row that belongs to someone else.

## Gmail Setup (one-time, ~10 min)

1. Go to https://console.cloud.google.com and create a project
2. Enable the **Gmail API** (APIs & Services → Enable APIs)
3. **Credentials** → Create → OAuth 2.0 Client ID → Web Application
4. Add `{BASE_URL}/auth/gmail/callback` as an authorized redirect URI
5. Copy the Client ID and Secret into your `.env`
6. **OAuth consent screen** → add each user's email as a test user

Scopes requested: `gmail.send` (to send your digest) and `gmail.readonly` (to read your
replies and confirm your address).

## Deploy to Railway

1. Push this repo to GitHub
2. https://railway.app → New Project → Deploy from GitHub → select your repo
3. Add the environment variables above
4. Set `BASE_URL` to your Railway URL
5. Update the Gmail OAuth redirect URI in Google Cloud Console to match

Note: SQLite lives on the container's disk. On a host with an ephemeral filesystem, point
`DB_PATH` at a mounted volume or your accounts will disappear on redeploy.

## Job Sources

**Greenhouse & Lever** — new accounts are pre-filled with a list of well-known companies,
so a friend gets real matches from onboarding alone. Anyone can edit the list in
Settings. A slug is the company's identifier in its job board URL:

- `https://boards.greenhouse.io/stripe` → slug is `stripe`
- `https://jobs.lever.co/vercel` → slug is `vercel`

If a company in the default list has moved off that board, it just contributes no jobs
and the digest notes it — it doesn't break the run.

**Indeed** — searched automatically from your job titles and locations. Indeed actively
blocks scrapers, so it often returns nothing; the digest reports that as a note rather
than failing. Greenhouse and Lever are the reliable sources.

## How It Works

1. **Daily at `DIGEST_HOUR`** — fetch jobs from every configured source, score each
   against your criteria, and keep the ones scoring ≥70%
2. **Email digest** — the top `DIGEST_LIMIT` (default 10) matches, numbered, to your
   inbox. Anything below the cut stays queued for the next digest.
3. **You reply** — `1, 3, 5`. Quoted text is ignored, so the digest's own numbers aren't
   read back as selections.
4. **The agent picks it up within `REPLY_POLL_MINUTES`** (default 15), writes a tailored
   cover letter for each job you picked, and emails them back with a direct apply link
   per job.
5. **You tap the link and submit.** Everything is also on the dashboard if you'd rather
   edit a letter first.

You can also hit **Run digest** on the dashboard to do all of this on demand.

### What "auto apply" does and doesn't do

Steps 1–4 are fully automatic — you reply to an email and the cover letters arrive
written. The final submit is still yours: the agent does not blind-fill and submit
employer application forms. Those forms vary by company, usually want a resume file
upload and custom questions, and an application can't be un-sent, so a garbled
auto-submission burns that opportunity. `apply/browser.py` has an optional Playwright
form pre-filler if you want to experiment locally.

## Running the Tests

```bash
pip install -r requirements.txt
pytest
```

The suite covers onboarding, sign-in and the invite gate, cross-account isolation, the
digest pipeline, reply parsing, job-board parsing, and both AI providers. Every network
call is stubbed, so no internet access or API key is needed.

## Project Layout

```
server.py              FastAPI routes, sessions, onboarding, digest orchestration
llm.py                 Claude/Gemini client, model config, error handling
db/database.py         SQLite data layer (all queries live here)
db/schema.sql          Schema + additive migrations
matching/scorer.py     Scores a job against a user's criteria
apply/cover_letter.py  Cover letter generation + stop-slop filter
apply/browser.py       Optional Playwright form pre-filler (not used by the web app)
email_handler/gmail.py OAuth, digest send, reply parsing
sourcing/              Greenhouse, Lever, Indeed
templates/ static/     UI
```
