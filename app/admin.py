"""后台管理模块：/vanilla?totp=xxx"""

import pathlib
import time
import hashlib
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Depends
import yaml
import aiofiles
import re
import markdown
import html as html_module
from bs4 import BeautifulSoup
import jinja2

from .utils import app
from .config import config
from .docs import DOCS_PATH, _read_category_yaml, build_category_tree
from . import es_client

__all__ = ["admin_router"]

admin_router = APIRouter()

# TOTP 失败记录: {hash(totp): fail_time}
_totp_failures: dict[str, float] = {}

# 卖萌字符串
CUTE_RESPONSE = """(=^･ω･^=)
(,,• ₃ •,)
(>^ω^<)喵~
(=^･^=)
"""


def _check_totp_access(totp: str, cfg) -> bool:
    """检查 totp 访问权限（含半分钟冷却）"""
    if cfg.env == "dev" and cfg.admin_bypass_auth:
        return True

    secret = cfg.admin_totp_secret
    import pyotp
    totp_obj = pyotp.TOTP(secret)

    # 检查当前时间戳是否匹配
    now = int(time.time())
    if totp_obj.verify(totp):
        return True

    # 检查是否触发冷却
    key = hashlib.sha256(totp.encode()).hexdigest()
    if key in _totp_failures:
        last_fail = _totp_failures[key]
        if now - last_fail < 30:
            return False
        else:
            del _totp_failures[key]

    return False


def _record_totp_failure(totp: str):
    """记录 totp 失败"""
    key = hashlib.sha256(totp.encode()).hexdigest()
    _totp_failures[key] = time.time()


@admin_router.get("/vanilla")
async def admin_page(
    request: Request,
    totp: str = Query("", description="TOTP 验证码"),
):
    """管理员页面入口"""
    cfg = request.app.state.config

    if not cfg.admin_enable:
        return HTMLResponse(f"<pre>{CUTE_RESPONSE}</pre>", status_code=403)

    # 检查 totp
    if not _check_totp_access(totp, cfg):
        return HTMLResponse(f"<pre>{CUTE_RESPONSE}</pre>", status_code=403)

    # 获取所有分类
    categories = await build_category_tree(DOCS_PATH)
    categories_html = _render_admin_categories(categories)

    try:
        template = jinja2.Template(_ADMIN_TEMPLATE_RAW)
    except Exception:
        template = jinja2.Template(_ADMIN_TEMPLATE_RAW)
    return HTMLResponse(template.render(
        dev=cfg.env == "dev",
        categories_html=categories_html,
        icp=cfg.icp_number and request.headers.get("host", "").lower() in cfg.icp_host,
        icp_number=cfg.icp_number,
    ))


def _render_admin_categories(items: list[dict], depth: int = 0) -> str:
    """渲染分类列表到管理页面"""
    html = '<ul class="list-group">'
    for item in items:
        indent = "&nbsp;" * (depth * 4)
        if item["type"] == "directory":
            html += f'''<li class="list-group-item">
                <span>{indent}<strong>&#128193; {item['title']}</strong></span>
                <span class="badge bg-primary">目录</span>
            </li>'''
            if item.get("items"):
                html += _render_admin_categories(item["items"], depth + 1)
        else:
            html += f'''<li class="list-group-item">
                <span>{indent}&#128196; {item['title']}</span>
                <span class="badge bg-secondary">文档</span>
            </li>'''
    html += '</ul>'
    return html


