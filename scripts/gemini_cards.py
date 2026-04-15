#!/usr/bin/env python3
"""
Gemini 知识图卡生成

两步流程：
1. 用 gemini-3.1-pro-preview 分析视频，决定图卡数量（1-5张）和内容
2. 用 gemini-3-pro-image-preview 生成 2K 分辨率知识图卡图片

用法：
  python scripts/gemini_cards.py --url <youtube_url> --output <dir> [--max-cards 5]
"""

import argparse
import base64
import json
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

# 模型配置
MODEL_VIDEO_ANALYSIS = "gemini-3.1-pro-preview"
MODEL_IMAGE_GEN = "gemini-3-pro-image-preview"

# ==================== Prompt 模板 ====================

PROMPT_GENERATOR_INSTRUCTION = """你是一个专业的知识可视化专家。你的任务是观看这个 YouTube 视频，然后为生成知识图卡写出详细的图片描述 prompt。

【重要】请务必仔细观看视频内容，确保生成的知识图卡与视频实际内容完全匹配。

## 目标用户
希望快速理解视频核心内容的学习者

## 图卡目的
帮助快速理解视频核心内容，将复杂概念可视化

## 生成规则
- 最多生成 {max_cards} 张知识图卡
- 你需要自主判断这个视频适合生成几张图卡（1-{max_cards} 张）
- 根据视频内容自主决定每张图卡展示什么内容
- 不要强行凑数，如果视频内容简单，1-2 张图卡就够了
- 每张图卡应该有独立的价值，不要重复

## 可选的图卡类型（参考，不必全部使用）
- 核心概念/架构图：展示关键概念及其关系
- 工作流程图：展示流程、步骤、数据流
- 对比分析图：对比不同方案的优劣
- 功能拆解图：拆解某个系统/产品的功能模块
- 时间线/演进图：展示发展历程或版本演进
- 公式/原理图：可视化核心公式或原理
- 案例示意图：用具体案例说明抽象概念

## 输出要求
对于每个图卡，请输出一个详细的图片生成 prompt，包含：
1. 具体要展示的内容（基于视频实际内容，不要编造）
2. 视觉布局建议
3. 要使用的文字标注（中文为主，专有名词保留英文）

请用以下 JSON 格式输出：
```json
{{
  "video_title": "视频标题",
  "video_summary": "一句话概括视频内容",
  "card_count": 实际生成的图卡数量,
  "cards": [
    {{
      "name": "图卡英文标识（用于文件名）",
      "title": "图卡中文标题",
      "type": "图卡类型",
      "prompt": "详细的图片生成 prompt..."
    }}
  ]
}}
```
"""

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


def analyze_video_and_generate_prompts(
    client: genai.Client, youtube_url: str, max_cards: int = 5
) -> dict | None:
    """Step 1: 用视频分析模型分析视频，生成图卡 prompt"""
    print("📺 分析视频内容...")

    prompt = PROMPT_GENERATOR_INSTRUCTION.format(max_cards=max_cards)

    try:
        response = client.models.generate_content(
            model=MODEL_VIDEO_ANALYSIS,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            file_data=types.FileData(
                                file_uri=youtube_url,
                                mime_type="video/mp4"
                            )
                        ),
                        types.Part(text=prompt)
                    ]
                )
            ],
            config=types.GenerateContentConfig(temperature=0.7)
        )

        if not response.candidates:
            print("   ⚠️ 没有响应")
            return None

        text = response.candidates[0].content.parts[0].text

        # 提取 JSON
        json_start = text.find('{')
        json_end = text.rfind('}') + 1

        if json_start != -1 and json_end > json_start:
            result = json.loads(text[json_start:json_end])
            print(f"   ✅ 分析完成: {result.get('video_title', 'Unknown')}")
            print(f"   📝 摘要: {result.get('video_summary', '')[:100]}")
            return result

        print(f"   ⚠️ 无法提取 JSON")
        return None

    except Exception as e:
        print(f"   ❌ 分析失败: {e}")
        return None


def generate_image_from_prompt(
    client: genai.Client, prompt: str, output_path: Path, filename: str
) -> str | None:
    """Step 2: 用图片生成模型生成单张知识图卡"""
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
        print(f"      ❌ 生成失败: {e}")
        return None


def generate_knowledge_cards(
    youtube_url: str, output_dir: str, max_cards: int = 5
) -> list[str]:
    """生成知识图卡（两步流程）

    Args:
        youtube_url: YouTube 视频链接
        output_dir: 输出目录
        max_cards: 最大图卡数量（1-5）

    Returns:
        list[str]: 生成的图片文件路径列表
    """
    client = init_client()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Step 1: 分析视频
    video_analysis = analyze_video_and_generate_prompts(client, youtube_url, max_cards)

    if not video_analysis or "cards" not in video_analysis:
        print("❌ 视频分析失败，无法生成图卡")
        return []

    cards = video_analysis.get("cards", [])
    actual_count = video_analysis.get("card_count", len(cards))
    print(f"\n📊 将生成 {actual_count} 张知识图卡")

    # Step 2: 逐张生成图片
    print("\n🎨 生成知识图卡...")
    generated_images = []

    for i, card in enumerate(cards, 1):
        card_name = card.get("name", f"card_{i}")
        card_title = card.get("title", f"图卡 {i}")
        card_prompt = card.get("prompt", "")

        print(f"   [{i}/{len(cards)}] {card_title}...")

        if not card_prompt:
            print(f"      ⚠️ 没有 prompt，跳过")
            continue

        filename = f"knowledge_card_{i}_{card_name}"
        image_path = generate_image_from_prompt(client, card_prompt, output_path, filename)

        if image_path:
            print(f"      ✅ 保存: {os.path.basename(image_path)}")
            generated_images.append(image_path)
        else:
            print(f"      ⚠️ 生成失败")

    return generated_images


def main():
    parser = argparse.ArgumentParser(description="Gemini 知识图卡生成")
    parser.add_argument("--url", required=True, help="YouTube 视频 URL")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--max-cards", type=int, default=5, help="最大图卡数量 (1-5)")

    args = parser.parse_args()

    print(f"🎬 视频链接: {args.url}")
    print(f"📁 输出目录: {args.output}")
    print(f"🎴 最大图卡数: {args.max_cards}")
    print()

    generated_images = generate_knowledge_cards(
        youtube_url=args.url,
        output_dir=args.output,
        max_cards=min(args.max_cards, 5)
    )

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
