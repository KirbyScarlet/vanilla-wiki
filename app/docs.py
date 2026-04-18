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


async def _list_documents(dirpath: pathlib.Path, cat_yaml: dict) -> list[dict]:
    """列出目录下的文档文件，按 order 规则排序"""
    manual_order = cat_yaml.get("categories", [])
    order = cat_yaml.get("order", "none")

    files: list[pathlib.Path] = []
    for p in dirpath.iterdir():
        if p.is_file() and p.name not in ("_category.yaml",):
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
            docs.append({
                "name": f.name,
                "title": f.stem,
                "url": f"/{dirpath.name}/{f.name}",
                "active": False,
            })

    for f in files:
        if order != "manual" or f.name not in [m for m in manual_order]:
            docs.append({
                "name": f.name,
                "title": f.stem,
                "url": f"/{dirpath.name}/{f.name}",
                "active": False,
            })

    return docs


async def build_category_tree(dirpath: pathlib.Path, parent_uri: str = "") -> list[dict]:
    """递归构建分类树，返回 [{type, name, title, description, uri, items?}]"""
    cat_data = await _read_category_yaml(dirpath)

    entries: list[dict] = []

    for p in sorted(dirpath.iterdir()):
        if p.is_dir():
            sub_yml = await _read_category_yaml(p)
            uri = f"{parent_uri}/{p.name}" if parent_uri else f"/{p.name}"
            entries.append({
                "type": "directory",
                "name": p.name,
                "title": sub_yml.get("title", p.name),
                "description": sub_yml.get("description", ""),
                "uri": uri,
                "enable": sub_yml.get("enable", True),
            })
            sub_items = await build_category_tree(p, uri)
            if sub_items:
                entries[-1]["items"] = sub_items

    for p in sorted(dirpath.iterdir()):
        if p.is_file() and p.name != "_category.yaml":
            entries.append({
                "type": "file",
                "name": p.name,
                "title": p.stem,
                "uri": f"{parent_uri}/{p.name}" if parent_uri else f"/{p.name}",
            })

    return entries


async def build_category(dirpath: pathlib.Path, recursion: bool = True, parent: str = "") -> list[dict]:
    """构建分类列表（首页用，包含文件）"""
    dirpath = pathlib.Path(dirpath)
    categories = []
    for p in sorted(dirpath.iterdir()):
        if p.is_dir():
            yml = await _read_category_yaml(p)
            entry = {
                "type": "directory",
                "name": p.name,
                "title": yml.get("title", p.name),
                "description": yml.get("description", ""),
                "url": f"/{p.name}",
                "enable": yml.get("enable", True),
            }
            if recursion:
                child_parent = (parent + "/" + p.name) if parent else p.name
                sub_tree = await build_category(p, True, child_parent)
                entry["items"] = sub_tree
            categories.append(entry)
        elif p.is_file() and p.name != "category.yaml":
            categories.append({
                "type": "file",
                "name": p.name,
                "title": p.stem,
                "url": f"/{parent}/{p.name}" if parent else f"/{dirpath.name}/{p.name}",
                "description": "",
            })
    return categories


@docs_router.get("/category")
async def get_categories():
    """获取所有文档分类"""
    categories = await build_category(DOCS_PATH)
    return {"category": categories}


@docs_router.get("/search")
async def search_docs(q: str, semantic: bool = False):
    """搜索文档（基础全文搜索）"""
    if not q or len(q.strip()) < 1:
        return {"results": [], "total": 0}
    q_lower = q.strip().lower()
    results = []
    for p in DOCS_PATH.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".html", ".txt") and p.name != "_category.yaml":
            try:
                async with aiofiles.open(p, "r", encoding="utf-8") as f:
                    content = await f.read()
            except Exception:
                continue
            if q_lower in content.lower():
                results.append({
                    "title": p.stem,
                    "url": f"/{p.parent.name}/{p.name}",
                    "category": p.parent.name,
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

_docs_template_content = pathlib.Path("static/templates/docs.html").read_text(encoding="utf-8")


def _get_docs_template():
    import jinja2
    return jinja2.Template(_docs_template_content)


@app.get("/{category:path}", response_class=HTMLResponse)
async def category_or_doc_page(request: Request, category: str):
    """统一路由：分类浏览页或文档页"""
    # 空路径或系统路径不匹配，交由其他路由处理
    if not category:
        raise HTTPException(status_code=404, detail="Not found")
    # 排除系统路径，避免与 /api /static /vanilla 等冲突
    system_prefixes = ("api/", "static/", "vanilla/", "_", ".")
    if any(category.startswith(p) for p in system_prefixes):
        raise HTTPException(status_code=404, detail="Not found")

    cfg = request.app.state.config
    cat_path = DOCS_PATH / category

    host = request.headers.get("host", "").lower()
    show_icp = bool(cfg.icp_number and host in cfg.icp_host)

    template = _get_docs_template()

    # 尝试作为文档页处理：category 包含 /filename.md
    parts = category.rsplit("/", 1)
    if len(parts) == 2:
        dir_name, filename = parts
        file_path = DOCS_PATH / dir_name / filename
        if file_path.exists() and file_path.is_file():
            # 文档页
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    raw = await f.read()
            except Exception:
                raise HTTPException(status_code=500, detail="读取文档失败")

            content_html = markdown_to_html(raw)
            toc_html = extract_toc(content_html)

            cat_yaml = await _read_category_yaml(file_path.parent)
            documents = await _list_documents(file_path.parent, cat_yaml)
            for d in documents:
                if d["name"] == filename:
                    d["active"] = True

            cat_yml = await _read_category_yaml(file_path.parent)
            category_name = cat_yml.get("title", file_path.parent.name)

            return HTMLResponse(
                template.render(
                    dev=cfg.env == "dev",
                    content_html=content_html,
                    toc_html=toc_html,
                    category_name=category_name,
                    documents=documents,
                    icp=show_icp,
                    icp_number=cfg.icp_number,
                    public_security=bool(cfg.public_security_number and host in cfg.icp_host),
                    public_security_link=cfg.public_security_link,
                    public_security_number=cfg.public_security_number,
                )
            )

    # 分类页
    if not cat_path.exists():
        raise HTTPException(status_code=404, detail="分类不存在")

    cat_yaml = await _read_category_yaml(cat_path)
    tree = await build_category_tree(cat_path, f"/{category}")

    return HTMLResponse(
        template.render(
            dev=cfg.env == "dev",
            content_html="",
            toc_html="",
            category_name=cat_yaml.get("title", category),
            documents=[],
            category_tree_data=tree,
            icp=show_icp,
            icp_number=cfg.icp_number,
            public_security=bool(cfg.public_security_number and host in cfg.icp_host),
            public_security_link=cfg.public_security_link,
            public_security_number=cfg.public_security_number,
        )
    )
