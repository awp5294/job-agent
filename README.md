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
- A tailored cover letter per job, checked for AI writing tells and rewritten if it has any
- Dashboard: New Matches / Selected / Applied
- Multi-user: share an invite link; each person gets their own account, criteria and jobs
- Sources: Remotive (keyword search), Greenhouse API, Lever API, Indeed

## Quick Start (local)

```bash
git clone https://github.com/awp5294/job-agent
cd job-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add GEMINI_API_KEY (or ANTHROPIC_API_KEY), SECRET_KEY, SMTP_USER, SMTP_PASS
uvicorn server:app --reload
```

Open http://localhost:8000 — the chat onboarding starts automatically. The first account
created becomes the owner and doesn't need an invite.

Email is optional to get started: without a mailbox configured, matches still appear on
your dashboard — you just don't get the daily digest.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | Yes | One AI key. Either provider works — see below. |
| `SECRET_KEY` | Yes | Random string for session signing — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Yes | Your public URL, e.g. `https://yourapp.railway.app`. Used for invite links. |
| `SMTP_USER` / `SMTP_PASS` | For email | The app's own mailbox — a Gmail address and an App Password |
| `SMTP_HOST` / `SMTP_PORT` | No | Defaults to Gmail (`smtp.gmail.com`, 587) |
| `IMAP_HOST` / `IMAP_PORT` | No | Where replies are read. Defaults to Gmail. |
| `MAIL_FROM` | No | Show a different From address than the mailbox login |
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

Email and password. During onboarding each person picks a password; after that
they sign in at `/signin`. Passwords are hashed with scrypt — the plaintext is
never stored.

Nobody needs a Google account, and nothing has to be approved. Typing someone
else's email into the onboarding chat does **not** sign you in as them.

Each account also gets an emergency sign-in link (`/auth/token?t=…`) on its
Settings page, for when someone forgets their password. Treat it like a password.

## How Email Works

**The app owns one mailbox. Your friends don't connect anything.**

You set `SMTP_USER` and `SMTP_PASS` once. Every digest is sent from that address
to whatever email each person entered during onboarding, and every reply comes
back to that same inbox, where the app matches it to an account by the address it
came from.

Setting it up for Gmail takes about two minutes:

1. Turn on 2-Step Verification on the Google account you want to send from
2. Google Account → Security → **App passwords** → generate one
3. Put the address in `SMTP_USER` and the 16-character App Password in `SMTP_PASS`
4. Make sure IMAP is enabled (Gmail → Settings → Forwarding and POP/IMAP)

No Google Cloud project, no OAuth consent screen, and nothing for your friends
to approve. A regular Gmail account can send about 500 emails a day, which is
plenty for a group of friends on one digest each.

## Sharing With Friends

1. Go to **Settings** → copy your invite link (`/onboard?invite=…`) → send it to a friend.
2. They open it, answer the chat with their own criteria and resume, pick a password, and
   get their own dashboard. That's the whole sign-up — no Google account, no approval
   step, nothing to install.
3. Everyone gets their own invite link, so they can pass it on.

Sign-up requires an invite link by default (`REQUIRE_INVITE=1`), so the URL is safe to
leave public — the only exception is the very first account, which bootstraps the owner.
Each user's jobs, criteria, resume and cover letters are private to them; the API refuses
any request for a row that belongs to someone else.

## Deploy to Railway

1. Push this repo to GitHub
2. https://railway.app → New Project → Deploy from GitHub → select your repo
3. Add the environment variables above
4. Set `BASE_URL` to your Railway URL

Note: SQLite lives on the container's disk. On a host with an ephemeral filesystem, point
`DB_PATH` at a mounted volume or your accounts will disappear on redeploy.

## Job Sources

**Remotive** — searched by your job titles, across thousands of companies at once.
This is what gives a brand-new account real matches without naming any company.
Remote roles only. Public API, no key.

**Greenhouse & Lever** — these answer "what is company X hiring for?", so they need a
company list. New accounts are pre-filled with well-known companies; edit the list in
Settings. A slug is the company's identifier in its job board URL:

- `https://boards.greenhouse.io/stripe` → slug is `stripe`
- `https://jobs.lever.co/vercel` → slug is `vercel`

If a company in the default list has moved off that board, it contributes no jobs and
the digest notes it — it doesn't break the run.

**Indeed** — searched from your titles and locations, but Indeed actively blocks
scrapers, so it usually returns nothing. Treat anything it finds as a bonus.

### Why not LinkedIn

LinkedIn has no public jobs API, requires a logged-in session to see postings, and its
terms prohibit scraping. Doing it anyway means running a logged-in account against
their anti-bot systems — which gets the account restricted or banned, and it would be
*your* account, since there's no other way to authenticate. That risk lands on a real
person's professional profile, so this app doesn't do it.

The sources above cover the same ground for the companies that matter: nearly every
startup and tech company posts to Greenhouse, Lever, or Ashby, and Remotive aggregates
remote roles across all of them. Adding a company slug in Settings gets you that
company's full posting list, straight from the source and always current.

## How It Works

1. **Daily at `DIGEST_HOUR`** — fetch jobs from every configured source, score each
   against your criteria, and keep the ones scoring ≥70%
2. **Email digest** — the top `DIGEST_LIMIT` (default 10) matches, numbered, to your
   inbox. Anything below the cut stays queued for the next digest.
3. **You reply** — `1, 3, 5`. Quoted text is ignored, so the digest's own numbers aren't
   read back as selections.
4. **The agent reads the reply within `REPLY_POLL_MINUTES`** (default 15) from its own
   inbox, matches it to your account by your email address, writes a tailored cover
   letter for each job you picked, and emails them back with a direct apply link.
5. **You tap the link and submit.** Everything is also on the dashboard if you'd rather
   edit a letter first.

You can also hit **Run digest** on the dashboard to do all of this on demand.

### Keeping the cover letters human

An employer can spot a machine-written cover letter, so three things guard against it:

1. **The prompt** names the patterns to avoid rather than handing over a banned-word
   list: no stock openers, active voice, a concrete detail behind every claim, varied
   sentence length, no em dashes.
2. **A check** scans the draft for the tells that survived, by category: stock openers
   and closers, buzzwords, filler adverbs, em dashes, "not just X, but Y", vague claims
   like "significant impact" with no number attached.
3. **One rewrite**, quoting those specific tells back at the model and telling it to keep
   every fact and number as-is. If the rewrite isn't cleaner, the first draft is kept.

What it deliberately does **not** do is edit letters with find-and-replace. An earlier
version deleted any sentence containing a stock phrase, which threw away "Throughout my
career I shipped 12 products at Stripe" along with the cliche, and rewrote "dynamic
pricing engine" as "strong pricing engine". These letters go to employers, so a mangled
sentence is worse than a slightly stiff one. Only wrappers get stripped mechanically:
salutations, sign-offs, markdown fences.

The same rules apply to the one-line "Why:" reason in the digest.

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
auth.py                Password hashing (scrypt, standard library)
db/database.py         SQLite data layer (all queries live here)
db/schema.sql          Schema + additive migrations
matching/scorer.py     Scores a job against a user's criteria
apply/cover_letter.py  Cover letter generation + stop-slop filter
apply/browser.py       Optional Playwright form pre-filler (not used by the web app)
email_handler/mailbox.py  The app's mailbox: SMTP send, IMAP reply reading
email_handler/digest.py   Email rendering
sourcing/              Greenhouse, Lever, Indeed
templates/ static/     UI
```
