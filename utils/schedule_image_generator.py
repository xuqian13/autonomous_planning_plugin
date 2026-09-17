"""Schedule Image Generator Module.

This module generates beautiful schedule visualization images with a
winter theme, including decorative elements and status indicators.

Features:
    - Winter-themed visual design with snowflakes and gradients
    - Font caching for improved performance
    - Image resource caching and reuse
    - Concurrent generation limiting (max 3 simultaneous)
    - Resolution limiting to prevent OOM
    - Activity status indicators (current/completed/upcoming)
    - Automatic highlighting of current/next activity

Performance Optimizations:
    - Cached font loading
    - Pre-processed character images
    - Semaphore-based concurrency control
    - Memory-efficient image composition

Example:
    >>> from schedule_image_generator import ScheduleImageGenerator
    >>>
    >>> items = [
    ...     {"time": "09:00-10:00", "name": "Morning exercise",
    ...      "description": "Yoga and stretching", "goal_type": "exercise"},
    ...     {"time": "10:00-11:00", "name": "Study time",
    ...      "description": "Read a book", "goal_type": "study"}
    ... ]
    >>> path, base64_str = ScheduleImageGenerator.generate_schedule_image(
    ...     title="Today's Schedule",
    ...     schedule_items=items
    ... )
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import base64
import io
import logging
import math
import os
import random
import threading

from PIL import Image, ImageDraw, ImageFont

from .timezone_manager import TimezoneManager

logger = logging.getLogger(__name__)


class ScheduleImageGenerator:
    """生成日程图片"""

    # P2优化：并发限制（最多3个并发生成）
    _generation_semaphore = threading.Semaphore(3)

    # 插件根目录（使用相对路径）
    PLUGIN_ROOT = Path(__file__).parent.parent

    # 图片资源路径（相对于插件根目录）
    BIRD_IMAGE_PATH = PLUGIN_ROOT / "assets" / "bird.jpg"
    WINTER_CHAR_IMAGE_PATH = PLUGIN_ROOT / "assets" / "winter_char.jpg"

    # 目标类型图标（不使用emoji）
    TYPE_ICONS = {
        "meal": "●",
        "study": "■",
        "entertainment": "◆",
        "daily_routine": "▲",
        "social_maintenance": "◇",
        "learn_topic": "★",
        "health_check": "◎",
        "exercise": "▶",
        "rest": "◐",
        "free_time": "♦",
        "custom": "◈",
    }

    # ===== 性能优化：缓存机制 =====
    _cached_bird_image = None
    _cached_winter_char = None
    _cached_winter_char_alpha = None  # 预处理后的透明角色
    _cached_fonts = {}  # 字体缓存 {size: font}

    @classmethod
    def _load_images(cls):
        """加载并缓存图片资源"""
        if cls._cached_bird_image is None:
            try:
                cls._cached_bird_image = Image.open(cls.BIRD_IMAGE_PATH).convert('RGBA')
            except (FileNotFoundError, IOError) as e:
                logger.warning(f"加载鸟图片失败: {e}")
                cls._cached_bird_image = Image.new('RGBA', (100, 100), (255, 150, 80, 255))

        if cls._cached_winter_char is None:
            try:
                winter_char = Image.open(cls.WINTER_CHAR_IMAGE_PATH).convert('RGBA')
                # 预处理：调整大小和透明度（缩小以适应720p）
                winter_char_resized = winter_char.resize((367, 533))  # 从550x800缩小
                # 使用PIL的内置方法调整透明度，比逐像素快得多
                alpha = winter_char_resized.split()[3]  # 获取alpha通道
                alpha = alpha.point(lambda p: int(p * 0.65))  # 批量处理透明度
                winter_char_resized.putalpha(alpha)
                cls._cached_winter_char_alpha = winter_char_resized
            except (FileNotFoundError, IOError) as e:
                logger.warning(f"加载冬季角色图片失败: {e}")
                cls._cached_winter_char_alpha = Image.new('RGBA', (367, 533), (150, 200, 255, 165))

        return cls._cached_bird_image, cls._cached_winter_char_alpha

    @classmethod
    def _get_font(cls, size: int) -> ImageFont.FreeTypeFont:
        """获取字体（带缓存）"""
        # 检查缓存
        if size in cls._cached_fonts:
            return cls._cached_fonts[size]

        font_paths = [
            # 优先使用支持数字和符号的字体
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # ✅ 支持中文+数字
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # ✅ 支持中文+数字
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",  # ⚠️ 数字显示为方块，作为后备
        ]

        for path in font_paths:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    # 🔧 修复：同时测试中文、数字和符号（日程图片需要显示时间）
                    test_text = "测试2025-11-18 09:30"
                    test_bbox = font.getbbox(test_text)
                    if test_bbox[2] - test_bbox[0] > 0:
                        # 缓存字体
                        cls._cached_fonts[size] = font
                        logger.info(f"已加载字体: {path} (size={size})")
                        return font
                except Exception as e:
                    logger.debug(f"加载字体失败: {path} - {e}")
                    continue

        raise RuntimeError("未找到可用的中文字体")

    @staticmethod
    def _draw_rounded_rectangle(draw, coords, radius, fill, outline=None, width=2):
        """绘制圆角矩形"""
        x1, y1, x2, y2 = coords
        if x2 <= x1 or y2 <= y1 or radius * 2 > min(x2 - x1, y2 - y1):
            draw.rectangle([x1, y1, x2, y2], fill=fill, outline=outline, width=width)
            return
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill)
        draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill)
        if outline:
            draw.arc([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=outline, width=width)
            draw.arc([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=outline, width=width)
            draw.arc([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=outline, width=width)
            draw.arc([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=outline, width=width)
            draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=width)
            draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=width)
            draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=width)
            draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=width)

    @staticmethod
    def _draw_snowflake(draw, x, y, size, color):
        """绘制雪花"""
        for angle in range(0, 360, 60):
            rad = math.radians(angle)
            end_x = x + size * math.cos(rad)
            end_y = y + size * math.sin(rad)
            draw.line([(x, y), (end_x, end_y)], fill=color, width=2)

            branch_size = size * 0.4
            for branch_angle in [-30, 30]:
                branch_rad = math.radians(angle + branch_angle)
                branch_x = x + size * 0.6 * math.cos(rad)
                branch_y = y + size * 0.6 * math.sin(rad)
                branch_end_x = branch_x + branch_size * math.cos(branch_rad)
                branch_end_y = branch_y + branch_size * math.sin(branch_rad)
                draw.line([(branch_x, branch_y), (branch_end_x, branch_end_y)], fill=color, width=1)

    @staticmethod
    def _parse_time_str(time_str: str) -> tuple:
        """解析时间字符串，返回开始和结束的分钟数"""
        try:
            parts = time_str.split('-')
            if len(parts) != 2:
                return (0, 0)

            start_time = parts[0].strip().split(':')
            end_time = parts[1].strip().split(':')

            start_minutes = int(start_time[0]) * 60 + int(start_time[1])
            end_minutes = int(end_time[0]) * 60 + int(end_time[1])

            return (start_minutes, end_minutes)
        except (ValueError, IndexError, AttributeError):
            return (0, 0)

    @staticmethod
    def _get_activity_status(time_str: str) -> str:
        """获取活动状态: current/completed/upcoming"""
        tz_manager = TimezoneManager()
        now = tz_manager.get_now()
        current_minutes = now.hour * 60 + now.minute

        start_minutes, end_minutes = ScheduleImageGenerator._parse_time_str(time_str)

        if start_minutes <= current_minutes < end_minutes:
            return "current"
        elif current_minutes >= end_minutes:
            return "completed"
        else:
            return "upcoming"

    # 🆕 生成图片保存路径（相对于插件根目录）
    SCHEDULE_IMAGE_PATH = PLUGIN_ROOT / "data" / "images" / "schedule_today.jpg"

    # 🆕 分辨率限制（防止OOM）
    MAX_WIDTH = 1920
    MAX_HEIGHT = 1080
    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 720

    # ========================================================================
    # 🆕 重构：私有方法 - 职责单一
    # ========================================================================

    @classmethod
    def _prepare_resources(cls, width: int) -> Tuple[int, int, Any, Any]:
        """准备资源：验证参数、加载图片、计算尺寸

        Args:
            width: 请求的图片宽度

        Returns:
            (实际宽度, 实际高度, 鸟图片, 冬季角色图片)
        """
        # 使用默认值或限制最大分辨率
        if width is None:
            width = cls.DEFAULT_WIDTH
        else:
            width = min(width, cls.MAX_WIDTH)

        # 按比例计算高度（16:9）
        height = int(width * 9 / 16)
        height = min(height, cls.MAX_HEIGHT)

        # 使用缓存加载图片资源（性能优化）
        bird, winter_char_alpha = cls._load_images()

        return width, height, bird, winter_char_alpha

    @classmethod
    def _create_base_canvas(
        cls,
        width: int,
        height: int,
        winter_char_alpha: Any
    ) -> Tuple[Any, Any, Any]:
        """创建基础画布：背景渐变、纹理、冬季角色、雪花

        Args:
            width: 画布宽度
            height: 画布高度
            winter_char_alpha: 冬季角色图片（已预处理）

        Returns:
            (主图像, draw对象, overlay图像)
        """
        # 创建冬季主题背景
        img = Image.new('RGB', (width, height), (240, 245, 252))
        draw = ImageDraw.Draw(img)

        # 蓝白渐变
        for y in range(height):
            ratio = y / height
            r = int(240 - 25 * ratio)
            g = int(245 - 20 * ratio)
            b = int(252 - 10 * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # 冬季纹理（减少纹理点数量，降低内存占用）
        texture_count = int(1500 * (width / 1280))
        for _ in range(texture_count):
            x = random.randint(0, width)
            y = random.randint(0, height)
            brightness = random.randint(-5, 15)
            draw.point((x, y), fill=(245 + brightness, 248 + brightness, 255))

        # 创建overlay对象
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        # 添加冬季角色（根据分辨率缩放）
        char_scale = width / 1280
        char_x = int(width - 400 * char_scale)
        char_y = int(height - 553 * char_scale)

        if char_scale != 1.0:
            new_char_width = int(367 * char_scale)
            new_char_height = int(533 * char_scale)
            winter_char_scaled = winter_char_alpha.resize((new_char_width, new_char_height))
            img.paste(winter_char_scaled, (char_x, char_y), winter_char_scaled)
            del winter_char_scaled
        else:
            img.paste(winter_char_alpha, (char_x, char_y), winter_char_alpha)

        # 绘制雪花装饰
        snowflake_count_large = int(12 * char_scale)
        snowflake_count_small = int(25 * char_scale)

        for _ in range(snowflake_count_large):
            sx = random.randint(int(100 * char_scale), width - int(100 * char_scale))
            sy = random.randint(int(50 * char_scale), height - int(100 * char_scale))
            size = random.randint(15, 25)
            cls._draw_snowflake(draw_overlay, sx, sy, size, (220, 235, 255, 180))

        for _ in range(snowflake_count_small):
            sx = random.randint(int(50 * char_scale), width - int(50 * char_scale))
            sy = random.randint(0, height)
            size = random.randint(8, 14)
            cls._draw_snowflake(draw_overlay, sx, sy, size, (230, 240, 255, 140))

        # 合并overlay
        img.paste(overlay, (0, 0), overlay)

        return img, draw, overlay

    @classmethod
    def _calculate_display_items(
        cls,
        schedule_items: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """计算要显示的日程项：固定显示5个，当前/下一个日程在第3个位置

        Args:
            schedule_items: 所有日程项

        Returns:
            (要显示的5个日程项, 目标索引)
        """
        if not schedule_items:
            return [], -1

        # 找到当前或下一个日程的索引
        target_index = -1
        tz_manager = TimezoneManager()
        now = tz_manager.get_now()
        current_time_minutes = now.hour * 60 + now.minute

        # 优先查找正在进行的日程
        for idx, item in enumerate(schedule_items):
            status = cls._get_activity_status(item.get("time", ""))
            if status == "current":
                target_index = idx
                break

        # 如果没有正在进行的，找下一个即将开始的
        if target_index == -1:
            for idx, item in enumerate(schedule_items):
                time_str = item.get("time", "")
                start_minutes, _ = cls._parse_time_str(time_str)
                if start_minutes > current_time_minutes:
                    target_index = idx
                    break

        # 如果还是没找到，使用最后一个
        if target_index == -1:
            target_index = len(schedule_items) - 1

        # 固定显示5个日程
        display_items = []
        display_target_index = -1

        if len(schedule_items) <= 5:
            display_items = schedule_items
            display_target_index = target_index
        else:
            if target_index < 2:
                display_items = schedule_items[:5]
                display_target_index = target_index
            elif target_index >= len(schedule_items) - 2:
                display_items = schedule_items[-5:]
                display_target_index = 5 - (len(schedule_items) - target_index)
            else:
                start_idx = target_index - 2
                display_items = schedule_items[start_idx:start_idx + 5]
                display_target_index = 2

        return display_items, display_target_index

    @classmethod
    def _draw_title_area(
        cls,
        img: Any,
        draw: Any,
        overlay: Any,
        title: str,
        width: int,
        height: int,
        bird: Any
    ) -> Any:
        """绘制标题区域：头像、标题、副标题、装饰线

        Args:
            img: 主图像
            draw: 绘制对象
            overlay: overlay图像
            title: 标题文字
            width: 画布宽度
            height: 画布高度
            bird: 鸟图片

        Returns:
            更新后的overlay对象
        """
        font_scale = width / 1280
        font_title = cls._get_font(int(40 * font_scale))
        font_small = cls._get_font(int(16 * font_scale))

        title_y = int(40 * font_scale)
        draw_overlay = ImageDraw.Draw(overlay)

        # 绘制小鸟头像
        bird_size = int(90 * font_scale)
        bird_avatar = bird.resize((bird_size, bird_size))
        mask = Image.new('L', (bird_size, bird_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, bird_size, bird_size], fill=255)

        bird_avatar_circle = Image.new('RGBA', (bird_size, bird_size), (0, 0, 0, 0))
        bird_avatar_circle.paste(bird_avatar, (0, 0), mask)
        del bird_avatar, mask, mask_draw

        # 头像光晕
        for r in range(int(55 * font_scale), 0, int(-8 * font_scale)):
            alpha = int(100 * (r / 55))
            draw_overlay.ellipse(
                [int(70 * font_scale) - r, title_y - r,
                 int(160 * font_scale) + r, title_y + bird_size + r],
                fill=(180, 210, 255, alpha)
            )

        img.paste(overlay, (0, 0), overlay)
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        draw.ellipse([70, title_y, 160, title_y + 90], outline=(150, 200, 255), width=4)
        img.paste(bird_avatar_circle, (70, title_y), bird_avatar_circle)

        # 绘制标题
        title_x = 180
        for offset in range(3, 0, -1):
            shadow_color = (100 + offset * 20, 130 + offset * 25, 180 + offset * 20)
            draw.text((title_x + offset, title_y + offset), title, fill=shadow_color, font=font_title)

        draw.text((title_x, title_y), title, fill=(70, 120, 200), font=font_title)

        # 副标题
        subtitle = "冬日温暖时光~"
        subtitle_y = title_y + 75
        subtitle_bbox = font_small.getbbox(subtitle)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_height = subtitle_bbox[3] - subtitle_bbox[1]

        padding_x, padding_y = 5, 3
        cls._draw_rounded_rectangle(
            draw_overlay,
            (title_x - padding_x, subtitle_y - padding_y,
             title_x + subtitle_width + padding_x, subtitle_y + subtitle_height + padding_y),
            radius=8,
            fill=(255, 255, 255, 180)
        )
        img.paste(overlay, (0, 0), overlay)
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        draw.text((title_x, subtitle_y), subtitle, fill=(120, 160, 220), font=font_small)

        # 装饰线
        line_y = title_y + 95
        line_end_x = 1240
        for i in range(4):
            alpha = 160 - i * 30
            draw.line([(80, line_y + i), (line_end_x, line_y + i)],
                     fill=(150, 190, 240, alpha), width=1)

        return overlay

    @classmethod
    def _draw_schedule_cards(
        cls,
        img: Any,
        draw: Any,
        overlay: Any,
        display_items: List[Dict[str, Any]],
        display_target_index: int,
        width: int,
        height: int
    ) -> Any:
        """绘制日程卡片：遍历日程项，绘制卡片、图标、文字、状态

        Args:
            img: 主图像
            draw: 绘制对象
            overlay: overlay图像
            display_items: 要显示的日程项
            display_target_index: 高亮的目标索引
            width: 画布宽度
            height: 画布高度

        Returns:
            更新后的overlay对象
        """
        font_scale = width / 1280
        font_title = cls._get_font(int(40 * font_scale))
        font_text = cls._get_font(int(21 * font_scale))
        font_time = cls._get_font(int(19 * font_scale))
        font_small = cls._get_font(int(16 * font_scale))

        y = 155
        card_spacing = 115

        for item in display_items:
            time_str = item.get("time", "")
            name = item.get("name", "")
            desc = item.get("description", "")
            goal_type = item.get("goal_type", "custom")

            icon = cls.TYPE_ICONS.get(goal_type, "◈")
            item_index = display_items.index(item)
            is_target = (item_index == display_target_index)

            colors = [(150, 200, 255), (120, 180, 255), (180, 220, 255), (200, 180, 255), (220, 200, 255)]
            color = colors[min(item_index, len(colors) - 1)]

            card_x, card_width, card_height = 80, 830, 100

            draw_overlay = ImageDraw.Draw(overlay)

            # 目标高亮
            if is_target:
                for i in range(6):
                    glow_offset = i * 10
                    alpha = int(140 - i * 22)
                    draw_overlay.rounded_rectangle(
                        [card_x - glow_offset, y - glow_offset,
                         card_x + card_width + glow_offset, y + card_height + glow_offset],
                        radius=26,
                        fill=(150, 220, 255, alpha)
                    )

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            # 阴影
            for i in range(3):
                shadow_offset = 10 + i * 3
                shadow_alpha = 80 - i * 20
                cls._draw_rounded_rectangle(
                    draw_overlay,
                    (card_x + shadow_offset, y + shadow_offset,
                     card_x + card_width + shadow_offset, y + card_height + shadow_offset),
                    radius=26,
                    fill=(180, 200, 220, shadow_alpha)
                )

            # 卡片背景
            cls._draw_rounded_rectangle(
                draw_overlay,
                (card_x, y, card_x + card_width, y + card_height),
                radius=26,
                fill=(250, 252, 255, 250),
                outline=color,
                width=5 if is_target else 4
            )

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            # 左侧渐变条
            for i in range(18):
                x_offset = card_x + i
                gradient_ratio = i / 18
                r = int(color[0] * (1 - gradient_ratio * 0.2))
                g = int(color[1] * (1 - gradient_ratio * 0.2))
                b = int(color[2] * (1 - gradient_ratio * 0.1))
                draw.line([(x_offset, y + 26), (x_offset, y + card_height - 26)],
                         fill=(r, g, b), width=1)

            # 图标
            icon_x, icon_y = card_x + 40, y + 35
            for i in range(2):
                draw.text((icon_x + 3 - i, icon_y + 3 - i), icon,
                         fill=(200, 210, 230), font=font_title)
            draw.text((icon_x, icon_y), icon, fill=color, font=font_title)

            # 时间
            time_x = card_x + 120
            for dx, dy in [(1, 0), (0, 1)]:
                draw.text((time_x + dx, y + 20 + dy), time_str, fill=(130, 150, 180), font=font_time)
            draw.text((time_x, y + 20), time_str, fill=(100, 130, 170), font=font_time)

            # 名称
            name_y = y + 45
            for dx, dy in [(1, 0), (0, 1), (1, 1)]:
                draw.text((time_x + dx, name_y + dy), name, fill=(90, 120, 160), font=font_text)
            draw.text((time_x, name_y), name, fill=(70, 100, 140), font=font_text)

            # 描述
            draw.text((time_x, y + 72), desc, fill=(130, 150, 180), font=font_small)

            # 状态标签
            tag_x, tag_y = card_x + card_width - 140, y + 30
            status = cls._get_activity_status(time_str)

            if status == "current":
                status_text, tag_color, tag_bg = "进行中", (100, 200, 255), (100, 200, 255, 240)
            elif status == "completed":
                status_text, tag_color, tag_bg = "已完成", (180, 220, 255), (180, 220, 255, 240)
            else:
                status_text, tag_color, tag_bg = "未开始", (200, 210, 255), (200, 210, 255, 240)

            if is_target:
                for i in range(4):
                    glow_size = i * 6
                    draw_overlay.ellipse(
                        [tag_x - glow_size, tag_y - glow_size,
                         tag_x + 100 + glow_size, tag_y + 40 + glow_size],
                        fill=(*tag_color[:3], 60 - i * 14)
                    )

            draw_overlay.ellipse([tag_x, tag_y, tag_x + 100, tag_y + 40], fill=tag_bg)

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            for dx, dy in [(1, 0), (0, 1)]:
                draw.text((tag_x + 20 + dx, tag_y + 10 + dy), status_text,
                         fill=(255, 255, 255), font=font_small)
            draw.text((tag_x + 20, tag_y + 10), status_text, fill=(255, 255, 255), font=font_small)

            # 装饰雪花
            cls._draw_snowflake(draw_overlay, card_x + card_width - 35, y + 25, 8, (*color, 180))

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))

            y += card_spacing

        return overlay

    @classmethod
    def _add_signature(
        cls,
        img: Any,
        draw: Any,
        overlay: Any,
        width: int,
        height: int
    ):
        """添加底部签名

        Args:
            img: 主图像
            draw: 绘制对象
            overlay: overlay图像
            width: 画布宽度
            height: 画布高度
        """
        font_small = cls._get_font(int(16 * (width / 1280)))
        draw_overlay = ImageDraw.Draw(overlay)

        signature = "Powered by Mai-Bot"
        sig_x, sig_y = 10, height - 25

        text_bbox = font_small.getbbox(signature)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        padding_x, padding_y = 5, 3
        cls._draw_rounded_rectangle(
            draw_overlay,
            (sig_x - padding_x, sig_y - padding_y,
             sig_x + text_width + padding_x, sig_y + text_height + padding_y),
            radius=6,
            fill=(255, 255, 255, 180)
        )
        img.paste(overlay, (0, 0), overlay)
        draw.text((sig_x, sig_y), signature, fill=(120, 160, 220), font=font_small)

    # ========================================================================
    # 🆕 重构后的主函数 - 清晰的流程编排
    # ========================================================================

    @classmethod
    def generate_schedule_image(
        cls,
        title: str,
        schedule_items: List[Dict[str, Any]],
        width: int = None,
        output_dir: Optional[Path] = None,
    ) -> Tuple[str, str]:
        """生成日程图片（重构版：清晰的流程编排）

        遵循单一职责原则，将复杂的417行函数拆分为多个职责单一的私有方法。
        主函数只负责高层次的流程编排，具体实现细节委托给专门的方法。

        Args:
            title: 标题文字
            schedule_items: 日程项列表
            width: 图片宽度（None=使用默认1280）
            output_dir: 图片输出目录（插件持久化目录）；为 None 时回退到源码
                ``data/images/``，供未进入 Runner 的测试场景使用。

        Returns:
            (图片路径, base64编码字符串)
        """
        save_path = (
            Path(output_dir) / "schedule_today.jpg" if output_dir else cls.SCHEDULE_IMAGE_PATH
        )
        # 并发控制：最多3个并发生成
        cls._generation_semaphore.acquire()

        try:
            # 1️⃣ 准备资源：验证参数、加载图片、计算尺寸
            width, height, bird, winter_char_alpha = cls._prepare_resources(width)

            # 2️⃣ 创建基础画布：背景渐变、纹理、冬季角色、雪花
            img, draw, overlay = cls._create_base_canvas(width, height, winter_char_alpha)

            # 3️⃣ 计算要显示的日程项：固定5个，当前/下一个在第3个位置
            display_items, display_target_index = cls._calculate_display_items(schedule_items)

            # 4️⃣ 绘制标题区域：头像、标题、副标题、装饰线
            overlay = cls._draw_title_area(img, draw, overlay, title, width, height, bird)

            # 5️⃣ 绘制日程卡片：遍历日程项，绘制卡片、图标、文字、状态
            if display_items:
                overlay = cls._draw_schedule_cards(
                    img, draw, overlay, display_items,
                    display_target_index, width, height
                )

            # 6️⃣ 添加底部签名
            cls._add_signature(img, draw, overlay, width, height)

            # 7️⃣ 保存并编码：确保目录存在，转换格式，保存文件，生成base64
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # 转换为RGB格式（JPEG不支持透明度）
            rgb_img = Image.new('RGB', img.size, (240, 245, 252))
            rgb_img.paste(img, (0, 0))

            # 保存为JPEG，质量85%（平衡清晰度和文件大小）
            rgb_img.save(str(save_path), format='JPEG', quality=85, optimize=True)

            # 生成base64编码（用于发送）
            img_byte_arr = io.BytesIO()
            rgb_img.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

            return str(save_path), img_base64

        finally:
            # 确保释放信号量（即使发生异常）
            cls._generation_semaphore.release()
