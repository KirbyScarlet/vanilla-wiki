import fastapi

app = fastapi.FastAPI()

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