#!/usr/bin/env python3
"""
Kyle MegamassfitnessUSA Reply Watcher v2
Monitors james@stricklandtechnology.com (Dovecot/Virtualmin on VPS)
for Kyle's discovery questionnaire replies.
Parses answers -> updates mock-up site -> notifies via email.
"""

import imaplib
import email
import email.header
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Primary: Virtualmin Dovecot for james@stricklandtechnology.com
IMAP_HOST = 'localhost'
IMAP_PORT = 143
IMAP_USER = 'james@stricklandtechnology.com'
IMAP_PASS = 'STech2026!#'
USE_SSL = False

# Fallback: Gmail (swimhack) in case reply goes there
GMAIL_USER = 'swimhack@gmail.com'
GMAIL_APP_PASSWORD = 'cycuyjbzbftlsadq'

KYLE_EMAIL_PATTERN = 'megamassfitnesusa'
SITE_DIR = '/var/www/sites/megamassfitnesusa.com/public'
STATE_FILE = '/var/www/sites/megamassfitnesusa.com/kyle_replies.json'
LOG_FILE = '/var/www/sites/megamassfitnesusa.com/kyle_watcher.log'
RESEND_KEY_PATH = '/var/www/sites/benchonly.com/app/training/admin/check_email_delivery.php'


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def decode_hdr(v):
    parts = email.header.decode_header(v or '')
    res = []
    for part, charset in parts:
        if isinstance(part, bytes):
            res.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            res.append(str(part))
    return ''.join(res)


def get_email_body(msg):
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
    return body


def check_virtualmin_mailbox():
    """Check james@stricklandtechnology.com Dovecot mailbox for Kyle's reply."""
    try:
        if USE_SSL:
            conn = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        else:
            conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        conn.login(IMAP_USER, IMAP_PASS)
        conn.select('INBOX')

        _, data = conn.search(None, 'ALL')
        all_uids = data[0].split() if data[0] else []
        log(f"Virtualmin mailbox: {len(all_uids)} total messages")

        results = []
        for uid in reversed(all_uids[-20:]):
            _, msg_data = conn.fetch(uid, '(RFC822)')
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            frm = decode_hdr(msg.get('From', '')).lower()
            subj = decode_hdr(msg.get('Subject', ''))
            date = msg.get('Date', '')
            body = get_email_body(msg)

            if KYLE_EMAIL_PATTERN in frm:
                results.append({
                    'uid': uid.decode(),
                    'source': 'virtualmin',
                    'subject': subj,
                    'from': decode_hdr(msg.get('From', '')),
                    'date': date,
                    'body': body
                })

        conn.logout()

        if results:
            log(f"Found {len(results)} message(s) from Kyle in Virtualmin mailbox")
            return results[-1]  # Most recent

        log("No reply from Kyle in Virtualmin mailbox")
        return None

    except Exception as e:
        log(f"Virtualmin IMAP error: {e}")
        return None


def check_gmail_fallback():
    """Fallback: check swimhack@gmail.com in case Kyle replied to old address."""
    try:
        conn = imaplib.IMAP4_SSL('imap.gmail.com')
        conn.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        conn.select('INBOX')

        _, data = conn.search(None, 'FROM', 'megamassfitnesusa@outlook.com')
        uids = data[0].split() if data[0] else []

        _, data2 = conn.search(None, 'FROM', 'MegamassfitnessUSA@outlook.com')
        uids2 = data2[0].split() if data2[0] else []

        all_uids = list(set(uids + uids2))

        if not all_uids:
            log("No reply from Kyle in Gmail fallback")
            conn.logout()
            return None

        uid = sorted(all_uids, key=lambda x: int(x))[-1]
        _, msg_data = conn.fetch(uid if isinstance(uid, bytes) else uid.encode(), '(RFC822)')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        result = {
            'uid': f"gmail_{uid.decode() if isinstance(uid, bytes) else uid}",
            'source': 'gmail',
            'subject': decode_hdr(msg.get('Subject', '')),
            'from': decode_hdr(msg.get('From', '')),
            'date': msg.get('Date', ''),
            'body': get_email_body(msg)
        }

        conn.logout()
        log(f"Found reply from Kyle in Gmail fallback: {result['subject']}")
        return result

    except Exception as e:
        log(f"Gmail fallback error: {e}")
        return None


def parse_discovery_answers(body):
    answers = {}

    patterns = [
        ('products',       r'(?:Q1|1[\.\)]|what.*?products?.*?sell).*?[\n:]+\s*(.+?)(?=Q2|2[\.\)]|$)'),
        ('differentiators',r'(?:Q2|2[\.\)]|what.*?sets.*?apart|different).*?[\n:]+\s*(.+?)(?=Q3|3[\.\)]|$)'),
        ('audience',       r'(?:Q3|3[\.\)]|who.*?customer|target|audience).*?[\n:]+\s*(.+?)(?=Q4|4[\.\)]|$)'),
        ('branding',       r'(?:Q4|4[\.\)]|color|brand|logo).*?[\n:]+\s*(.+?)(?=Q5|5[\.\)]|$)'),
        ('photos',         r'(?:Q5|5[\.\)]|photo|image|visual).*?[\n:]+\s*(.+?)(?=Q6|6[\.\)]|$)'),
        ('cta',            r'(?:Q6|6[\.\)]|call.to.action|cta|want.*?visitor).*?[\n:]+\s*(.+?)(?=Q7|7[\.\)]|$)'),
        ('location',       r'(?:Q7|7[\.\)]|location|address|city|state).*?[\n:]+\s*(.+?)(?=Q8|8[\.\)]|$)'),
        ('inspiration',    r'(?:Q8|8[\.\)]|inspiration|competitor|similar|like).*?[\n:]+\s*(.+?)$'),
    ]

    for key, pattern in patterns:
        m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if m:
            answers[key] = m.group(1).strip()[:500]

    log(f"Parsed {len(answers)} answers from reply")
    return answers


