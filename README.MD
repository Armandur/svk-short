# svky.se — Self-hosted URL Shortener

A lightweight, self-hosted URL shortener built for organizations. Users request short links via a form, verify ownership via a magic link sent to their email, and manage their links through a simple dashboard. Admins can monitor and moderate all links.

Originally built for use within Svenska kyrkan (Church of Sweden), but easy to adapt for any organization.

## Features

- **No passwords** — login and link verification via one-time email links
- **Email domain restriction** — only allow sign-ups from your organization's domain
- **Short link management** — users can update the target URL or deactivate their own links
- **Click statistics** — per-link click chart (last 30 days) visible to both users and admins
- **Admin panel** — view all links, toggle status, transfer ownership, manage users
- **Link takeover requests** — users can request ownership of an existing link; admins approve/reject directly from the notification email
- **Editable pages** — About and Privacy pages are editable by admin via Markdown with live preview
- **Rate limiting** — built-in per-IP rate limiting via SQLite
- **GDPR-friendly** — no IP addresses stored, only email, link metadata, and anonymous click counts

## Tech Stack

- **Python 3.12 + FastAPI** (ASGI, served by uvicorn)
- **SQLite** via the standard library — no ORM, no external database
- **Jinja2** templates
- **itsdangerous** for signed session cookies and action tokens
- **smtplib** for transactional email (works with any SMTP provider)
- **Caddy** as reverse proxy in production (automatic TLS)
- **Docker + Docker Compose** for deployment

## Getting Started

### Prerequisites

- Docker and Docker Compose
- An SMTP provider (e.g. [Lettermint](https://lettermint.co), Mailgun, Postmark, etc.)

### Local Development

```bash
git clone https://github.com/armandur/svk-short.git
cd svk-short
cp .env.example .env
# Edit .env with your settings
docker compose -f docker-compose.dev.yml up
```

The app will be available at `http://localhost:8000`.

### Production (with Caddy)

```bash
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

Caddy handles TLS automatically. Point your domain's DNS to the server and set `BASE_URL` accordingly.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_PATH` | Path to the SQLite database file | `data/links.db` |
| `SECRET_KEY` | Secret key for signing cookies and tokens | — |
| `BASE_URL` | Public URL of your instance, e.g. `https://example.com` | `http://localhost:8000` |
| `MAIL_FROM` | Sender address for outgoing email | `link@svky.se` |
| `SMTP_HOST` | SMTP server hostname | `smtp.lettermint.net` |
| `SMTP_PORT` | SMTP port | `587` |
| `SMTP_USER` | SMTP username | — |
| `SMTP_PASS` | SMTP password | — |

Generate a secure `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Adapting for Your Organization

### Restrict to your email domain

Edit `app/validation.py` — the `validate_email()` function checks the domain. Change `@svenskakyrkan.se` to your organization's domain.

### Restrict target URLs

Edit `app/validation.py` — the `validate_target_url()` function currently only allows `https://*.svenskakyrkan.se`. Adjust the domain check or remove it entirely to allow any HTTPS URL.

### Grant admin access

```bash
sqlite3 data/links.db "UPDATE users SET is_admin=1 WHERE email='you@example.com';"
```

### Edit the About and Privacy pages

Log in as admin and go to **Admin → Om-sidan** or **Admin → Integritet** to edit the page content using Markdown.

## Deployment Notes

- The database is a single SQLite file — back it up regularly (e.g. daily `cp` to object storage)
- All redirects use **302** (not 301) to allow target URL changes without browser cache issues
- The app uses `PRAGMA foreign_keys = ON` — schema integrity is enforced at the SQLite level

## CI/CD

GitHub Actions builds and publishes a Docker image on every push:

| Trigger | Image tag |
|---|---|
| Push to `main` | `:latest` + `:<sha>` |
| Push to branch | `:<branch-name>` |
| Git tag `v1.2.3` | `:1.2.3`, `:1.2`, `:1` |

Images are published to `ghcr.io/armandur/svk-short`.

## License

MIT
