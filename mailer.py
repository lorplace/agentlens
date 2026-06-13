"""Optional email alerts. Create email_config.json next to the code:

{
  "host": "smtp-relay.brevo.com",
  "port": 587,
  "user": "your-smtp-login",
  "password": "your-smtp-key",
  "from": "alerts@yourdomain.com",
  "to": "you@gmail.com"
}

No file = email alerts silently disabled.
"""

import json
import os
import smtplib
from email.message import EmailMessage

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_config.json")


def configured():
    return os.path.exists(CFG_PATH)


def send(subject, body):
    """Send an email per config. Returns True on success, False otherwise."""
    try:
        with open(CFG_PATH) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = cfg["to"]
        msg.set_content(body)
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=20) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return True
    except Exception:  # noqa: BLE001
        return False
