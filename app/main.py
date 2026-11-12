import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.db import engine
from app.inventory.router import router
from app.inventory.service import InventoryError

logger = logging.getLogger("checkstock")
app = FastAPI(title="CheckStock", version="1.0.0", docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
)
app.include_router(router)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"message": "Запрос с другого сайта запрещён"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"message": "Запрос с другого сайта запрещён"}, status_code=403)
        if request.method != "DELETE":
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
            if media_type != "application/json":
                return JSONResponse({"message": "Ожидается JSON"}, status_code=415)
        content_length = request.headers.get("content-length", "0")
        if not content_length.isdigit() or int(content_length) > 8192:
            return JSONResponse({"message": "Слишком большой запрос"}, status_code=413)
        if len(await request.body()) > 8192:
            return JSONResponse({"message": "Слишком большой запрос"}, status_code=413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(InventoryError)
async def inventory_error(request: Request, error: InventoryError):
    return JSONResponse({"message": error.message, **error.details}, status_code=error.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse(
        {
            "message": "Проверьте поля: количество должно быть целым и больше нуля, "
            "названия не должны быть пустыми, маршрут должен быть корректным."
        },
        status_code=422,
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, error: IntegrityError):
    logger.warning(
        "Database constraint rejected %s: %s", request.url.path, type(error.orig).__name__
    )
    return JSONResponse(
        {
            "message": "Данные конфликтуют с существующей записью. "
            "Обновите страницу и проверьте название ФФ."
        },
        status_code=409,
    )


@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeout)
async def database_error(request: Request, error: OperationalError | PoolTimeout):
    logger.warning("Database unavailable for %s: %s", request.url.path, type(error).__name__)
    return JSONResponse(
        {"message": "База данных временно занята или недоступна. Повторите запрос."},
        status_code=503,
        headers={"Retry-After": "2"},
    )


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}
