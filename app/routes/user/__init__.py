from fastapi import APIRouter

from . import account, bundles, links

router = APIRouter()
router.include_router(links.router)
router.include_router(bundles.router)
router.include_router(account.router)
