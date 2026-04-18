import os
import uuid
import glob
import hashlib
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

import aiofiles
import aiofiles.os
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse

# 并发锁管理：为每个 bucket/uuid 维护一个 asyncio 锁
_lock_dict: Dict[str, asyncio.Lock] = {}
_lock_dict_lock = asyncio.Lock()  # 保护 _lock_dict 的锁

async def get_object_lock(bucket: str, object_uuid: str) -> asyncio.Lock:
    """获取指定对象的锁，用于防止并发写/删冲突"""
    key = f"{bucket}/{object_uuid}"
    async with _lock_dict_lock:
        if key not in _lock_dict:
            _lock_dict[key] = asyncio.Lock()
        return _lock_dict[key]

class Storage:
    """底层存储操作，所有方法均为异步且线程安全（依赖外部锁）"""

    @staticmethod
    def _get_storage_path(bucket: str, object_uuid: str) -> Path:
        """根据bucket和uuid生成存储路径"""
        uuid_str = str(object_uuid)
        xx = uuid_str[:2]
        yy = uuid_str[2:4]
        dir_path = Path("data") / bucket / xx / yy
        return dir_path / f"{uuid_str}"

    @staticmethod
    async def _find_file_path(bucket: str, object_uuid: str) -> Optional[Path]:
        """查找uuid对应的实际文件（不知道扩展名时使用）"""
        dir_path = Storage._get_storage_path(bucket, object_uuid)
        if not await aiofiles.os.path.exists(dir_path):
            return None
        # 查找 dir_path 下以 uuid. 开头的文件
        pattern = f"{object_uuid}.*"
        # glob 在异步环境中可能阻塞，使用 asyncio.to_thread
        files = await asyncio.to_thread(glob.glob, str(dir_path / pattern))
        if not files:
            return None
        return Path(files[0])

    @staticmethod
    async def upload(
        bucket: str,
        file: UploadFile
    ) -> dict:
        """上传文件，生成随机uuid，返回uuid和扩展名"""
        object_uuid = uuid.uuid4().hex

        lock = await get_object_lock(bucket, object_uuid)
        async with lock:
            target_path = Storage._get_storage_path(bucket, object_uuid)
            await aiofiles.os.makedirs(target_path.parent, exist_ok=True)
            async with aiofiles.open(target_path, "wb") as f:
                content = await file.read()
                await f.write(content)

        return {"uuid": object_uuid, "bucket": bucket}

    @staticmethod
    async def download(bucket: str, object_uuid: str):
        """下载文件，返回文件路径（用于FileResponse）"""
        file_path = await Storage._find_file_path(bucket, object_uuid)
        if file_path is None:
            raise HTTPException(status_code=404, detail="Object not found")
        return file_path

    @staticmethod
    async def delete(bucket: str, object_uuid: str):
        """删除文件"""
        lock = await get_object_lock(bucket, object_uuid)
        async with lock:
            file_path = await Storage._find_file_path(bucket, object_uuid)
            if file_path is None:
                raise HTTPException(status_code=404, detail="Object not found")
            await aiofiles.os.remove(file_path)

    @staticmethod
    async def update(
        bucket: str,
        object_uuid: str,
        file: UploadFile,
    ):
        """更新对象内容（替换文件）"""
        lock = await get_object_lock(bucket, object_uuid)
        async with lock:
            old_path = await Storage._find_file_path(bucket, object_uuid)
            if old_path is None:
                raise HTTPException(status_code=404, detail="Object not found")

            new_path = Storage._get_storage_path(bucket, object_uuid)
            if old_path != new_path:
                await aiofiles.os.remove(old_path)
                await aiofiles.os.makedirs(new_path.parent, exist_ok=True)
            # 写入新内容
            async with aiofiles.open(new_path, "wb") as f:
                content = await file.read()
                await f.write(content)

    @staticmethod
    async def list_objects(bucket: str) -> List[dict]:
        """列出bucket中的所有对象（uuid和扩展名）"""
        base_dir = Path("data") / bucket
        if not await aiofiles.os.path.exists(base_dir):
            return []

        objects = []
        def walk_sync():
            results = []
            for xx in os.listdir(base_dir):
                xx_path = base_dir / xx
                if not xx_path.is_dir():
                    continue
                for yy in os.listdir(xx_path):
                    yy_path = xx_path / yy
                    if not yy_path.is_dir():
                        continue
                    for file_name in os.listdir(yy_path):
                        results.append({
                            "uuid": file_name,
                            "path": str(yy_path / file_name)
                        })
            return results

        objects = await asyncio.to_thread(walk_sync)
        return objects

