from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
import yaml
import markdown
import re
import html as html_module
import aiofiles
import pathlib
from bs4 import BeautifulSoup

from .utils import app
from .config import config
from . import es_client

__all__ = ["build_category", "build_category_tree", "docs_router", "get_categories"]

docs_router = APIRouter()

DOCS_PATH = pathlib.Path(config.data_dir)

# ── markdown → HTML ──────────────────────────────────────────────
_md_extensions = [
    "extra",
    "tables",
    "fenced_code",
    "codehilite",
    "toc",
    "attr_list",
    "md_in_html",
]


def markdown_to_html(text: str) -> str:
    return markdown.markdown(text, extensions=_md_extensions, extension_configs={
        "codehilite": {"guess_lang": False},
    })


def extract_toc(html: str) -> str:
    """从渲染后的 HTML 中提取目录 (h1~h4)"""
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.find_all(re.compile(r"^h[1-4]$"))
    if not headings:
        return ""
    lines: list[str] = []
    for h in headings:
        text = h.get_text(strip=True)
        anchor = h.get("id") or text.lower().replace(" ", "-").replace("—", "-")
        if not h.get("id"):
            h["id"] = anchor
        lines.append(
            f'<li><a href="#{html_module.escape(anchor)}">{html_module.escape(text)}</a></li>'
        )
    return "\n".join(lines)


# ── category helpers ─────────────────────────────────────────────

async def _read_category_yaml(dirpath: pathlib.Path) -> dict:
    yml = dirpath / "category.yaml"
    if not yml.exists():
        return {}
    async with aiofiles.open(yml, "r", encoding="utf-8") as f:
        content = await f.read()
    data = yaml.safe_load(content)
    return data if isinstance(data, dict) else {}


async def _list_documents(dirpath: pathlib.Path, cat_yaml: dict, prefix: str = "") -> list[dict]:
    """列出目录下的文档文件，按 order 规则排序，返回带哈希 URL 的结果"""
    manual_order = cat_yaml.get("categories", [])
    order = cat_yaml.get("order", "none")

    files: list[pathlib.Path] = []
    for p in dirpath.iterdir():
        if p.is_file() and p.name not in ("_category.yaml", "category.yaml"):
            files.append(p)

    if order == "manual" and manual_order:
        order_map = {name: i for i, name in enumerate(manual_order)}
        files.sort(key=lambda f: order_map.get(f.name, 999999))
    else:
        files.sort(key=lambda f: f.name)

    docs = []
    stem_map = {f.stem: f for f in files}
    for name in manual_order if order == "manual" and manual_order else []:
        if name in stem_map:
            f = stem_map[name]
            doc_id = es_client.generate_doc_id(prefix or dirpath.name, f.name, "doc")
            docs.append({
                "name": f.name,
                "title": f.stem,
                "url": f"/docs/{doc_id}",
                "active": False,
                "category": prefix or dirpath.name,
                "filename": f.name,
            })

    for f in files:
        if order != "manual" or f.name not in [m for m in manual_order]:
            doc_id = es_client.generate_doc_id(prefix or dirpath.name, f.name, "doc")
            docs.append({
                "name": f.name,
                "title": f.stem,
                "url": f"/docs/{doc_id}",
                "active": False,
                "category": prefix or dirpath.name,
                "filename": f.name,
            })

    return docs


