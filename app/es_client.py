"""Elasticsearch 异步客户端与文档映射管理"""

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import ConnectionError, NotFoundError, ConflictError
import hashlib
import logging

logger = logging.getLogger(__name__)

# 全局 ES 客户端实例
es_client: AsyncElasticsearch | None = None

# 映射索引名称
DOC_MAPPING_INDEX = "doc-mapping"


def generate_doc_id(category: str, filename: str, doc_type: str = "doc") -> str:
    """根据分类和文件名生成短 ID"""
    raw = f"{doc_type}:{category}/{filename}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def generate_path_hash(category: str, filename: str) -> str:
    """生成完整路径的 hash（用于精确查找和去重）"""
    raw = f"{category}/{filename}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def init_es_client(cfg) -> AsyncElasticsearch:
    """初始化 ES 客户端"""
    global es_client
    import ssl
    host = getattr(cfg, "es_host", "http://localhost:9200")
    api_key_encoded = getattr(cfg, "es_api_key_encoded", "")
    use_cert = getattr(cfg, "es_cert", False)

    kwargs = {"hosts": [host], "api_key": api_key_encoded}
    if not use_cert:
        # 禁用 SSL 证书验证（开发环境/自签证书）
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ssl_context

    es_client = AsyncElasticsearch(**kwargs)
    logger.info("Elasticsearch client initialized: %s", host)
    return es_client


async def close_es_client():
    """关闭 ES 客户端"""
    global es_client
    if es_client:
        await es_client.close()
        es_client = None
        logger.info("Elasticsearch client closed")


async def ensure_mapping_index(es: AsyncElasticsearch):
    """确保映射索引存在，不存在则创建"""
    index_name = DOC_MAPPING_INDEX
    if await es.indices.exists(index=index_name):
        return

    settings = {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    }

    mappings = {
        "properties": {
            "doc_id": {"type": "keyword"},
            "path_hash": {"type": "keyword"},
            "file_path": {"type": "keyword"},
            "relative_path": {"type": "keyword"},
            "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "category": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "updated_at": {"type": "date"},
        }
    }

    await es.indices.create(index=index_name, settings=settings, mappings=mappings)
    logger.info("Created mapping index: %s", index_name)


async def get_mapping_by_id(es: AsyncElasticsearch, doc_id: str) -> dict | None:
    """根据短 ID 查找映射"""
    try:
        result = await es.get(index=DOC_MAPPING_INDEX, id=doc_id)
        return result.get("_source", {})
    except NotFoundError:
        return None


async def get_mapping_by_path(es: AsyncElasticsearch, relative_path: str) -> dict | None:
    """根据相对路径查找映射"""
    query = {
        "query": {"term": {"relative_path": relative_path}},
        "size": 1,
    }
    result = await es.search(index=DOC_MAPPING_INDEX, body=query)
    hits = result.get("hits", {}).get("hits", [])
    if hits:
        return hits[0].get("_source", {})
    return None


async def put_mapping(es: AsyncElasticsearch, doc_id: str, relative_path: str,
                      title: str, category: str, doc_type: str = "doc") -> bool:
    """存储或更新映射"""
    path_hash = generate_path_hash(relative_path.split("/", 1)[0] if "/" in relative_path else "",
                                    relative_path.split("/", 1)[1] if "/" in relative_path else relative_path)
    # 修正 path_hash 计算
    parts = relative_path.split("/", 1)
    if len(parts) == 2:
        path_hash = generate_path_hash(parts[0], parts[1])
    else:
        path_hash = generate_path_hash("", parts[0])

    doc = {
        "doc_id": doc_id,
        "path_hash": path_hash,
        "file_path": relative_path,
        "relative_path": relative_path,
        "title": title,
        "category": category,
        "doc_type": doc_type,
        "updated_at": _now_iso(),
    }

    try:
        await es.create(index=DOC_MAPPING_INDEX, id=doc_id, document=doc)
        return True
    except ConflictError:
        # 文档已存在，更新
        await es.update(index=DOC_MAPPING_INDEX, id=doc_id, doc=doc)
        return True
    except ConnectionError as e:
        logger.error("Failed to put mapping: %s", e)
        return False


