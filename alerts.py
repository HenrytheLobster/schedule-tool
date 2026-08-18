#!/usr/bin/env python3
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from config_loader import merged_env


REQUIRED_SETTINGS = (
    "NOTIFY_EMAIL_TO",
    "SMTP_HOST",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
)


def send_failure_alert(subject: str, body: str, env_file: str | Path | None = None) -> None:
    env = merged_env(env_file)
    missing = [name for name in REQUIRED_SETTINGS if not env.get(name)]
    if missing:
        raise RuntimeError(
            "SMTP alert settings are incomplete: missing " + ", ".join(sorted(missing))
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = env["SMTP_FROM"]
    message["To"] = env["NOTIFY_EMAIL_TO"]
    message.set_content(body)

    with smtplib.SMTP(env["SMTP_HOST"], int(env.get("SMTP_PORT", "587")), timeout=20) as server:
        server.starttls()
        server.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
        server.send_message(message)