async def build_category_tree(dirpath: pathlib.Path) -> list[dict]:
    """递归构建分类树，返回 [{type, name, title, description, uri, items?}]"""
    cat_data = await _read_category_yaml(dirpath)

    entries: list[dict] = []

    for p in sorted(dirpath.iterdir()):
        if p.is_dir():
            sub_yml = await _read_category_yaml(p)
            cat_id = es_client.generate_doc_id(p.name, "category.yaml", "dir")
            entries.append({
                "type": "directory",
                "name": p.name,
                "title": sub_yml.get("title", p.name),
                "description": sub_yml.get("description", ""),
                "uri": f"/docs/{cat_id}",
                "enable": sub_yml.get("enable", True),
            })
            sub_items = await build_category_tree(p)
            if sub_items:
                entries[-1]["items"] = sub_items

    # 添加文件条目（使用哈希 URL）
    manual_order = cat_data.get("categories", [])
    order = cat_data.get("order", "none")
    files = [p for p in sorted(dirpath.iterdir()) if p.is_file() and p.name not in ("category.yaml",)]

    if order == "manual" and manual_order:
        stem_map = {f.stem: f for f in files}
        for name in manual_order:
            if name in stem_map:
                f = stem_map[name]
                doc_id = es_client.generate_doc_id(dirpath.name, f.name, "doc")
                entries.append({
                    "type": "file",
                    "name": f.name,
                    "title": f.stem,
                    "uri": f"/docs/{doc_id}",
                })
        added = set(stem_map.keys())
        for f in files:
            if f.stem not in added:
                doc_id = es_client.generate_doc_id(dirpath.name, f.name, "doc")
                entries.append({
                    "type": "file",
                    "name": f.name,
                    "title": f.stem,
                    "uri": f"/docs/{doc_id}",
                })
    else:
        for f in files:
            doc_id = es_client.generate_doc_id(dirpath.name, f.name, "doc")
            entries.append({
                "type": "file",
                "name": f.name,
                "title": f.stem,
                "uri": f"/docs/{doc_id}",
            })

    return entries


async def build_category(dirpath: pathlib.Path, recursion: bool = True, parent: str = "") -> list[dict]:
    """构建分类列表（首页用，包含文件）"""
    dirpath = pathlib.Path(dirpath)
    categories = []
    for p in sorted(dirpath.iterdir()):
        if p.is_dir():
            yml = await _read_category_yaml(p)
            cat_id = es_client.generate_doc_id(p.name, "category.yaml", "dir")
            entry = {
                "type": "directory",
                "name": p.name,
                "title": yml.get("title", p.name),
                "description": yml.get("description", ""),
                "url": f"/docs/{cat_id}",
                "enable": yml.get("enable", True),
            }
            if recursion:
                sub_tree = await build_category(p)
                entry["items"] = sub_tree
            categories.append(entry)
        elif p.is_file() and p.name != "category.yaml":
            parent_cat = dirpath.name
            doc_id = es_client.generate_doc_id(parent_cat, p.name, "doc")
            categories.append({
                "type": "file",
                "name": p.name,
                "title": p.stem,
                "url": f"/docs/{doc_id}",
                "description": "",
                "category": parent_cat,
                "filename": p.name,
            })
    return categories


@docs_router.get("/category")
async def get_categories():
    """获取所有文档分类"""
    categories = await build_category(DOCS_PATH)
    return {"category": categories}


@docs_router.post("/cleanup")
async def cleanup_stale():
    """清理已删除文档的 ES 映射"""
    es = app.state.es
    if not es:
        return {"success": False, "message": "Elasticsearch 未连接"}

    cleaned = await es_client.cleanup_stale_mappings(es, str(DOCS_PATH))
    stats = await es_client.get_es_stats(es)
    return {"success": True, "cleaned": cleaned, "remaining": stats.get("total_mappings", 0)}


@docs_router.get("/search")
async def search_docs(q: str, semantic: bool = False):
    """搜索文档（支持 ES 全文搜索）"""
    if not q or len(q.strip()) < 1:
        return {"results": [], "total": 0}
    q_lower = q.strip().lower()

    es = None
    try:
        es = app.state.es
        if es:
            results = await es_client.search_mappings(es, q.strip(), limit=50)
            if results:
                return {"results": results, "total": len(results)}
    except Exception as e:
        app.state.logger.error if hasattr(app.state, 'logger') else None
        pass

    # 回退到本地文件搜索
    results = []
    for p in DOCS_PATH.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".html", ".txt") and p.name != "_category.yaml":
            try:
                async with aiofiles.open(p, "r", encoding="utf-8") as f:
                    content = await f.read()
            except Exception:
                continue
            if q_lower in content.lower():
                parent_cat = p.parent.name
                doc_id = es_client.generate_doc_id(parent_cat, p.name, "doc")
                results.append({
                    "title": p.stem,
                    "url": f"/docs/{doc_id}",
                    "category": parent_cat,
                    "doc_type": "doc",
                    "snippet": _snippet(content, q, 80),
                })
    return {"results": results[:50], "total": len(results)}


