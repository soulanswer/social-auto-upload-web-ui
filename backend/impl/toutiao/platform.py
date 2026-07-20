"""
今日头条平台实现 — 100% CloakBrowser。

所有浏览器操作通过 ``BasePlatform.create_browser()`` /
``BasePlatform.create_context()`` 委托给 CloakBrowser（隐身 Chromium）。

创作中心地址：https://mp.toutiao.com/profile_v4/index
视频发布地址：https://mp.toutiao.com/profile_v4/xigua/upload-video
"""

import asyncio
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue

from util._logger import bind_account_name, get_channel_logger
from util.publish_debug import log_event
from conf import BASE_DIR

from .._browser import create_browser_sync, create_context_sync
from .._utils import (
    clear_and_type,
    get_account_name_by_cookie_file,
    parse_schedule_time,
    save_login_result,
    scrape_toutiao_profile,
)
from ..base_platform import BasePlatform

logger = get_channel_logger("toutiao")


class ToutiaoPlatform(BasePlatform):
    platform_id = 13
    platform_key = "toutiao"
    platform_name = "今日头条"

    # 支持 cookie 字符串导入账号
    supports_cookie_import = True
    # 头条 cookie 全部由 mp.toutiao.com / sso.toutiao.com 下发，
    # 通配 .toutiao.com 后对创作中心和子域都生效。
    platform_cookie_domain = ".toutiao.com"

    def _parse_cookie_to_storage_state(
        self, cookie_str: str
    ) -> tuple[list[dict], list[dict]]:
        """把 'k=v; k=v' 解析为 Playwright storage_state 的 (cookies, origins)。

        - 全部 cookie 归属 ``platform_cookie_domain`` (.toutiao.com)
        - expires 给 7 天保守占位，sync_profile 跑完后 storage_state 会被
          回写为真实的 cookie（含真实 expires + localStorage）
        - localStorage 留空，由 sync_profile 自然补全
        """
        cookies: list[dict] = []
        expires = time.time() + BasePlatform._IMPORT_COOKIE_EXPIRES_SECONDS
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": self.platform_cookie_domain,
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            })
        logger.info(
            f"[toutiao] cookie 解析: {len(cookies)} 条, domain={self.platform_cookie_domain}"
        )
        return cookies, []

    # ------------------------------------------------------------------
    # login — QR code scan via CloakBrowser
    # ------------------------------------------------------------------

    async def login(self, id: str, status_queue: Queue, account_id=None) -> None:
        """Perform Toutiao login via QR code scan."""
        logger.info("=" * 60)
        logger.info("[登录] 开始今日头条登录流程")
        logger.info("=" * 60)

        browser = await self.create_browser(login_mode=True)
        success = False
        try:
            context = await self.create_context(browser)
            try:
                page = await context.new_page()
                logger.info("[登录] 正在打开头条创作中心...")
                await page.goto("https://mp.toutiao.com/profile_v4/index")
                await asyncio.sleep(3)

                # Extract QR code image
                src = None
                qr_selectors = [
                    'img[class*="qrcode"]',
                    'img[class*="qr-code"]',
                    'img[class*="QRCode"]',
                    'img[class*="scan-code"]',
                    'div[class*="qrcode"] img',
                    'div[class*="login"] img',
                    'img.web-login-scan-code__content__',
                ]
                for selector in qr_selectors:
                    try:
                        img_locator = page.locator(selector).first
                        if await img_locator.count():
                            src = await img_locator.get_attribute("src")
                            if src and (src.startswith("http") or src.startswith("data:")):
                                logger.info("[登录] 找到二维码图片，选择器: %s", selector)
                                break
                            src = None
                    except Exception:
                        continue

                if src:
                    logger.info("[登录] 二维码图片已发送到前端")
                    status_queue.put(src)
                else:
                    logger.warning("[登录] 未找到二维码图片")
                    status_queue.put(json.dumps({"error": "无法找到登录二维码"}))

                # Wait for login
                logger.info("[登录] 等待用户扫码...")
                max_wait = 300  # 5 minutes
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < max_wait:
                    try:
                        current_url = page.url
                        if "auth/page/login" not in current_url and "profile_v4" in current_url:
                            logger.info("[登录] 检测到页面跳转，登录成功!")
                            break
                        user_panel = page.locator('div.user-panel')
                        if await user_panel.count():
                            logger.info("[登录] 检测到用户面板，登录成功!")
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                # Scrape profile & save
                logger.info("[登录] 正在获取用户信息...")
                await save_login_result(
                    context,
                    page,
                    platform_id=self.platform_id,
                    platform_name=self.platform_name,
                    status_queue=status_queue,
                    scrape_fn=scrape_toutiao_profile,
                    account_id=account_id,
                )
                logger.info("[登录] 登录流程完成!")
                success = True
            finally:
                await context.close()
        finally:
            if success:
                await browser.close()

    # ------------------------------------------------------------------
    # check_cookie — verify stored cookie is still valid
    # ------------------------------------------------------------------

    async def check_cookie(self, cookie_file: str) -> bool:
        """Return True if the saved cookie file is still valid."""
        logger.info("[Cookie检查] 开始检查cookie有效性: %s", cookie_file)
        cookie_path = str(Path(BASE_DIR / "cookiesFile" / cookie_file))
        browser = await self.create_browser(headless=True)
        try:
            context = await self.create_context(browser, storage_state=cookie_path)
            try:
                page = await context.new_page()
                await page.goto(
                    "https://mp.toutiao.com/profile_v4/index",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(3)

                user_panel = page.locator('div.user-panel')
                if await user_panel.count():
                    logger.info("[Cookie检查] Cookie有效，用户面板存在")
                    return True

                logger.warning("[Cookie检查] Cookie无效，未找到用户面板")
                return False
            finally:
                await context.close()
        finally:
            await browser.close()

    # ------------------------------------------------------------------
    # sync_profile — refresh user name / avatar
    # ------------------------------------------------------------------

    async def sync_profile(self, cookie_file: str) -> tuple:
        """Sync profile info (name, avatar) from Toutiao creator centre."""
        logger.info("[同步资料] 开始同步用户资料: %s", cookie_file)
        cookie_path = str(Path(BASE_DIR / "cookiesFile" / cookie_file))
        browser = await self.create_browser(headless=True)
        try:
            context = await self.create_context(browser, storage_state=cookie_path)
            try:
                page = await context.new_page()
                try:
                    await page.goto(
                        "https://mp.toutiao.com/profile_v4/index",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                except Exception:
                    pass
                await asyncio.sleep(3)
                name, avatar = await scrape_toutiao_profile(page)
                logger.info("[同步资料] 获取到用户信息 - 昵称: %s, 头像: %s", name, avatar[:50] if avatar else "无")
                return name, avatar
            finally:
                await context.close()
        finally:
            await browser.close()

    # ------------------------------------------------------------------
    # open_creator_center — visible browser window
    # ------------------------------------------------------------------

    async def open_creator_center(self, cookie_file: str) -> None:
        """Open the Toutiao creator centre in a visible browser window."""
        logger.info("[打开创作中心] 正在打开创作中心...")
        cookie_path = str(Path(BASE_DIR / "cookiesFile" / cookie_file))
        url = "https://mp.toutiao.com/profile_v4/index"

        def _launch():
            browser = create_browser_sync(headless=False)
            try:
                context = create_context_sync(browser, storage_state=cookie_path)
                page = context.new_page()
                page.goto(url)
                logger.info("[打开创作中心] 创作中心已打开")
                try:
                    page.wait_for_event("close", timeout=0)
                except Exception:
                    pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        thread = threading.Thread(target=_launch, daemon=True)
        thread.start()

    # ------------------------------------------------------------------
    # publish_video — full Toutiao upload pipeline
    # ------------------------------------------------------------------

    async def publish_video(self, **kwargs) -> bool:
        """Publish a video to Toutiao via CloakBrowser."""
        logger.info("=" * 60)
        logger.info("[发布视频] 开始今日头条视频发布流程")
        logger.info("=" * 60)

        # 打印所有接收到的参数
        logger.info("[发布参数] 接收到的所有参数:")
        for key, value in kwargs.items():
            logger.info("[发布参数]   %s = %s (类型: %s)", key, value, type(value).__name__)

        title = kwargs.get("title", "")
        files = kwargs.get("files", [])
        tags = kwargs.get("tags", []) or []
        account_file = kwargs.get("account_file", [])
        desc = kwargs.get("desc", "")
        enableTimer = kwargs.get("enableTimer", False)
        videos_per_day = kwargs.get("videos_per_day", 1)
        daily_times = kwargs.get("daily_times")
        start_days = kwargs.get("start_days", 0)
        schedule_time_str = kwargs.get("schedule_time_str", "")
        thumbnail_landscape_path = kwargs.get("thumbnail_landscape_path", "")
        thumbnail_portrait_path = kwargs.get("thumbnail_portrait_path", "")
        # 16:9 / 9:16 次尺寸封面(头条横版视频用 16:9,竖版视频用 9:16)
        thumbnail_landscape_169_path = kwargs.get("thumbnail_landscape_169_path", "")
        thumbnail_portrait_916_path = kwargs.get("thumbnail_portrait_916_path", "")
        creation_declaration = kwargs.get("creation_declaration", "") or ""
        enable_generate_image = kwargs.get("enable_generate_image", True)
        collection_id = kwargs.get("collection_id", "")
        extend_link = kwargs.get("extend_link", False)
        extend_link_url = kwargs.get("extend_link_url", "")

        # 打印接收到的参数，用于调试
        logger.info("[参数调试] creation_declaration 原始值: %s (类型: %s)", creation_declaration, type(creation_declaration).__name__)
        logger.info("[参数调试] enable_generate_image: %s", enable_generate_image)
        logger.info("[参数调试] collection_id: %s", collection_id)
        logger.info("[参数调试] extend_link: %s (类型: %s)", extend_link, type(extend_link).__name__)
        logger.info("[参数调试] extend_link_url: %s", extend_link_url)

        # 处理作品声明：可能是字符串（逗号分隔）或列表
        if isinstance(creation_declaration, str):
            creation_declaration = [d.strip() for d in creation_declaration.split(",") if d.strip()]
        elif not isinstance(creation_declaration, list):
            creation_declaration = []

        # 打印发布参数
        logger.info("[发布参数] 标题: %s", title)
        logger.info("[发布参数] 文件数量: %d", len(files))
        logger.info("[发布参数] 标签: %s", tags)
        logger.info("[发布参数] 视频简介: %s", desc[:50] if desc else "无")
        logger.info("[发布参数] 账号数量: %d", len(account_file))
        logger.info("[发布参数] 定时发布: %s", enableTimer)
        logger.info("[发布参数] 作品声明: %s", creation_declaration)
        logger.info("[发布参数] 生成图文: %s", enable_generate_image)
        logger.info("[发布参数] 合集ID: %s", collection_id or "无")
        logger.info("[发布参数] 扩展链接: %s (URL: %s)", extend_link, extend_link_url or "无")
        logger.info("[发布参数] 横版封面: %s", thumbnail_landscape_path or "无")
        logger.info("[发布参数] 竖版封面: %s", thumbnail_portrait_path or "无")
        logger.info("[发布参数] 横版16:9封面: %s", thumbnail_landscape_169_path or "无")
        logger.info("[发布参数] 竖版9:16封面: %s", thumbnail_portrait_916_path or "无")

        # Resolve full paths
        account_paths = [str(Path(BASE_DIR / "cookiesFile" / f)) for f in account_file]
        file_paths = [str(f) for f in files]
        if thumbnail_landscape_path:
            thumbnail_landscape_path = str(thumbnail_landscape_path)
        if thumbnail_portrait_path:
            thumbnail_portrait_path = str(thumbnail_portrait_path)
        if thumbnail_landscape_169_path:
            thumbnail_landscape_169_path = str(thumbnail_landscape_169_path)
        if thumbnail_portrait_916_path:
            thumbnail_portrait_916_path = str(thumbnail_portrait_916_path)

        # Determine publish strategy and schedule times
        publish_strategy = "scheduled" if enableTimer and schedule_time_str else "immediate"
        logger.info("[发布策略] 发布策略: %s", publish_strategy)
        if schedule_time_str:
            logger.info("[发布策略] 定时发布时间: %s", schedule_time_str)

        publish_datetimes = parse_schedule_time(
            schedule_time_str,
            len(file_paths),
            enableTimer,
            videos_per_day,
            daily_times,
            start_days,
        )

        for file_index, file_path in enumerate(file_paths):
            logger.info("-" * 40)
            logger.info("[发布进度] 处理第 %d/%d 个视频: %s", file_index + 1, len(file_paths), file_path)
            for cookie_index, cookie_path in enumerate(account_paths):
                cookie_name = Path(cookie_path).name
                nick = get_account_name_by_cookie_file(cookie_name)
                with bind_account_name(nick or "-"):
                    logger.info("[发布进度] 发布到第 %d/%d 个账号 (%s)", cookie_index + 1, len(account_paths), nick or "未知")
                    ok = await self._upload_one_video(
                        title=title,
                        file_path=file_path,
                        tags=tags,
                        publish_date=publish_datetimes[file_index],
                        account_file=cookie_path,
                        publish_strategy=publish_strategy,
                        desc=desc,
                        thumbnail_landscape_path=thumbnail_landscape_path or None,
                        thumbnail_portrait_path=thumbnail_portrait_path or None,
                        thumbnail_landscape_169_path=thumbnail_landscape_169_path or None,
                        thumbnail_portrait_916_path=thumbnail_portrait_916_path or None,
                        creation_declaration=creation_declaration,
                        enable_generate_image=enable_generate_image,
                        collection_id=collection_id,
                        extend_link=extend_link,
                        extend_link_url=extend_link_url,
                    )
                    if not ok:
                        logger.error(
                            "[发布进度] 账号发布失败: file=%s account=%s",
                            file_path,
                            nick or cookie_name,
                        )
                        return False

        logger.info("=" * 60)
        logger.info("[发布视频] 视频发布流程完成!")
        logger.info("=" * 60)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _upload_one_video(
        self,
        title: str,
        file_path: str,
        tags: list,
        publish_date,
        account_file: str,
        publish_strategy: str,
        desc="",
        thumbnail_landscape_path=None,
        thumbnail_portrait_path=None,
        thumbnail_landscape_169_path=None,
        thumbnail_portrait_916_path=None,
        creation_declaration=None,
        enable_generate_image=True,
        collection_id="",
        extend_link=False,
        extend_link_url="",
    ) -> bool:
        """Upload a single video to one Toutiao account."""
        logger.info("[上传视频] 开始上传视频: %s", file_path)
        browser = await self.create_browser(headless=False)
        preserve_browser = False
        try:
            context = await self.create_context(browser, storage_state=account_file)
            try:
                page = await context.new_page()
                logger.info("[上传视频] 正在打开发布页面...")
                await page.goto(
                    "https://mp.toutiao.com/profile_v4/xigua/upload-video"
                )
                await page.wait_for_url(
                    "https://mp.toutiao.com/profile_v4/xigua/upload-video"
                )
                logger.info("[上传视频] 发布页面已打开")

                # Upload video file
                logger.info("[上传视频] 正在上传视频文件...")
                file_input = page.locator('input[type="file"][accept*="video"]')
                if not await file_input.count():
                    file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(file_path)
                logger.info("[上传视频] 视频文件已选择，等待上传完成...")

                # Wait for upload to complete
                max_wait = 14400  # 4 hours for large files (no timeout limit)
                start_time = asyncio.get_event_loop().time()
                upload_complete = False
                last_progress = ""
                while (asyncio.get_event_loop().time() - start_time) < max_wait:
                    try:
                        success_text = page.locator('span.percent:has-text("上传成功")')
                        if await success_text.count():
                            upload_complete = True
                            logger.info(
                                "[上传视频] 上传完成摘要: 耗时 %.1f 秒, 原因=检测到上传成功, 页面=%s",
                                round(asyncio.get_event_loop().time() - start_time, 1),
                                page.url,
                            )
                            logger.info("[上传视频] 视频上传成功!")
                            break
                        # 打印上传进度
                        progress_text = page.locator('span.percent')
                        if await progress_text.count():
                            current_progress = await progress_text.first.text_content()
                            if current_progress and current_progress != last_progress:
                                logger.info("[上传视频] %s", current_progress)
                                last_progress = current_progress
                    except Exception:
                        pass
                    await asyncio.sleep(2)

                if not upload_complete:
                    logger.error(
                        "[上传视频] 上传超时摘要: 已等待 %d 秒, 页面=%s",
                        max_wait,
                        page.url,
                    )
                    logger.error("[上传视频] 视频上传超时! 已等待 %d 秒", max_wait)
                    preserve_browser = True
                    return False

                await asyncio.sleep(2)

                # Determine if this is a portrait video
                is_portrait = False
                try:
                    poster_editor = page.locator('div.xigua-poster-editor.portrait')
                    if await poster_editor.count():
                        is_portrait = True
                        logger.info("[视频类型] 检测到竖版视频")
                    else:
                        logger.info("[视频类型] 检测到横版视频")
                except Exception:
                    logger.info("[视频类型] 默认为横版视频")

                # Fill title (max 30 chars)
                logger.info("[填写标题] 标题: %s", title[:30])
                title_input = page.locator('input[placeholder*="请输入"][placeholder*="字符"]')
                if not await title_input.count():
                    title_input = page.locator('.article-title-wrap input')
                await title_input.wait_for(state="visible", timeout=10000)
                await title_input.fill(title[:30])
                logger.info("[填写标题] 标题填写完成")

                # Fill video description/简介 (横版视频才有)
                if not is_portrait:
                    logger.info("[填写简介] 开始填写视频简介...")
                    if desc:
                        logger.info("[填写简介] 简介内容: %s...", desc[:50])
                        # 头条的视频简介是一个 textarea 或 contenteditable div
                        desc_selectors = [
                            'div.video-form-item.form-item-desc div[contenteditable="true"]',
                            'div.form-item-desc textarea',
                            'div.form-item-desc div[contenteditable]',
                            '.video-form-item-wrapper textarea[placeholder*="简介"]',
                            '.video-form-item-wrapper div[contenteditable="true"]',
                        ]
                        desc_filled = False
                        for selector in desc_selectors:
                            try:
                                desc_el = page.locator(selector).first
                                if await desc_el.count():
                                    await desc_el.click()
                                    await asyncio.sleep(0.5)
                                    # 清空后输入(跨平台:Mac 用 Cmd+A,其他用 Ctrl+A)
                                    await clear_and_type(page, desc[:400])
                                    desc_filled = True
                                    logger.info("[填写简介] 视频简介填写成功!")
                                    break
                            except Exception as e:
                                logger.debug("[填写简介] 选择器 %s 失败: %s", selector, e)
                                continue

                        if not desc_filled:
                            # 尝试通过 placeholder 找
                            try:
                                desc_by_placeholder = page.get_by_placeholder("请输入视频简介")
                                if await desc_by_placeholder.count():
                                    await desc_by_placeholder.fill(desc[:400])
                                    logger.info("[填写简介] 通过 placeholder 填写成功")
                                    desc_filled = True
                            except Exception:
                                pass

                        if not desc_filled:
                            logger.warning("[填写简介] 未找到视频简介输入框!")
                    else:
                        logger.info("[填写简介] 无视频简介")
                else:
                    logger.info("[填写简介] 竖版视频不支持视频简介")

                # Fill tags (max 10)
                if tags:
                    logger.info("[填写标签] 开始填写标签: %s", tags[:10])
                    await self._fill_tags(page, tags[:10])
                    logger.info("[填写标签] 标签填写完成")
                else:
                    logger.info("[填写标签] 无标签")

                # Set thumbnail/cover
                if (thumbnail_landscape_path or thumbnail_portrait_path
                        or thumbnail_landscape_169_path or thumbnail_portrait_916_path):
                    logger.info("[设置封面] 开始设置封面...")
                    await self._set_thumbnail(
                        page,
                        thumbnail_landscape_path,
                        thumbnail_portrait_path,
                        thumbnail_landscape_169_path,
                        thumbnail_portrait_916_path,
                        is_portrait,
                    )
                    logger.info("[设置封面] 封面设置完成")
                else:
                    logger.info("[设置封面] 无自定义封面")

                # Set creation declaration (multi-select)
                if creation_declaration:
                    logger.info("[设置声明] 开始设置作品声明: %s", creation_declaration)
                    await self._set_creation_declaration(page, creation_declaration)
                    logger.info("[设置声明] 作品声明设置完成")
                else:
                    logger.info("[设置声明] 无作品声明")

                # Toggle video-to-image generation
                logger.info("[生成图文] 设置视频生成图文: %s", enable_generate_image)
                await self._toggle_generate_image(page, enable_generate_image)
                logger.info("[生成图文] 视频生成图文设置完成")

                # Set collection (landscape only)
                if collection_id and not is_portrait:
                    logger.info("[设置合集] 开始设置合集: %s", collection_id)
                    await self._set_collection(page, collection_id)
                    logger.info("[设置合集] 合集设置完成")
                elif collection_id and is_portrait:
                    logger.info("[设置合集] 竖版视频不支持合集功能")
                else:
                    logger.info("[设置合集] 无合集")

                # Set extend link (landscape only)
                if extend_link and not is_portrait:
                    logger.info("[扩展链接] 开始设置扩展链接: %s", extend_link_url)
                    await self._toggle_extend_link(page, extend_link_url)
                    logger.info("[扩展链接] 扩展链接设置完成")
                elif extend_link and is_portrait:
                    logger.info("[扩展链接] 竖版视频不支持扩展链接")
                else:
                    logger.info("[扩展链接] 无扩展链接")

                # Schedule if needed
                if publish_strategy == "scheduled" and publish_date != 0:
                    logger.info("[定时发布] 开始设置定时发布时间: %s", publish_date)
                    schedule_ok = await self._set_schedule_time(page, publish_date)
                    if not schedule_ok:
                        logger.error("[定时发布] 定时发布时间设置失败，终止本次发布")
                        preserve_browser = True
                        return False
                    logger.info("[定时发布] 定时发布时间设置完成")
                    schedule_status, schedule_reason, schedule_url = await self._wait_for_schedule_submit_result(page)
                    if schedule_status == "submitted":
                        logger.info(
                            "[定时发布] 弹窗确认后已直接提交成功! 判定=%s, 当前页面: %s",
                            schedule_reason,
                            schedule_url,
                        )
                        try:
                            await context.storage_state(path=account_file)
                            logger.info("[发布] Cookie状态已更新")
                        except Exception as e:
                            logger.warning("[发布] Cookie状态更新失败(非致命): %s", e)
                        return True
                    if schedule_status == "failed":
                        logger.error(
                            "[定时发布] 弹窗确认后检测到失败! 判定=%s, 当前页面: %s",
                            schedule_reason,
                            schedule_url,
                        )
                        preserve_browser = True
                        return False
                    logger.info(
                        "[定时发布] 弹窗确认后页面仍在发布页，继续查找主发布按钮。当前页面: %s",
                        schedule_url,
                    )

                # Click publish
                logger.info("[发布] 正在点击发布按钮...")
                publish_btn, publish_selector = await self._wait_for_publish_button(page)
                if publish_btn is None:
                    logger.error("[发布] 未找到发布按钮，终止本次发布。当前页面: %s", page.url)
                    preserve_browser = True
                    return False
                logger.info("[发布] 命中发布按钮选择器: %s", publish_selector)
                await publish_btn.click()
                logger.info("[发布] 发布按钮已点击，等待页面跳转或成功提示...")

                publish_success, result, current_url = await self._wait_for_publish_result(page)
                if publish_success:
                    logger.info("[发布] 视频发布成功! 判定=%s, 当前页面: %s", result, current_url)
                else:
                    logger.error("[发布] 发布失败! 判定=%s, 当前页面: %s", result, current_url)
                    preserve_browser = True

                # Save updated cookie state
                try:
                    await context.storage_state(path=account_file)
                    logger.info("[发布] Cookie状态已更新")
                except Exception as e:
                    logger.warning("[发布] Cookie状态更新失败(非致命): %s", e)
                return publish_success
            except asyncio.CancelledError:
                raise
            except Exception as e:
                preserve_browser = True
                logger.exception("[发布] 自动化流程异常，保留当前页面供人工检查: %s", e)
                return False
            finally:
                if preserve_browser:
                    logger.warning("[发布] 未确认成功，保留当前页面供人工检查")
                else:
                    await context.close()
        finally:
            if preserve_browser:
                logger.warning("[发布] 未确认成功，保留浏览器窗口供人工检查")
            else:
                await self.close_browser(browser, is_close_by_code=True)

    @staticmethod
    async def _find_first_visible(page, selectors: list[str], timeout_ms: int = 2000):
        """Return the first visible locator for the given selector list."""
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.wait_for(state="visible", timeout=timeout_ms)
                if await locator.is_enabled():
                    return locator, selector
            except Exception:
                continue
        return None, None

    @staticmethod
    async def _read_text(locator) -> str:
        """Safely read locator text."""
        try:
            return ((await locator.text_content()) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _normalize_day_text(value: str) -> str:
        match = re.search(r"(\d{1,2})月(\d{1,2})日", (value or "").strip())
        if not match:
            return (value or "").strip()
        return f"{int(match.group(1)):02d}月{int(match.group(2)):02d}日"

    @staticmethod
    def _normalize_hour_text(value: str) -> str:
        match = re.search(r"\d+", (value or "").strip())
        if not match:
            return (value or "").strip()
        return str(int(match.group(0)))

    @staticmethod
    def _normalize_minute_text(value: str) -> str:
        match = re.search(r"\d+", (value or "").strip())
        if not match:
            return (value or "").strip()
        return f"{int(match.group(0)):02d}"

    @staticmethod
    def _parse_timer_summary_text(value: str) -> datetime | None:
        text = (value or "").strip()
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M")
        except Exception:
            return None

    @staticmethod
    def _validate_schedule_publish_date(
        publish_date: datetime,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Validate Toutiao scheduled publish time window."""
        if now is None:
            now = datetime.now().replace(second=0, microsecond=0)
        target = publish_date.replace(second=0, microsecond=0)
        min_allowed = now + timedelta(hours=2)
        max_allowed = now + timedelta(days=7)
        if target < min_allowed:
            return False, f"目标时间早于当前时间+2小时: target={target:%Y-%m-%d %H:%M}, min={min_allowed:%Y-%m-%d %H:%M}"
        if target > max_allowed:
            return False, f"目标时间晚于当前时间+7天: target={target:%Y-%m-%d %H:%M}, max={max_allowed:%Y-%m-%d %H:%M}"
        return True, ""

    @staticmethod
    async def _read_schedule_modal_state(modal) -> dict:
        """Read current selected values from schedule modal."""
        day_raw = await ToutiaoPlatform._read_text(
            modal.locator(".day-select .byte-select-view-value").first
        )
        hour_raw = await ToutiaoPlatform._read_text(
            modal.locator(".hour-select .byte-select-view-value").first
        )
        minute_raw = await ToutiaoPlatform._read_text(
            modal.locator(".minute-select .byte-select-view-value").first
        )
        summary = await ToutiaoPlatform._read_text(
            modal.locator(".timer-time").first
        )
        summary_dt = ToutiaoPlatform._parse_timer_summary_text(summary)
        return {
            "day_raw": day_raw,
            "hour_raw": hour_raw,
            "minute_raw": minute_raw,
            "summary": summary,
            "day_norm": ToutiaoPlatform._normalize_day_text(day_raw),
            "hour_norm": ToutiaoPlatform._normalize_hour_text(hour_raw),
            "minute_norm": ToutiaoPlatform._normalize_minute_text(minute_raw),
            "summary_dt": summary_dt,
        }

    @staticmethod
    async def _click_visible_option_by_value(
        page,
        field_label: str,
        expected_value: str,
        normalizer,
    ) -> tuple[bool, str]:
        """Click a visible dropdown option by normalized text."""
        expected_norm = normalizer(expected_value)
        option_locator = page.locator(".byte-select-option, [role='option']")
        count = await option_locator.count()
        visible_texts: list[str] = []
        for idx in range(count):
            option = option_locator.nth(idx)
            try:
                if not await option.is_visible():
                    continue
                raw_text = ((await option.text_content()) or "").strip()
                if not raw_text:
                    continue
                visible_texts.append(raw_text)
                if normalizer(raw_text) != expected_norm:
                    continue
                await option.click()
                logger.info(
                    "[定时发布][%s] 已点击步进选项: raw=%s normalized=%s",
                    field_label,
                    raw_text,
                    expected_norm,
                )
                return True, raw_text
            except Exception:
                continue
        logger.warning(
            "[定时发布][%s] 未找到目标选项: expected=%s visible=%s",
            field_label,
            expected_norm,
            visible_texts[:20],
        )
        return False, ""

    @staticmethod
    async def _wait_modal_hidden(modal, timeout_seconds: int = 5) -> bool:
        """Wait until schedule modal is hidden."""
        deadline = time.perf_counter() + timeout_seconds
        while time.perf_counter() < deadline:
            try:
                if await modal.count() == 0:
                    return True
                if not await modal.first.is_visible():
                    return True
            except Exception:
                return True
            await asyncio.sleep(0.2)
        return False

    @staticmethod
    async def _select_schedule_value(
        page,
        modal,
        *,
        field_label: str,
        select_selector: str,
        value_selector: str,
        target_display: str,
        normalizer,
    ) -> bool:
        """Open one select and directly choose the target value."""
        current_value = normalizer(
            await ToutiaoPlatform._read_text(modal.locator(value_selector).first)
        )
        target_norm = normalizer(target_display)
        logger.info(
            "[定时发布][%s] 当前值=%s，目标值=%s",
            field_label,
            current_value,
            target_norm,
        )
        if current_value == target_norm:
            logger.info("[定时发布][%s] 当前值已匹配目标值，跳过选择", field_label)
            return True

        logger.info("[定时发布][%s] 打开下拉并直接选择目标值: %s", field_label, target_norm)
        await modal.locator(select_selector).first.click()
        await asyncio.sleep(0.5)
        ok, clicked_text = await ToutiaoPlatform._click_visible_option_by_value(
            page,
            field_label,
            target_display,
            normalizer,
        )
        if not ok:
            logger.warning("[定时发布][%s] 未能直接选中目标值: %s", field_label, target_norm)
            return False
        await asyncio.sleep(0.5)
        actual_value = normalizer(
            await ToutiaoPlatform._read_text(modal.locator(value_selector).first)
        )
        logger.info(
            "[定时发布][%s] 选择结果: 点击=%s，实际值=%s，目标值=%s",
            field_label,
            clicked_text,
            actual_value,
            target_norm,
        )
        return actual_value == target_norm

    @staticmethod
    async def _wait_for_publish_button(page, timeout_seconds: int = 10):
        """Wait for Toutiao publish button to become visible after schedule modal closes."""
        selectors = [
            'button[data-wkswitch="disable-auto-publish"].action-footer-btn.submit:has-text("发布")',
            'button.action-footer-btn.submit:has-text("发布")',
            'button[data-wkswitch="disable-auto-publish"]:has-text("发布")',
            'button.action-footer-btn.submit',
            'button:has-text("发布")',
            '[role="button"]:has-text("发布")',
        ]
        deadline = time.perf_counter() + timeout_seconds
        last_url = page.url
        while time.perf_counter() < deadline:
            publish_btn, publish_selector = await ToutiaoPlatform._find_first_visible(
                page,
                selectors,
                timeout_ms=800,
            )
            if publish_btn is not None:
                return publish_btn, publish_selector
            last_url = page.url
            await asyncio.sleep(0.3)
        logger.warning("[发布] 等待发布按钮超时，当前页面: %s", last_url)
        return None, None

    @staticmethod
    async def _wait_for_schedule_submit_result(
        page,
        timeout_seconds: int = 12,
    ) -> tuple[str, str, str]:
        """Wait after schedule modal confirmation to see whether submit already succeeded."""
        success_texts = ("发布成功", "定时发布成功", "预约成功", "提交成功")
        for _ in range(max(timeout_seconds * 2, 1)):
            current_url = page.url
            if "upload-video" not in current_url:
                return "submitted", "页面已跳转", current_url

            success_text = await ToutiaoPlatform._find_visible_success_text(page, success_texts)
            if success_text:
                return "submitted", f"检测到成功提示：{success_text}", current_url

            feedback = await ToutiaoPlatform._extract_publish_feedback(page)
            if feedback:
                return "failed", f"检测到错误提示：{feedback}", current_url

            await asyncio.sleep(0.5)

        return "pending", "页面仍停留在发布页", page.url

    @staticmethod
    async def _step_schedule_day(page, modal, current_dt: datetime, target_dt: datetime) -> tuple[bool, datetime]:
        """Move schedule day by a single-day step toward target."""
        direction = 1 if current_dt.date() < target_dt.date() else -1
        next_dt = current_dt + timedelta(days=direction)
        expected_day = next_dt.strftime("%m月%d日")
        step_started_at = time.perf_counter()
        logger.info(
            "[定时发布][日期] 步进开始: current=%s target=%s next=%s step=%+d天",
            current_dt.strftime("%Y-%m-%d"),
            target_dt.strftime("%Y-%m-%d"),
            next_dt.strftime("%Y-%m-%d"),
            direction,
        )
        log_event(
            logger,
            "SCHEDULE_DAY_STEP_START",
            current=current_dt.strftime("%Y-%m-%d"),
            target=target_dt.strftime("%Y-%m-%d"),
            expected=next_dt.strftime("%Y-%m-%d"),
            step=direction,
            current_url=page.url,
        )
        await modal.locator(".day-select.byte-select").first.click()
        await asyncio.sleep(0.5)
        ok, clicked_text = await ToutiaoPlatform._click_visible_option_by_value(
            page,
            "日期",
            expected_day,
            ToutiaoPlatform._normalize_day_text,
        )
        elapsed_ms = int((time.perf_counter() - step_started_at) * 1000)
        log_event(
            logger,
            "SCHEDULE_DAY_STEP_RESULT",
            expected=expected_day,
            clicked=clicked_text,
            result=ok,
            current_url=page.url,
            elapsed_ms=elapsed_ms,
        )
        if not ok:
            return False, current_dt
        await asyncio.sleep(0.5)
        state = await ToutiaoPlatform._read_schedule_modal_state(modal)
        actual_day = state["day_norm"]
        actual_summary = state["summary"]
        actual_dt = state["summary_dt"]
        verify_ok = (
            actual_day == ToutiaoPlatform._normalize_day_text(expected_day)
            and actual_dt is not None
            and actual_dt.date() == next_dt.date()
        )
        logger.info(
            "[定时发布][日期] 步进校验: expected=%s actual=%s summary=%s result=%s",
            ToutiaoPlatform._normalize_day_text(expected_day),
            actual_day,
            actual_summary,
            "ok" if verify_ok else "failed",
        )
        log_event(
            logger,
            "SCHEDULE_DAY_STEP_VERIFY",
            expected=ToutiaoPlatform._normalize_day_text(expected_day),
            actual=actual_day,
            summary=actual_summary,
            result=verify_ok,
            current_url=page.url,
        )
        return verify_ok, (actual_dt or current_dt)

    @staticmethod
    async def _step_schedule_numeric(
        page,
        modal,
        *,
        field_label: str,
        current_value: int,
        target_value: int,
        select_selector: str,
        state_key: str,
        normalizer,
        summary_attr: str,
    ) -> tuple[bool, int]:
        """Move hour/minute value by a single unit toward target."""
        direction = 1 if current_value < target_value else -1
        next_value = current_value + direction
        expected_display = f"{next_value:02d}" if field_label == "分钟" else str(next_value)
        step_started_at = time.perf_counter()
        logger.info(
            "[定时发布][%s] 步进开始: current=%s target=%s next=%s step=%+d",
            field_label,
            current_value,
            target_value,
            next_value,
            direction,
        )
        log_event(
            logger,
            f"SCHEDULE_{'HOUR' if field_label == '小时' else 'MINUTE'}_STEP_START",
            current=current_value,
            target=target_value,
            expected=next_value,
            step=direction,
            current_url=page.url,
        )
        await modal.locator(select_selector).first.click()
        await asyncio.sleep(0.5)
        ok, clicked_text = await ToutiaoPlatform._click_visible_option_by_value(
            page,
            field_label,
            expected_display,
            normalizer,
        )
        elapsed_ms = int((time.perf_counter() - step_started_at) * 1000)
        log_event(
            logger,
            f"SCHEDULE_{'HOUR' if field_label == '小时' else 'MINUTE'}_STEP_RESULT",
            expected=expected_display,
            clicked=clicked_text,
            result=ok,
            current_url=page.url,
            elapsed_ms=elapsed_ms,
        )
        if not ok:
            return False, current_value
        await asyncio.sleep(0.5)
        state = await ToutiaoPlatform._read_schedule_modal_state(modal)
        actual_display = state[state_key]
        actual_summary_dt = state["summary_dt"]
        actual_summary_value = getattr(actual_summary_dt, summary_attr) if actual_summary_dt else None
        expected_norm = normalizer(expected_display)
        verify_ok = actual_display == expected_norm and actual_summary_value == next_value
        logger.info(
            "[定时发布][%s] 步进校验: expected=%s actual=%s summary=%s result=%s",
            field_label,
            expected_norm,
            actual_display,
            state["summary"],
            "ok" if verify_ok else "failed",
        )
        log_event(
            logger,
            f"SCHEDULE_{'HOUR' if field_label == '小时' else 'MINUTE'}_STEP_VERIFY",
            expected=expected_norm,
            actual=actual_display,
            summary=state["summary"],
            result=verify_ok,
            current_url=page.url,
        )
        return verify_ok, (actual_summary_value if actual_summary_value is not None else current_value)

    @staticmethod
    async def _find_visible_success_text(page, success_texts: tuple[str, ...]) -> str:
        """Return the first visible success text."""
        for text in success_texts:
            try:
                locator = page.get_by_text(text, exact=False).first
                if await locator.count() and await locator.is_visible():
                    return text
            except Exception:
                continue
        return ""

    @staticmethod
    async def _extract_publish_feedback(page) -> str:
        """Read visible validation/error feedback text after clicking publish."""
        selectors = (
            "[role='alert']",
            ".byte-message-content",
            ".byte-message-notice-content",
            ".arco-message-content",
            ".byte-form-item-status-error",
            ".byte-form-item-feedback",
            ".form-item-error",
            "[class*='error-message']",
            "[class*='error-tip']",
        )
        failure_keywords = ("失败", "错误", "请", "至少", "最多", "2小时", "7天", "非法", "不能为空")
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for idx in range(min(count, 10)):
                    item = locator.nth(idx)
                    if not await item.is_visible():
                        continue
                    text = ((await item.text_content()) or "").strip()
                    if text and any(keyword in text for keyword in failure_keywords):
                        return text
            except Exception:
                continue
        return ""

    @staticmethod
    async def _wait_for_publish_accept(
        page,
        publish_selector: str,
        timeout_seconds: int = 5,
    ) -> tuple[bool, str]:
        """Wait for any immediate post-click acknowledgment."""
        success_texts = ("发布成功", "定时发布成功", "预约成功", "提交成功")
        for _ in range(max(timeout_seconds * 2, 1)):
            current_url = page.url
            if "upload-video" not in current_url:
                return True, "url_changed"
            success_text = await ToutiaoPlatform._find_visible_success_text(page, success_texts)
            if success_text:
                return True, f"success_text:{success_text}"
            feedback = await ToutiaoPlatform._extract_publish_feedback(page)
            if feedback:
                return False, f"feedback_error:{feedback}"
            try:
                button = page.locator(publish_selector).first
                if await button.count() == 0:
                    return True, "button_hidden"
                classes = (await button.get_attribute("class") or "").lower()
                if "loading" in classes or "disabled" in classes:
                    return True, "button_loading_or_disabled"
                if not await button.is_enabled():
                    return True, "button_disabled"
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False, "no_immediate_feedback"

    @staticmethod
    async def _wait_for_publish_result(page, timeout_seconds: int = 30) -> tuple[bool, str, str]:
        """Wait for a definitive publish success signal."""
        success_texts = ("发布成功", "定时发布成功", "预约成功", "提交成功")
        for _ in range(max(timeout_seconds, 0) + 1):
            current_url = page.url
            if "upload-video" not in current_url:
                return True, "页面已跳转", current_url

            success_text = await ToutiaoPlatform._find_visible_success_text(page, success_texts)
            if success_text:
                return True, f"检测到成功提示：{success_text}", current_url

            feedback = await ToutiaoPlatform._extract_publish_feedback(page)
            if feedback:
                return False, f"检测到错误提示：{feedback}", current_url

            if timeout_seconds <= 0:
                break
            await asyncio.sleep(1)
            timeout_seconds -= 1

        return False, "超时后仍停留在发布页", page.url

    # ------------------------------------------------------------------
    # Helper: fill tags
    # ------------------------------------------------------------------

    @staticmethod
    async def _fill_tags(page, tags: list):
        """Fill hashtags (max 10) with dropdown selection."""
        logger.info("[标签] 开始填写 %d 个标签", len(tags))
        try:
            tag_input = page.locator('.hash-tag-editor input, .arco-input-tag-input')
            if not await tag_input.count():
                logger.warning("[标签] 未找到标签输入框!")
                return

            for i, tag in enumerate(tags[:10]):
                if not tag:
                    continue
                logger.info("[标签] 填写第 %d 个标签: %s", i + 1, tag)
                await tag_input.click()
                await asyncio.sleep(0.5)

                await page.keyboard.insert_text(tag)
                await asyncio.sleep(1.5)

                # Try to select from dropdown
                try:
                    dropdown_items = page.locator('.arco-dropdown-menu-item, [role="menuitem"]')
                    count = await dropdown_items.count()
                    if count > 0:
                        for j in range(count):
                            item = dropdown_items.nth(j)
                            item_text = (await item.text_content() or '').strip()
                            if tag in item_text:
                                await item.click()
                                logger.info("[标签] 从下拉列表选择: %s", item_text)
                                break
                        else:
                            await dropdown_items.first.click()
                            logger.info("[标签] 选择第一个下拉选项")
                    else:
                        await page.keyboard.press("Enter")
                        logger.info("[标签] 按 Enter 确认标签")
                except Exception:
                    await page.keyboard.press("Enter")
                    logger.info("[标签] 按 Enter 确认标签 (fallback)")

                await asyncio.sleep(0.5)

            logger.info("[标签] 所有标签填写完成")
        except Exception as e:
            logger.error("[标签] 填写标签失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: set thumbnail (cover images)
    # ------------------------------------------------------------------

    @staticmethod
    async def _set_thumbnail(
        page,
        thumbnail_landscape_path=None,
        thumbnail_portrait_path=None,
        thumbnail_landscape_169_path=None,
        thumbnail_portrait_916_path=None,
        is_portrait=False,
    ):
        """Set video cover/thumbnail.

        封面尺寸选择策略(按视频方向):
        - 横版视频 → 优先 16:9 横封面(thumbnail_landscape_169_path),
                    没有则回退到 4:3 横封面(thumbnail_landscape_path)
        - 竖版视频 → 优先 9:16 竖封面(thumbnail_portrait_916_path),
                    没有则回退到 3:4 竖封面(thumbnail_portrait_path)
        两者都为空才跳过。
        """
        if (not thumbnail_landscape_path and not thumbnail_portrait_path
                and not thumbnail_landscape_169_path and not thumbnail_portrait_916_path):
            return

        logger.info("[封面] 开始设置视频封面")
        try:
            cover_editor = page.locator('div.xigua-poster-editor')
            if not await cover_editor.count():
                logger.warning("[封面] 未找到封面编辑器!")
                return

            await cover_editor.click()
            await asyncio.sleep(2)
            logger.info("[封面] 封面编辑器已打开")

            # Switch to "本地上传" tab
            upload_tab = page.locator('li:has-text("本地上传")')
            if await upload_tab.count():
                await upload_tab.click()
                await asyncio.sleep(1)
                logger.info("[封面] 已切换到本地上传")

            # Find hidden file input and upload
            cover_input = page.locator('input[type="file"][accept*="image"]')
            if not await cover_input.count():
                cover_input = page.locator('input[type="file"]').first

            # 按视频方向优先选 16:9 / 9:16 新尺寸,没有才回退到 4:3 / 3:4
            if is_portrait:
                thumb_path = (thumbnail_portrait_916_path
                              or thumbnail_portrait_path
                              or thumbnail_landscape_path)
                size_label = "9:16 竖封面" if thumbnail_portrait_916_path else "3:4 竖封面(回退)"
            else:
                thumb_path = (thumbnail_landscape_169_path
                              or thumbnail_landscape_path
                              or thumbnail_portrait_path)
                size_label = "16:9 横封面" if thumbnail_landscape_169_path else "4:3 横封面(回退)"

            logger.info("[封面] 上传封面图片[%s]: %s", size_label, thumb_path)
            await cover_input.set_input_files(thumb_path)
            await asyncio.sleep(2)

            # 上传后头条会进入裁剪/预览页,依次尝试点击「完成裁剪」「确定」按钮。
            # 注意:不能用 class 定位(antd/头条自有 hash 会漂移),改用
            # button + 文字精确定位 + role=button 兜底。
            #
            # 规则:如果上传的图片就是头条规定比例(16:9 / 9:16),
            # 头条不会弹「完成裁剪」,直接显示「确定」→ 这时必须立刻跳过
            # 「完成裁剪」步骤,不能傻等。
            async def _click_btn_by_text(text, wait_timeout_ms=5000):
                """按可见文字点击按钮(button / [role=button]),不依赖 class。

                先用 count() 毫秒级探测元素是否存在,不存在立即返回 False
                (避免 wait_for 把整个 timeout 浪费在等一个不会出现的按钮上)。
                存在才 wait_for + click。返回 True/False。
                """
                candidates = [
                    f"button:has-text('{text}')",
                    f"[role='button']:has-text('{text}')",
                ]
                for sel in candidates:
                    loc = page.locator(sel).first
                    # 毫秒级探测:不存在直接跳下一个,不浪费时间
                    if await loc.count() == 0:
                        continue
                    try:
                        await loc.wait_for(state="visible", timeout=wait_timeout_ms)
                        if await loc.is_enabled():
                            await loc.click(timeout=wait_timeout_ms)
                            logger.info("[封面] 已点击「%s」(选择器=%s)", text, sel)
                            return True
                    except Exception:
                        continue
                logger.info("[封面] 未找到「%s」按钮,跳过", text)
                return False

            # 1. 完成裁剪(可选):图片符合规定比例时不会出现,直接跳过
            #    用 count() 探测,不存在立即跳过,不会卡 3 秒
            if await page.locator("button:has-text('完成裁剪')").count() > 0:
                await _click_btn_by_text("完成裁剪", wait_timeout_ms=5000)
                await asyncio.sleep(1)
            else:
                logger.info("[封面] 未出现「完成裁剪」(图片符合规定比例),直接点确定")

            # 2. 确定(必点,关闭封面编辑弹窗)
            ok = await _click_btn_by_text("确定", wait_timeout_ms=8000)
            if not ok:
                logger.warning("[封面] 未点到「确定」按钮,封面可能未生效")
            await asyncio.sleep(2)

            # 3. 二次确认对话框(可选)
            #    DOM 结构(用户提供的真实 DOM):
            #      <div class="m-xigua-dialog m-modal m-dialog-edit">  ← 弹窗容器
            #        <div class="mask"></div>                          ← 遮罩
            #        <div class="m-content">
            #          <svg class="close">...</svg>
            #          <div class="content">
            #            <div class="body">完成后无法继续编辑,是否确定完成？</div>
            #            <div class="footer">
            #              <button class="m-button">取消</button>
            #              <button class="m-button red">确定</button>   ← 要点这个
            #            </div>
            #          </div>
            #        </div>
            #      </div>
            #
            # 定位策略(全程不依赖 class):
            # 1) 先用 body 文字"完成后无法继续编辑"找到对话框(产品文案稳定)
            # 2) 在该对话框范围内找 footer 里第 2 个 button(第 1 个是取消,第 2 个是确定)
            #    不能用 button:has-text('确定') —— 主弹窗也含同名按钮会误匹配
            # 3) 直接用 force=True 点击(避免被遮罩层/动画拦截)
            try:
                # 用 XPath 精确定位二次确认弹窗的「确定」按钮:
                # 1) 找同时含「完成后无法继续编辑」+「取消」+「确定」的元素(会匹配祖先链)
                # 2) 排除有更深层匹配的祖先(not(.//*[...])),只留最深一层的弹窗容器
                #    避免 Playwright 把 <html>/<body> 当匹配导致点中遮罩层
                # 3) 在该容器内定位同时含「取消」「确定」的 footer 的确定按钮
                #    (主弹窗只有「确定」无「取消」,不会误匹配)
                cond = (
                    ".//*[contains(normalize-space(.), '完成后无法继续编辑')] "
                    "and .//button[normalize-space()='取消'] "
                    "and .//button[normalize-space()='确定']"
                )
                dialog_ok_btn = page.locator(
                    f"xpath=//*[{cond} and not(.//*[{cond}])]"
                    "//div[button[normalize-space()='取消'] "
                    "and button[normalize-space()='确定']]"
                    "//button[normalize-space()='确定']"
                ).first
                if await dialog_ok_btn.count() > 0:
                    try:
                        await dialog_ok_btn.wait_for(state="visible", timeout=5000)
                    except Exception:
                        # 弹窗动画中可能 is_visible=False,继续 force click
                        pass
                    await dialog_ok_btn.click(force=True, timeout=5000)
                    logger.info("[封面] 已点击二次确认弹窗「确定」")
                    await asyncio.sleep(1)
                else:
                    logger.info("[封面] 未出现二次确认弹窗,流程结束")
            except Exception as e:
                logger.warning("[封面] 二次确认弹窗处理失败: %s", e)

            logger.info("[封面] 封面设置完成")
        except Exception as e:
            logger.error("[封面] 设置封面失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: set creation declaration (multi-select)
    # ------------------------------------------------------------------

    @staticmethod
    async def _set_creation_declaration(page, declarations: list):
        """Set creation declarations (multi-select checkboxes)."""
        logger.info("[声明] 开始设置作品声明: %s", declarations)
        try:
            for i, decl in enumerate(declarations):
                if not decl:
                    continue
                logger.info("[声明] 选择第 %d/%d 个声明: %s", i + 1, len(declarations), decl)

                # 精确匹配：通过 inner-text 文本找到 checkbox，然后点击
                # DOM 结构: label > span.byte-checkbox-wrapper > span.byte-checkbox-inner-text
                checkbox_text = page.locator(f'span.byte-checkbox-inner-text:has-text("{decl}")').first
                if await checkbox_text.count():
                    # 找到包含该文本的 label
                    label = checkbox_text.locator('xpath=ancestor::label[1]')
                    if await label.count():
                        # 检查是否已勾选
                        checkbox = label.locator('input[type="checkbox"]')
                        if await checkbox.count():
                            is_checked = await checkbox.is_checked()
                            if not is_checked:
                                await label.click()
                                logger.info("[声明] 已勾选: %s", decl)
                                await asyncio.sleep(1)  # 等待勾选生效
                            else:
                                logger.info("[声明] 已经是勾选状态: %s", decl)
                        else:
                            await label.click()
                            logger.info("[声明] 点击 label: %s", decl)
                            await asyncio.sleep(1)
                    else:
                        logger.warning("[声明] 未找到 label: %s", decl)
                else:
                    logger.warning("[声明] 未找到声明选项: %s", decl)

            logger.info("[声明] 作品声明设置完成")
        except Exception as e:
            logger.error("[声明] 设置作品声明失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: toggle video-to-image generation
    # ------------------------------------------------------------------

    @staticmethod
    async def _toggle_generate_image(page, enable: bool):
        """Toggle the '生成图文' checkbox."""
        logger.info("[生成图文] 设置视频生成图文: %s", enable)
        try:
            checkbox_label = page.locator('label:has-text("生成图文")')
            if await checkbox_label.count():
                checkbox = checkbox_label.locator('input[type="checkbox"]')
                if await checkbox.count():
                    is_checked = await checkbox.is_checked()
                    if enable and not is_checked:
                        await checkbox_label.click()
                        logger.info("[生成图文] 已启用视频生成图文")
                    elif not enable and is_checked:
                        await checkbox_label.click()
                        logger.info("[生成图文] 已禁用视频生成图文")
                    else:
                        logger.info("[生成图文] 视频生成图文状态已是目标状态")
            else:
                logger.warning("[生成图文] 未找到生成图文选项!")
        except Exception as e:
            logger.error("[生成图文] 设置视频生成图文失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: set collection (合集)
    # ------------------------------------------------------------------

    @staticmethod
    async def _set_collection(page, collection_id: str):
        """Set collection/series for the video."""
        logger.info("[合集] 开始设置合集: %s", collection_id)
        try:
            collection_btn = page.locator('button:has-text("选择合集")')
            if await collection_btn.count():
                await collection_btn.click()
                await asyncio.sleep(2)
                logger.info("[合集] 已打开合集选择弹窗")

                # Select the collection by ID or text
                collection_option = page.locator(f'input[type="radio"][value="{collection_id}"]')
                if await collection_option.count():
                    await collection_option.click()
                    logger.info("[合集] 已选择合集 (by ID): %s", collection_id)
                else:
                    collection_label = page.locator(f'label:has-text("{collection_id}")')
                    if await collection_label.count():
                        await collection_label.click()
                        logger.info("[合集] 已选择合集 (by text): %s", collection_id)
                    else:
                        logger.warning("[合集] 未找到合集: %s", collection_id)

                # Click confirm button
                confirm_btn = page.locator('.add-to-series-action button:has-text("确定")')
                if await confirm_btn.count():
                    await confirm_btn.click()
                    await asyncio.sleep(1)
                    logger.info("[合集] 已点击确定")
            else:
                logger.warning("[合集] 未找到选择合集按钮!")
        except Exception as e:
            logger.error("[合集] 设置合集失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: toggle extend link
    # ------------------------------------------------------------------

    @staticmethod
    async def _toggle_extend_link(page, link_url=""):
        """Toggle the extend link checkbox and fill the link URL."""
        logger.info("[扩展链接] 开始设置扩展链接, URL: %s", link_url)
        try:
            # 找到扩展链接的复选框（通过 form-item-external-link 定位）
            extend_link_section = page.locator('div.video-form-item.form-item-external-link')
            if not await extend_link_section.count():
                logger.warning("[扩展链接] 未找到扩展链接区域!")
                return

            # 在该区域中找到 checkbox label
            checkbox_label = extend_link_section.locator('label.byte-checkbox').first
            if not await checkbox_label.count():
                logger.warning("[扩展链接] 未找到扩展链接复选框!")
                return

            # 勾选复选框
            checkbox = checkbox_label.locator('input[type="checkbox"]')
            if await checkbox.count():
                is_checked = await checkbox.is_checked()
                if not is_checked:
                    await checkbox_label.click()
                    logger.info("[扩展链接] 已勾选扩展链接")
                    await asyncio.sleep(2)  # 等待输入框出现
                else:
                    logger.info("[扩展链接] 扩展链接已经是勾选状态")

            # 如果没有提供链接地址，直接返回
            if not link_url:
                logger.info("[扩展链接] 无链接地址，仅勾选复选框")
                return

            # 填写链接地址
            logger.info("[扩展链接] 正在寻找链接输入框...")

            # 根据用户提供的DOM，输入框在 div.video-form-item-extra 下
            link_input = page.locator('div.video-form-item-extra input[placeholder*="请填写链接地址"]')
            if not await link_input.count():
                # 尝试更宽松的选择器
                link_input = page.locator('input[placeholder*="请填写链接地址"]')
            if not await link_input.count():
                link_input = page.locator('input[placeholder*="https://www.toutiao.com"]')

            if await link_input.count():
                await link_input.fill(link_url)
                logger.info("[扩展链接] 链接地址已填写: %s", link_url)
            else:
                logger.warning("[扩展链接] 未找到链接输入框!")

        except Exception as e:
            logger.error("[扩展链接] 设置扩展链接失败: %s", e)

    # ------------------------------------------------------------------
    # Helper: set schedule time
    # ------------------------------------------------------------------

    @staticmethod
    async def _set_schedule_time(page, publish_date) -> bool:
        """Set scheduled publish time."""
        logger.info("[定时发布] 开始设置定时发布时间: %s", publish_date)
        try:
            valid, reason = ToutiaoPlatform._validate_schedule_publish_date(publish_date)
            if not valid:
                logger.error("[定时发布] 目标时间校验失败: %s", reason)
                return False

            target_day = publish_date.strftime("%m月%d日")
            target_hour = publish_date.hour
            target_minute = publish_date.minute
            target_summary = publish_date.strftime("%Y-%m-%d %H:%M")
            logger.info(
                "[定时发布] 目标时间: 日期=%s 小时=%s 分钟=%02d 汇总=%s",
                target_day,
                target_hour,
                target_minute,
                target_summary,
            )

            timer_btn, timer_selector = await ToutiaoPlatform._find_first_visible(
                page,
                [
                    'button.action-footer-btn.timer:has-text("定时发布")',
                    'button.action-footer-btn.timer:has-text("预约发布")',
                    'button.action-footer-btn.timer',
                    'button:has-text("定时发布")',
                    'button:has-text("预约发布")',
                    '[role="button"]:has-text("定时发布")',
                    '[role="button"]:has-text("预约发布")',
                ],
                timeout_ms=3000,
            )
            if timer_btn is None:
                logger.warning("[定时发布] 未找到定时发布按钮!")
                return False

            logger.info("[定时发布] 准备点击定时发布按钮: selector=%s", timer_selector)
            await timer_btn.click()
            modal = page.locator(
                "div.byte-modal.common-timing-picker.video-publish-timer-picker"
            ).first
            try:
                await modal.wait_for(state="visible", timeout=5000)
            except Exception:
                logger.warning("[定时发布] 点击按钮后未出现定时发布弹窗")
                return False

            modal_title = await ToutiaoPlatform._read_text(
                modal.locator(".byte-modal-title").first
            )
            logger.info("[定时发布] 已打开定时发布弹窗: title=%s", modal_title)
            if modal_title != "定时发布":
                logger.warning("[定时发布] 弹窗标题异常: %s", modal_title)
                return False

            state = await ToutiaoPlatform._read_schedule_modal_state(modal)
            logger.info(
                "[定时发布] 当前值: day=%s hour=%s minute=%s summary=%s",
                state["day_norm"],
                state["hour_norm"],
                state["minute_norm"],
                state["summary"],
            )
            if state["summary_dt"] is None:
                logger.warning("[定时发布] 无法解析弹窗汇总时间: %s", state["summary"])
                return False

            if not await ToutiaoPlatform._select_schedule_value(
                page,
                modal,
                field_label="日期",
                select_selector=".day-select.byte-select",
                value_selector=".day-select .byte-select-view-value",
                target_display=target_day,
                normalizer=ToutiaoPlatform._normalize_day_text,
            ):
                logger.warning("[定时发布] 日期选择失败")
                return False

            if not await ToutiaoPlatform._select_schedule_value(
                page,
                modal,
                field_label="小时",
                select_selector=".hour-select.byte-select",
                value_selector=".hour-select .byte-select-view-value",
                target_display=str(target_hour),
                normalizer=ToutiaoPlatform._normalize_hour_text,
            ):
                logger.warning("[定时发布] 小时选择失败")
                return False

            if not await ToutiaoPlatform._select_schedule_value(
                page,
                modal,
                field_label="分钟",
                select_selector=".minute-select.byte-select",
                value_selector=".minute-select .byte-select-view-value",
                target_display=f"{target_minute:02d}",
                normalizer=ToutiaoPlatform._normalize_minute_text,
            ):
                logger.warning("[定时发布] 分钟选择失败")
                return False

            final_state = await ToutiaoPlatform._read_schedule_modal_state(modal)
            logger.info(
                "[定时发布] 最终校验: expected_summary=%s actual_summary=%s",
                target_summary,
                final_state["summary"],
            )
            if not (
                final_state["day_norm"] == target_day
                and final_state["hour_norm"] == str(target_hour)
                and final_state["minute_norm"] == f"{target_minute:02d}"
                and final_state["summary"] == target_summary
            ):
                logger.warning(
                    "[定时发布] 最终校验失败: day=%s/%s hour=%s/%s minute=%s/%s summary=%s/%s",
                    final_state["day_norm"],
                    target_day,
                    final_state["hour_norm"],
                    target_hour,
                    final_state["minute_norm"],
                    f"{target_minute:02d}",
                    final_state["summary"],
                    target_summary,
                )
                return False

            confirm_btn = modal.locator(".byte-modal-footer button.byte-btn-primary").first
            if not await confirm_btn.count():
                logger.warning("[定时发布] 未找到定时确认按钮")
                return False
            confirm_text = await ToutiaoPlatform._read_text(confirm_btn)
            if "定时发布" not in confirm_text:
                logger.warning("[定时发布] 定时确认按钮文本异常: %s", confirm_text)
                return False
            logger.info("[定时发布] 准备点击弹窗确认按钮: text=%s", confirm_text)
            await confirm_btn.click()
            modal_closed = await ToutiaoPlatform._wait_modal_hidden(modal)
            if not modal_closed:
                logger.warning("[定时发布] 点击确认后弹窗未关闭")
                return False
            logger.info("[定时发布] 定时发布弹窗确认完成")
            return True
        except Exception as e:
            logger.error("[定时发布] 设置定时发布时间失败: %s", e)
            return False
