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

from .utils import app
from .config import config
from .docs import DOCS_PATH, _read_category_yaml, build_category_tree

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
    totp: str = Query(..., description="TOTP 验证码"),
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

    return HTMLResponse(_ADMIN_TEMPLATE.render(
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
_ADMIN_TEMPLATE_STR = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>管理 - Vanilla Wiki</title>
  <link rel="stylesheet" href="static/css/bootstrap.min.css">
  <script src="static/js/bootstrap.bundle.min.js"></script>
  <style>
    html, body { height: 100%; }
    body { display: flex; flex-direction: column; min-height: 100vh; }
    main { flex: 1; }
    .sidebar { max-height: calc(100vh - 160px); overflow-y: auto; }
    .markdown-preview { background: #f8f9fa; padding: 1rem; border-radius: 8px; }
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-sm navbar-light bg-light sticky-top">
    <div class="container-fluid">
      <a class="navbar-brand text-body" href="/">Vanilla Wiki</a>
      <span class="navbar-text ms-3">后台管理</span>
    </div>
  </nav>

  <main>
    <div class="container-fluid py-3">
      <div class="row">
        <!-- 左侧：分类管理 -->
        <div class="col-md-4 col-lg-3 d-none d-md-block sidebar">
          <div class="card">
            <div class="card-header bg-light fw-bold">&#128193; 分类目录</div>
            <div class="card-body p-0">
              <div id="category-list">
                {{ categories_html | safe }}
              </div>
            </div>
          </div>
        </div>

        <!-- 右侧：操作面板 -->
        <div class="col-12 col-md-8 col-lg-7">
          <div class="card mb-3">
            <div class="card-header bg-light fw-bold">
              <button class="btn btn-sm btn-success" data-bs-toggle="modal" data-bs-target="#addModal">
                &#10010; 新建文档
              </button>
            </div>
            <div class="card-body">
              <h5>操作说明</h5>
              <ul>
                <li>点击左侧分类可查看详情</li>
                <li>新建文档需要填写分类、文件名和内容</li>
                <li>文档使用 Markdown 格式编写</li>
              </ul>
            </div>
          </div>

          <!-- 文档预览区 -->
          <div class="card">
            <div class="card-header bg-light fw-bold">文档预览</div>
            <div class="card-body">
              <p class="text-muted">请在左侧选择文档进行预览</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>

  <footer class="text-center bg-light mt-auto">
    <div class="container-fluid p-1">
      <p class="m-0 text-secondary" style="font-size:12px">&copy; 2025-2026 Vanilla Wiki. All rights reserved.</p>
      {% if icp %}<p class="m-0 text-secondary" style="font-size:12px">{{ icp_number }}</p>{% endif %}
    </div>
  </footer>

  <!-- 新建文档弹窗 -->
  <div class="modal fade" id="addModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
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
          <div class="markdown-preview" id="add-preview"></div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
          <button type="button" class="btn btn-primary" onclick="addDocument()">创建</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    // 加载分类到下拉框
    fetch('/api/docs/category')
      .then(r => r.json())
      .then(data => {
        const select = document.getElementById('add-category');
        function walk(items, prefix = '') {
          for (const item of items) {
            if (item.type === 'directory' && item.enable) {
              const opt = document.createElement('option');
              opt.value = item.name;
              opt.textContent = prefix + item.title;
              select.appendChild(opt);
              if (item.items) walk(item.items, prefix + item.name + '/');
            }
          }
        }
        walk(data.category);
      });

    // 预览 Markdown
    const contentEl = document.getElementById('add-content');
    const previewEl = document.getElementById('add-preview');
    if (contentEl && previewEl) {
      contentEl.addEventListener('input', () => {
        previewEl.innerHTML = markdownToHtml(contentEl.value);
      });
    }

    function markdownToHtml(text) {
      // Simple markdown parser for preview
      return text
        .replace(/^### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^## (.+)$/gm, '<h3>$1</h3>')
        .replace(/^# (.+)$/gm, '<h2>$1</h2>')
        .replace(/```([\w\\s]*?)```/g, '<pre><code>$1</code></pre>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
    }

    async function addDocument() {
      const category = document.getElementById('add-category').value;
      const filename = document.getElementById('add-filename').value;
      const content = document.getElementById('add-content').value;

      if (!category || !filename || !content) {
        alert('请填写所有必填项');
        return;
      }

      try {
        const res = await fetch('/api/admin/document', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category, filename, content })
        });
        const data = await res.json();
        if (res.ok) {
          alert('创建成功！');
          bootstrap.Modal.getInstance(document.getElementById('addModal')).hide();
        } else {
          alert('创建失败: ' + data.detail);
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


# 挂载 admin 路由
app.include_router(admin_router)
