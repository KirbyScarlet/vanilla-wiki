import fastapi
from fastapi.logger import logger
from fastapi.responses import HTMLResponse
from fastapi import Request, HTTPException
from contextlib import asynccontextmanager

from .config import load_config

_docs_template_content = __import__("pathlib").Path("static/templates/docs.html").read_text(encoding="utf-8")

@asynccontextmanager
async def startup(app: fastapi.FastAPI):
    try:
        config = load_config()
        app.state.config = config
    except Exception as e:
        logger.error("config load faild")


    yield
    

    logger.info("bye~")

app = fastapi.FastAPI(lifespan=startup)

# ── Route middleware ─────────────────────────────────────────────
# Serve the homepage at / without relying on main.py's route decorator.
# This ensures the root route always takes precedence over the catch-all.
@app.middleware("http")
async def homepage_middleware(request: fastapi.Request, call_next):
    if request.url.path == "/":
        from .main import root
        response = await root(request)
        return response
    return await call_next(request)



@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "message": "Hay you, finally awake!"}

@app.get("/static/{path:path}")
async def static_files(path: str):
    """静态文件服务"""
    if path.startswith("..") or path.startswith("/") or path.startswith("templates"):
        return fastapi.responses.JSONResponse(
            status_code=400,
            content={"error": "Invalid path"}
        )
    return fastapi.responses.FileResponse(f"static/{path}")