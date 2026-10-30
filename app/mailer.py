"""Outbound email for sign-in links and codes.

Backends (CC_MAIL_BACKEND):
  smtp    - any SMTP provider (Gmail app password, Resend, SendGrid, Amazon SES, Postmark...). Default when CC_SMTP_HOST is set.
  outbox  - development/testing only: messages are written to CC_DATA_DIR/outbox as .eml files and are never delivered.
  (unset) - email sign-in is disabled.
"""
import json, os, smtplib, ssl, time
from email.message import EmailMessage
from pathlib import Path


class MailError(RuntimeError):
    pass


def backend():
    b = os.getenv('CC_MAIL_BACKEND', '').strip().lower()
    if b: return b
    return 'smtp' if os.getenv('CC_SMTP_HOST') else ''


def enabled(): return backend() in ('smtp', 'outbox')


def sender(): return os.getenv('CC_MAIL_FROM') or os.getenv('CC_SMTP_USER') or 'no-reply@localhost'


def send(to, subject, text, html=None, outbox_dir=None):
    msg = EmailMessage()
    msg['From'] = sender(); msg['To'] = to; msg['Subject'] = subject
    msg.set_content(text)
    if html: msg.add_alternative(html, subtype='html')
    b = backend()
    if b == 'outbox':
        d = Path(outbox_dir); d.mkdir(parents=True, exist_ok=True)
        f = d / f'{time.time_ns()}.eml'; f.write_bytes(bytes(msg))
        (d / 'latest.json').write_text(json.dumps({'to': to, 'subject': subject, 'text': text, 'file': f.name}))
        return {'backend': 'outbox', 'file': str(f)}
    if b != 'smtp': raise MailError('Email delivery is not configured')
    host = os.environ['CC_SMTP_HOST']; mode = os.getenv('CC_SMTP_TLS', 'starttls').lower()
    port = int(os.getenv('CC_SMTP_PORT', '465' if mode == 'ssl' else '587'))
    ctx = ssl.create_default_context()
    try:
        if mode == 'ssl': s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else: s = smtplib.SMTP(host, port, timeout=20)
        with s:
            if mode == 'starttls': s.starttls(context=ctx)
            if os.getenv('CC_SMTP_USER'): s.login(os.environ['CC_SMTP_USER'], os.getenv('CC_SMTP_PASSWORD', ''))
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(f'Email could not be sent: {type(e).__name__}') from e
    return {'backend': 'smtp'}