def _snippet(text: str, query: str, max_len: int = 80) -> str:
    """生成搜索结果摘要"""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[:max_len] + "..." if len(text) > max_len else text
    start = max(0, idx - 20)
    end = min(len(text), idx + len(query) + 60)
    snippet = text[start:end].strip()
    return snippet + "..." if end < len(text) else snippet


# ── page routes ──────────────────────────────────────────────────

import importlib

_docs_template = None


def _get_docs_template():
    global _docs_template
    if _docs_template is None:
        global env
        env = importlib.import_module("app.main").env
        _docs_template = env.get_template("docs.html")
    return _docs_template


def _build_breadcrumb(relative_path: str, is_file: bool = True) -> list[dict]:
    """根据相对路径构建面包屑导航"""
    if not relative_path:
        return []
    parts = relative_path.rstrip("/").split("/")
    breadcrumb = []
    for i, part in enumerate(parts[:-1]):
        cat_id = es_client.generate_doc_id(part, "category.yaml", "dir")
        breadcrumb.append({
            "name": part,
            "url": f"/docs/{cat_id}",
        })
    # 最后一部分不显示链接（文件名或目录名）
    if parts:
        breadcrumb.append({
            "name": parts[-1].replace(".md", ""),
            "url": None,
        })
    return breadcrumb


def _render_doc_page(cfg, template, content_html, toc_html, category_name,
                     documents, relative_path, breadcrumb, icp_show, host):
    """通用文档页渲染"""
    return HTMLResponse(
        template.render(
            dev=cfg.env == "dev",
            content_html=content_html,
            toc_html=toc_html,
            category_name=category_name,
            documents=documents,
            breadcrumb=breadcrumb or [],
            icp=icp_show,
            icp_number=cfg.icp_number or "",
            public_security=bool(cfg.public_security_number and host in cfg.icp_host),
            public_security_link=cfg.public_security_link or "",
            public_security_number=cfg.public_security_number or "",
        )
    )


def _render_cat_page(cfg, template, category_name, tree, breadcrumb, icp_show, host):
    """通用分类页渲染"""
    return HTMLResponse(
        template.render(
            dev=cfg.env == "dev",
            content_html="",
            toc_html="",
            category_name=category_name,
            documents=[],
            breadcrumb=breadcrumb or [],
            category_tree_data=tree,
            icp=icp_show,
            icp_number=cfg.icp_number or "",
            public_security=bool(cfg.public_security_number and host in cfg.icp_host),
            public_security_link=cfg.public_security_link or "",
            public_security_number=cfg.public_security_number or "",
        )
    )


