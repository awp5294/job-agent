CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,            -- scrypt hash; set during onboarding
    resume_text TEXT,
    linkedin_url TEXT,
    phone TEXT,
    auto_apply INTEGER DEFAULT 0,  -- 1 = submit without review
    invite_token TEXT UNIQUE,      -- token this user hands out to invite friends
    login_token TEXT UNIQUE,       -- personal sign-in token (fallback when Google OAuth isn't configured)
    is_owner INTEGER DEFAULT 0,    -- 1 for the first account created on this deployment
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_criteria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE REFERENCES users(id),
    job_titles TEXT DEFAULT '[]',        -- JSON array
    min_salary INTEGER,
    max_salary INTEGER,
    locations TEXT DEFAULT '[]',         -- JSON array
    remote_preference TEXT DEFAULT 'any', -- remote/hybrid/onsite/any
    seniority_levels TEXT DEFAULT '[]',  -- JSON array
    industries_include TEXT DEFAULT '[]',
    industries_exclude TEXT DEFAULT '[]',
    keywords_include TEXT DEFAULT '[]',
    keywords_exclude TEXT DEFAULT '[]',
    greenhouse_companies TEXT DEFAULT '[]', -- company slugs to watch
    lever_companies TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    state TEXT DEFAULT '{}',  -- JSON onboarding state
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- indeed/greenhouse/lever
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    company_domain TEXT,
    location TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    remote_type TEXT,              -- remote/hybrid/onsite
    description TEXT,
    apply_url TEXT NOT NULL,
    posted_at TIMESTAMP,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS user_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    job_id INTEGER REFERENCES jobs(id),
    score INTEGER,
    score_reason TEXT,
    status TEXT DEFAULT 'new',   -- new/sent/selected/applying/applied/ignored
    digest_sent_at TIMESTAMP,
    digest_batch TEXT,           -- id of the digest email this job appeared in
    digest_position INTEGER,     -- the number the user sees in that email ("reply with 1, 3, 5")
    selected_at TIMESTAMP,
    applied_at TIMESTAMP,
    cover_letter_text TEXT,
    notes TEXT,
    UNIQUE(user_id, job_id)
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT DEFAULT 'running',  -- running/ok/error
    jobs_found INTEGER DEFAULT 0,
    jobs_matched INTEGER DEFAULT 0,
    email_sent INTEGER DEFAULT 0,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_jobs_user ON user_jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_digest_runs_user ON digest_runs(user_id, started_at DESC);
