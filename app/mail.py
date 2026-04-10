import smtplib
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.lettermint.net")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "link@svky.se")


class MailError(Exception):
    pass


def _send(to: str, subject: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(MAIL_FROM, to, msg.as_string())
    except Exception as e:
        log.error("Kunde inte skicka mail till %s: %s", to, e)
        raise MailError(str(e)) from e


def skicka_verifieringsmail(to: str, verify_url: str, code: str, target_url: str):
    _send(
        to=to,
        subject=f"Aktivera din kortlänk /{code}",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <div style="max-width:540px;margin:0 auto;background:#fff;border:1px solid #cdd5e0;
              border-radius:6px;padding:32px 36px;">
    <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">
      svky.se
    </div>
    <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">
      Aktivera din kortlänk
    </h1>
    <p>Du har beställt kortlänken <strong style="font-family:monospace;">svky.se/{code}</strong>
    som pekar till:</p>
    <p style="background:#f0f4fb;border-radius:4px;padding:10px 14px;
              font-size:.9rem;word-break:break-all;margin:12px 0;">{target_url}</p>
    <p>Klicka på knappen nedan för att aktivera länken:</p>
    <a href="{verify_url}"
       style="display:inline-block;background:#2355a0;color:#fff;padding:12px 24px;
              border-radius:6px;text-decoration:none;font-weight:600;margin:8px 0 20px;">
      Aktivera kortlänk
    </a>
    <p style="font-size:.85rem;color:#5a6070;margin-top:20px;">
      Länken är giltig i 24 timmar. Om du inte beställt en kortlänk kan du ignorera detta mail.
    </p>
    <hr style="border:none;border-top:1px solid #cdd5e0;margin:20px 0;">
    <p style="font-size:.78rem;color:#5a6070;">
      svky.se — intern URL-förkortare för Svenska kyrkan
    </p>
  </div>
</body>
</html>
        """,
    )


def skicka_loginmail(to: str, login_url: str):
    _send(
        to=to,
        subject="Logga in på svky.se",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <div style="max-width:540px;margin:0 auto;background:#fff;border:1px solid #cdd5e0;
              border-radius:6px;padding:32px 36px;">
    <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">
      svky.se
    </div>
    <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">
      Logga in på svky.se
    </h1>
    <p>Klicka på knappen nedan för att logga in. Länken är giltig i 1 timme
       och kan bara användas en gång.</p>
    <a href="{login_url}"
       style="display:inline-block;background:#2355a0;color:#fff;padding:12px 24px;
              border-radius:6px;text-decoration:none;font-weight:600;margin:8px 0 20px;">
      Logga in
    </a>
    <p style="font-size:.85rem;color:#5a6070;margin-top:20px;">
      Beställde du inte en inloggning? Du kan ignorera detta mail.
    </p>
    <hr style="border:none;border-top:1px solid #cdd5e0;margin:20px 0;">
    <p style="font-size:.78rem;color:#5a6070;">
      svky.se — intern URL-förkortare för Svenska kyrkan
    </p>
  </div>
</body>
</html>
        """,
    )