@app.get("/docs/{doc_id:path}", response_class=HTMLResponse)
async def doc_page_by_id(request: Request, doc_id: str):
    """通过哈希 ID 访问文档或分类页"""
    es = app.state.es
    cfg = request.app.state.config
    template = _get_docs_template()
    host = request.headers.get("host", "").lower()
    show_icp = bool(cfg.icp_number and host in cfg.icp_host)

    # 尝试 ES 查找
    mapping = None
    if es:
        mapping = await es_client.get_mapping_by_id(es, doc_id)
        if not mapping:
            mapping = await es_client.get_mapping_by_path(es, doc_id)

    if mapping:
        if mapping.get("doc_type") == "doc":
            return await _serve_document(request, mapping)
        elif mapping.get("doc_type") == "dir":
            return await _serve_category_by_mapping(request, mapping)

    # ES 没有数据 → 扫描本地文件系统查找
    result = await _find_file_by_hash(doc_id)
    if result:
        file_path, category = result
        rel_path = str(file_path.relative_to(DOCS_PATH))
        file_name = file_path.name
        mapping = {
            "relative_path": rel_path,
            "category": category,
            "title": file_path.stem,
            "doc_id": doc_id,
        }
        return await _serve_document(request, mapping, cfg, template, host, show_icp)

    # 也尝试分类目录
    for cat_dir in sorted(DOCS_PATH.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat_id = es_client.generate_doc_id(cat_dir.name, "category.yaml", "dir")
        if cat_id == doc_id:
            cat_yaml = await _read_category_yaml(cat_dir)
            cat_name = cat_yaml.get("title", cat_dir.name)
            return _render_cat_page(
                cfg, template, cat_name, [],
                [{"name": cat_name, "url": None}],
                show_icp, host
            )

    raise HTTPException(status_code=404, detail="Not found")


async def _find_file_by_hash(doc_id: str) -> tuple[pathlib.Path, str] | None:
    """扫描本地 docs 目录，通过 hash 匹配查找文件"""
    for cat_dir in sorted(DOCS_PATH.iterdir()):
        if not cat_dir.is_dir():
            continue
        # 检查顶层文件
        for f in cat_dir.iterdir():
            if f.is_file() and f.name not in ("category.yaml",):
                file_id = es_client.generate_doc_id(cat_dir.name, f.name, "doc")
                if file_id == doc_id:
                    return f, cat_dir.name
        # 递归检查子目录
        for subdir in cat_dir.iterdir():
            if not subdir.is_dir():
                continue
            for f in subdir.iterdir():
                if f.is_file() and f.name not in ("category.yaml",):
                    # 使用顶层分类目录名作为 category（与 scan_and_sync_all 一致）
                    file_id = es_client.generate_doc_id(cat_dir.name, f.name, "doc")
                    if file_id == doc_id:
                        return f, cat_dir.name
    return None


async def _serve_document(request: Request, mapping: dict,
                          cfg=None, template=None, host=None, show_icp=None):
    """根据 ES 映射渲染文档页"""
    if cfg is None:
        cfg = request.app.state.config
    if template is None:
        template = _get_docs_template()
    if host is None:
        host = request.headers.get("host", "").lower()
    if show_icp is None:
        show_icp = bool(cfg.icp_number and host in cfg.icp_host)

    rel_path = mapping["relative_path"]
    file_path = DOCS_PATH / rel_path

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文档不存在")

    category = mapping.get("category", file_path.parent.name)

    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            raw = await f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="读取文档失败")

    content_html = markdown_to_html(raw)
    toc_html = extract_toc(content_html)

    # 获取同级文档列表
    cat_yaml = await _read_category_yaml(file_path.parent)
    documents = await _list_documents(file_path.parent, cat_yaml, category)
    for d in documents:
        if d["filename"] == file_path.name:
            d["active"] = True

    cat_yml = await _read_category_yaml(file_path.parent)
    category_name = cat_yml.get("title", file_path.parent.name)
    breadcrumb = _build_breadcrumb(rel_path)

    return _render_doc_page(
        cfg, template, content_html, toc_html, category_name,
        documents, rel_path, breadcrumb, show_icp, host
    )


async def _serve_category_by_mapping(request: Request, mapping: dict):
    """根据 ES 映射渲染分类页"""
    cfg = request.app.state.config
    category = mapping["relative_path"]
    cat_path = DOCS_PATH / category

    if not cat_path.exists():
        raise HTTPException(status_code=404, detail="分类不存在")

    host = request.headers.get("host", "").lower()
    show_icp = bool(cfg.icp_number and host in cfg.icp_host)
    template = _get_docs_template()

    cat_yaml = await _read_category_yaml(cat_path)
    tree = await build_category_tree(cat_path)
    breadcrumb = _build_breadcrumb(category)

    return _render_cat_page(
        cfg, template,
        cat_yaml.get("title", category),
        tree, breadcrumb, show_icp, host
    )


# ── 保留旧的 catch-all 路由作为兼容层 ──────────────────────────

catch_all_router = APIRouter()

