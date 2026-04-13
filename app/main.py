from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.database import init_db, log_page_view
from app.routes import public, auth, user, admin
from app.csrf import generate_csrf_token
from app.templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


_TRACKED_PATHS = {"/", "/login", "/mina-lankar", "/mina-samlingar", "/om", "/integritet", "/bestall"}

app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def track_page_views(request: Request, call_next):
    response = await call_next(request)
    if (
        request.method == "GET"
        and request.url.path in _TRACKED_PATHS
        and response.status_code == 200
    ):
        log_page_view(request.url.path, request.headers.get("referer"))
    return response


templates.env.globals["csrf_token"] = generate_csrf_token
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(user.router)
app.include_router(admin.router)
app.include_router(public.router)  # sist — innehåller catch-all GET /{code}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    code = request.url.path.lstrip("/")
    return templates.TemplateResponse(
        "404.html",
        {"request": request, "code": code},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return HTMLResponse("Förbjudet", status_code=403)
