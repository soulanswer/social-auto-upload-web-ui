"""定时任务服务层。

职责：
1. 管理 scheduled_tasks 的导入、查询、设置时间、立即执行、删除；
2. 把任务快照转换为现有发布队列任务；
3. 维护账号级执行日志与任务状态回写；
4. 在调度器与手动立即执行之间复用同一套派发逻辑。
"""

import json
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from conf import BASE_DIR
from services.account_cookie_check import check_cookie_by_record, get_account_record, update_account_status
from services.draft_merge import build_platform_kwargs, merge_config, validate_draft_for_publish
from util._logger import get_channel_logger

logger = get_channel_logger("scheduled_tasks")

DB_PATH = BASE_DIR / "db" / "database.db"
SCHEDULER_INTERVAL_SECONDS = 3

PLATFORM_ID_TO_KEY = {
    1: "xiaohongshu",
    2: "channels",
    3: "douyin",
    4: "kuaishou",
    5: "bilibili",
    6: "baijiahao",
    7: "tiktok",
    8: "youtube",
    9: "tencent_video",
    10: "iqiyi",
    11: "weibo",
    12: "alipay",
    13: "toutiao",
    14: "zhihu",
    15: "csdn",
    16: "vivo",
}

PLATFORM_KEY_TO_NAME = {
    "xiaohongshu": "小红书",
    "channels": "视频号",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "baijiahao": "百家号",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "tencent_video": "腾讯视频",
    "iqiyi": "爱奇艺",
    "weibo": "微博",
    "alipay": "支付宝",
    "toutiao": "今日头条",
    "zhihu": "知乎",
    "csdn": "CSDN",
    "vivo": "VIVO",
}

INVALID_ERROR_CODES = {"account_missing", "snapshot_invalid", "video_missing", "cover_missing"}
STATUS_LABELS = {
    "draft": "草稿",
    "scheduled": "已排期",
    "dispatching": "派发中",
    "queued": "排队中",
    "running": "发布中",
    "success": "成功",
    "partial": "部分成功",
    "failed": "失败",
    "cancelled": "已取消",
    "invalid": "数据失效",
}

_scheduler_lock = threading.Lock()
_scheduler_started = False
_stream_lock = threading.Lock()
_stream_subscribers: list[queue.Queue] = []


def ensure_scheduled_task_tables(conn: sqlite3.Connection) -> None:
    """确保定时任务相关表与列存在。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'video',
            task_name TEXT NOT NULL DEFAULT '',
            task_note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'publish_center',
            snapshot_data TEXT NOT NULL DEFAULT '{}',
            scheduled_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            publish_batch_id TEXT NOT NULL DEFAULT '',
            account_count INTEGER NOT NULL DEFAULT 0,
            channel_count INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            tag_summary TEXT NOT NULL DEFAULT '',
            channel_names TEXT NOT NULL DEFAULT '[]',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dispatched_at TIMESTAMP,
            finished_at TIMESTAMP,
            deleted_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_detail_logs (
            id TEXT PRIMARY KEY,
            detail_id TEXT NOT NULL,
            scheduled_task_id TEXT NOT NULL DEFAULT '',
            step_order INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL DEFAULT '',
            expected_value TEXT NOT NULL DEFAULT '',
            actual_value TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (detail_id) REFERENCES publish_details(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status_time ON scheduled_tasks(status, scheduled_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_deleted ON scheduled_tasks(deleted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_detail_logs_detail ON publish_detail_logs(detail_id, step_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_detail_logs_task ON publish_detail_logs(scheduled_task_id, created_at)")

    _safe_add_column(conn, "publish_details", "scheduled_task_id", "TEXT NOT NULL DEFAULT ''")
    _safe_add_column(conn, "publish_details", "error_code", "TEXT NOT NULL DEFAULT ''")
    _safe_add_column(conn, "publish_details", "error_source", "TEXT NOT NULL DEFAULT ''")
    _safe_add_column(conn, "publish_batches", "source", "TEXT NOT NULL DEFAULT ''")
    _safe_add_column(conn, "publish_batches", "draft_id", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_batches_draft ON publish_batches(source, draft_id)")


def _safe_add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """安全添加列，已存在时忽略。"""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError:
        pass


def _db_conn() -> sqlite3.Connection:
    """统一获取带 row_factory 的数据库连接。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    ensure_scheduled_task_tables(conn)
    return conn


def _json_loads(raw, default):
    """安全反序列化 JSON。"""
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _now_str() -> str:
    """返回当前 ISO 时间字符串。"""
    return datetime.now().isoformat(timespec="seconds")


def subscribe_task_events(*, maxsize: int = 100) -> queue.Queue:
    """涓哄畾鏃朵换鍔″垪琛ㄧ殑 SSE 杩炴帴娉ㄥ唽涓€涓闃呴槦鍒椼€?"""
    subscriber = queue.Queue(maxsize=maxsize)
    with _stream_lock:
        _stream_subscribers.append(subscriber)
    return subscriber


