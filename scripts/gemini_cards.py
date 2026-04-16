#!/usr/bin/env python3
"""
Gemini 知识图卡生成

从预生成的图卡 prompt 生成 2K 分辨率知识图卡图片。
图卡 prompt 由 gemini_notes.py 在生成笔记时一并产出，避免重复分析视频。

用法：
  python scripts/gemini_cards.py --prompts <card_prompts.json> --output <dir>
  python scripts/gemini_cards.py --url <youtube_url> --output <dir>  # 独立模式（会单独分析视频）
"""

import argparse
import base64
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("错误: 请先安装 google-genai: pip install google-genai")
    sys.exit(1)

# Load environment
load_dotenv()

# Setup logging
logger = logging.getLogger(__name__)

# 模型配置
MODEL_IMAGE_GEN = "gemini-3-pro-image-preview"

# ==================== 设计风格 Prompt ====================

IMAGE_GEN_PREFIX = """请生成一张知识图卡图片。

## 设计风格：温暖学术人文主义

### 基础规格
- 图片比例：16:9 横版
- 背景：暖米白色 (#F6F4F1)，略带高级纸张质感

### 配色方案
- 主色调：森林绿 (#55644A) - 用于重点色块、标题背景、主要按钮
- 深绿色：(#2E4226) - 用于深色文字、强调
- 浅绿色：(#E0E7D0) - 用于浅色背景、卡片
- 强调色：明亮橙黄 (#FFD15D) - 用于高亮、重点标记
- 辅助橙：浅橙色 (#F8DFAA) - 用于次要背景
- 深橙色：(#A55500) - 用于深色橙文字
- 文字颜色：深灰色 rgba(0,0,0,0.95)，避免纯黑
- 边框：极淡灰色 rgba(0,0,0,0.06)
- 禁止使用：霓虹色、纯黑色、高饱和蓝色

### 语言要求
- 所有文案以中文为主
- 专有名词可保留英文（如 GPT-4, Agent, LLM 等）
- 标题、说明、标注都用中文

### 排版设计
- 标题：优雅字体，大号，放置在森林绿色块上，白色文字
- 正文：现代无衬线体，清晰易读
- 布局：网格式布局，注重留白和呼吸感
- 层次：通过字号、颜色深浅和色块区分信息层级

### 视觉元素
- 插图风格：抽象的、有机的手绘线条画
- 色块：使用森林绿、浅绿色、浅橙色作为功能区域背景
- 高亮：使用明亮橙黄 (#FFD15D) 标记重点
- 圆角：使用柔和圆角 (10px-30px)
- 插图原则：非常克制，只在必要时使用，为内容服务

### 图表规范
- 风格：扁平化、极简
- 重点：强调数据对比和信息层级
- 边框：去除多余边框，使用留白分隔
- 连接线：细线条，深绿色或深灰色

### 整体气质
- 温暖、自然、专业
- 森林绿为主调，橙黄点缀
- 简约但有深度

## 图卡内容
"""


def init_client() -> genai.Client:
    """初始化 Gemini 客户端"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("错误: 请在 .env 中配置 GEMINI_API_KEY")
        sys.exit(1)
    return genai.Client(api_key=api_key)


def generate_image_from_prompt(
    client: genai.Client, prompt: str, output_path: Path, filename: str
) -> str | None:
    """用图片生成模型生成单张知识图卡"""
    try:
        full_prompt = IMAGE_GEN_PREFIX + prompt

        response = client.models.generate_content(
            model=MODEL_IMAGE_GEN,
            contents=[full_prompt],
            config=types.GenerateContentConfig(
                temperature=0.8,
                response_modalities=["image", "text"],
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                    image_size="2K"
                )
            )
        )

        if not response.candidates:
            return None

        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                mime_type = part.inline_data.mime_type

                ext = "png"
                if "jpeg" in mime_type or "jpg" in mime_type:
                    ext = "jpg"
                elif "webp" in mime_type:
                    ext = "webp"

                image_filename = f"{filename}.{ext}"
                image_path = output_path / image_filename

                with open(image_path, "wb") as f:
                    if isinstance(image_data, str):
                        f.write(base64.b64decode(image_data))
                    else:
                        f.write(image_data)

                return str(image_path)

        return None

    except Exception as e:
        logger.error(f"      ❌ 生成失败: {e}")
        return None


def generate_cards_from_prompts(
    card_prompts: dict, output_dir: str
) -> list[str]:
    """从预生成的图卡 prompt 生成图片

    Args:
        card_prompts: 图卡 prompt 字典（包含 cards 列表）
        output_dir: 输出目录

    Returns:
        list[str]: 生成的图片文件路径列表
    """
    client = init_client()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    cards = card_prompts.get("cards", [])
    if not cards:
        logger.warning("没有图卡 prompt，跳过生成")
        return []

    logger.info(f"🎨 开始生成 {len(cards)} 张知识图卡...")
    generated_images = []

    for i, card in enumerate(cards, 1):
        card_name = card.get("name", f"card_{i}")
        card_title = card.get("title", f"图卡 {i}")
        card_prompt = card.get("prompt", "")

        logger.info(f"   [{i}/{len(cards)}] {card_title}...")

        if not card_prompt:
            logger.warning(f"      ⚠️ 没有 prompt，跳过")
            continue

        filename = f"knowledge_card_{i}_{card_name}"
        image_path = generate_image_from_prompt(client, card_prompt, output_path, filename)

        if image_path:
            logger.info(f"      ✅ 保存: {os.path.basename(image_path)}")
            generated_images.append(image_path)
        else:
            logger.warning(f"      ⚠️ 生成失败")

    return generated_images


def generate_knowledge_cards(
    youtube_url: str, output_dir: str, max_cards: int = 5
) -> list[str]:
    """独立模式：分析视频并生成图卡（兼容旧接口）

    会单独调用一次 Gemini API 分析视频。
    推荐使用 generate_cards_from_prompts() 配合 gemini_notes 的联合输出。
    """
    from gemini_notes import generate_notes_and_card_prompts

    logger.info("📺 分析视频并生成图卡 prompt...")
    _, card_prompts = generate_notes_and_card_prompts(youtube_url, max_cards)

    if not card_prompts:
        logger.error("❌ 视频分析失败，无法生成图卡")
        return []

    return generate_cards_from_prompts(card_prompts, output_dir)


def main():
    parser = argparse.ArgumentParser(description="Gemini 知识图卡生成")
    parser.add_argument("--url", help="YouTube 视频 URL（独立模式）")
    parser.add_argument("--prompts", help="图卡 prompt JSON 文件路径")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--max-cards", type=int, default=5, help="最大图卡数量 (1-5)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if not args.url and not args.prompts:
        print("错误: 请指定 --url 或 --prompts")
        sys.exit(1)

    if args.prompts:
        # 从 JSON 文件加载 prompt
        card_prompts = json.loads(Path(args.prompts).read_text(encoding='utf-8'))
        generated_images = generate_cards_from_prompts(card_prompts, args.output)
    else:
        # 独立模式：分析视频
        generated_images = generate_knowledge_cards(args.url, args.output, min(args.max_cards, 5))

    print()
    if generated_images:
        print(f"🎉 完成！生成了 {len(generated_images)} 张知识图卡:")
        for img in generated_images:
            print(f"   - {img}")
    else:
        print("⚠️ 未能生成任何图卡")

    sys.exit(0 if generated_images else 1)


if __name__ == "__main__":
    main()
