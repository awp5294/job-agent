"""
Run this once to create the GitHub repo and push all files.
Usage: python push_to_github.py
"""
import os
import sys
import base64
import json
import time
from pathlib import Path
from urllib import request, error

TOKEN = os.environ.get("GITHUB_TOKEN", "")  # set GITHUB_TOKEN env var before running
USERNAME = os.environ.get("GITHUB_USERNAME", "awp5294")
REPO = "job-agent"
BASE_DIR = Path(__file__).parent

SKIP = {".git", "__pycache__", ".venv", "venv", "*.pyc", "*.db", "push_to_github.py"}

def gh(method, path, body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req) as r:
            return json.loads(r.read())
    except error.HTTPError as e:
        return json.loads(e.read())

def all_files():
    for f in sorted(BASE_DIR.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(BASE_DIR)
        parts = rel.parts
        if any(p.startswith(".git") or p == "__pycache__" or p.endswith(".pyc") or p.endswith(".db") for p in parts):
            continue
        if f.name == "push_to_github.py":
            continue
        yield f, str(rel).replace("\\", "/")

def main():
    print(f"Creating repo {USERNAME}/{REPO}...")
    result = gh("POST", "/user/repos", {
        "name": REPO,
        "description": "AI-powered job sourcing and auto-apply agent with web dashboard",
        "private": False,
        "auto_init": False,
    })
    if "full_name" in result:
        print(f"✓ Repo created: https://github.com/{result['full_name']}")
    elif result.get("message", "").startswith("name already exists"):
        print(f"✓ Repo already exists — pushing files to it")
    else:
        print(f"Error creating repo: {result.get('message')}")
        sys.exit(1)

    time.sleep(1)

    files = list(all_files())
    print(f"\nPushing {len(files)} files...")

    for f, rel_path in files:
        try:
            content = base64.b64encode(f.read_bytes()).decode()
        except Exception as e:
            print(f"  skip {rel_path}: {e}")
            continue

        # Check if file already exists (get its SHA)
        existing = gh("GET", f"/repos/{USERNAME}/{REPO}/contents/{rel_path}")
        sha = existing.get("sha")

        body = {"message": f"add {rel_path}", "content": content}
        if sha:
            body["sha"] = sha

        result = gh("PUT", f"/repos/{USERNAME}/{REPO}/contents/{rel_path}", body)
        if "content" in result:
            print(f"  ✓ {rel_path}")
        else:
            print(f"  ✗ {rel_path}: {result.get('message', 'unknown error')}")
        time.sleep(0.3)  # avoid rate limits

    print(f"\n✅ Done! View your repo at: https://github.com/{USERNAME}/{REPO}")
    print(f"\nNext steps:")
    print(f"  1. Go to https://replit.com/new → Import from GitHub → {USERNAME}/{REPO}")
    print(f"  2. Add env vars in Replit Secrets: ANTHROPIC_API_KEY, SECRET_KEY, BASE_URL")
    print(f"  3. Click Run — the app starts automatically")

if __name__ == "__main__":
    main()
