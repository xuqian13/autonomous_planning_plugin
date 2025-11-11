"""
日程图片生成器 - 将日程信息转换为美观的图片
"""

import os
import io
import base64
from typing import Tuple, List, Dict, Any
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


class ScheduleImageGenerator:
    """生成日程图片"""

    # 颜色配置 - 清新的渐变配色
    BG_START_COLOR = (25, 35, 50)  # 深蓝紫色
    BG_END_COLOR = (30, 25, 45)  # 深紫色

    CARD_BG_COLOR = (45, 55, 75, 150)  # 半透明蓝紫色卡片背景
    CARD_BORDER_COLOR = (120, 150, 200, 120)  # 蓝色边框（半透明）

    TITLE_COLOR = (200, 220, 255)  # 淡蓝色标题
    SUBTITLE_COLOR = (180, 200, 255)  # 浅蓝色副标题
    TEXT_COLOR = (240, 245, 255)  # 淡白色文本
    TIME_COLOR = (150, 200, 255)  # 亮蓝色时间
    ACCENT_COLOR = (100, 180, 255)  # 蓝色强调

    # 装饰色
    GLOW_COLOR = (150, 200, 255, 100)  # 蓝色光晕
    SHADOW_COLOR = (10, 15, 25, 120)  # 暗色阴影

    # 目标类型emoji和颜色
    TYPE_EMOJIS = {
        "meal": ("🍽️", (255, 200, 150)),
        "study": ("📚", (150, 200, 255)),
        "entertainment": ("🎮", (255, 150, 200)),
        "daily_routine": ("🏠", (200, 200, 200)),
        "social_maintenance": ("💬", (255, 220, 150)),
        "learn_topic": ("📖", (200, 180, 255)),
        "health_check": ("🔧", (150, 255, 200)),
        "exercise": ("🏃", (255, 180, 150)),
        "rest": ("😴", (200, 200, 255)),
        "free_time": ("🎨", (200, 255, 200)),
        "custom": ("📌", (220, 220, 220)),
    }

    # 字体大小
    TITLE_SIZE = 48
    SUBTITLE_SIZE = 28
    TEXT_SIZE = 24
    TIME_SIZE = 22

    # 间距
    PADDING = 50
    CARD_PADDING = 18
    LINE_SPACING = 6
    ITEM_SPACING = 15
    CARD_RADIUS = 12

    @staticmethod
    def _get_font(size: int) -> ImageFont.FreeTypeFont:
        """获取字体"""
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        ]

        for path in font_paths:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    test_bbox = font.getbbox("测试")
                    if test_bbox[2] - test_bbox[0] > 0:
                        return font
                except Exception:
                    continue

        raise RuntimeError("未找到可用的中文字体")

    @staticmethod
    def _draw_rounded_rectangle(
        draw: ImageDraw.ImageDraw,
        coords: tuple,
        radius: int,
        fill: tuple,
        outline: tuple = None,
        width: int = 2
    ):
        """绘制圆角矩形"""
        x1, y1, x2, y2 = coords

        # 绘制主体
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)

        # 四个圆角
        draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill)
        draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill)

        # 绘制边框
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
    def _create_gradient_background(width: int, height: int) -> Image.Image:
        """创建渐变背景"""
        base = Image.new('RGB', (width, height), ScheduleImageGenerator.BG_START_COLOR)

        for y in range(height):
            r1, g1, b1 = ScheduleImageGenerator.BG_START_COLOR
            r2, g2, b2 = ScheduleImageGenerator.BG_END_COLOR

            ratio = y / height
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)

            draw = ImageDraw.Draw(base)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        return base

    @staticmethod
    def _wrap_text(text: str, max_width: int, font: ImageFont.FreeTypeFont) -> list:
        """文本自动换行"""
        lines = []
        for line in text.split('\n'):
            if not line:
                lines.append('')
                continue

            current_line = ''
            for char in line:
                test_line = current_line + char
                bbox = font.getbbox(test_line)
                w = bbox[2] - bbox[0]

                if w <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = char

            if current_line:
                lines.append(current_line)

        return lines

    @staticmethod
    def generate_schedule_image(
        title: str,
        schedule_items: List[Dict[str, Any]],
        width: int = 1920
    ) -> Tuple[bytes, str]:
        """
        生成日程图片

        Args:
            title: 标题（如"今日日程"）
            schedule_items: 日程项列表，每项包含:
                - time: 时间字符串 "HH:MM-HH:MM"
                - name: 活动名称
                - description: 活动描述
                - goal_type: 目标类型
            width: 图片宽度（默认1920，横屏）

        Returns:
            (图片字节数据, base64编码字符串)
        """
        font_title = ScheduleImageGenerator._get_font(ScheduleImageGenerator.TITLE_SIZE)
        font_subtitle = ScheduleImageGenerator._get_font(ScheduleImageGenerator.SUBTITLE_SIZE)
        font_text = ScheduleImageGenerator._get_font(ScheduleImageGenerator.TEXT_SIZE)
        font_time = ScheduleImageGenerator._get_font(ScheduleImageGenerator.TIME_SIZE)

        # 计算所需高度
        content_width = width - ScheduleImageGenerator.PADDING * 2
        max_text_width = content_width - ScheduleImageGenerator.CARD_PADDING * 2 - 80  # 留空间给emoji和时间

        # 标题区域高度
        title_bbox = font_title.getbbox(title)
        header_height = (
            ScheduleImageGenerator.PADDING
            + (title_bbox[3] - title_bbox[1])
            + 30
        )

        # 计算每个日程项的高度
        total_items_height = 0
        for item in schedule_items:
            # 时间行 + 名称行 + 描述（可能多行）
            item_height = ScheduleImageGenerator.CARD_PADDING

            # 时间和名称在同一行
            time_bbox = font_time.getbbox(item.get("time", ""))
            name_bbox = font_subtitle.getbbox(item.get("name", ""))
            item_height += max(time_bbox[3] - time_bbox[1], name_bbox[3] - name_bbox[1])
            item_height += ScheduleImageGenerator.LINE_SPACING

            # 描述（可能多行）
            description = item.get("description", "")
            if description:
                wrapped_lines = ScheduleImageGenerator._wrap_text(
                    description, max_text_width, font_text
                )
                for _ in wrapped_lines:
                    text_bbox = font_text.getbbox('A')
                    item_height += (text_bbox[3] - text_bbox[1]) + ScheduleImageGenerator.LINE_SPACING

            item_height += ScheduleImageGenerator.CARD_PADDING
            total_items_height += item_height + ScheduleImageGenerator.ITEM_SPACING

        # 总高度
        height = header_height + total_items_height + ScheduleImageGenerator.PADDING

        # 创建图片
        img = ScheduleImageGenerator._create_gradient_background(width, height)

        # 创建RGBA层用于半透明元素
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw = ImageDraw.Draw(img)

        # 绘制标题区域
        y = ScheduleImageGenerator.PADDING

        # 标题光晕
        glow_radius = 60
        for i in range(3):
            alpha = int(ScheduleImageGenerator.GLOW_COLOR[3] * (1 - i / 3))
            r, g, b = ScheduleImageGenerator.GLOW_COLOR[:3]
            draw_overlay.ellipse(
                [width//2 - glow_radius, y - glow_radius//2,
                 width//2 + glow_radius, y + glow_radius//2],
                fill=(r, g, b, alpha)
            )

        # 绘制主标题
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, y), title, fill=ScheduleImageGenerator.TITLE_COLOR, font=font_title)
        y += (title_bbox[3] - title_bbox[1]) + 20

        # 装饰线
        draw.line(
            [(width//2 - 100, y), (width//2 + 100, y)],
            fill=ScheduleImageGenerator.ACCENT_COLOR,
            width=3
        )
        y += 10

        # 绘制日程项
        x_offset = ScheduleImageGenerator.PADDING

        for item in schedule_items:
            time_str = item.get("time", "")
            name = item.get("name", "")
            description = item.get("description", "")
            goal_type = item.get("goal_type", "custom")

            # 获取emoji和颜色
            emoji, type_color = ScheduleImageGenerator.TYPE_EMOJIS.get(
                goal_type,
                ScheduleImageGenerator.TYPE_EMOJIS["custom"]
            )

            # 计算卡片高度
            card_height = ScheduleImageGenerator.CARD_PADDING
            time_bbox = font_time.getbbox(time_str)
            name_bbox = font_subtitle.getbbox(name)
            card_height += max(time_bbox[3] - time_bbox[1], name_bbox[3] - name_bbox[1])
            card_height += ScheduleImageGenerator.LINE_SPACING

            if description:
                wrapped_lines = ScheduleImageGenerator._wrap_text(
                    description, max_text_width, font_text
                )
                for _ in wrapped_lines:
                    text_bbox = font_text.getbbox('A')
                    card_height += (text_bbox[3] - text_bbox[1]) + ScheduleImageGenerator.LINE_SPACING

            card_height += ScheduleImageGenerator.CARD_PADDING

            # 绘制卡片阴影
            shadow_offset = 3
            ScheduleImageGenerator._draw_rounded_rectangle(
                draw_overlay,
                (x_offset + shadow_offset,
                 y + shadow_offset,
                 x_offset + content_width + shadow_offset,
                 y + card_height + shadow_offset),
                ScheduleImageGenerator.CARD_RADIUS,
                fill=ScheduleImageGenerator.SHADOW_COLOR
            )

            # 绘制卡片背景
            ScheduleImageGenerator._draw_rounded_rectangle(
                draw_overlay,
                (x_offset, y,
                 x_offset + content_width, y + card_height),
                ScheduleImageGenerator.CARD_RADIUS,
                fill=ScheduleImageGenerator.CARD_BG_COLOR,
                outline=ScheduleImageGenerator.CARD_BORDER_COLOR,
                width=2
            )

            # 合并overlay
            img.paste(overlay, (0, 0), overlay)
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            # 绘制内容
            card_y = y + ScheduleImageGenerator.CARD_PADDING
            card_x = x_offset + ScheduleImageGenerator.CARD_PADDING

            # 绘制emoji（左侧）
            draw.text(
                (card_x, card_y),
                emoji,
                font=font_subtitle
            )

            # 绘制时间（emoji右侧）
            draw.text(
                (card_x + 40, card_y),
                time_str,
                fill=ScheduleImageGenerator.TIME_COLOR,
                font=font_time
            )

            # 绘制名称（时间右侧）
            time_width = font_time.getbbox(time_str)[2] - font_time.getbbox(time_str)[0]
            draw.text(
                (card_x + 40 + time_width + 20, card_y),
                name,
                fill=type_color,
                font=font_subtitle
            )

            card_y += max(time_bbox[3] - time_bbox[1], name_bbox[3] - name_bbox[1])
            card_y += ScheduleImageGenerator.LINE_SPACING

            # 绘制描述
            if description:
                wrapped_lines = ScheduleImageGenerator._wrap_text(
                    description, max_text_width, font_text
                )
                for wrapped_line in wrapped_lines:
                    draw.text(
                        (card_x + 60, card_y),
                        wrapped_line,
                        fill=ScheduleImageGenerator.TEXT_COLOR,
                        font=font_text
                    )
                    text_bbox = font_text.getbbox('A')
                    card_y += (text_bbox[3] - text_bbox[1]) + ScheduleImageGenerator.LINE_SPACING

            y += card_height + ScheduleImageGenerator.ITEM_SPACING

        # 最终合并overlay
        img.paste(overlay, (0, 0), overlay)

        # 转换为字节和base64
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')

        return img_bytes, img_base64
