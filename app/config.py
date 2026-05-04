from pydantic import BaseModel
from pathlib import Path
from fastapi import FastAPI,logger
import yaml

class Config(BaseModel):
    # 基础配置
    app_name: str = "vanilla wiki"
    app_version: str = "0.2.1"
    debug: bool = True
    env: str = "dev"

    # 网络配置
    host: str = "0.0.0.0"
    port: int = 8085

    # 数据目录
    data_dir: str = "data"
    # 数据选项
    max_file_size: int = 0  # 单位字节，0表示不限制
    buffer_size: int = 64 * 1024  # 流式传输处理大文件时的缓冲区大小
    hash_type: str = "md5"

    # 向量数据配置
    ollama_url: str = "http://localhost:11434"

    # Elasticsearch 配置
    es_host: str = "http://localhost:9200"
    es_api_key: str = ""
    es_api_key_encoded: str = ""
    es_cert: bool = False
    es_index_prefix: str = "vanilla-wiki"

    # 认证配置
    admin_enable: bool = True
    admin_totp_secret: str = "vanilla"
    admin_bypass_auth: bool = False  # 当处于debug模式时，可以随意输入验证码跳过验证

    # 备案号
    icp_host: list[str] = []  # 需要展示备案号的域名
    icp_number: str = ""
    public_security_link: str = ""
    public_security_number: str = ""

    class Config:
        extra = "ignore"

def load_config():
    with open("config.yaml") as f:
        raw_config = yaml.safe_load(f)
    config = Config.model_validate(raw_config)
    logger.logger.info("config load success")
    return config

config = load_config()
