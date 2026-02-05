
from .main import app
from .config import env
from fastapi.staticfiles import StaticFiles
from fastapi import APIRouter
from fastapi.responses import FileResponse
import yaml
from typing import Mapping
import aiofiles

import pathlib

__all__ = ["build_category", "docs_router", "get_categories", "get_docs"]

docs_router = APIRouter()

DOCS_PATH = pathlib.Path(env.docs_dir)

async def build_category(dir: pathlib.Path|str, recursion: bool=True) -> Mapping:
    """构建文档分类"""
    dir = pathlib.Path(dir)
    categories = []
    for category in dir.iterdir():
        if category.is_dir():
            category_info_file = category / "category.yaml"
            category_data = {}
            if category_info_file.exists():
                async with aiofiles.open(category_info_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    category_data.update(yaml.safe_load(content))
            categories.append({
                "type": "directory",
                "name": category.name,
                "title": category_data.get("title", category.name),
                "description": category_data.get("description", ""),
            })
            if recursion:
                categories[-1].update({"items": await build_category(category)})
        else:
            if category.name == "category.yaml":
                continue
            categories.append({
                "type": "file",
                "name": category.name,
                "title": category.stem,
                "description": "",
            })
    return categories
        

@docs_router.get("/category")
async def get_categories():
    """获取所有文档分类"""
    category = await build_category(DOCS_PATH)

    return {"category": category}

app.include_router(docs_router, prefix="/api/docs", tags=["docs"])

@app.get("/docs/{file_id}")
async def get_docs_by_id(file_id: str):
    """也是获取文档内容，但是需要先获取id对应的文件"""
    pass

@app.get("/{category}/{filename:path}")
async def get_docs(category: str, filename: str):
    """获取文档内容"""
    file_path = DOCS_PATH / category / filename
    if not file_path.exists():
        return {"error": "File not found"}
    
    return FileResponse(file_path, media_type="text/markdown")