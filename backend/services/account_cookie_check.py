"""账号 Cookie 校验共享服务。

用途：
1. 复用 `/checkAccount` 现有平台 `check_cookie()` 逻辑；
2. 给定时任务执行前做账号预检；
3. 统一返回用户可读的校验消息，避免各处自行拼接。
"""

import asyncio
import sqlite3
from pathlib import Path

from conf import BASE_DIR
from impl.registry import get_platform
from util._logger import get_channel_logger

logger = get_channel_logger("account_cookie_check")

DB_PATH = BASE_DIR / "db" / "database.db"


def get_account_record(account_id: int) -> dict | None:
    """按账号 ID 获取数据库记录。"""
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM user_info WHERE id = ?",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def check_cookie_by_record(record: dict) -> tuple[bool, str]:
    """根据账号记录检查 Cookie 是否有效。

    返回：
    - bool: 是否有效
    - str: 用户可读的结果说明
    """
    if not record:
        return False, "账号不存在"

    platform = get_platform(record.get("type"))
    if not platform:
        return False, "不支持的平台类型"

    cookie_file = record.get("filePath", "")
    if not cookie_file:
        return False, "账号未绑定 Cookie 文件"

    try:
        valid = asyncio.run(platform.check_cookie(cookie_file))
    except Exception as exc:
        logger.info("[Cookie校验] 平台校验抛错 account_id=%s: %s", record.get("id"), exc)
        return False, f"Cookie 检查失败: {exc}"

    if valid:
        return True, "Cookie 有效"
    return False, "Cookie 已失效，请重新登录"


def check_cookie_by_account_id(account_id: int) -> tuple[bool, str, dict | None]:
    """根据账号 ID 检查 Cookie。

    返回：
    - bool: 是否有效
    - str: 用户可读消息
    - dict|None: 账号记录
    """
    record = get_account_record(account_id)
    valid, message = check_cookie_by_record(record)
    return valid, message, record


def update_account_status(account_id: int, valid: bool) -> None:
    """同步更新 user_info.status，便于前端账号状态复用。"""
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "UPDATE user_info SET status = ? WHERE id = ?",
            (1 if valid else 0, account_id),
        )
        conn.commit()
