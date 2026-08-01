# Job Agent

An AI-powered job sourcing and application agent. Sends you a daily curated email digest of matched jobs. You reply with the numbers you want to apply to. The agent generates tailored cover letters and opens the application for you.

**No terminal required for end users.** Everything happens through a web UI.

## Features

- Chat-based onboarding — set your criteria in-browser
- Daily email digest with AI-scored job matches (only ≥70% shown)
- Reply to the email with job numbers → agent handles the rest
- Claude generates a tailored cover letter per job (stop-slop filtered)
- Dashboard: New Matches / Selected / Applied
- Multi-user: share an invite link, friends sign up themselves
- Sources: Indeed, Greenhouse API, Lever API

## Quick Start (local)

```bash
git clone https://github.com/awp5294/job-agent
cd job-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY at minimum
uvicorn server:app --reload
```

Open http://localhost:8000 — the chat onboarding starts automatically.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key |
| `SECRET_KEY` | Yes | Random string for session signing — run `python -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Yes | Your public URL, e.g. `https://yourapp.railway.app` |
| `GMAIL_CLIENT_ID` | For email | From Google Cloud Console |
| `GMAIL_CLIENT_SECRET` | For email | From Google Cloud Console |
| `SMTP_USER` | Fallback | Gmail address for SMTP fallback |
| `SMTP_PASS` | Fallback | Gmail app password |

## Gmail Setup (one-time, ~10 min)

1. Go to https://console.cloud.google.com and create a new project
2. Enable the **Gmail API** (APIs & Services → Enable APIs)
3. Go to **Credentials** → Create → OAuth 2.0 Client ID → Web Application
4. Add `{BASE_URL}/auth/gmail/callback` as an authorized redirect URI
5. Copy the Client ID and Client Secret into your `.env`
6. Go to **OAuth consent screen** → add your email as a test user

## Deploy to Railway

1. Push this repo to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Select your repo
4. Add environment variables in Railway's dashboard
5. Set `BASE_URL` to your Railway URL (e.g. `https://job-agent-production.up.railway.app`)
6. Update the Gmail OAuth redirect URI in Google Cloud Console to match

## Sharing with Friends

Once deployed, go to **Settings** → copy your invite link → share it. Your friend opens the link, goes through chat onboarding with their own criteria and Gmail, and gets their own digest.

## Job Sources

**Greenhouse & Lever**: Add company slugs in Settings. The slug is the company identifier in their job board URL:
- `https://boards.greenhouse.io/stripe` → slug is `stripe`
- `https://jobs.lever.co/vercel` → slug is `vercel`

**Indeed**: Automatically searched based on your job titles and locations.

## How It Works

1. **Daily at 8am**: Agent fetches jobs from all sources, scores each against your criteria using Claude, stores matches scoring ≥70%
2. **Email digest**: Sends a numbered list to your inbox
3. **You reply**: Type the numbers you want (`1, 3, 5`) — agent parses your reply
4. **Cover letter**: Claude generates a tailored letter, stop-slop filter removes AI tells
5. **Dashboard**: Click Apply ₒ review cover letter ₒ open application in new tab