@catch_all_router.get("/{category:path}", response_class=HTMLResponse)
async def category_or_doc_page(request: Request, category: str):
    """兼容旧格式路由：分类浏览页或文档页（直接传路径名）"""
    if not category:
        raise HTTPException(status_code=404, detail="Not found")
    system_prefixes = ("api/", "static/", "vanilla/", "_", ".")
    if any(category.startswith(p) for p in system_prefixes):
        raise HTTPException(status_code=404, detail="Not found")

    # 兼容 /docs/{hash} 路由（当主路由未命中时作为 fallback）
    if category.startswith("docs/"):
        hash_id = category[5:]  # 去掉 "docs/" 前缀
        es = app.state.es
        cfg = request.app.state.config
        template = _get_docs_template()
        host = request.headers.get("host", "").lower()
        show_icp = bool(cfg.icp_number and host in cfg.icp_host)

        mapping = None
        if es:
            mapping = await es_client.get_mapping_by_id(es, hash_id)
            if not mapping:
                mapping = await es_client.get_mapping_by_path(es, hash_id)

        if mapping:
            if mapping.get("doc_type") == "doc":
                return await _serve_document(request, mapping, cfg, template, host, show_icp)
            elif mapping.get("doc_type") == "dir":
                return await _serve_category_by_mapping(request, mapping)

        # 文件系统回退
        result = await _find_file_by_hash(hash_id)
        if result:
            file_path, category_name = result
            rel_path = str(file_path.relative_to(DOCS_PATH))
            mapping = {
                "relative_path": rel_path,
                "category": category_name,
                "title": file_path.stem,
                "doc_id": hash_id,
            }
            return await _serve_document(request, mapping, cfg, template, host, show_icp)

        for cat_dir in sorted(DOCS_PATH.iterdir()):
            if not cat_dir.is_dir():
                continue
            cat_id = es_client.generate_doc_id(cat_dir.name, "category.yaml", "dir")
            if cat_id == hash_id:
                cat_yaml = await _read_category_yaml(cat_dir)
                tree = await build_category_tree(cat_dir)
                return _render_cat_page(
                    cfg, template, cat_yaml.get("title", cat_dir.name),
                    tree, [{"name": cat_yaml.get("title", cat_dir.name), "url": None}],
                    show_icp, host
                )

        raise HTTPException(status_code=404, detail="Not found")

    cfg = request.app.state.config
    cat_path = DOCS_PATH / category

    host = request.headers.get("host", "").lower()
    show_icp = bool(cfg.icp_number and host in cfg.icp_host)

    template = _get_docs_template()

    # 尝试作为文档页处理
    parts = category.rsplit("/", 1)
    if len(parts) == 2:
        dir_name, filename = parts
        file_path = DOCS_PATH / dir_name / filename
        if file_path.exists() and file_path.is_file():
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    raw = await f.read()
            except Exception:
                raise HTTPException(status_code=500, detail="读取文档失败")

            content_html = markdown_to_html(raw)
            toc_html = extract_toc(content_html)

            cat_yaml = await _read_category_yaml(file_path.parent)
            documents = await _list_documents(file_path.parent, cat_yaml, dir_name)
            for d in documents:
                if d["name"] == filename:
                    d["active"] = True

            cat_yml = await _read_category_yaml(file_path.parent)
            category_name = cat_yml.get("title", file_path.parent.name)
            breadcrumb = _build_breadcrumb(f"{dir_name}/{filename}")

            return _render_doc_page(
                cfg, template, content_html, toc_html, category_name,
                documents, f"{dir_name}/{filename}", breadcrumb, show_icp, host
            )

    # 分类页
    if not cat_path.exists():
        raise HTTPException(status_code=404, detail="分类不存在")

    cat_yaml = await _read_category_yaml(cat_path)
    tree = await build_category_tree(cat_path)
    breadcrumb = _build_breadcrumb(category, is_file=False)

    return _render_cat_page(
        cfg, template, cat_yaml.get("title", category),
        tree, breadcrumb, show_icp, host
    )
