# Putting this online, start to finish

You end up with a link you send a friend. They open it, enter their own email
and what they're looking for, and start getting their own daily job matches.
Nothing for them to install, no account to connect.

This takes about 20 minutes. You need a Google account you don't mind using as
the app's mailbox, and a Gemini API key.

---

## Step 1: Make the mailbox

The app needs its own email account. Every digest is sent from it, and every
reply comes back to it. Don't use your personal Gmail: replies from your friends
land in this inbox and the app marks them as read.

1. Make a new Gmail, something like `yournamejobagent@gmail.com`. The address can
   be anything you want; nothing here depends on its length or wording.
2. Sign in to it.
3. Turn on 2-Step Verification: **Google Account → Security → 2-Step
   Verification**. You have to do this or the next step won't exist.
4. Go to https://myaccount.google.com/apppasswords
5. Type any name (`job agent`), click **Create**.
6. Google generates a password and shows it as 16 characters in four groups, like
   `abcd efgh ijkl mnop`. Copy it and delete the spaces, so you have
   `abcdefghijklmnop`. You don't pick this and you can't change it.
   **This is the only time Google shows it**, so paste it somewhere now.
7. Turn on IMAP: in that Gmail, **Settings (gear) → See all settings →
   Forwarding and POP/IMAP → Enable IMAP → Save Changes**.

Hold on to two things: the email address, and the generated password. They go into
two different secrets later, `SMTP_USER` and `SMTP_PASS`.

> Skipping the IMAP step is the failure that's hardest to spot. Digests go out
> fine and replies are never read, so it half works and nothing says why.

---

## Step 2: Get the app onto Replit

1. Go to https://replit.com and sign in.
2. **Create App → Import from GitHub → `awp5294/job-agent`**.
3. Wait for it to finish importing. Don't run it yet.

---

## Step 3: Add the database

This is the step that keeps your friends' accounts from disappearing. Without
it, everything works until the next time you change something, and then every
account, resume and saved job is gone.

1. In the left sidebar of your Repl, find the **Tools** section.
2. Click **Database**. (If you don't see it, click **+** or **All tools** and
   pick PostgreSQL.)
3. Click **Create a database**.
4. Wait about thirty seconds.

Replit creates the database and sets `DATABASE_URL` for you automatically. **You don't need to copy or paste anything.** The app looks for
that name on its own.

To confirm: open the **Secrets** tab (padlock icon) and check `DATABASE_URL` is
listed. If it isn't, go back to the Database tool and copy the connection string
it shows, then add it as a secret named exactly `DATABASE_URL`.

---

## Step 4: Add your keys

Click the **padlock icon** (Secrets) in the left sidebar. Add each of these with
**New secret**. The name has to match exactly, capital letters included.

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your Gemini key |
| `SECRET_KEY` | any long random string; mash the keyboard for 40 characters |
| `SMTP_USER` | the Gmail address from step 1 |
| `SMTP_PASS` | the generated App Password from step 1, spaces removed |

`SMTP_PASS` is the App Password Google generated, **not** the password you type to
sign in to Gmail. Google rejects the normal one. It should be 16 characters with
no spaces; if what you have is longer, shorter, or something you chose yourself,
it's the wrong value.

---

## Step 5: Deploy

1. Click **Deploy** (top right).
2. Choose **Reserved VM**. Not Autoscale.
3. App type: **Web server**.
4. Take the smallest machine size. You can change it later.
5. Click **Deploy**.

**Why not Autoscale**, since Replit suggests it: Autoscale shuts your app off
when nobody is looking at it. This app does its real work with nobody looking.
It sends the digest in the morning and checks for replies every 15 minutes. On
Autoscale it would sit switched off through both, and never tell you.

When it finishes, Replit shows you a URL like
`https://job-agent-yourname.replit.app`. Copy it.

---

## Step 6: Tell the app its own address

The app builds invite links out of this, so until you set it, the link you send
your friend points at your own computer and does nothing on theirs.

1. Secrets → **New secret**
2. Name: `BASE_URL`
3. Value: the URL from step 5, no slash on the end
4. **Redeploy**

---

## Step 7: Make your own account

Open the URL. The onboarding chat starts on its own. Answer it: your name, your
email, what jobs you want, where, salary, and paste your resume. Pick a password
at the end.

The first account created is the owner, and it's the only one that doesn't need
an invite.

---

## Step 8: Test it on yourself before anyone else sees it

Do not skip this. It's the only way to find out whether the mailbox actually
works, and it's better to find out alone.

1. On your dashboard, click **Run digest**. That finds jobs, scores them, and
   emails you the ones worth looking at, all in one go. Give it a minute.
2. Check the inbox of the email you signed up with. You should get a numbered
   list of jobs.
3. **Reply to it** with `1` (just the number).
4. Wait up to 15 minutes.
5. You should get a second email with a cover letter and a link to apply.

If the first email never arrives, `SMTP_USER` or `SMTP_PASS` is wrong. If it
arrives but the reply gets you nothing, IMAP is off in that Gmail account. Go
back to step 1.7.

If the digest says no jobs were found, that's not a bug: nothing scored above
70% today. Widen your job titles in Settings and run it again.

---

## Step 9: Send your friend the link

1. Go to **Settings**.
2. Copy your invite link. It looks like
   `https://your-app.replit.app/onboard?invite=abc123`.
3. Send it.

They open it, answer the same chat with their own email, their own criteria and
their own resume, and pick their own password. They get their own dashboard and
their own daily digest. They can't see your jobs and you can't see theirs.

Every account gets its own invite link, so they can pass it on.

---

## When something's wrong

Open the deployment logs in Replit. The app prints its own problems at startup
and names the specific thing that's missing:

```
Job Agent started, but some things won't work:
 - No mailbox configured — set SMTP_USER and SMTP_PASS ...
```

A clean boot says so instead:

```
Job Agent ready. AI: gemini (gemini-2.5-flash). Mail: you@gmail.com.
Digests at 08:00 UTC, replies checked every 15 min.
```

Read that line first. It usually names the problem outright.

| What you see | What it is |
|---|---|
| No digest arrives at all | `SMTP_USER` / `SMTP_PASS` wrong, or `SMTP_PASS` is your normal Gmail password instead of the App Password |
| Digest arrives, reply does nothing | IMAP is off in that Gmail account (step 1.7) |
| Friend's invite link 404s or goes to localhost | `BASE_URL` wrong or not set, and you need to redeploy after setting it |
| Everyone's account vanished | `DATABASE_URL` wasn't set, so the accounts were in a file the redeploy erased. Step 3. |
| Digests stop arriving after a quiet day | Deployed on Autoscale instead of Reserved VM. Step 5. |
