"""定时任务接口与 Cookie 跳过逻辑测试。"""

import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_TMPDIR = tempfile.mkdtemp()
os.environ['SAU_DATA_DIR'] = _TMPDIR
DB_PATH = Path(_TMPDIR) / "db" / "database.db"


def _setup_db():
    """创建测试数据库并插入一个抖音账号。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import init_db as init_db_module
    init_db_module.DB_PATH = DB_PATH
    from init_db import init_database, migrate_database

    init_database()
    migrate_database()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT INTO user_info (id, type, filePath, userName, status, avatar)
        VALUES (1, 3, 'douyin-1.json', '账号A', 1, '')
        """
    )
    conn.execute(
        """
        INSERT INTO user_info (id, type, filePath, userName, status, avatar)
        VALUES (2, 1, 'xiaohongshu-2.json', '账号B', 1, '')
        """
    )
    conn.commit()
    conn.close()


def _build_snapshot():
    """构造满足 validate_draft_for_publish 的最小视频快照。"""
    return {
        "commonConfig": {
            "videoLandscape": {
                "id": "mat-video-1",
                "name": "video.mp4",
                "stored_path": "materials/video.mp4",
                "url": "/api/materials/file/materials%2Fvideo.mp4",
                "size": 1024,
                "type": "video/mp4",
                "duration": 10,
                "orientation": "horizontal",
            },
            "videoPortrait": None,
            "coverLandscape": {
                "id": "mat-cover-1",
                "name": "cover.jpg",
                "stored_path": "materials/cover.jpg",
                "url": "/api/materials/file/materials%2Fcover.jpg",
                "size": 256,
                "type": "image/jpeg",
            },
            "coverPortrait": None,
        },
        "platformConfigs": {
            "douyin": {
                "title": "测试标题",
                "description": "测试描述",
                "tags": ["游戏", "高光"],
                "aiContent": "内容由AI生成",
                "activityId": [],
            }
        },
        "platformOverrides": {},
        "accountOverrides": {},
        "publishAccountIds": [1],
        "selectedPlatform": "douyin",
        "selectedAccountId": 1,
        "expandedGroups": [],
        "platformChecked": {},
        "accountChecked": {},
    }


class TestScheduledTasksEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['SAU_DATA_DIR'] = _TMPDIR
        _setup_db()

        import app as app_module
        import ext_api
        from services import account_cookie_check
        from services import scheduled_tasks

        app_module.DB_PATH = DB_PATH
        ext_api.DB_PATH = DB_PATH
        account_cookie_check.DB_PATH = DB_PATH
        scheduled_tasks.DB_PATH = DB_PATH

        cls.client = ext_api.app.test_client()

    def test_import_and_schedule_task(self):
        """导入任务后应为 draft，设置时间后应变成 scheduled。"""
        resp = self.client.post(
            '/api/v2/scheduled-tasks/import',
            json={
                "task_name": "测试定时任务",
                "task_note": "",
                "snapshot_data": _build_snapshot(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()['data']
        self.assertEqual(data['status'], 'draft')

        task_id = data['id']
        scheduled_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        resp = self.client.patch(
            f'/api/v2/scheduled-tasks/{task_id}/schedule',
            json={"scheduled_at": scheduled_at},
        )
        self.assertEqual(resp.status_code, 200)

        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT status, scheduled_at FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 'scheduled')
        self.assertEqual(row[1], scheduled_at)

    def test_task_title_uses_first_account_and_dispatch_keeps_each_account_title(self):
        """列表取首账号标题，派发仍使用每个账号各自的有效标题。"""
        snapshot = _build_snapshot()
        snapshot["platformConfigs"]["xiaohongshu"] = {
            "title": "小红书渠道标题",
            "description": "小红书描述",
            "tags": [],
            "aiContent": "内容由AI生成",
        }
        snapshot["accountOverrides"] = {"1": {"title": "抖音账号标题"}}
        snapshot["accountChecked"] = {"1": True}
        snapshot["publishAccountIds"] = [1, 2]

        resp = self.client.post(
            '/api/v2/scheduled-tasks/import',
            json={"task_note": "", "snapshot_data": snapshot},
        )
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()['data']['id']

        list_resp = self.client.get('/api/v2/scheduled-tasks')
        item = next(row for row in list_resp.get_json()['data']['items'] if row['id'] == task_id)
        self.assertEqual(item['title'], '抖音账号标题')
        self.assertEqual(item['task_name'], '抖音账号标题 定时任务')

        queued_tasks = []
        with patch('services.scheduled_tasks.check_cookie_by_record', return_value=(True, 'Cookie 有效')):
            with patch('ext_api.task_queue.get_task_queue') as get_queue:
                get_queue.return_value.add_task.side_effect = queued_tasks.append
                run_resp = self.client.post(f'/api/v2/scheduled-tasks/{task_id}/run-now')

        self.assertEqual(run_resp.status_code, 200)
        tasks_by_account = {task.account_id: task for task in queued_tasks}
        self.assertEqual(tasks_by_account[1].title, '抖音账号标题')
        self.assertEqual(tasks_by_account[2].title, '小红书渠道标题')

    def test_draft_card_title_uses_first_account_effective_title(self):
        """草稿卡片标题应取首个发布账号的实际表单标题。"""
        snapshot = _build_snapshot()
        snapshot["accountOverrides"] = {"1": {"title": "账号表单标题"}}
        snapshot["accountChecked"] = {"1": True}

        create_resp = self.client.post('/api/v2/drafts', json={"draft_data": snapshot})
        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(create_resp.get_json()['data']['title'], '账号表单标题')

        list_resp = self.client.get('/api/v2/drafts?type=video')
        self.assertEqual(list_resp.status_code, 200)
        draft_id = create_resp.get_json()['data']['id']
        draft = next(item for item in list_resp.get_json()['data'] if item['id'] == draft_id)
        self.assertEqual(draft['title'], '账号表单标题')

    def test_run_now_cookie_invalid_skips_current_account_and_writes_logs(self):
        """Cookie 无效时，只失败当前账号，并写入失败原因和操作日志。"""
        resp = self.client.post(
            '/api/v2/scheduled-tasks/import',
            json={
                "task_name": "立即执行任务",
                "task_note": "",
                "snapshot_data": _build_snapshot(),
            },
        )
        task_id = resp.get_json()['data']['id']

        with patch('services.scheduled_tasks.check_cookie_by_record', return_value=(False, 'Cookie 已失效，请重新登录')):
            resp = self.client.post(f'/api/v2/scheduled-tasks/{task_id}/run-now')

        self.assertEqual(resp.status_code, 200)

        list_resp = self.client.get('/api/v2/scheduled-tasks')
        items = list_resp.get_json()['data']['items']
        target = next(item for item in items if item['id'] == task_id)
        self.assertEqual(target['status'], 'failed')
        self.assertEqual(target['channel_statuses'][0]['display_name'], '抖音-账号A')
        self.assertIn('Cookie 已失效', target['channel_statuses'][0]['error_message'])

        detail_resp = self.client.get(f'/api/v2/scheduled-tasks/{task_id}/detail')
        detail = detail_resp.get_json()['data']
        self.assertEqual(detail['accounts'][0]['display_name'], '抖音-账号A')
        self.assertEqual(detail['accounts'][0]['error_code'], 'cookie_invalid')
        self.assertTrue(
            any(log['action'] == 'Cookie校验' and log['result'] == 'failed' for log in detail['accounts'][0]['operation_logs'])
        )
    def test_run_now_allows_retry_from_failed_status(self):
        """failed 状态的定时任务也应允许立即执行。"""
        resp = self.client.post(
            '/api/v2/scheduled-tasks/import',
            json={
                "task_name": "失败后重跑任务",
                "task_note": "",
                "snapshot_data": _build_snapshot(),
            },
        )
        task_id = resp.get_json()['data']['id']

        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE scheduled_tasks SET status = 'failed', publish_batch_id = 'old-batch-id' WHERE id = ?",
            (task_id,),
        )
        conn.commit()
        conn.close()

        with patch(
            'services.scheduled_tasks.dispatch_task',
            return_value={"id": task_id, "status": "queued", "publish_batch_id": "new-batch-id"},
        ) as dispatch_mock:
            resp = self.client.post(f'/api/v2/scheduled-tasks/{task_id}/run-now')

        self.assertEqual(resp.status_code, 200)
        dispatch_mock.assert_called_once()
        self.assertEqual(
            dispatch_mock.call_args.kwargs['allowed_statuses'],
            {"draft", "scheduled", "success", "partial", "failed"},
        )


if __name__ == '__main__':
    unittest.main()
