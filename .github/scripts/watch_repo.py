import os, json, requests, pathlib, smtplib, ssl, email.utils
from email.message import EmailMessage

TARGET_REPO = os.environ["TARGET_REPO"]
WATCH_PATHS = set(json.loads(os.environ["WATCH_PATHS"]))
GH = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
    "Accept": "application/vnd.github+json"
}

STATE = pathlib.Path(".state"); STATE.mkdir(exist_ok=True, parents=True)
LAST = STATE / "last_seen_sha.txt"

def list_commits(per_page=15):
    r = requests.get(f"{GH}/repos/{TARGET_REPO}/commits",
                     headers=HEADERS, params={"per_page": per_page})
    r.raise_for_status()
    return r.json()

def commit_files(sha):
    r = requests.get(f"{GH}/repos/{TARGET_REPO}/commits/{sha}", headers=HEADERS)
    r.raise_for_status()
    return [f["filename"] for f in r.json().get("files",[])]

def watched(path):
    return any(path == p or path.startswith(p) for p in WATCH_PATHS)

def send_telegram(text):
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: return False
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
    return True

def send_discord(text):
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url: return False
    requests.post(url, json={"content": text[:1900]})
    return True

def send_email(subject, body):
    host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT","0") or 0)
    user, pwd, to = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"), os.getenv("MAIL_TO")
    if not all([host, port, user, pwd, to]): return False
    msg = EmailMessage()
    msg["From"] = user; msg["To"] = to; msg["Subject"] = subject
    thread_id = os.getenv("MAIL_THREAD_ID")
    if thread_id:
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id
    msg["Message-ID"] = email.utils.make_msgid(domain="job-tracker.local")
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
    return True

def notify(lines):
    body = "\n".join(lines)
    if send_telegram(body): return
    if send_discord(body): return
    send_email("Summer2026 updates", body)

def main():
    last_seen = LAST.read_text().strip() if LAST.exists() else None
    commits = list_commits()
    if not commits: return
    new = []
    for c in commits:
        if c["sha"] == last_seen: break
        new.append(c)
    if not new:
        return
    digest = []
    for c in reversed(new):  # oldest → newest
        sha, url = c["sha"], c["html_url"]
        msg = c["commit"]["message"].splitlines()[0]
        files = commit_files(sha)
        touched = [f for f in files if watched(f)]
        if touched:
            digest.append(f"• {msg} – {url}\n   files: {', '.join(touched[:6])}")
    LAST.write_text(commits[0]["sha"])
    if digest:
        notify(digest)

if __name__ == "__main__":
    main()
