import jinja2
import aiofiles
from fastapi.responses import HTMLResponse

from .utils import app
from .config import env
from .docs import build_category

with open("static/templates/index.html", "r", encoding="utf-8") as f:
    template_content = f.read()

HOMEPAGE = jinja2.Template(template_content)

@app.get("/")
async def root():
    cagegories = await build_category(env.docs_dir, recursion=False)
    return HTMLResponse(HOMEPAGE.render(
        dev = env.environment=="dev",
        categories = cagegories,
        icp = True if env.icp_number else None,
        icp_number = env.icp_number,
        )
    )