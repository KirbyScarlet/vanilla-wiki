from .main import root
from .docs import *
from .admin import *
from .utils import app
from . import docs
from . import admin

# 注册 API 路由（在 catch-all 之前注册）
app.include_router(docs.docs_router, prefix="/api/docs")
app.include_router(admin.admin_router)
# 注册 catch-all 兼容路由（最后注册，优先级最低）
app.include_router(docs.catch_all_router)
