"""Helpers for structured publish diagnostics."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _normalize(value: Any, *, max_len: int = 240) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        text = value.replace("\r", " ").replace("\n", " ").strip()
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text
    if isinstance(value, dict):
        return {str(k): _normalize(v, max_len=max_len) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        if len(seq) > 12:
            seq = seq[:12] + [f"...(+{len(value) - 12})"]
        return [_normalize(v, max_len=max_len) for v in seq]
    return _normalize(str(value), max_len=max_len)


def log_event(logger, tag: str, **fields: Any) -> None:
    """Emit one structured diagnostic line."""
    payload = {key: _normalize(value) for key, value in fields.items()}
    logger.info("[%s] %s", tag, json.dumps(payload, ensure_ascii=False, sort_keys=True))


async def collect_visible_keyword_texts(page, keywords: list[str], *, limit: int = 8) -> list[str]:
    """Collect visible texts that contain any of the given keywords."""
    try:
        return await page.evaluate(
            """(payload) => {
                const keywords = (payload?.keywords || []).filter(Boolean);
                const limit = payload?.limit || 8;
                const hits = [];
                const isVisible = (el) => {
                    const style = window.getComputedStyle(el);
                    if (!style || style.visibility === "hidden" || style.display === "none") {
                        return false;
                    }
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const elements = Array.from(document.querySelectorAll("body *"));
                for (const el of elements) {
                    if (!isVisible(el)) continue;
                    const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
                    if (!text || text.length > 200) continue;
                    if (!keywords.some((kw) => text.includes(kw))) continue;
                    if (!hits.includes(text)) {
                        hits.push(text);
                    }
                    if (hits.length >= limit) break;
                }
                return hits;
            }""",
            {"keywords": keywords, "limit": limit},
        )
    except Exception:
        return []
