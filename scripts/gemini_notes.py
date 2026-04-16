#!/usr/bin/env python3
"""
Gemini 学习笔记 + 知识图卡 prompt 生成（两步 API 调用）

1. 第一次调用：传入 YouTube 视频 → 生成学习笔记
2. 第二次调用：传入笔记文本 → 生成图卡 prompt（不需要重新看视频）

用法：
  python scripts/gemini_notes.py --url <youtube_url> [--output <path>]
"""

import argparse
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

# Config
PROJECT_DIR = Path(__file__).parent.parent
MODEL = "gemini-3.1-pro-preview"


def init_client() -> genai.Client:
    """初始化 Gemini 客户端（官方 API endpoint）"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("错误: 请在 .env 中配置 GEMINI_API_KEY")
        print("获取方式: https://aistudio.google.com/apikey")
        sys.exit(1)
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=600000),  # 10 分钟
    )


def load_prompts() -> tuple[str, str]:
    """读取用户背景和输出格式配置"""
    user_profile_path = PROJECT_DIR / "config" / "user_profile.md"
    output_format_path = PROJECT_DIR / "config" / "output_format.md"

    user_profile = user_profile_path.read_text(encoding='utf-8') if user_profile_path.exists() else ""
    output_format = output_format_path.read_text(encoding='utf-8') if output_format_path.exists() else ""

    return user_profile, output_format


def _generate_notes(client: genai.Client, youtube_url: str) -> str:
    """第一步：分析视频生成学习笔记"""
    user_profile, output_format = load_prompts()

    prompt = f"""你是一个专业的视频学习助手。请仔细观看这个 YouTube 视频，生成学习笔记。

## 视频信息
链接: {youtube_url}

## 用户背景
{user_profile}

## 输出格式要求
{output_format}

请严格按照输出格式要求生成学习笔记。注意：
1. 必须完整观看视频后再写笔记，不要遗漏任何重要内容
2. 全部使用中文（专有名词保留英文）
3. Insight 部分必须结合用户背景来写
4. 详细内容部分要保留所有有价值的信息
5. 直接输出 Markdown 内容，不要用代码块包裹
6. 链接字段必须使用上面提供的实际 YouTube 链接，不要用占位符
"""

    logger.info(f"🎬 正在分析视频: {youtube_url}")
    logger.info(f"🤖 使用模型: {MODEL}")

    chunks = []
    for chunk in client.models.generate_content_stream(
        model=MODEL,
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
        config=types.GenerateContentConfig(
            temperature=0.7,
        )
    ):
        if chunk.candidates and chunk.candidates[0].content.parts:
            chunks.append(chunk.candidates[0].content.parts[0].text)

    if not chunks:
        raise Exception("Gemini 未返回任何结果")

    text = "".join(chunks)

    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    return text


def _generate_card_prompts(client: genai.Client, notes_markdown: str, max_cards: int = 5) -> dict | None:
    """第二步：根据笔记内容生成图卡 prompt（不需要视频）"""

    prompt = f"""你是一个知识可视化专家。根据以下学习笔记，生成知识图卡的图片生成 prompt。

## 学习笔记
{notes_markdown}

## 图卡生成规则
- 最多生成 {max_cards} 张知识图卡
- 你需要自主判断这篇笔记适合生成几张图卡（1-{max_cards} 张）
- 根据笔记内容自主决定每张图卡展示什么内容
- 不要强行凑数，如果内容简单，1-2 张图卡就够了
- 每张图卡应该有独立的价值，不要重复

## 可选的图卡类型（参考，不必全部使用）
- 核心概念/架构图：展示关键概念及其关系
- 工作流程图：展示流程、步骤、数据流
- 对比分析图：对比不同方案的优劣
- 功能拆解图：拆解某个系统/产品的功能模块
- 时间线/演进图：展示发展历程或版本演进
- 公式/原理图：可视化核心公式或原理
- 案例示意图：用具体案例说明抽象概念

## 输出格式
直接输出 JSON（不要用代码块包裹）：
{{
  "card_count": 实际生成的图卡数量,
  "cards": [
    {{
      "name": "图卡英文标识（用于文件名）",
      "title": "图卡中文标题",
      "type": "图卡类型",
      "prompt": "详细的图片生成 prompt，包含具体展示内容、视觉布局建议、中文文字标注"
    }}
  ]
}}
"""

    logger.info("🃏 生成图卡 prompt...")

    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=0.7,
        )
    )

    if not response.candidates or not response.candidates[0].content.parts:
        logger.warning("   ⚠️ Gemini 未返回图卡 prompt")
        return None

    json_text = response.candidates[0].content.parts[0].text.strip()

    # 清理 JSON 文本
    if json_text.startswith("```json"):
        json_text = json_text[len("```json"):].strip()
    if json_text.startswith("```"):
        json_text = json_text[3:].strip()
    if json_text.endswith("```"):
        json_text = json_text[:-3].strip()

    try:
        json_start = json_text.find('{')
        json_end = json_text.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            card_prompts = json.loads(json_text[json_start:json_end])
            card_count = card_prompts.get("card_count", len(card_prompts.get("cards", [])))
            logger.info(f"   ✅ 图卡 prompt 生成完成（{card_count} 张图卡）")
            return card_prompts
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"   ⚠️ 图卡 JSON 解析失败: {e}")

    return None


def generate_notes_and_card_prompts(
    youtube_url: str, max_cards: int = 5
) -> tuple[str, dict | None]:
    """两步生成：先笔记（需要视频），再图卡 prompt（纯文本）

    Args:
        youtube_url: YouTube 视频链接
        max_cards: 最大图卡数量

    Returns:
        (notes_markdown, card_prompts_dict)
    """
    client = init_client()

    # Step 1: 分析视频生成笔记
    notes_markdown = _generate_notes(client, youtube_url)
    logger.info(f"   ✅ 笔记生成完成（{len(notes_markdown)} 字符）")

    # Step 2: 根据笔记生成图卡 prompt（不需要视频）
    card_prompts = _generate_card_prompts(client, notes_markdown, max_cards)

    return notes_markdown, card_prompts


def generate_notes(youtube_url: str) -> str:
    """兼容旧接口：只返回学习笔记"""
    notes, _ = generate_notes_and_card_prompts(youtube_url)
    return notes


def main():
    parser = argparse.ArgumentParser(description="Gemini 学习笔记生成")
    parser.add_argument("--url", required=True, help="YouTube 视频 URL")
    parser.add_argument("--output", "-o", help="输出文件路径（默认打印到终端）")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    try:
        notes, card_prompts = generate_notes_and_card_prompts(args.url)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(notes, encoding='utf-8')
            print(f"\n✅ 笔记已保存: {args.output}")

            if card_prompts:
                json_path = output_path.parent / "card_prompts.json"
                json_path.write_text(json.dumps(card_prompts, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f"✅ 图卡 prompt 已保存: {json_path}")
        else:
            print("\n" + "=" * 60)
            print(notes)
            print("=" * 60)
            if card_prompts:
                print("\n📊 图卡 prompt:")
                print(json.dumps(card_prompts, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
