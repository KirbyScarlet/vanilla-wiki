#!/usr/bin/python3

__doc__ = """\
vanilla wiki 后端实现
完整文档参考：https://vanilla.wiki/about#api

接口列表：
# 运维类
- /api/health
# 获取文档类
- /api/docs
# 后台管理类
- /vanilla


"""

import uvicorn

from app.config import env
from app.utils import app

if __name__ == "__main__":
    uvicorn.run(
        app=app,
        host=env.host,
        port=env.port
    )