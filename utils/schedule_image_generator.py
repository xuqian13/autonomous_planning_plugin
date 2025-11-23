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

import base64
import io
import math
import os
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.schedule_image_generator")


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
        now = datetime.now()
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

    @classmethod
    def generate_schedule_image(
        cls,
        title: str,
        schedule_items: List[Dict[str, Any]],
        width: int = None  # None表示使用默认值
    ) -> Tuple[str, str]:
        """
        生成日程图片（冬季主题，内存优化）

        Args:
            title: 标题（如"今日日程"）
            schedule_items: 日程项列表
            width: 图片宽度（None=使用默认1280，最大1920）

        Returns:
            (图片路径, base64编码字符串)
        """
        # P2优化：使用信号量限制并发（最多3个并发生成）
        cls._generation_semaphore.acquire()

        # 使用默认值或限制最大分辨率
        if width is None:
            width = ScheduleImageGenerator.DEFAULT_WIDTH
        else:
            width = min(width, ScheduleImageGenerator.MAX_WIDTH)

        # 按比例计算高度（16:9）
        height = int(width * 9 / 16)
        height = min(height, ScheduleImageGenerator.MAX_HEIGHT)

        # 使用缓存加载图片资源（性能优化）
        bird, winter_char_alpha = ScheduleImageGenerator._load_images()

        # 创建冬季主题背景
        img = Image.new('RGB', (width, height), (240, 245, 252))

        # 蓝白渐变
        draw = ImageDraw.Draw(img)
        for y in range(height):
            ratio = y / height
            r = int(240 - 25 * ratio)
            g = int(245 - 20 * ratio)
            b = int(252 - 10 * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # 冬季纹理（减少纹理点数量，降低内存占用）
        texture_count = int(1500 * (width / 1280))  # 根据分辨率缩放
        for _ in range(texture_count):
            x = random.randint(0, width)
            y = random.randint(0, height)
            brightness = random.randint(-5, 15)
            draw.point((x, y), fill=(245 + brightness, 248 + brightness, 255))

        # 🆕 复用overlay对象，减少内存分配
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        # 直接使用预处理的冬季角色（已调整透明度，性能优化）
        # 根据实际宽度调整角色位置和大小
        char_scale = width / 1280
        char_x = int(width - 400 * char_scale)
        char_y = int(height - 553 * char_scale)
        if char_scale != 1.0:
            # 缩放角色图片以适应分辨率
            new_char_width = int(367 * char_scale)
            new_char_height = int(533 * char_scale)
            winter_char_scaled = winter_char_alpha.resize((new_char_width, new_char_height))
            img.paste(winter_char_scaled, (char_x, char_y), winter_char_scaled)
            del winter_char_scaled  # 立即释放
        else:
            img.paste(winter_char_alpha, (char_x, char_y), winter_char_alpha)

        # 雪花（根据分辨率调整数量）
        snowflake_count_large = int(12 * char_scale)
        snowflake_count_small = int(25 * char_scale)
        snowflakes = []
        for _ in range(snowflake_count_large):
            sx = random.randint(int(100 * char_scale), width - int(100 * char_scale))
            sy = random.randint(int(50 * char_scale), height - int(100 * char_scale))
            size = random.randint(15, 25)
            snowflakes.append((sx, sy, size, (220, 235, 255, 180)))

        for _ in range(snowflake_count_small):
            sx = random.randint(int(50 * char_scale), width - int(50 * char_scale))
            sy = random.randint(0, height)
            size = random.randint(8, 14)
            snowflakes.append((sx, sy, size, (230, 240, 255, 140)))

        for sx, sy, size, color in snowflakes:
            ScheduleImageGenerator._draw_snowflake(draw_overlay, sx, sy, size, color)

        # 合并overlay到主图像
        img.paste(overlay, (0, 0), overlay)

        # 🆕 清空overlay以复用
        draw_overlay.rectangle([(0, 0), (width, height)], fill=(0, 0, 0, 0))

        # 字体（根据分辨率缩放）
        font_scale = width / 1280
        font_title = ScheduleImageGenerator._get_font(int(40 * font_scale))
        font_text = ScheduleImageGenerator._get_font(int(21 * font_scale))
        font_time = ScheduleImageGenerator._get_font(int(19 * font_scale))
        font_small = ScheduleImageGenerator._get_font(int(16 * font_scale))

        # 标题区域（上移优化）
        title_y = int(40 * font_scale)

        # 小鸟头像
        bird_size = int(90 * font_scale)
        bird_avatar = bird.resize((bird_size, bird_size))
        mask = Image.new('L', (bird_size, bird_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, bird_size, bird_size], fill=255)

        bird_avatar_circle = Image.new('RGBA', (bird_size, bird_size), (0, 0, 0, 0))
        bird_avatar_circle.paste(bird_avatar, (0, 0), mask)
        # 🆕 释放不再使用的临时对象
        del bird_avatar
        del mask
        del mask_draw

        for r in range(int(55 * font_scale), 0, int(-8 * font_scale)):
            alpha = int(100 * (r / 55))
            draw_overlay.ellipse([int(70 * font_scale) - r, title_y - r, int(160 * font_scale) + r, title_y + bird_size + r],
                                fill=(180, 210, 255, alpha))

        img.paste(overlay, (0, 0), overlay)
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        draw.ellipse([70, title_y, 160, title_y + 90], outline=(150, 200, 255), width=4)
        img.paste(bird_avatar_circle, (70, title_y), bird_avatar_circle)

        # 标题（移除" Schedule"后缀，标题更简洁）
        title_display = title  # 直接使用传入的标题，不再添加" Schedule"
        title_x = 180

        for offset in range(3, 0, -1):
            shadow_color = (100 + offset * 20, 130 + offset * 25, 180 + offset * 20)
            draw.text((title_x + offset, title_y + offset), title_display, fill=shadow_color, font=font_title)

        title_color = (70, 120, 200)
        draw.text((title_x, title_y), title_display, fill=title_color, font=font_title)

        # 副标题（动态大小的透明框）
        subtitle = "冬日温暖时光~"
        subtitle_y = title_y + 75

        # 计算文字的实际大小，让透明框刚好比字体大一圈
        subtitle_bbox = font_small.getbbox(subtitle)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_height = subtitle_bbox[3] - subtitle_bbox[1]

        # 透明框：比文字大一圈（左右各5px，上下各3px）
        padding_x = 5
        padding_y = 3
        ScheduleImageGenerator._draw_rounded_rectangle(draw_overlay,
                                                        (title_x - padding_x, subtitle_y - padding_y,
                                                         title_x + subtitle_width + padding_x, subtitle_y + subtitle_height + padding_y),
                                                        radius=8, fill=(255, 255, 255, 180))
        img.paste(overlay, (0, 0), overlay)
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)

        draw.text((title_x, subtitle_y), subtitle, fill=(120, 160, 220), font=font_small)

        # 装饰线（上移优化，延伸覆盖卡片+角色区域）
        line_y = title_y + 95
        line_end_x = 1240  # 覆盖日程卡片(870px) + 冬季角色(367px)
        for i in range(4):
            alpha = 160 - i * 30
            draw.line([(80, line_y + i), (line_end_x, line_y + i)],
                     fill=(150, 190, 240, alpha), width=1)

        # 找到当前或下一个日程的索引
        target_index = -1
        current_time_minutes = datetime.now().hour * 60 + datetime.now().minute

        # 优先查找正在进行的日程
        for idx, item in enumerate(schedule_items):
            status = ScheduleImageGenerator._get_activity_status(item.get("time", ""))
            if status == "current":
                target_index = idx
                break

        # 如果没有正在进行的，找下一个即将开始的
        if target_index == -1:
            for idx, item in enumerate(schedule_items):
                time_str = item.get("time", "")
                start_minutes, _ = ScheduleImageGenerator._parse_time_str(time_str)
                if start_minutes > current_time_minutes:
                    target_index = idx
                    break

        # 如果还是没找到（所有日程都已完成），使用最后一个
        if target_index == -1 and schedule_items:
            target_index = len(schedule_items) - 1

        # 固定显示5个日程，目标日程在第3个位置（索���2）
        display_items = []
        display_target_index = -1

        if len(schedule_items) <= 5:
            # 日程不足5个，全部显示
            display_items = schedule_items
            display_target_index = target_index if target_index >= 0 else 0
        else:
            # 日程超过5个，选择5个显示，目标在第3个位置（索引2）
            if target_index < 2:
                # 目标在前面，从头开始取5个
                display_items = schedule_items[:5]
                display_target_index = target_index
            elif target_index >= len(schedule_items) - 2:
                # 目标在后面，从后往前取5个
                display_items = schedule_items[-5:]
                # 计算目标在新列表中的位置
                display_target_index = 5 - (len(schedule_items) - target_index)
            else:
                # 目标在中间，让它显示在第3个位置（索引2）
                start_idx = target_index - 2
                display_items = schedule_items[start_idx:start_idx + 5]
                display_target_index = 2  # 目标在第3个位置

        # 起始y坐标（整体上移）
        y = 155
        card_spacing = 115  # 增加卡片间距，留出更多空白

        # 活动卡片（使用display_items）
        for item in display_items:
            time_str = item.get("time", "")
            name = item.get("name", "")
            desc = item.get("description", "")
            goal_type = item.get("goal_type", "custom")

            # 获取图标
            icon = ScheduleImageGenerator.TYPE_ICONS.get(goal_type, "◈")

            # 判断是否是目标日程（高亮显示）
            item_index = display_items.index(item)
            is_target = (item_index == display_target_index)

            # 冬季色系
            colors = [(150, 200, 255), (120, 180, 255), (180, 220, 255), (200, 180, 255), (220, 200, 255)]
            color = colors[min(item_index, len(colors) - 1)]

            card_x = 80         # 左边距（与装饰线对齐）
            card_width = 830    # 保持右边缘不变
            card_height = 100

            # 目标日程高亮
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
                ScheduleImageGenerator._draw_rounded_rectangle(
                    draw_overlay,
                    (card_x + shadow_offset, y + shadow_offset,
                     card_x + card_width + shadow_offset, y + card_height + shadow_offset),
                    radius=26,
                    fill=(180, 200, 220, shadow_alpha)
                )

            # 卡片背景
            ScheduleImageGenerator._draw_rounded_rectangle(
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

            # 图标（调整位置适应100px高度）
            icon_x, icon_y = card_x + 40, y + 35  # 从52→35
            for i in range(2):
                draw.text((icon_x + 3 - i, icon_y + 3 - i), icon,
                         fill=(200, 210, 230), font=font_title)
            draw.text((icon_x, icon_y), icon, fill=color, font=font_title)

            # 时间（调整位置）
            time_x = card_x + 120  # 从135→120
            for dx, dy in [(1, 0), (0, 1)]:
                draw.text((time_x + dx, y + 20 + dy), time_str, fill=(130, 150, 180), font=font_time)  # 从28→20
            draw.text((time_x, y + 20), time_str, fill=(100, 130, 170), font=font_time)

            # 名称（调整位置）
            name_y = y + 45  # 从68→45
            for dx, dy in [(1, 0), (0, 1), (1, 1)]:
                draw.text((time_x + dx, name_y + dy), name, fill=(90, 120, 160), font=font_text)
            draw.text((time_x, name_y), name, fill=(70, 100, 140), font=font_text)

            # 描述（调整位置）
            draw.text((time_x, y + 72), desc, fill=(130, 150, 180), font=font_small)  # 从110→72

            # 状态标签（调整位置适应100px高度，根据实际时间判断状态）
            tag_x = card_x + card_width - 140  # 从-160→-140
            tag_y = y + 30  # 从50→30（更靠近顶部）

            # 获取实际状态
            status = ScheduleImageGenerator._get_activity_status(time_str)

            if status == "current":
                status_text = "进行中"
                tag_color = (100, 200, 255)
                tag_bg = (100, 200, 255, 240)
            elif status == "completed":
                status_text = "已完成"
                tag_color = (180, 220, 255)
                tag_bg = (180, 220, 255, 240)
            else:
                status_text = "未开始"
                tag_color = (200, 210, 255)
                tag_bg = (200, 210, 255, 240)

            # 只给目标日程添加光晕效果
            if is_target:
                for i in range(4):
                    glow_size = i * 6
                    draw_overlay.ellipse(
                        [tag_x - glow_size, tag_y - glow_size,
                         tag_x + 100 + glow_size, tag_y + 40 + glow_size],  # 减小标签尺寸
                        fill=(*tag_color[:3], 60 - i * 14)
                    )

            draw_overlay.ellipse([tag_x, tag_y, tag_x + 100, tag_y + 40], fill=tag_bg)  # 减小标签尺寸

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            for dx, dy in [(1, 0), (0, 1)]:
                draw.text((tag_x + 20 + dx, tag_y + 10 + dy), status_text,  # 调整文字位置：28,14→20,10
                         fill=(255, 255, 255), font=font_small)
            draw.text((tag_x + 20, tag_y + 10), status_text, fill=(255, 255, 255), font=font_small)

            # 装饰雪花（调整位置）
            ScheduleImageGenerator._draw_snowflake(draw_overlay, card_x + card_width - 35, y + 25, 8, (*color, 180))  # 缩小雪花

            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            y += card_spacing  # 使用优化后的间距

        # 底部签名（移到图片左边缘）
        signature = "Powered by Mai-Bot"
        sig_x = 10  # 靠近图片左边缘
        sig_y = height - 25  # 再往下移

        # 计算文字的实际大小，让透明框刚好比字体大一圈
        text_bbox = font_small.getbbox(signature)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # 透明框：比文字大一圈（左右各5px，上下各3px）
        padding_x = 5
        padding_y = 3
        ScheduleImageGenerator._draw_rounded_rectangle(
            draw_overlay,
            (sig_x - padding_x, sig_y - padding_y,
             sig_x + text_width + padding_x, sig_y + text_height + padding_y),
            radius=6,
            fill=(255, 255, 255, 180)
        )
        img.paste(overlay, (0, 0), overlay)

        draw.text((sig_x, sig_y), signature, fill=(120, 160, 220), font=font_small)

        # 🆕 确保目录存在（使用 Path 对象）
        ScheduleImageGenerator.SCHEDULE_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 转换为RGB格式（JPEG不支持透明度）
        rgb_img = Image.new('RGB', img.size, (240, 245, 252))  # 使用浅蓝色背景
        rgb_img.paste(img, (0, 0))

        # 保存为JPEG格式，质量85%（平衡清晰度和文件大小）
        rgb_img.save(str(ScheduleImageGenerator.SCHEDULE_IMAGE_PATH), format='JPEG', quality=85, optimize=True)

        # 同时生成base64（用于发送）
        img_byte_arr = io.BytesIO()
        rgb_img.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
        img_bytes = img_byte_arr.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')

        # P2优化：释放信号量
        cls._generation_semaphore.release()

        return str(ScheduleImageGenerator.SCHEDULE_IMAGE_PATH), img_base64

