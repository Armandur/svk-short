import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.templating import mail_env

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


def _render(template_name: str, **kwargs) -> str:
    return mail_env.get_template(template_name).render(**kwargs)


def skicka_verifieringsmail(to: str, verify_url: str, code: str, target_url: str):
    _send(
        to=to,
        subject=f"Aktivera din kortlänk /{code}",
        html=_render("verifiera.html", code=code, target_url=target_url, verify_url=verify_url),
    )


def skicka_overlatelse_godkand(to: str, code: str, base_url: str, bundle_name: str | None = None):
    _send(
        to=to,
        subject=f"Din begäran om svky.se/{code} har godkänts",
        html=_render(
            "overlatelse_godkand.html",
            code=code,
            bundle_name=bundle_name,
            manage_url=f"{base_url}/mina-lankar",
        ),
    )


def skicka_overlatelse_avslagen(to: str, code: str, bundle_name: str | None = None):
    _send(
        to=to,
        subject=f"Din begäran om svky.se/{code} har avslagits",
        html=_render("overlatelse_avslagen.html", code=code, bundle_name=bundle_name),
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
    _send(
        to=to,
        subject=f"Ny överlåtelsebegäran — svky.se/{code}",
        html=_render(
            "overlatelse_notis_admin.html",
            code=code,
            requester_email=requester_email,
            reason=reason,
            approve_url=approve_url,
            reject_url=reject_url,
            admin_url=admin_url,
        ),
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
    _send(
        to=to,
        subject=f"Ny överlåtelsebegäran — svky.se/{code} (samling)",
        html=_render(
            "bundle_overlatelse_notis_admin.html",
            code=code,
            bundle_name=bundle_name,
            requester_email=requester_email,
            reason=reason,
            approve_url=approve_url,
            reject_url=reject_url,
            admin_url=admin_url,
        ),
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
        html=_render(
            "overlatelseforfragan.html",
            from_email=from_email,
            code=code,
            target_url=target_url,
            accept_url=accept_url,
            decline_url=decline_url,
        ),
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
    n_links = len(links)
    n_bundles = len(bundles)
    parts = []
    if n_links:
        parts.append(f"{n_links} kortlänk{'ar' if n_links != 1 else ''}")
    if n_bundles:
        parts.append(f"{n_bundles} samling{'ar' if n_bundles != 1 else ''}")
    subject_items = " och ".join(parts)
    _send(
        to=to,
        subject=f"{from_email} vill överlåta {subject_items} till dig",
        html=_render(
            "bulk_overlatelseforfragan.html",
            from_email=from_email,
            links=links,
            bundles=bundles,
            subject_items=subject_items,
            accept_url=accept_url,
            decline_url=decline_url,
        ),
    )


def skicka_bulk_overlatelse_bekraftad_agare(
    to: str,
    codes: list[str],
    to_email: str,
    base_url: str = "",
    bundles: list[dict] | None = None,
):
    """codes är kortlänk-koder som flyttats. bundles är en lista med dict med
    nycklarna 'code' och 'name' för samlingar som flyttats tillsammans."""
    bundles = bundles or []
    n_links = len(codes)
    n_bundles = len(bundles)
    parts = []
    if n_links:
        parts.append(f"{n_links} kortlänk{'ar' if n_links != 1 else ''}")
    if n_bundles:
        parts.append(f"{n_bundles} samling{'ar' if n_bundles != 1 else ''}")
    subject_items = " och ".join(parts) if parts else "resurser"
    _send(
        to=to,
        subject=f"{subject_items.capitalize()} har överlåtits",
        html=_render(
            "bulk_overlatelse_bekraftad_agare.html",
            codes=codes,
            bundles=bundles,
            to_email=to_email,
        ),
    )


def skicka_bulk_overlatelse_avbojd_agare(
    to: str,
    codes: list[str],
    to_email: str,
    bundles: list[dict] | None = None,
):
    """codes är kortlänk-koder i den avböjda förfrågan. bundles är en lista
    med dict med nycklarna 'code' och 'name' för samlingar som också ingick."""
    bundles = bundles or []
    n_links = len(codes)
    n_bundles = len(bundles)
    parts = []
    if n_links:
        parts.append(f"{n_links} kortlänk{'ar' if n_links != 1 else ''}")
    if n_bundles:
        parts.append(f"{n_bundles} samling{'ar' if n_bundles != 1 else ''}")
    subject_items = " och ".join(parts) if parts else "resurser"
    _send(
        to=to,
        subject=f"Överlåtelsen av {subject_items} avböjdes",
        html=_render(
            "bulk_overlatelse_avbojd_agare.html",
            codes=codes,
            bundles=bundles,
            to_email=to_email,
        ),
    )


def skicka_overlatelse_bekraftad_agare(to: str, code: str, to_email: str, base_url: str = ""):
    _send(
        to=to,
        subject=f"svky.se/{code} har överlåtits",
        html=_render("overlatelse_bekraftad_agare.html", code=code, to_email=to_email),
    )


def skicka_overlatelse_avbojd_agare(to: str, code: str, to_email: str):
    _send(
        to=to,
        subject=f"Överlåtelsen av svky.se/{code} avböjdes",
        html=_render("overlatelse_avbojd_agare.html", code=code, to_email=to_email),
    )


def skicka_loginmail(to: str, login_url: str):
    _send(
        to=to,
        subject="Logga in på svky.se",
        html=_render("login.html", login_url=login_url),
    )


def skicka_radera_konto_bekraftelse(to: str, confirm_url: str):
    _send(
        to=to,
        subject="Bekräfta borttagning av ditt svky.se-konto",
        html=_render("radera_konto.html", confirm_url=confirm_url),
    )


def skicka_bundle_overlatelse(
    to_email: str, bundle_name: str, bundle_code: str, transfer_url: str, from_email: str = ""
):
    _send(
        to=to_email,
        subject=f"Du har fått en länksamling på svky.se: {bundle_name}",
        html=_render(
            "bundle_overlatelse.html",
            bundle_name=bundle_name,
            bundle_code=bundle_code,
            transfer_url=transfer_url,
            from_email=from_email,
        ),
    )