def unsubscribe_task_events(subscriber: queue.Queue) -> None:
    """鍦?SSE 杩炴帴鍏抽棴鏃舵竻鐞嗚闃呴槦鍒楋紝閬垮厤鍐呭瓨鍫嗙Н銆?"""
    with _stream_lock:
        if subscriber in _stream_subscribers:
            _stream_subscribers.remove(subscriber)


def emit_task_event(task_id: str, *, reason: str = "updated", status: str = "") -> None:
    """鍚戝墠绔彂閫佲€滄煇鏉″畾鏃朵换鍔″凡缁忓彂鐢熷彉鏇粹€濈殑淇″彿銆?"""
    if not task_id:
        return
    payload = {
        "task_id": task_id,
        "reason": reason,
        "timestamp": _now_str(),
    }
    if status:
        payload["status"] = status
    message = json.dumps(payload, ensure_ascii=False)
    with _stream_lock:
        subscribers = list(_stream_subscribers)
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(message)
        except queue.Full:
            # 鏉ュ緱杩囧揩鏃跺彧涓㈠純鍗曟潯鍒锋柊淇″彿锛屽墠绔笅娆℃敹鍒颁簨浠朵緷鐒朵細鎷夊埌鏈€鏂扮姸鎬併€?
            pass


def _status_label(status: str) -> str:
    """把状态字段转换成前端展示用的中文文案。"""
    return STATUS_LABELS.get(status or "", status or "-")


def _format_display_time(value: str) -> str:
    """把数据库时间统一整理成前端可直接展示的格式。

    目标格式：
    - YYYY-MM-DD HH:mm:ss
    - 如果原值带微秒，则截掉微秒
    - 如果原值为空，返回空串
    """
    if not value:
        return ""
    text = str(value).strip().replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    return text


def _parse_schedule_time(raw: str) -> datetime:
    """解析前端传来的排期时间。"""
    if not raw:
        raise ValueError("发布时间不能为空")
    text = str(raw).strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("发布时间格式错误，必须是 YYYY-MM-DD HH:mm:ss") from exc


def _normalize_snapshot(snapshot_data: dict) -> dict:
    """统一补全快照结构，避免后续字段读取散落默认值。"""
    return {
        "commonConfig": snapshot_data.get("commonConfig") or {},
        "platformConfigs": snapshot_data.get("platformConfigs") or {},
        "platformOverrides": snapshot_data.get("platformOverrides") or {},
        "accountOverrides": snapshot_data.get("accountOverrides") or {},
        "publishAccountIds": snapshot_data.get("publishAccountIds") or [],
        "selectedPlatform": snapshot_data.get("selectedPlatform"),
        "selectedAccountId": snapshot_data.get("selectedAccountId"),
        "expandedGroups": snapshot_data.get("expandedGroups") or [],
        "platformChecked": snapshot_data.get("platformChecked") or {},
        "accountChecked": snapshot_data.get("accountChecked") or {},
    }


def _derive_task_title(snapshot_data: dict, selected_titles: list[str] | None = None) -> str:
    """从快照中提取标题。

    优先只看当前任务实际选中账号对应平台的标题，避免误拿未选中平台里残留的文件名。
    """
    if selected_titles:
        for title in selected_titles:
            clean_title = str(title or "").strip()
            if clean_title:
                return clean_title[:100]
    return "未命名任务"


def _is_override_enabled(checked: dict, identifier: int | str) -> bool:
    """快照中的覆写仅在对应开关启用时参与合并。"""
    value = checked.get(str(identifier), checked.get(identifier))
    return value is True or value == 1


def _get_effective_overrides(snapshot: dict, platform_key: str, account_id: int) -> tuple[dict, dict]:
    """按发布页的覆写开关读取渠道和账号配置。"""
    platform_override = {}
    if _is_override_enabled(snapshot.get("platformChecked") or {}, platform_key):
        platform_override = (snapshot.get("platformOverrides") or {}).get(platform_key) or {}

    account_override = {}
    if _is_override_enabled(snapshot.get("accountChecked") or {}, account_id):
        overrides = snapshot.get("accountOverrides") or {}
        account_override = overrides.get(str(account_id), overrides.get(account_id)) or {}

    return platform_override, account_override


def _build_task_metadata(conn: sqlite3.Connection, snapshot_data: dict) -> dict:
    """根据快照构建列表展示所需的派生字段。"""
    snapshot = _normalize_snapshot(snapshot_data)
    publish_account_ids = snapshot.get("publishAccountIds") or []
    common = snapshot.get("commonConfig") or {}
    platform_configs = snapshot.get("platformConfigs") or {}

    selected_titles = []
    tag_values = []
    channel_names = []

    for raw_account_id in publish_account_ids:
        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            continue
        row = conn.execute("SELECT id, type, userName FROM user_info WHERE id = ?", (account_id,)).fetchone()
        if not row:
            continue
        platform_key = PLATFORM_ID_TO_KEY.get(row["type"], "")
        platform_name = PLATFORM_KEY_TO_NAME.get(platform_key, "未知平台")
        if platform_name not in channel_names:
            channel_names.append(platform_name)
        platform_override, account_override = _get_effective_overrides(snapshot, platform_key, account_id)
        merged = merge_config(
            common,
            platform_configs.get(platform_key) or {},
            platform_override,
            account_override,
        )
        merged_title = str(merged.get("title") or "").strip()
        if merged_title:
            selected_titles.append(merged_title)
        merged_tags = merged.get("tags") or []
        for tag in merged_tags:
            clean_tag = str(tag).strip()
            if clean_tag and clean_tag not in tag_values:
                tag_values.append(clean_tag)

    title = _derive_task_title(snapshot, selected_titles)
    return {
        "title": title,
        "task_name": f"{title} 定时任务" if title else "未命名定时任务",
        "tag_summary": "，".join(tag_values[:8]),
        "channel_names": channel_names,
        "account_count": len(publish_account_ids),
        "channel_count": len(channel_names),
    }