# 管理页面模板
_ADMIN_TEMPLATE_RAW = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理 - Vanilla Wiki</title>
  {% if dev %}
  <link rel="stylesheet" href="static/css/bootstrap.min.css">
  <script src="static/js/bootstrap.bundle.min.js"></script>
  {% else %}
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  {% endif %}
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
      background-color: #f5f5f5;
      color: #333;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    main { flex: 1 0 auto; }
    footer { flex-shrink: 0; }

    /* Navbar */
    .navbar {
      background-color: #fff !important;
      border-bottom: 1px solid #e8e8e8;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
      height: 56px;
    }
    .navbar-brand {
      font-weight: 600;
      font-size: 1.15rem;
      color: #333 !important;
    }
    .navbar-text { color: #888; font-size: 0.9rem; }

    /* Cards */
    .card {
      border: 1px solid #e8e8e8;
      border-radius: 10px;
      background: #fff;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    }
    .card-header {
      background-color: #fafafa;
      border-bottom: 1px solid #e8e8e8;
      border-radius: 10px 10px 0 0 !important;
      font-size: 0.9rem;
      color: #555;
    }

    /* Sidebar */
    .admin-sidebar {
      position: sticky;
      top: 70px;
      max-height: calc(100vh - 90px);
      overflow-y: auto;
    }
    .admin-sidebar .card-body {
      padding: 0;
      max-height: calc(100vh - 180px);
      overflow-y: auto;
    }
    .admin-sidebar .card-body::-webkit-scrollbar { width: 4px; }
    .admin-sidebar .card-body::-webkit-scrollbar-thumb { background-color: #ddd; border-radius: 2px; }
    .admin-sidebar .list-group-item {
      border-color: #f0f0f0;
      padding: 0.6rem 1rem;
      font-size: 0.85rem;
    }
    .admin-sidebar .list-group-item:hover { background-color: #fafafa; }

    /* Content area */
    .admin-content .card { margin-bottom: 1rem; }
    .admin-content .card-body { padding: 1.25rem; }

    /* Markdown preview */
    .markdown-preview {
      background-color: #fafafa;
      border: 1px solid #e8e8e8;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      min-height: 100px;
      color: #555;
      font-size: 0.875rem;
      line-height: 1.7;
    }
    .markdown-preview h2 { font-size: 1.15rem; color: #333; border-bottom: 1px solid #eee; padding-bottom: 0.3rem; margin-bottom: 0.5rem; }
    .markdown-preview h3 { font-size: 1rem; color: #333; margin-bottom: 0.4rem; }
    .markdown-preview h4 { font-size: 0.95rem; color: #444; margin-bottom: 0.3rem; }
    .markdown-preview strong { color: #333; }
    .markdown-preview em { color: #666; }
    .markdown-preview code {
      background: #f0f0f0;
      padding: 0.1rem 0.35rem;
      border-radius: 3px;
      font-size: 0.85em;
    }
    .markdown-preview pre {
      background: #f0f0f0;
      border-radius: 6px;
      padding: 0.75rem;
      overflow-x: auto;
    }
    .markdown-preview br { display: block; content: ""; margin-top: 0.3rem; }

    /* Buttons */
    .btn { border-radius: 6px; font-size: 0.875rem; padding: 0.375rem 0.875rem; transition: all 0.15s; }
    .btn-success { background-color: #6c757d; border-color: #6c757d; color: #fff; }
    .btn-success:hover { background-color: #5a6268; border-color: #545b62; }
    .btn-outline-secondary { border-color: #d0d0d0; color: #666; }
    .btn-outline-secondary:hover { background-color: #eee; color: #333; }

    /* Form controls */
    .form-control, .form-select {
      border: 1px solid #e0e0e0;
      border-radius: 6px;
      font-size: 0.875rem;
    }
    .form-control:focus, .form-select:focus {
      border-color: #ccc;
      box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.04);
    }
    .form-label { font-size: 0.85rem; color: #555; font-weight: 500; }

    /* Modal */
    .modal-content {
      border: 1px solid #e8e8e8;
      border-radius: 12px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
    }
    .modal-header { border-bottom: 1px solid #f0f0f0; padding: 1rem 1.25rem; }
    .modal-title { font-size: 1rem; font-weight: 600; color: #333; }
    .modal-body { padding: 1.25rem; }
    .modal-footer { border-top: 1px solid #f0f0f0; padding: 0.75rem 1.25rem; }

    /* Footer */
    footer {
      background-color: #fff;
      border-top: 1px solid #e8e8e8;
      padding: 0.75rem 0;
    }
    footer p { font-size: 0.75rem; color: #bbb; margin-bottom: 0; }
    footer a { color: #bbb; text-decoration: none; }
    footer a:hover { color: #888; }

    /* Misc */
    a { color: #555; }
    a:hover { color: #222; }
    .text-muted-custom { color: #aaa !important; }
    ul.admin-list { padding-left: 1.25rem; }
    ul.admin-list li { margin-bottom: 0.3rem; color: #666; font-size: 0.875rem; }

    /* Mobile */
    @media (max-width: 767.98px) {
      .navbar-brand { font-size: 1.05rem; }
      .admin-sidebar {
        display: none;
        position: fixed;
        top: 56px;
        left: 0;
        right: 0;
        z-index: 1050;
        max-height: calc(100vh - 56px);
        background: #fff;
        border-bottom: 1px solid #e8e8e8;
      }
      .admin-sidebar.show { display: block; }
      .admin-sidebar .card { border: none; box-shadow: none; border-radius: 0; }
      .admin-toggle-btn {
        display: inline-flex !important;
        align-items: center;
        gap: 0.25rem;
        border: 1px solid #d0d0d0;
        background: #fff;
        color: #666;
        border-radius: 6px;
        padding: 0.3rem 0.65rem;
        font-size: 0.8rem;
        transition: all 0.15s;
      }
      .admin-toggle-btn:hover { background-color: #f5f5f5; color: #333; }
    }
    @media (min-width: 768px) {
      .admin-toggle-btn { display: none !important; }
    }
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-sm sticky-top">
    <div class="container-fluid">
      <a class="navbar-brand" href="/">Vanilla Wiki</a>
      <span class="navbar-text ms-3">后台管理</span>
      <button class="admin-toggle-btn ms-auto" type="button" onclick="toggleSidebar()">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path fill-rule="evenodd" d="M2.5 12a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5z"/></svg>
        分类
      </button>
    </div>
  </nav>

  <main>
    <div style="max-width:1100px; margin:0 auto; padding:1.5rem 1rem;">
      <div class="row g-3">
        <!-- Left: Category sidebar -->
        <div class="col-md-4 col-lg-3 d-none d-md-block">
          <div class="admin-sidebar">
            <div class="card">
              <div class="card-header">&#128193; 分类目录</div>
              <div class="card-body p-0">
                <div id="category-list">
                  {{ categories_html | safe }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: Operations -->
        <div class="col-12 col-md-8 col-lg-7 admin-content">
          <div class="card">
            <div class="card-header d-flex justify-content-between align-items-center">
              <span>文档管理</span>
              <button class="btn btn-sm btn-success" data-bs-toggle="modal" data-bs-target="#addModal">
                &#10010; 新建文档
              </button>
            </div>
            <div class="card-body">
              <h6 style="font-size:0.9rem; font-weight:600; color:#444; margin-bottom:0.75rem;">操作说明</h6>
              <ul class="admin-list">
                <li>新建文档需要填写分类、文件名和内容</li>
                <li>文档使用 Markdown 格式编写</li>
                <li>支持实时预览编辑内容</li>
              </ul>
            </div>
          </div>

          <div class="card">
            <div class="card-header">Markdown 预览</div>
            <div class="card-body p-0">
              <div class="markdown-preview" id="add-preview">
                <p class="text-muted-custom mb-0" style="font-size:0.85rem;">在弹窗中编写文档内容，此处实时显示预览</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>

  <footer class="text-center">
    <div class="container">
      <p>&copy; 2025-2026 Vanilla Wiki. All rights reserved.</p>
      {% if icp %}<p><a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">{{ icp_number }}</a></p>{% endif %}
    </div>
  </footer>

  <!-- 新建文档弹窗 -->
  <div class="modal fade" id="addModal" tabindex="-1">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">新建文档</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <form id="add-form">
            <div class="mb-3">
              <label class="form-label">分类</label>
              <select class="form-select" id="add-category" required>
                <option value="">选择分类...</option>
              </select>
            </div>
            <div class="mb-3">
              <label class="form-label">文件名（不含扩展名）</label>
              <input type="text" class="form-control" id="add-filename" placeholder="example-doc" required>
            </div>
            <div class="mb-3">
              <label class="form-label">文档内容（Markdown）</label>
              <textarea class="form-control" id="add-content" rows="15" placeholder="# 文档标题&#10;&#10;内容..."></textarea>
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
          <button type="button" class="btn btn-success" onclick="addDocument()">创建</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    // ── Mobile sidebar toggle ──────────────────────
    function toggleSidebar() {
      var sidebar = document.querySelector('.admin-sidebar');
      if (sidebar) sidebar.classList.toggle('show');
    }

    document.addEventListener('click', function(e) {
      var sidebar = document.querySelector('.admin-sidebar');
      var toggleBtn = document.querySelector('.admin-toggle-btn');
      if (!sidebar || !toggleBtn) return;
      if (window.innerWidth >= 768) return;
      if (sidebar.classList.contains('show') && !sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
        sidebar.classList.remove('show');
      }
    });

    // ── Load categories into dropdown ──────────────
    fetch('/api/docs/category')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var select = document.getElementById('add-category');
        function walk(items, prefix) {
          prefix = prefix || '';
          for (var i = 0; i < items.length; i++) {
            var item = items[i];
            if (item.type === 'directory' && item.enable) {
              var opt = document.createElement('option');
              opt.value = item.name;
              opt.textContent = prefix + item.title;
              select.appendChild(opt);
              if (item.items) walk(item.items, prefix + item.name + '/');
            }
          }
        }
        walk(data.category);
      });

    // ── Markdown preview ───────────────────────────
    var contentEl = document.getElementById('add-content');
    var previewEl = document.getElementById('add-preview');
    if (contentEl && previewEl) {
      contentEl.addEventListener('input', function() {
        previewEl.innerHTML = markdownToHtml(contentEl.value);
      });
    }

    function markdownToHtml(text) {
      if (!text.trim()) return '<p class="text-muted-custom mb-0" style="font-size:0.85rem;">在左侧编写文档内容，此处实时显示预览</p>';
      return text
        .replace(/^### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^## (.+)$/gm, '<h3>$1</h3>')
        .replace(/^# (.+)$/gm, '<h2>$1</h2>')
        .replace(/```([\w\s]*?)```/g, '<pre><code>$1</code></pre>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
    }

    // ── Create document ────────────────────────────
    async function addDocument() {
      var category = document.getElementById('add-category').value;
      var filename = document.getElementById('add-filename').value;
      var content = document.getElementById('add-content').value;

      if (!category || !filename || !content) {
        alert('请填写所有必填项');
        return;
      }

      try {
        var res = await fetch('/api/admin/document', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: category, filename: filename, content: content })
        });
        var data = await res.json();
        if (res.ok) {
          alert('创建成功！');
          bootstrap.Modal.getInstance(document.getElementById('addModal')).hide();
          document.getElementById('add-content').value = '';
          document.getElementById('add-filename').value = '';
          document.getElementById('add-preview').innerHTML = '<p class="text-muted-custom mb-0" style="font-size:0.85rem;">在弹窗中编写文档内容，此处实时显示预览</p>';
        } else {
          alert('创建失败: ' + data.error || data.detail);
        }
      } catch (e) {
        alert('创建失败: ' + e.message);
      }
    }
  </script>
</body>
</html>
"""


@admin_router.post("/api/admin/document")
async def admin_create_document(request: Request):
    """创建/编辑文档（仅管理员）"""
    cfg = request.app.state.config

    if not cfg.admin_enable:
        return JSONResponse(status_code=403, content={"error": "管理功能已禁用"})

    body = await request.json()
    category = body.get("category", "").strip()
    filename = body.get("filename", "").strip()
    content = body.get("content", "")

    if not category or not filename or not content:
        return JSONResponse(status_code=400, content={"error": "缺少必填字段"})

    # 安全检查：文件名不能包含路径遍历
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse(status_code=400, content={"error": "非法文件名"})

    doc_path = DOCS_PATH / category / f"{filename}.md"
    if not (DOCS_PATH / category).exists():
        return JSONResponse(status_code=400, content={"error": f"分类 '{category}' 不存在"})

    async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
        await f.write(content)

    # 同步到 ES 映射
    es = app.state.es
    if es:
        doc_id = es_client.generate_doc_id(category, f"{filename}.md", "doc")
        await es_client.put_mapping(es, doc_id, f"{category}/{filename}.md",
                                     filename, category, "doc")

    return JSONResponse(content={"ok": True, "path": str(doc_path)})


@admin_router.post("/api/admin/delete-document")
async def admin_delete_document(request: Request):
    """删除文档（仅管理员）"""
    cfg = request.app.state.config

    if not cfg.admin_enable:
        return JSONResponse(status_code=403, content={"error": "管理功能已禁用"})

    body = await request.json()
    category = body.get("category", "").strip()
    filename = body.get("filename", "").strip()

    if not category or not filename:
        return JSONResponse(status_code=400, content={"error": "缺少必填字段"})

    if ".." in filename or "/" in filename:
        return JSONResponse(status_code=400, content={"error": "非法文件名"})

    doc_path = DOCS_PATH / category / f"{filename}.md"
    if not doc_path.exists():
        return JSONResponse(status_code=404, content={"error": "文档不存在"})

    doc_path.unlink()

    # 从 ES 映射中删除
    es = app.state.es
    if es:
        doc_id = es_client.generate_doc_id(category, f"{filename}.md", "doc")
        await es_client.delete_mapping(es, doc_id)

    return JSONResponse(content={"ok": True})


admin_router.post("/api/admin/update-category")
async def admin_update_category(request: Request):
    """更新 _category.yaml 配置"""
    cfg = request.app.state.config

    if not cfg.admin_enable:
        return JSONResponse(status_code=403, content={"error": "管理功能已禁用"})

    body = await request.json()
    category = body.get("category", "").strip()

    if not category:
        return JSONResponse(status_code=400, content={"error": "缺少分类名称"})

    cat_path = DOCS_PATH / category
    yml_path = cat_path / "category.yaml"

    if not cat_path.exists():
        return JSONResponse(status_code=404, content={"error": "分类不存在"})

    yml_data = {}
    if yml_path.exists():
        async with aiofiles.open(yml_path, "r", encoding="utf-8") as f:
            content = await f.read()
            yml_data = yaml.safe_load(content) or {}

    yml_data.update(body.get("config", {}))

    async with aiofiles.open(yml_path, "w", encoding="utf-8") as f:
        await f.write(yaml.safe_dump(yml_data, allow_unicode=True, default_flow_style=False))

    return JSONResponse(content={"ok": True})
