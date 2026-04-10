from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from app.database import init_db
from app.routes import public, auth, user, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory="app/templates")
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