def update_mockup(answers):
    index_path = os.path.join(SITE_DIR, 'index.html')
    if not os.path.exists(index_path):
        log(f"ERROR: index.html not found at {index_path}")
        return []

    with open(index_path) as f:
        html = f.read()

    changes = []

    # Color update from branding answer
    if 'branding' in answers:
        text = answers['branding'].lower()
        color_map = {
            'red': '#cc1f1f', 'crimson': '#dc143c', 'blue': '#1565c0',
            'navy': '#001f5b', 'green': '#2e7d32', 'black': '#0a0a0a',
            'gold': '#ffd700', 'orange': '#e65100', 'purple': '#6a1b9a',
            'silver': '#9e9e9e', 'white': '#f5f5f5',
        }
        for name, hex_val in color_map.items():
            if name in text and hex_val != '#cc1f1f':
                html = html.replace('#cc1f1f', hex_val).replace('#b01515', hex_val)
                changes.append(f"Brand color updated to {name} ({hex_val})")
                break

    # CTA update
    if 'cta' in answers:
        cta = answers['cta'].lower()
        if 'shop' in cta or 'buy' in cta or 'order' in cta:
            html = html.replace('Request a Quote', 'Shop Now').replace('Get a Quote', 'Shop Now')
            changes.append("CTA: Shop Now")
        elif 'call' in cta or 'phone' in cta:
            html = html.replace('Request a Quote', 'Call Us').replace('Get a Quote', 'Call Us')
            changes.append("CTA: Call Us")
        elif 'contact' in cta or 'form' in cta:
            changes.append("CTA: Contact form (unchanged)")

    # Location update
    if 'location' in answers:
        loc = answers['location'].strip()
        if loc and 'katy' not in loc.lower() and len(loc) > 5:
            html = html.replace('Katy, TX', loc[:60])
            changes.append(f"Location: {loc[:40]}")

    # Update ribbon to show answers received
    html = html.replace('MOCK-UP PREVIEW', 'PREVIEW — v1.1')
    html = html.replace(
        '</head>',
        f'<!-- Kyle answers received {datetime.now().strftime("%Y-%m-%d")} -->\n</head>'
    )
    changes.append("Ribbon updated to PREVIEW v1.1")

    with open(index_path, 'w') as f:
        f.write(html)

    log(f"Mock-up updated: {changes}")
    return changes


def get_resend_key():
    try:
        content = open(RESEND_KEY_PATH).read()
        m = re.search(r"RESEND_API_KEY',\s*'([^']+)'", content)
        return m.group(1) if m else None
    except Exception:
        return None


def send_notification(answers, changes, source):
    import urllib.request

    answers_text = '\n'.join(
        f"  Q{i+1} ({k}): {v[:120]}"
        for i, (k, v) in enumerate(answers.items())
    ) if answers else '  (No structured answers parsed — review raw reply)'

    body = (
        f"Kyle replied to the MegamassfitnessUSA discovery questionnaire! (via {source})\n\n"
        f"ANSWERS:\n{answers_text}\n\n"
        f"CHANGES APPLIED:\n" + '\n'.join(f"  - {c}" for c in changes) + "\n\n"
        f"PREVIEW: http://137.184.136.55 (or http://megamassfitnesusa.com once DNS is pointed)\n\n"
        f"Reply here if further customization needed.\n\n"
        f"— NanoClaw"
    )

    key = get_resend_key()
    if not key:
        log("No Resend key found, skipping notification")
        return

    payload = json.dumps({
        'from': 'NanoClaw <james@benchonly.com>',
        'to': ['james@benchonly.com'],
        'subject': 'MegamassfitnessUSA — Kyle replied, mock-up updated',
        'text': body
    }).encode()

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json'
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
        log(f"Notification sent: {result.get('id', 'ok')}")
    except Exception as e:
        log(f"Notification failed: {e}")


def already_processed(uid):
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state.get('uid') == str(uid)
    except Exception:
        return False


def save_state(reply, answers):
    state = {
        'processed_at': datetime.now().isoformat(),
        'uid': reply['uid'],
        'source': reply.get('source', 'unknown'),
        'from': reply['from'],
        'subject': reply['subject'],
        'date': reply['date'],
        'answers': answers,
        'body_preview': reply['body'][:500]
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    log(f"State saved")


def main():
    log("=== Kyle Watcher v2 Starting ===")

    # Check primary: Virtualmin mailbox for james@stricklandtechnology.com
    reply = check_virtualmin_mailbox()

    # Fallback: Gmail (in case Kyle replied to the first email)
    if not reply:
        reply = check_gmail_fallback()

    if not reply:
        log("No reply from Kyle yet. Next check in 30 minutes.")
        return

    if already_processed(reply['uid']):
        log(f"Already processed UID {reply['uid']}. Skipping.")
        return

    log(f"New reply from Kyle — processing (source: {reply.get('source')})")

    answers = parse_discovery_answers(reply['body'])
    changes = update_mockup(answers)
    save_state(reply, answers)
    send_notification(answers, changes, reply.get('source', 'unknown'))

    log("=== Kyle Watcher v2 Complete ===")
    print(json.dumps({
        'status': 'replied',
        'source': reply.get('source'),
        'answers_count': len(answers),
        'changes': changes
    }, indent=2))


if __name__ == '__main__':
    main()