def _build_display_name(platform_name: str, account_name: str) -> str:
    """生成列表状态展示名：渠道-账号名。"""
    return f"{platform_name}-{account_name}"


def _aggregate_batch_status(*, succ: int, fail: int, in_flight: int, total: int) -> str:
    """按现有发布队列规则聚合批次状态。"""
    if total == 0:
        return "pending"
    if in_flight > 0:
        return "running"
    if fail == 0:
        return "success"
    if succ == 0:
        return "failed"
    return "partial"


def _insert_publish_detail_log(
    conn: sqlite3.Connection,
    *,
    detail_id: str,
    scheduled_task_id: str,
    step_order: int,
    stage: str,
    action: str,
    result: str,
    message: str,
    expected_value: str = "",
    actual_value: str = "",
    meta_json: dict | None = None,
) -> None:
    """插入结构化操作日志。"""
    conn.execute(
        """
        INSERT INTO publish_detail_logs
        (id, detail_id, scheduled_task_id, step_order, stage, action, result,
         message, expected_value, actual_value, created_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            detail_id,
            scheduled_task_id,
            step_order,
            stage,
            action,
            result,
            message,
            expected_value,
            actual_value,
            _now_str(),
            json.dumps(meta_json or {}, ensure_ascii=False),
        ),
    )


def _bulk_insert_logs(
    conn: sqlite3.Connection,
    *,
    detail_id: str,
    scheduled_task_id: str,
    logs: list[dict],
) -> None:
    """批量插入步骤日志。"""
    for index, log in enumerate(logs, start=1):
        _insert_publish_detail_log(
            conn,
            detail_id=detail_id,
            scheduled_task_id=scheduled_task_id,
            step_order=index,
            stage=log.get("stage", ""),
            action=log.get("action", ""),
            result=log.get("result", "info"),
            message=log.get("message", ""),
            expected_value=log.get("expected_value", ""),
            actual_value=log.get("actual_value", ""),
            meta_json=log.get("meta_json") or {},
        )


def _recompute_publish_batch(conn: sqlite3.Connection, batch_id: str) -> str:
    """根据 publish_details 聚合 publish_batches 状态。"""
    counts = conn.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status IN ('running', 'queued') THEN 1 ELSE 0 END)
        FROM publish_details
        WHERE batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    total = counts[0] or 0
    succ = counts[1] or 0
    fail = counts[2] or 0
    in_flight = counts[3] or 0
    batch_status = _aggregate_batch_status(succ=succ, fail=fail, in_flight=in_flight, total=total)
    now = _now_str()
    conn.execute(
        """
        UPDATE publish_batches
        SET status = ?, success_count = ?, failed_count = ?, account_count = ?,
            updated_at = ?, finished_at = CASE WHEN ? IN ('success', 'failed', 'partial') THEN ? ELSE finished_at END
        WHERE id = ?
        """,
        (batch_status, succ, fail, total, now, batch_status, now, batch_id),
    )
    return batch_status


def refresh_scheduled_task_status(conn: sqlite3.Connection, scheduled_task_id: str) -> str:
    """根据账号级 publish_details 状态回写 scheduled_tasks。"""
    batch_row = conn.execute(
        "SELECT publish_batch_id FROM scheduled_tasks WHERE id = ?",
        (scheduled_task_id,),
    ).fetchone()
    active_batch_id = (batch_row["publish_batch_id"] if batch_row else "") or ""
    if active_batch_id:
        rows = conn.execute(
            """
            SELECT batch_id, status, error_code, error_message, finished_at, started_at, created_at
            FROM publish_details
            WHERE scheduled_task_id = ? AND batch_id = ?
            ORDER BY COALESCE(finished_at, started_at, created_at) DESC
            """,
            (scheduled_task_id, active_batch_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT batch_id, status, error_code, error_message, finished_at, started_at, created_at
            FROM publish_details
            WHERE scheduled_task_id = ?
            ORDER BY COALESCE(finished_at, started_at, created_at) DESC
            """,
            (scheduled_task_id,),
        ).fetchall()
    if not rows:
        return ""

    statuses = [row["status"] for row in rows]
    if any(status == "running" for status in statuses):
        task_status = "running"
    elif any(status == "queued" for status in statuses):
        task_status = "queued"
    elif all(status == "success" for status in statuses):
        task_status = "success"
    elif all(status == "failed" for status in statuses):
        error_codes = {row["error_code"] for row in rows if row["error_code"]}
        task_status = "invalid" if error_codes and error_codes.issubset(INVALID_ERROR_CODES) else "failed"
    else:
        task_status = "partial"

    last_error = ""
    for row in rows:
        if row["error_message"]:
            last_error = row["error_message"]
            break

    finished_at = _now_str() if task_status in {"success", "partial", "failed", "invalid"} else None
    conn.execute(
        """
        UPDATE scheduled_tasks
        SET status = ?, last_error = ?, updated_at = ?,
            finished_at = CASE WHEN ? IS NULL THEN finished_at ELSE ? END
        WHERE id = ?
        """,
        (task_status, last_error, _now_str(), finished_at, finished_at, scheduled_task_id),
    )
    return task_status