async def delete_mapping(es: AsyncElasticsearch, doc_id: str) -> bool:
    """删除映射"""
    try:
        await es.delete(index=DOC_MAPPING_INDEX, id=doc_id)
        return True
    except NotFoundError:
        return False
    except ConnectionError as e:
        logger.error("Failed to delete mapping: %s", e)
        return False


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def scan_and_sync_all(es: AsyncElasticsearch, docs_path, config_obj) -> int:
    """扫描本地 docs 目录，与 ES 映射同步（全量同步 + 清理已删除）"""
    import pathlib
    count = 0
    try:
        docs = pathlib.Path(docs_path)
        if not docs.exists():
            logger.warning("Docs path not found: %s", docs_path)
            return 0

        count = await _sync_dir(es, docs, docs, "", count)
        logger.info("Synced %d mappings to Elasticsearch", count)

        cleaned = await cleanup_stale_mappings(es, docs_path)
        if cleaned:
            logger.info("Removed %d stale mappings", cleaned)
    except Exception as e:
        logger.error("Failed to sync mappings: %s", e)
    return count


async def _sync_dir(es, root_dir, current_dir, prefix, count) -> int:
    """递归同步目录及其子目录"""
    import pathlib

    for item in sorted(current_dir.iterdir()):
        if item.is_dir():
            # 同步子目录
            dir_name = item.name
            rel_path = f"{prefix}/{dir_name}" if prefix else dir_name
            dir_doc_id = generate_doc_id(dir_name, "category.yaml", "dir")
            title = await _get_category_title(item)
            await put_mapping(es, dir_doc_id, rel_path, title, dir_name, "dir")
            count += 1

            # 递归同步子目录的文件和子目录
            count = await _sync_dir(es, root_dir, item, rel_path, count)

        elif item.is_file() and item.name not in ("category.yaml",):
            # 同步文档文件
            filename = item.name
            rel_path = f"{prefix}/{filename}" if prefix else f"{current_dir.name}/{filename}"
            # 使用最内层目录名作为 category
            category = current_dir.name if not prefix else prefix.split("/")[-1]
            doc_id = generate_doc_id(category, filename, "doc")
            title = item.stem

            await put_mapping(es, doc_id, rel_path, title, category, "doc")
            count += 1

    return count


async def _get_category_title(dirpath) -> str:
    """读取 category.yaml 获取标题"""
    import yaml
    import aiofiles
    yml = dirpath / "category.yaml"
    if not yml.exists():
        return dirpath.name
    try:
        async with aiofiles.open(yml, "r", encoding="utf-8") as f:
            content = await f.read()
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data.get("title", dirpath.name)
    except Exception:
        pass
    return dirpath.name


async def cleanup_stale_mappings(es: AsyncElasticsearch, docs_path: str) -> int:
    """清理 ES 中对应本地文件已删除的映射"""
    import pathlib
    count = 0
    docs = pathlib.Path(docs_path)
    try:
        result = await es.search(
            index=DOC_MAPPING_INDEX,
            body={"query": {"match_all": {}}, "size": 10000},
        )
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return 0

        for hit in hits:
            src = hit.get("_source", {})
            rel_path = src.get("relative_path", "")
            doc_id = src.get("doc_id", "")
            doc_type = src.get("doc_type", "doc")

            if doc_type == "dir":
                file_path = docs / rel_path
            else:
                file_path = docs / rel_path

            if not file_path.exists():
                await delete_mapping(es, doc_id)
                logger.info("Deleted stale mapping: %s (path: %s)", doc_id, rel_path)
                count += 1

        logger.info("Cleaned up %d stale mappings", count)
    except Exception as e:
        logger.error("Failed to cleanup stale mappings: %s", e)
    return count


async def search_mappings(es: AsyncElasticsearch, query_text: str, limit: int = 50) -> list[dict]:
    """在映射索引中搜索（用于全文搜索的 ES 后端）"""
    body = {
        "query": {
            "multi_match": {
                "query": query_text,
                "fields": ["title^3", "relative_path^2"],
            }
        },
        "size": limit,
    }
    result = await es.search(index=DOC_MAPPING_INDEX, body=body)
    hits = result.get("hits", {}).get("hits", [])
    results = []
    for hit in hits:
        src = hit.get("_source", {})
        results.append({
            "title": src.get("title", ""),
            "url": f"/docs/{src.get('doc_id', '')}",
            "category": src.get("category", ""),
            "doc_type": src.get("doc_type", "doc"),
        })
    return results


async def get_es_stats(es: AsyncElasticsearch) -> dict:
    """获取 ES 映射统计信息"""
    try:
        result = await es.count(index=DOC_MAPPING_INDEX)
        total = result.get("count", 0)
        return {"total_mappings": total}
    except Exception as e:
        logger.error("Failed to get ES stats: %s", e)
        return {"total_mappings": 0}
