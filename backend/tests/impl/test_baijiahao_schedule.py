"""Unit tests for Baijiahao scheduled-publish helpers."""
import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from impl.baijiahao.platform import BaijiahaoPlatform  # noqa: E402


class _FakeOption:
    """Minimal Playwright locator double for selector-value verification."""

    def __init__(self, value: str, fallback_value: str = ""):
        self._value = value
        self._fallback_value = fallback_value

    async def input_value(self):
        """Return the visible value held by the selector input."""
        return self._value

    async def evaluate(self, _script: str):
        """Return the selected-option text exposed by the DOM fallback."""
        return self._fallback_value


class _FakePage:
    """Minimal Playwright page double keyed by selector id."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def locator(self, selector: str):
        """Return the configured selector locator."""
        value = self._values[selector]
        if isinstance(value, tuple):
            return _FakeOption(*value)
        return _FakeOption(value)


class _SchedulePickerOption:
    """Simulate an RC Select input with an active option text."""

    def __init__(self, page):
        self._page = page

    async def click(self, force: bool = False):
        """Open the dropdown in the same way as a Playwright click."""
        _ = force

    async def focus(self):
        """Keep keyboard events routed to this dropdown."""

    async def evaluate(self, _script: str):
        """Return the text of the active virtual-list option."""
        return f"{self._page.active_value}点"


class _SchedulePickerKeyboard:
    """Move the fake active option in response to keyboard navigation."""

    def __init__(self, page):
        self._page = page
        self.presses = []

    async def press(self, key: str):
        """Record and apply the requested keyboard action."""
        self.presses.append(key)
        if key == "ArrowDown":
            self._page.active_value += 1
        elif key == "ArrowUp":
            self._page.active_value -= 1


class _SchedulePickerPage:
    """Minimal page double for an hour list whose first item is not zero."""

    def __init__(self, active_value: int):
        self.active_value = active_value
        self.keyboard = _SchedulePickerKeyboard(self)

    def locator(self, _selector: str):
        """Return the select input being controlled."""
        return _SchedulePickerOption(self)

    async def wait_for_selector(self, _selector: str, timeout: int):
        """Model an immediately available select dropdown."""
        _ = timeout

    async def wait_for_timeout(self, _timeout: int):
        """Skip real waits in unit tests."""


class BaijiahaoScheduleTests(unittest.TestCase):
    """Cover Baijiahao's date-index conversion and selector verification."""

    def test_schedule_date_option_index_starts_with_tomorrow(self):
        """Tomorrow must map to the first date-dropdown option."""
        self.assertEqual(BaijiahaoPlatform._get_schedule_date_option_index(1), 0)
        self.assertEqual(BaijiahaoPlatform._get_schedule_date_option_index(2), 1)
        self.assertEqual(BaijiahaoPlatform._get_schedule_date_option_index(7), 6)

    def test_schedule_date_option_index_rejects_out_of_range_values(self):
        """The date selector must only receive a non-today, seven-day index."""
        for delta_days in (0, 8):
            with self.subTest(delta_days=delta_days):
                with self.assertRaises(ValueError):
                    BaijiahaoPlatform._get_schedule_date_option_index(delta_days)

    def test_schedule_option_matches_tolerates_zero_padding(self):
        """Selector verification accepts equivalent numeric labels."""
        self.assertTrue(
            BaijiahaoPlatform._schedule_option_matches("07月09日", "7月9日", "date")
        )
        self.assertTrue(
            BaijiahaoPlatform._schedule_option_matches("07点", "7点", "hour")
        )
        self.assertTrue(
            BaijiahaoPlatform._schedule_option_matches("05分", "5分", "minute")
        )

    def test_schedule_selection_reads_active_option_when_input_is_empty(self):
        """The verification reads RC Select's selected option when value is blank."""
        page = _FakePage({"#select-hour": ("", "11点")})
        actual_value = asyncio.run(
            BaijiahaoPlatform._read_schedule_option_value(page, "#select-hour")
        )
        self.assertEqual(actual_value, "11点")

    def test_hour_picker_uses_displayed_value_when_first_option_is_two(self):
        """An hour list beginning at 2 must select 11, not list index 11."""
        page = _SchedulePickerPage(active_value=2)
        asyncio.run(
            BaijiahaoPlatform._pick_schedule_option(
                page,
                "#select-hour",
                "11点",
            )
        )
        self.assertEqual(page.active_value, 11)
        self.assertEqual(page.keyboard.presses.count("ArrowDown"), 9)
        self.assertEqual(page.keyboard.presses[-1], "Enter")

    def test_verify_schedule_selection_for_today_checks_only_hour_and_minute(self):
        """Today's picker has no date selector and must still verify the time."""
        page = _FakePage({"#select-hour": "17点", "#select-minute": "05分"})

        asyncio.run(
            BaijiahaoPlatform._verify_schedule_publish_selection(
                page,
                datetime(2026, 7, 29, 17, 5),
                is_today=True,
            )
        )

    def test_verify_schedule_selection_rejects_a_wrong_future_date(self):
        """A date shift must stop submission before the dialog is confirmed."""
        page = _FakePage(
            {
                "#select-date": "7月31日",
                "#select-hour": "11点",
                "#select-minute": "00分",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "date"):
            asyncio.run(
                BaijiahaoPlatform._verify_schedule_publish_selection(
                    page,
                    datetime(2026, 7, 30, 11, 0),
                    is_today=False,
                )
            )
