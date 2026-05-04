import fastapi
from fastapi.logger import logger
from fastapi.responses import HTMLResponse
from fastapi import Request, HTTPException
from contextlib import asynccontextmanager

from .config import load_config
from . import es_client


@asynccontextmanager
async def startup(app: fastapi.FastAPI):
    try:
        config = load_config()
        app.state.config = config
    except Exception as e:
        logger.error("config load failed")

    # 初始化 Elasticsearch 客户端
    try:
        cfg = app.state.config
        es = await es_client.init_es_client(cfg)
        await es_client.ensure_mapping_index(es)
        # 全量同步本地文档到 ES 映射
        await es_client.scan_and_sync_all(es, cfg.data_dir, cfg)
        app.state.es = es
        logger.info("Elasticsearch mapping synced successfully")
    except Exception as e:
        logger.error("ES initialization failed: %s (will continue without ES)", e)
        app.state.es = None

    yield

    # Shutdown
    if app.state.es:
        try:
            await es_client.close_es_client()
        except Exception:
            pass
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
