import jinja2
import pathlib
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .utils import app
from .config import config
from .docs import build_category

with open("static/templates/index.html", "r", encoding="utf-8") as f:
    template_content = f.read()

HOMEPAGE = jinja2.Template(template_content)


def _icp_status(cfg, host: str) -> tuple[bool, str, str, str]:
    """判断备案号显示状态"""
    show_icp = cfg.icp_number and host in cfg.icp_host
    show_ps = cfg.public_security_number and host in cfg.icp_host
    return show_icp, cfg.icp_number or "", show_ps, cfg.public_security_link or ""


@app.get("/")
async def root(request: Request):
    cfg = request.app.state.config
    categories = await build_category(pathlib.Path(cfg.data_dir), recursion=False)
    host = request.headers.get("host", "").lower()
    show_icp, icp_num, show_ps, ps_link = _icp_status(cfg, host)

    return HTMLResponse(HOMEPAGE.render(
        dev=cfg.env == "dev",
        categories=categories,
        icp=show_icp,
        icp_number=icp_num,
        public_security=show_ps,
        public_security_link=ps_link,
        public_security_number=cfg.public_security_number or "",
    ))
