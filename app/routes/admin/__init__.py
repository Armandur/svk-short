"""Admin-routes — sammansatt paket.

Varje submodul hanterar ett ansvarsområde:
  links.py     — länklista, skapa, detalj, aktivera/deaktivera, uppdatera
  users.py     — användarlista, rättigheter, massöverlåtelse, inloggningslänk
  bundles.py   — samlingshantering (visa, redigera, inaktivera, konvertera)
  takeovers.py — överlåtelseförfrågningar (godkänn/avvisa via panel och e-post)
  featured.py  — snabblänkar på startsidan (featured links)
  settings.py  — om-sidan och integritetssidan (markdown-redigering)
  stats.py     — klick- och sidvisningsstatistik
  helpers.py   — interna hjälpfunktioner
"""

from fastapi import APIRouter

from . import bundles, featured, links, settings, stats, takeovers, transfers, users

router = APIRouter(prefix="/admin")

router.include_router(links.router)
router.include_router(users.router)
router.include_router(bundles.router)
router.include_router(takeovers.router)
router.include_router(featured.router)
router.include_router(settings.router)
router.include_router(stats.router)
router.include_router(transfers.router)
