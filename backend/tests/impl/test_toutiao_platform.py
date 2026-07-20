"""Focused unit tests for Toutiao scheduled publish helpers."""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 把 backend 目录加进 sys.path（与项目其他测试一致）
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from impl.toutiao.platform import ToutiaoPlatform  # noqa: E402


class _FakeVisibleTextLocator:
    def __init__(self, count: int = 0, visible: bool = True):
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible


class _FakeItemLocator:
    def __init__(self, text: str, visible: bool = True):
        self._text = text
        self._visible = visible

    async def is_visible(self):
        return self._visible

    async def text_content(self):
        return self._text


class _FakeCollectionLocator:
    def __init__(self, items=None):
        self._items = items or []

    async def count(self):
        return len(self._items)

    def nth(self, idx: int):
        return self._items[idx]


class _FakePage:
    def __init__(self, url: str, visible_texts=None, feedback_text: str = ""):
        self.url = url
        self.visible_texts = visible_texts or {}
        self.feedback_text = feedback_text

    def get_by_text(self, text: str, exact: bool = False):
        return _FakeVisibleTextLocator(self.visible_texts.get(text, 0))

    def locator(self, selector: str):
        if self.feedback_text:
            return _FakeCollectionLocator([_FakeItemLocator(self.feedback_text)])
        return _FakeCollectionLocator([])


def test_validate_schedule_publish_date_rejects_less_than_two_hours():
    now = datetime(2026, 7, 20, 19, 10)
    target = now + timedelta(hours=1, minutes=59)
    ok, reason = ToutiaoPlatform._validate_schedule_publish_date(target, now=now)
    assert ok is False
    assert "2小时" in reason


def test_validate_schedule_publish_date_rejects_more_than_seven_days():
    now = datetime(2026, 7, 20, 19, 10)
    target = now + timedelta(days=7, minutes=1)
    ok, reason = ToutiaoPlatform._validate_schedule_publish_date(target, now=now)
    assert ok is False
    assert "7天" in reason


def test_validate_schedule_publish_date_accepts_exact_boundary():
    now = datetime(2026, 7, 20, 19, 10)
    target = now + timedelta(hours=2)
    ok, reason = ToutiaoPlatform._validate_schedule_publish_date(target, now=now)
    assert ok is True
    assert reason == ""


def test_wait_for_publish_result_succeeds_when_url_changes():
    page = _FakePage("https://mp.toutiao.com/profile_v4/index")
    ok, result, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_publish_result(page, timeout_seconds=0)
    )
    assert ok is True
    assert result == "页面已跳转"
    assert current_url.endswith("/index")


def test_wait_for_publish_result_succeeds_when_success_text_visible():
    page = _FakePage(
        "https://mp.toutiao.com/profile_v4/xigua/upload-video",
        visible_texts={"预约成功": 1},
    )
    ok, result, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_publish_result(page, timeout_seconds=0)
    )
    assert ok is True
    assert result == "检测到成功提示：预约成功"
    assert "upload-video" in current_url


def test_wait_for_publish_result_returns_false_when_still_on_upload_page():
    page = _FakePage("https://mp.toutiao.com/profile_v4/xigua/upload-video")
    ok, result, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_publish_result(page, timeout_seconds=0)
    )
    assert ok is False
    assert result == "超时后仍停留在发布页"
    assert "upload-video" in current_url


def test_wait_for_publish_result_returns_false_when_feedback_visible():
    page = _FakePage(
        "https://mp.toutiao.com/profile_v4/xigua/upload-video",
        feedback_text="请选择当前时间后 2小时 至 7天 进行定时发布",
    )
    ok, result, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_publish_result(page, timeout_seconds=0)
    )
    assert ok is False
    assert result.startswith("检测到错误提示：")
    assert "upload-video" in current_url


def test_wait_for_schedule_submit_result_succeeds_when_url_changes():
    page = _FakePage("https://mp.toutiao.com/profile_v4/xigua/content-manage-v2")
    status, reason, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_schedule_submit_result(page, timeout_seconds=0)
    )
    assert status == "submitted"
    assert reason == "页面已跳转"
    assert current_url.endswith("content-manage-v2")


def test_wait_for_schedule_submit_result_returns_pending_when_still_on_upload_page():
    page = _FakePage("https://mp.toutiao.com/profile_v4/xigua/upload-video")
    status, reason, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_schedule_submit_result(page, timeout_seconds=0)
    )
    assert status == "pending"
    assert reason == "页面仍停留在发布页"
    assert "upload-video" in current_url


def test_wait_for_schedule_submit_result_returns_failed_when_feedback_visible():
    page = _FakePage(
        "https://mp.toutiao.com/profile_v4/xigua/upload-video",
        feedback_text="请选择当前时间后 2小时 至 7天 进行定时发布",
    )
    status, reason, current_url = asyncio.run(
        ToutiaoPlatform._wait_for_schedule_submit_result(page, timeout_seconds=0)
    )
    assert status == "failed"
    assert reason.startswith("检测到错误提示：")
    assert "upload-video" in current_url
