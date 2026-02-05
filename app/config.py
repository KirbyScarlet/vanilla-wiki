from pydantic import BaseModel
from pathlib import Path
import dotenv

class Config(BaseModel):
    # 基础配置
    app_name: str = "vanilla wiki"
    app_version: str = "0.2.1"
    debug: bool = True
    environment: str = "dev"

    # 网络配置
    host: str = "0.0.0.0"
    port: int = 8085

    # 数据目录
    data_dir: str = "data"
    docs_dir: str = "docs"

    # 向量数据配置
    ollama_url: str = "http://localhost:11434"

    # 认证配置
    admin_enable: bool = True
    admin_totp_secret: str = "vanilla"
    admin_bypass_auth: bool = False  # 当处于debug模式时，可以随意输入验证码跳过验证

    # 备案号
    icp_number: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

        @property
        def data_dir_path(self) -> Path:
            return Path(self.data_dir).resolve()
        

env = Config.model_validate(dotenv.dotenv_values())