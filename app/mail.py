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
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Aktivera din kortlänk</h1>
      <p style="margin:0 0 8px;">Du har beställt kortlänken
        <strong style="font-family:monospace;">svky.se/{code}</strong> som pekar till:</p>

      <table width="100%" cellspacing="0" cellpadding="0" style="margin:12px 0;">
        <tr>
          <td style="background:#f0f4fb;padding:10px 14px;font-size:13px;word-break:break-all;border-radius:4px;">
            {target_url}
          </td>
        </tr>
      </table>

      <p style="margin:0 0 16px;">Klicka på knappen nedan och bekräfta aktiveringen på sidan som öppnas:</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 24px;">
        <tr>
          <td style="background:#2355a0;border-radius:6px;">
            <a href="{verify_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              Gå till aktivering
            </a>
          </td>
        </tr>
      </table>

      <p style="font-size:.85rem;color:#5a6070;margin:0 0 20px;">
        Länken är giltig i 24 timmar. Om du inte beställt en kortlänk kan du ignorera detta mail.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">
        svky.se
      </p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelse_godkand(to: str, code: str, base_url: str, bundle_name: str | None = None):
    kind_txt = f"samlingen <em>{bundle_name}</em>" if bundle_name else "kortlänken"
    manage_url = f"{base_url}/mina-lankar"
    manage_label = "Gå till Mina länkar"
    _send(
        to=to,
        subject=f"Din begäran om svky.se/{code} har godkänts",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse godkänd</h1>
      <p style="margin:0 0 16px;">Din begäran om att ta över {kind_txt}
        <strong style="font-family:monospace;">svky.se/{code}</strong> har godkänts.
        Du är nu ägare och kan hantera den via Mina länkar.</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 24px;">
        <tr>
          <td style="background:#2355a0;border-radius:6px;">
            <a href="{manage_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              {manage_label}
            </a>
          </td>
        </tr>
      </table>

      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">
        svky.se
      </p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelse_avslagen(to: str, code: str, bundle_name: str | None = None):
    kind_txt = f"samlingen <em>{bundle_name}</em>" if bundle_name else "kortlänken"
    _send(
        to=to,
        subject=f"Din begäran om svky.se/{code} har avslagits",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse avslagen</h1>
      <p style="margin:0 0 12px;">Din begäran om att ta över {kind_txt}
        <strong style="font-family:monospace;">svky.se/{code}</strong> har tyvärr avslagits
        av en administratör.</p>
      <p style="color:#5a6070;font-size:.9rem;margin:0 0 20px;">
        Om du har frågor kan du kontakta administratören direkt.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">
        svky.se
      </p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelse_notis_admin(
    to: str,
    code: str,
    requester_email: str,
    reason: str | None,
    approve_url: str,
    reject_url: str,
    admin_url: str,
):
    reason_html = (
        f"<p style='margin:0 0 12px;'><strong>Anledning:</strong> {reason}</p>"
        if reason else ""
    )
    _send(
        to=to,
        subject=f"Ny överlåtelsebegäran — svky.se/{code}",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Ny överlåtelsebegäran</h1>
      <p style="margin:0 0 8px;">
        <strong>{requester_email}</strong> vill ta över kortlänken
        <strong style="font-family:monospace;">svky.se/{code}</strong>.
      </p>
      {reason_html}
      <table cellspacing="0" cellpadding="0" style="margin:16px 0 8px;">
        <tr>
          <td style="background:#1a7a3a;border-radius:6px;padding:0 8px 0 0;">
            <a href="{approve_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              &#10003;&nbsp; Godkänn
            </a>
          </td>
          <td style="padding-left:8px;">
            <a href="{reject_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;background:#b91c1c;
                      border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;">
              &#10007;&nbsp; Avslå
            </a>
          </td>
        </tr>
      </table>
      <p style="font-size:.82rem;color:#5a6070;margin:8px 0 20px;">
        Länkarna är giltiga i 7 dagar. Du kan även
        <a href="{admin_url}" style="color:#2355a0;">hantera begäran i adminpanelen</a>.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_bundle_overlatelse_notis_admin(
    to: str,
    code: str,
    bundle_name: str,
    requester_email: str,
    reason: str | None,
    approve_url: str,
    reject_url: str,
    admin_url: str,
):
    reason_html = (
        f"<p style='margin:0 0 12px;'><strong>Anledning:</strong> {reason}</p>"
        if reason else ""
    )
    _send(
        to=to,
        subject=f"Ny överlåtelsebegäran — svky.se/{code} (samling)",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Ny överlåtelsebegäran (samling)</h1>
      <p style="margin:0 0 8px;">
        <strong>{requester_email}</strong> vill ta över samlingen
        <strong>{bundle_name}</strong>
        (<strong style="font-family:monospace;">svky.se/{code}</strong>).
      </p>
      {reason_html}
      <table cellspacing="0" cellpadding="0" style="margin:16px 0 8px;">
        <tr>
          <td style="background:#1a7a3a;border-radius:6px;padding:0 8px 0 0;">
            <a href="{approve_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              &#10003;&nbsp; Godkänn
            </a>
          </td>
          <td>
            <a href="{reject_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;background:#b91c1c;
                      border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;">
              &#10007;&nbsp; Avslå
            </a>
          </td>
        </tr>
      </table>
      <p style="font-size:.82rem;color:#5a6070;margin:8px 0 20px;">
        Länkarna är giltiga i 7 dagar. Du kan även
        <a href="{admin_url}" style="color:#2355a0;">hantera begäran i adminpanelen</a>.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelseforfragan(
    to: str,
    from_email: str,
    code: str,
    target_url: str,
    accept_url: str,
    decline_url: str,
):
    _send(
        to=to,
        subject=f"Du har fått en förfrågan om kortlänken svky.se/{code}",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelseförfrågan</h1>
      <p style="margin:0 0 8px;">
        <strong>{from_email}</strong> vill överlåta kortlänken
        <strong style="font-family:monospace;">svky.se/{code}</strong> till dig.
      </p>

      <table width="100%" cellspacing="0" cellpadding="0" style="margin:12px 0;">
        <tr>
          <td style="background:#f0f4fb;padding:10px 14px;font-size:13px;word-break:break-all;border-radius:4px;">
            {target_url}
          </td>
        </tr>
      </table>

      <p style="margin:0 0 16px;">Vill du ta emot länken och bli ny ägare?</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 8px;">
        <tr>
          <td style="background:#1a7a3a;border-radius:6px;padding:0 8px 0 0;">
            <a href="{accept_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              &#10003;&nbsp; Ja, ta emot
            </a>
          </td>
          <td style="padding-left:8px;">
            <a href="{decline_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;background:#b91c1c;
                      border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;">
              &#10007;&nbsp; Nej tack
            </a>
          </td>
        </tr>
      </table>

      <p style="font-size:.82rem;color:#5a6070;margin:8px 0 20px;">
        Länkarna är giltiga i 7 dagar. Om du inte väntar dig detta mail kan du ignorera det.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_bulk_overlatelseforfragan(
    to: str,
    from_email: str,
    links: list[dict],
    accept_url: str,
    decline_url: str,
    bundles: list[dict] | None = None,
):
    """links har nycklarna 'code' och 'target_url'. bundles har 'code' och 'name'."""
    bundles = bundles or []
    link_rows_html = "".join(
        f"""<tr>
              <td style="padding:5px 0;font-family:monospace;font-size:13px;color:#193d7a;">
                svky.se/{lnk['code']}
              </td>
              <td style="padding:5px 0 5px 16px;font-size:13px;word-break:break-all;color:#5a6070;">
                {lnk['target_url']}
              </td>
            </tr>"""
        for lnk in links
    )
    bundle_rows_html = "".join(
        f"""<tr>
              <td style="padding:5px 0;font-family:monospace;font-size:13px;color:#193d7a;">
                svky.se/{b['code']}
              </td>
              <td style="padding:5px 0 5px 16px;font-size:13px;color:#5a6070;">
                📋 {b['name']}
              </td>
            </tr>"""
        for b in bundles
    )

    n_links = len(links)
    n_bundles = len(bundles)
    parts = []
    if n_links:
        parts.append(f"{n_links} kortlänk{'ar' if n_links != 1 else ''}")
    if n_bundles:
        parts.append(f"{n_bundles} samling{'ar' if n_bundles != 1 else ''}")
    subject_items = " och ".join(parts)

    links_section = ""
    if links:
        links_section = f"""
      <p style="margin:12px 0 6px;font-size:.85rem;font-weight:700;color:#193d7a;">Kortlänkar</p>
      <table width="100%" cellspacing="0" cellpadding="0"
             style="background:#f0f4fb;border-radius:4px;padding:10px 14px;margin:0 0 8px;">
        {link_rows_html}
      </table>"""
    bundles_section = ""
    if bundles:
        bundles_section = f"""
      <p style="margin:12px 0 6px;font-size:.85rem;font-weight:700;color:#193d7a;">Samlingar</p>
      <table width="100%" cellspacing="0" cellpadding="0"
             style="background:#f0f4fb;border-radius:4px;padding:10px 14px;margin:0 0 8px;">
        {bundle_rows_html}
      </table>"""

    _send(
        to=to,
        subject=f"{from_email} vill överlåta {subject_items} till dig",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelseförfrågan</h1>
      <p style="margin:0 0 12px;">
        <strong>{from_email}</strong> vill överlåta följande {subject_items} till dig:
      </p>
      {links_section}
      {bundles_section}
      <p style="margin:8px 0 16px;">Vill du ta emot allt och bli ny ägare?</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 8px;">
        <tr>
          <td style="background:#1a7a3a;border-radius:6px;padding:0 8px 0 0;">
            <a href="{accept_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              &#10003;&nbsp; Ja, ta emot allt
            </a>
          </td>
          <td style="padding-left:8px;">
            <a href="{decline_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;background:#b91c1c;
                      border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;">
              &#10007;&nbsp; Nej tack
            </a>
          </td>
        </tr>
      </table>

      <p style="font-size:.82rem;color:#5a6070;margin:8px 0 20px;">
        Giltigt i 7 dagar. Godkänner eller avböjer du övertas allt på en gång.
        Om du inte väntar dig detta mail kan du ignorera det.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_bulk_overlatelse_bekraftad_agare(
    to: str, codes: list[str], to_email: str, base_url: str
):
    codes_html = "".join(
        f"<li style='font-family:monospace;'>svky.se/{c}</li>" for c in codes
    )
    count = len(codes)
    _send(
        to=to,
        subject=f"{count} kortlänk{'ar' if count != 1 else ''} har överlåtits",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse genomförd</h1>
      <p style="margin:0 0 8px;">Följande kortlänkar har nu överlåtits till
        <strong>{to_email}</strong> och är inte längre kopplade till ditt konto:</p>
      <ul style="margin:8px 0 16px;padding-left:20px;color:#193d7a;">{codes_html}</ul>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_bulk_overlatelse_avbojd_agare(to: str, codes: list[str], to_email: str):
    codes_html = "".join(
        f"<li style='font-family:monospace;'>svky.se/{c}</li>" for c in codes
    )
    count = len(codes)
    _send(
        to=to,
        subject=f"Överlåtelsen av {count} kortlänk{'ar' if count != 1 else ''} avböjdes",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse avböjd</h1>
      <p style="margin:0 0 8px;"><strong>{to_email}</strong> har avböjt att ta emot följande kortlänkar:</p>
      <ul style="margin:8px 0 16px;padding-left:20px;color:#193d7a;">{codes_html}</ul>
      <p style="color:#5a6070;font-size:.9rem;margin:0 0 20px;">
        Länkarna är fortfarande kopplade till ditt konto och fungerar som tidigare.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelse_bekraftad_agare(to: str, code: str, to_email: str, base_url: str):
    _send(
        to=to,
        subject=f"svky.se/{code} har överlåtits",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse genomförd</h1>
      <p style="margin:0 0 16px;">Kortlänken
        <strong style="font-family:monospace;">svky.se/{code}</strong> har nu överlåtits till
        <strong>{to_email}</strong> och är inte längre kopplad till ditt konto.</p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_overlatelse_avbojd_agare(to: str, code: str, to_email: str):
    _send(
        to=to,
        subject=f"Överlåtelsen av svky.se/{code} avböjdes",
        html=f"""
<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             font-size:15px;line-height:1.6;color:#1a1a1a;background:#f4f6f9;margin:0;padding:20px;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Överlåtelse avböjd</h1>
      <p style="margin:0 0 12px;"><strong>{to_email}</strong> har avböjt att ta emot kortlänken
        <strong style="font-family:monospace;">svky.se/{code}</strong>.</p>
      <p style="color:#5a6070;font-size:.9rem;margin:0 0 20px;">
        Länken är fortfarande kopplad till ditt konto och fungerar som tidigare.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
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
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
  <table width="540" cellspacing="0" cellpadding="0"
         style="background:#fff;border:1px solid #cdd5e0;border-radius:6px;padding:32px 36px;max-width:540px;">
    <tr><td>
      <div style="font-size:1.2rem;font-weight:700;color:#193d7a;margin-bottom:24px;">svky.se</div>
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Logga in på svky.se</h1>
      <p style="margin:0 0 16px;">Klicka på knappen nedan och bekräfta inloggningen på sidan som öppnas.
        Länken är giltig i 1 timme och kan bara användas en gång.</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 24px;">
        <tr>
          <td style="background:#2355a0;border-radius:6px;">
            <a href="{login_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              Gå till inloggning
            </a>
          </td>
        </tr>
      </table>

      <p style="font-size:.85rem;color:#5a6070;margin:0 0 20px;">
        Beställde du inte en inloggning? Du kan ignorera detta mail.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">
        svky.se
      </p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )


def skicka_bundle_overlatelse(to_email: str, bundle_name: str, bundle_code: str, transfer_url: str):
    _send(
        to_email,
        f"Du har fått en länksamling på svky.se: {bundle_name}",
        f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;">
  <table width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:32px 16px;">
  <table width="560" cellspacing="0" cellpadding="0"
         style="background:#fff;border-radius:8px;border:1px solid #cdd5e0;
                font-family:system-ui,-apple-system,sans-serif;font-size:15px;
                color:#1a2535;overflow:hidden;">
    <tr><td style="background:#193d7a;padding:20px 32px;">
      <span style="color:#fff;font-size:1.1rem;font-weight:700;">svky.se</span>
    </td></tr>
    <tr><td style="padding:32px;">
      <h1 style="font-size:1.2rem;color:#193d7a;margin:0 0 16px;">Länksamling överlåten till dig</h1>
      <p style="margin:0 0 12px;">
        Någon vill överlåta länksamlingen <strong>{bundle_name}</strong>
        (<code>svky.se/{bundle_code}</code>) till dig.
      </p>
      <p style="margin:0 0 24px;">Klicka på knappen nedan och bekräfta på sidan som öppnas för att bli ny ägare. Länken är giltig i 7 dagar.</p>

      <table cellspacing="0" cellpadding="0" style="margin:0 0 24px;">
        <tr>
          <td style="background:#2355a0;border-radius:6px;">
            <a href="{transfer_url}"
               style="display:inline-block;padding:12px 28px;color:#fff;
                      text-decoration:none;font-weight:600;font-size:15px;">
              Gå till acceptering
            </a>
          </td>
        </tr>
      </table>

      <p style="font-size:.85rem;color:#5a6070;margin:0 0 20px;">
        Vill du inte ta emot samlingen? Ignorera bara detta mail.
      </p>
      <hr style="border:none;border-top:1px solid #cdd5e0;margin:0 0 16px;">
      <p style="font-size:.78rem;color:#5a6070;margin:0;">svky.se</p>
    </td></tr>
  </table>
  </td></tr></table>
</body>
</html>
        """,
    )