def write_publish_result_log(
    *,
    detail_id: str,
    scheduled_task_id: str,
    status: str,
    message: str,
) -> None:
    """为终态写入“发布结果确认”日志，避免详情弹窗缺少收尾步骤。"""
    if not scheduled_task_id:
        return
    with _db_conn() as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM publish_detail_logs
            WHERE detail_id = ? AND action = '发布结果确认'
            LIMIT 1
            """,
            (detail_id,),
        ).fetchone()
        if exists:
            return
        max_row = conn.execute(
            "SELECT COALESCE(MAX(step_order), 0) FROM publish_detail_logs WHERE detail_id = ?",
            (detail_id,),
        ).fetchone()
        next_order = (max_row[0] or 0) + 1
        _insert_publish_detail_log(
            conn,
            detail_id=detail_id,
            scheduled_task_id=scheduled_task_id,
            step_order=next_order,
            stage="publish",
            action="发布结果确认",
            result="success" if status == "success" else "failed",
            message=message,
        )
        conn.commit()


def import_task(data: dict) -> dict:
    """导入当前发布页快照到定时任务列表。"""
    snapshot_data = _normalize_snapshot(data.get("snapshot_data") or {})
    validate_errors = validate_draft_for_publish({"draft_data": snapshot_data})
    if validate_errors:
        raise ValueError("；".join(validate_errors))

    with _db_conn() as conn:
        metadata = _build_task_metadata(conn, snapshot_data)
        task_id = str(uuid.uuid4())
        # 当前没有单独的“自定义任务名称”输入框，统一以后端派生标题为准，
        # 避免前端误把未选中平台的文件名标题带进来。
        task_name = metadata["task_name"]
        task_note = (data.get("task_note") or "").strip()
        conn.execute(
            """
            INSERT INTO scheduled_tasks
            (id, type, task_name, task_note, source, snapshot_data, scheduled_at, status,
             publish_batch_id, account_count, channel_count, title, tag_summary, channel_names,
             last_error, created_at, updated_at)
            VALUES (?, 'video', ?, ?, 'publish_center', ?, '', 'draft', '', ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                task_id,
                task_name,
                task_note,
                json.dumps(snapshot_data, ensure_ascii=False),
                metadata["account_count"],
                metadata["channel_count"],
                metadata["title"],
                metadata["tag_summary"],
                json.dumps(metadata["channel_names"], ensure_ascii=False),
                _now_str(),
                _now_str(),
            ),
        )
        conn.commit()
    emit_task_event(task_id, reason="imported", status="draft")
    return {"id": task_id, "status": "draft"}


def schedule_task(task_id: str, scheduled_at: str) -> dict:
    """给定时任务设置或修改发布时间。"""
    schedule_dt = _parse_schedule_time(scheduled_at)
    if schedule_dt <= datetime.now():
        raise ValueError("发布时间必须晚于当前时间")

    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, status, deleted_at FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row["deleted_at"]:
            raise ValueError("任务不存在")
        if row["status"] not in {"draft", "scheduled"}:
            raise ValueError("当前状态不允许修改发布时间")
        next_status = "scheduled"
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET scheduled_at = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (schedule_dt.strftime("%Y-%m-%d %H:%M:%S"), next_status, _now_str(), task_id),
        )
        conn.commit()
    emit_task_event(task_id, reason="scheduled", status=next_status)
    return {"id": task_id, "status": next_status, "scheduled_at": schedule_dt.strftime("%Y-%m-%d %H:%M:%S")}


def delete_task(task_id: str) -> dict:
    """删除或取消定时任务。"""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, status, deleted_at FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row["deleted_at"]:
            raise ValueError("任务不存在")
        if row["status"] in {"dispatching", "queued", "running"}:
            raise ValueError("执行中的任务不允许删除")
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'cancelled', deleted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (_now_str(), _now_str(), task_id),
        )
        conn.commit()
    emit_task_event(task_id, reason="cancelled", status="cancelled")
    return {"id": task_id, "status": "cancelled"}


def run_now(task_id: str) -> dict:
    """手动立即执行任务。"""
    return dispatch_task(task_id, allowed_statuses={"draft", "scheduled", "success", "partial", "failed"})


def dispatch_due_tasks() -> None:
    """轮询并派发到期任务。"""
    with _db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM scheduled_tasks
            WHERE deleted_at IS NULL
              AND status = 'scheduled'
              AND scheduled_at != ''
              AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchall()
    for row in rows:
        try:
            dispatch_task(row["id"], allowed_statuses={"scheduled"})
        except Exception as exc:
            logger.info("[定时任务] 派发失败 task_id=%s: %s", row["id"], exc)


def start_scheduler() -> None:
    """启动后台调度线程。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return

        def _loop():
            while True:
                try:
                    dispatch_due_tasks()
                except Exception as exc:
                    logger.info("[定时任务] 调度器轮询失败: %s", exc)
                time.sleep(SCHEDULER_INTERVAL_SECONDS)

        thread = threading.Thread(
            target=_loop,
            name="scheduled-task-scheduler",
            daemon=True,
        )
        thread.start()
        _scheduler_started = True
        logger.info("[定时任务] 调度器已启动，轮询间隔=%s秒", SCHEDULER_INTERVAL_SECONDS)


def _claim_task(conn: sqlite3.Connection, task_id: str, allowed_statuses: set[str]) -> dict:
    """抢占任务，确保定时派发与立即执行不会重复运行。"""
    placeholders = ",".join("?" * len(allowed_statuses))
    now = _now_str()
    params = [now, now, task_id, *allowed_statuses]
    cur = conn.execute(
        f"""
        UPDATE scheduled_tasks
        SET status = 'dispatching', updated_at = ?, dispatched_at = ?, finished_at = NULL, last_error = ''
        WHERE id = ?
          AND deleted_at IS NULL
          AND status IN ({placeholders})
        """,
        params,
    )
    if cur.rowcount == 0:
        raise ValueError("当前状态不允许执行该任务")
    row = conn.execute(
        "SELECT * FROM scheduled_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return dict(row)


def _build_pending_channel_statuses(conn: sqlite3.Connection, task_row: dict) -> list[dict]:
    """为未执行任务构建账号级中性状态标签。"""
    snapshot = _normalize_snapshot(_json_loads(task_row.get("snapshot_data"), {}))
    result = []
    for raw_account_id in snapshot.get("publishAccountIds") or []:
        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            continue
        record = conn.execute(
            "SELECT id, type, userName FROM user_info WHERE id = ?",
            (account_id,),
        ).fetchone()
        if record:
            platform_name = PLATFORM_KEY_TO_NAME.get(PLATFORM_ID_TO_KEY.get(record["type"], ""), "未知平台")
            account_name = record["userName"] or f"账号{account_id}"
        else:
            platform_name = "未知平台"
            account_name = f"账号{account_id}"
        pending_status = task_row.get("status") if task_row.get("status") in {"draft", "scheduled", "dispatching", "queued", "running"} else "draft"
        result.append(
            {
                "platform": platform_name,
                "account_name": account_name,
                "display_name": _build_display_name(platform_name, account_name),
                "status": pending_status,
                "status_label": _status_label(pending_status),
                "error_message": "",
            }
        )
    return result


def _read_channel_statuses(conn: sqlite3.Connection, task_row: dict) -> list[dict]:
    """按任务当前状态返回账号级状态标签。"""
    task_id = task_row["id"]
    batch_id = task_row.get("publish_batch_id", "")
    if not batch_id:
        return _build_pending_channel_statuses(conn, task_row)
    rows = conn.execute(
        """
        SELECT platform, account_name, status, error_message
        FROM publish_details
        WHERE scheduled_task_id = ? AND batch_id = ?
        ORDER BY created_at ASC
        """,
        (task_id, batch_id),
    ).fetchall()
    if not rows:
        return _build_pending_channel_statuses(conn, task_row)
    return [
        {
            "platform": row["platform"],
            "account_name": row["account_name"],
            "display_name": _build_display_name(row["platform"], row["account_name"]),
            "status": row["status"],
            "status_label": _status_label(row["status"]),
            "error_message": row["error_message"] or "",
        }
        for row in rows
    ]


def list_tasks(*, page: int = 1, page_size: int = 20, keyword: str = "", status: str = "") -> dict:
    """分页查询定时任务列表。"""
    offset = max(page - 1, 0) * page_size
    conditions = ["deleted_at IS NULL"]
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if keyword:
        conditions.append("(task_name LIKE ? OR title LIKE ? OR tag_summary LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])
    where = " AND ".join(conditions)

    with _db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM scheduled_tasks WHERE {where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT *
            FROM scheduled_tasks
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ).fetchall()
        items = []
        for row in rows:
            task_row = dict(row)
            metadata = _build_task_metadata(conn, _json_loads(task_row.get("snapshot_data"), {}))
            items.append(
                {
                    "id": task_row["id"],
                    "task_name": (
                        metadata["task_name"]
                        if task_row.get("task_name", "") in {"", task_row.get("title", ""), f"{task_row.get('title', '')} 定时任务"}
                        else task_row.get("task_name", "")
                    ),
                    "title": metadata["title"] or task_row.get("title", ""),
                    "tag_summary": metadata["tag_summary"] or task_row.get("tag_summary", ""),
                    "channel_names": metadata["channel_names"] or _json_loads(task_row.get("channel_names"), []),
                    "channel_statuses": _read_channel_statuses(conn, task_row),
                    "status": task_row.get("status", "draft"),
                    "status_label": _status_label(task_row.get("status", "draft")),
                    "scheduled_at": task_row.get("scheduled_at", ""),
                    "created_at": _format_display_time(task_row.get("created_at", "")),
                    "updated_at": _format_display_time(task_row.get("updated_at", "")),
                    "publish_batch_id": task_row.get("publish_batch_id", ""),
                    "last_error": task_row.get("last_error", ""),
                }
            )
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_task_detail(task_id: str) -> dict:
    """查询任务详情弹窗数据。"""
    with _db_conn() as conn:
        task_row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ? AND deleted_at IS NULL",
            (task_id,),
        ).fetchone()
        if not task_row:
            raise ValueError("任务不存在")
        task = dict(task_row)
        metadata = _build_task_metadata(conn, _json_loads(task.get("snapshot_data"), {}))
        batch_id = task.get("publish_batch_id", "")
        accounts = []

        if batch_id:
            detail_rows = conn.execute(
                """
                SELECT *
                FROM publish_details
                WHERE scheduled_task_id = ? AND batch_id = ?
                ORDER BY created_at ASC
                """,
                (task_id, batch_id),
            ).fetchall()
            for detail in detail_rows:
                detail_dict = dict(detail)
                log_rows = conn.execute(
                    """
                    SELECT step_order, stage, action, result, message, expected_value,
                           actual_value, created_at
                    FROM publish_detail_logs
                    WHERE detail_id = ?
                    ORDER BY step_order ASC, created_at ASC
                    """,
                    (detail_dict["id"],),
                ).fetchall()
                accounts.append(
                    {
                        "detail_id": detail_dict["id"],
                        "platform": detail_dict["platform"],
                        "account_name": detail_dict["account_name"],
                        "display_name": _build_display_name(detail_dict["platform"], detail_dict["account_name"]),
                        "status": detail_dict["status"],
                        "status_label": _status_label(detail_dict["status"]),
                        "started_at": _format_display_time(detail_dict.get("started_at") or ""),
                        "finished_at": _format_display_time(detail_dict.get("finished_at") or ""),
                        "error_code": detail_dict.get("error_code") or "",
                        "error_message": detail_dict.get("error_message") or "",
                        "operation_logs": [
                            {
                                **dict(row),
                                "created_at": _format_display_time(dict(row).get("created_at", "")),
                            }
                            for row in log_rows
                        ],
                    }
                )
        if not accounts:
            snapshot = _normalize_snapshot(_json_loads(task.get("snapshot_data"), {}))
            for raw_account_id in snapshot.get("publishAccountIds") or []:
                try:
                    account_id = int(raw_account_id)
                except (TypeError, ValueError):
                    continue
                record = conn.execute(
                    "SELECT id, type, userName FROM user_info WHERE id = ?",
                    (account_id,),
                ).fetchone()
                if record:
                    platform_name = PLATFORM_KEY_TO_NAME.get(PLATFORM_ID_TO_KEY.get(record["type"], ""), "未知平台")
                    account_name = record["userName"] or f"账号{account_id}"
                else:
                    platform_name = "未知平台"
                    account_name = f"账号{account_id}"
                accounts.append(
                    {
                        "detail_id": "",
                        "platform": platform_name,
                        "account_name": account_name,
                        "display_name": _build_display_name(platform_name, account_name),
                        "status": task.get("status", "draft"),
                        "status_label": _status_label(task.get("status", "draft")),
                        "started_at": "",
                        "finished_at": "",
                        "error_code": "",
                        "error_message": "",
                        "operation_logs": [],
                    }
                )

    return {
        "id": task["id"],
        "task_name": (
            metadata["task_name"]
            if task.get("task_name", "") in {"", task.get("title", ""), f"{task.get('title', '')} 定时任务"}
            else task.get("task_name", "")
        ),
        "status": task.get("status", "draft"),
        "status_label": _status_label(task.get("status", "draft")),
        "scheduled_at": task.get("scheduled_at", ""),
        "publish_batch_id": task.get("publish_batch_id", ""),
        "title": metadata["title"] or task.get("title", ""),
        "created_at": _format_display_time(task.get("created_at", "")),
        "updated_at": _format_display_time(task.get("updated_at", "")),
        "accounts": accounts,
    }


def _insert_failed_detail(
    conn: sqlite3.Connection,
    *,
    detail_id: str,
    batch_id: str,
    scheduled_task_id: str,
    account_id: int | None,
    account_name: str,
    platform_name: str,
    account_configs: dict,
    error_code: str,
    error_source: str,
    error_message: str,
    logs: list[dict],
) -> None:
    """为跳过或预检失败的账号创建失败明细。"""
    now = _now_str()
    conn.execute(
        """
        INSERT INTO publish_details
        (id, batch_id, account_id, account_name, platform, account_configs, status,
         retry_count, max_retries, error_message, publish_url, created_at, started_at,
         finished_at, scheduled_task_id, error_code, error_source)
        VALUES (?, ?, ?, ?, ?, ?, 'failed', 0, 0, ?, '', ?, ?, ?, ?, ?, ?)
        """,
        (
            detail_id,
            batch_id,
            account_id,
            account_name,
            platform_name,
            json.dumps(account_configs, ensure_ascii=False),
            error_message,
            now,
            now,
            now,
            scheduled_task_id,
            error_code,
            error_source,
        ),
    )
    _bulk_insert_logs(
        conn,
        detail_id=detail_id,
        scheduled_task_id=scheduled_task_id,
        logs=logs,
    )


def dispatch_task(task_id: str, *, allowed_statuses: set[str]) -> dict:
    """把定时任务派发成现有发布队列任务。"""
    with _db_conn() as conn:
        task_row = _claim_task(conn, task_id, allowed_statuses)
        snapshot = _normalize_snapshot(_json_loads(task_row.get("snapshot_data"), {}))
        publish_account_ids = snapshot.get("publishAccountIds") or []
        common = snapshot.get("commonConfig") or {}
        platform_configs = snapshot.get("platformConfigs") or {}

        if not publish_account_ids:
            conn.execute(
                "UPDATE scheduled_tasks SET status = 'invalid', last_error = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                ("任务中没有可执行账号", _now_str(), _now_str(), task_id),
            )
            conn.commit()
            raise ValueError("任务中没有可执行账号")

        batch_id = str(uuid.uuid4())
        now = _now_str()
        conn.execute(
            """
            INSERT INTO publish_batches
            (id, type, title, description, video_material_id, image_material_ids,
             landscape_cover_material_id, portrait_cover_material_id, status, account_count,
             success_count, failed_count, schedule_time, created_at, started_at, finished_at,
             updated_at, source, draft_id)
            VALUES (?, 'video', ?, '', '', '[]', '', '', 'pending', 0, 0, 0, '', ?, ?, NULL, ?, 'scheduled_task', 0)
            """,
            (batch_id, task_row.get("title", ""), now, now, now),
        )
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET publish_batch_id = ?, dispatched_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (batch_id, now, now, task_id),
        )
        conn.commit()

    emit_task_event(task_id, reason="dispatching", status="dispatching")
    from ext_api.task_queue import PublishTask, get_task_queue

    queue = get_task_queue()
    enqueued_count = 0

    for raw_account_id in publish_account_ids:
        detail_id = str(uuid.uuid4())
        step_logs = [
            {
                "stage": "precheck",
                "action": "任务快照加载",
                "result": "success",
                "message": "任务快照加载成功",
            }
        ]

        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            with _db_conn() as conn:
                _insert_failed_detail(
                    conn,
                    detail_id=detail_id,
                    batch_id=batch_id,
                    scheduled_task_id=task_id,
                    account_id=None,
                    account_name=f"账号{raw_account_id}",
                    platform_name="未知平台",
                    account_configs={},
                    error_code="account_missing",
                    error_source="snapshot_invalid",
                    error_message="快照里的账号 ID 无效",
                    logs=step_logs
                    + [
                        {
                            "stage": "precheck",
                            "action": "任务基础校验",
                            "result": "failed",
                            "message": "快照里的账号 ID 无效",
                        }
                    ],
                )
                _recompute_publish_batch(conn, batch_id)
                refresh_scheduled_task_status(conn, task_id)
                conn.commit()
            emit_task_event(task_id, reason="progress")
            continue

        record = get_account_record(account_id)
        if not record:
            with _db_conn() as conn:
                _insert_failed_detail(
                    conn,
                    detail_id=detail_id,
                    batch_id=batch_id,
                    scheduled_task_id=task_id,
                    account_id=account_id,
                    account_name=f"账号{account_id}",
                    platform_name="未知平台",
                    account_configs={},
                    error_code="account_missing",
                    error_source="snapshot_invalid",
                    error_message="账号不存在，已跳过该账号",
                    logs=step_logs
                    + [
                        {
                            "stage": "precheck",
                            "action": "任务基础校验",
                            "result": "failed",
                            "message": "账号不存在，已跳过该账号",
                        }
                    ],
                )
                _recompute_publish_batch(conn, batch_id)
                refresh_scheduled_task_status(conn, task_id)
                conn.commit()
            emit_task_event(task_id, reason="progress")
            continue

        platform_key = PLATFORM_ID_TO_KEY.get(record.get("type"), "")
        platform_name = PLATFORM_KEY_TO_NAME.get(platform_key, "未知平台")
        account_name = record.get("userName") or f"账号{account_id}"
        platform_override, account_override = _get_effective_overrides(snapshot, platform_key, account_id)
        merged = merge_config(
            common,
            platform_configs.get(platform_key) or {},
            platform_override,
            account_override,
        )

        step_logs.append(
            {
                "stage": "precheck",
                "action": "任务基础校验",
                "result": "success",
                "message": "账号和平台配置读取成功",
            }
        )

        valid, cookie_message = check_cookie_by_record(record)
        update_account_status(account_id, valid)
        if not valid:
            step_logs.append(
                {
                    "stage": "precheck",
                    "action": "Cookie校验",
                    "result": "failed",
                    "message": "检测到未登录页面，判定 Cookie 已失效，已跳过该账号",
                    "expected_value": "账号处于已登录状态",
                    "actual_value": cookie_message,
                }
            )
            with _db_conn() as conn:
                _insert_failed_detail(
                    conn,
                    detail_id=detail_id,
                    batch_id=batch_id,
                    scheduled_task_id=task_id,
                    account_id=account_id,
                    account_name=account_name,
                    platform_name=platform_name,
                    account_configs=merged,
                    error_code="cookie_invalid",
                    error_source="operation_check",
                    error_message="Cookie 已失效，已跳过该账号",
                    logs=step_logs,
                )
                _recompute_publish_batch(conn, batch_id)
                refresh_scheduled_task_status(conn, task_id)
                conn.commit()
            emit_task_event(task_id, reason="progress")
            continue

        step_logs.append(
            {
                "stage": "precheck",
                "action": "Cookie校验",
                "result": "success",
                "message": "Cookie 校验通过",
                "expected_value": "账号处于已登录状态",
                "actual_value": "账号处于已登录状态",
            }
        )

        try:
            account_obj = type("Account", (), {})()
            account_obj.id = record["id"]
            account_obj.platform = platform_key
            account_obj.file_path = record["filePath"]
            payload = build_platform_kwargs(merged, common, account_obj)
            step_logs.append(
                {
                    "stage": "prepare",
                    "action": "发布参数构建",
                    "result": "success",
                    "message": "发布参数构建完成",
                }
            )

            expected_title = str(merged.get("title") or "").strip()
            actual_title = str(payload.get("title") or "").strip()
            step_logs.append(
                {
                    "stage": "prepare",
                    "action": "标题校验",
                    "result": "success" if expected_title == actual_title else "warning",
                    "message": "标题与任务快照一致" if expected_title == actual_title else "标题经过参数构建后发生变化",
                    "expected_value": expected_title,
                    "actual_value": actual_title,
                }
            )

            expected_tags = "，".join([f"#{tag}" for tag in (merged.get("tags") or [])])
            actual_tags = "，".join([f"#{tag}" for tag in (payload.get("tags") or [])])
            step_logs.append(
                {
                    "stage": "prepare",
                    "action": "标签校验",
                    "result": "success" if expected_tags == actual_tags else "warning",
                    "message": "标签与任务快照一致" if expected_tags == actual_tags else "标签经过参数构建后发生变化",
                    "expected_value": expected_tags,
                    "actual_value": actual_tags,
                }
            )

            expected_cover = str(
                (merged.get("coverPortrait") or merged.get("coverLandscape") or {}).get("stored_path", "")
            )
            actual_cover = str(payload.get("thumbnail_path") or "")
            step_logs.append(
                {
                    "stage": "prepare",
                    "action": "封面校验",
                    "result": "success" if actual_cover else "failed",
                    "message": "封面参数构建完成" if actual_cover else "未找到可用封面",
                    "expected_value": expected_cover,
                    "actual_value": actual_cover,
                }
            )

            task = PublishTask(
                id=detail_id,
                batch_id=batch_id,
                platform=platform_name,
                platform_type=record["type"],
                account_name=account_name,
                account_cookie_path=record["filePath"],
                video_path=(payload.get("files") or [""])[0] if payload.get("files") else "",
                title=payload.get("title", ""),
                description=payload.get("desc", ""),
                thumbnail_path=payload.get("thumbnail_path", ""),
                tags=payload.get("tags") or [],
                source="scheduled_task",
                draft_id=0,
                account_id=account_id,
                payload=payload,
                scheduled_task_id=task_id,
                account_config_snapshot=merged,
            )
            queue.add_task(task)
            enqueued_count += 1

            with _db_conn() as conn:
                step_logs.append(
                    {
                        "stage": "publish",
                        "action": "任务入队",
                        "result": "success",
                        "message": "任务已进入发布队列",
                    }
                )
                _bulk_insert_logs(conn, detail_id=detail_id, scheduled_task_id=task_id, logs=step_logs)
                _recompute_publish_batch(conn, batch_id)
                conn.commit()
        except Exception as exc:
            step_logs.append(
                {
                    "stage": "publish",
                    "action": "任务入队",
                    "result": "failed",
                    "message": f"任务入队失败: {exc}",
                }
            )
            with _db_conn() as conn:
                _insert_failed_detail(
                    conn,
                    detail_id=detail_id,
                    batch_id=batch_id,
                    scheduled_task_id=task_id,
                    account_id=account_id,
                    account_name=account_name,
                    platform_name=platform_name,
                    account_configs=merged,
                    error_code="dispatch_exception",
                    error_source="platform_exception",
                    error_message=f"任务入队失败: {exc}",
                    logs=step_logs,
                )
                _recompute_publish_batch(conn, batch_id)
                refresh_scheduled_task_status(conn, task_id)
                conn.commit()
            emit_task_event(task_id, reason="progress")
            continue

    with _db_conn() as conn:
        _recompute_publish_batch(conn, batch_id)
        if enqueued_count > 0:
            conn.execute(
                "UPDATE scheduled_tasks SET status = 'queued', updated_at = ? WHERE id = ?",
                (_now_str(), task_id),
            )
            final_status = "queued"
        else:
            final_status = refresh_scheduled_task_status(conn, task_id) or "failed"
        conn.commit()

    emit_task_event(
        task_id,
        reason="queued" if enqueued_count > 0 else "finished",
        status=final_status,
    )
    return {"id": task_id, "status": final_status, "publish_batch_id": batch_id}
