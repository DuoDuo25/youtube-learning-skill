#!/usr/bin/env python3
"""
Gemini 学习笔记 + 知识图卡 prompt 生成（一次 API 调用）

调用 Gemini API，直接传入 YouTube 视频 URL，同时生成：
1. 中文学习笔记（Markdown）
2. 知识图卡的图片生成 prompt（JSON）

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

# 分隔符，用于区分笔记和图卡 prompt
SEPARATOR = "===KNOWLEDGE_CARDS_JSON==="


def init_client() -> genai.Client:
    """初始化 Gemini 客户端（官方 API endpoint）"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("错误: 请在 .env 中配置 GEMINI_API_KEY")
        print("获取方式: https://aistudio.google.com/apikey")
        sys.exit(1)
    return genai.Client(api_key=api_key)


def load_prompts() -> tuple[str, str]:
    """读取用户背景和输出格式配置"""
    user_profile_path = PROJECT_DIR / "config" / "user_profile.md"
    output_format_path = PROJECT_DIR / "config" / "output_format.md"

    user_profile = user_profile_path.read_text(encoding='utf-8') if user_profile_path.exists() else ""
    output_format = output_format_path.read_text(encoding='utf-8') if output_format_path.exists() else ""

    return user_profile, output_format


def generate_notes_and_card_prompts(
    youtube_url: str, max_cards: int = 5
) -> tuple[str, dict | None]:
    """一次 API 调用同时生成学习笔记和知识图卡 prompt

    Args:
        youtube_url: YouTube 视频链接
        max_cards: 最大图卡数量

    Returns:
        (notes_markdown, card_prompts_dict)
        card_prompts_dict 可能为 None（如果解析失败，笔记仍然可用）
    """
    client = init_client()
    user_profile, output_format = load_prompts()

    prompt = f"""你是一个专业的视频学习助手和知识可视化专家。请仔细观看这个 YouTube 视频，然后完成两个任务。

## 视频信息
链接: {youtube_url}

## 用户背景
{user_profile}

---

# 任务一：生成学习笔记

## 输出格式要求
{output_format}

请严格按照输出格式要求生成学习笔记。注意：
1. 必须完整观看视频后再写笔记，不要遗漏任何重要内容
2. 全部使用中文（专有名词保留英文）
3. Insight 部分必须结合用户背景来写
4. 详细内容部分要保留所有有价值的信息
5. 直接输出 Markdown 内容，不要用代码块包裹
6. 链接字段必须使用上面提供的实际 YouTube 链接，不要用占位符

---

# 任务二：生成知识图卡 prompt

在学习笔记之后，请输出分隔符 `{SEPARATOR}`，然后输出知识图卡的 JSON。

## 图卡生成规则
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

## 图卡 JSON 格式
在分隔符 `{SEPARATOR}` 之后，输出以下 JSON（不要用代码块包裹）：
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

---

# 输出格式

先输出完整的学习笔记 Markdown，然后输出分隔符，最后输出图卡 JSON：

[学习笔记 Markdown 内容]

{SEPARATOR}

[图卡 JSON]
"""

    logger.info(f"🎬 正在分析视频: {youtube_url}")
    logger.info(f"🤖 使用模型: {MODEL}")

    response = client.models.generate_content(
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
    )

    if not response.candidates:
        raise Exception("Gemini 未返回任何结果")

    text = response.candidates[0].content.parts[0].text

    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    # 按分隔符拆分笔记和图卡 JSON
    if SEPARATOR in text:
        parts = text.split(SEPARATOR, 1)
        notes_markdown = parts[0].strip()
        json_text = parts[1].strip()

        # 清理 JSON 文本（去掉可能的代码块包裹）
        if json_text.startswith("```json"):
            json_text = json_text[len("```json"):].strip()
        if json_text.startswith("```"):
            json_text = json_text[3:].strip()
        if json_text.endswith("```"):
            json_text = json_text[:-3].strip()

        # 解析 JSON
        try:
            json_start = json_text.find('{')
            json_end = json_text.rfind('}') + 1
            if json_start != -1 and json_end > json_start:
                card_prompts = json.loads(json_text[json_start:json_end])
                card_count = card_prompts.get("card_count", len(card_prompts.get("cards", [])))
                logger.info(f"   ✅ 笔记和图卡 prompt 生成完成（{card_count} 张图卡）")
                return notes_markdown, card_prompts
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"   ⚠️ 图卡 JSON 解析失败: {e}，笔记仍然可用")

        return notes_markdown, None
    else:
        logger.warning("   ⚠️ 未找到分隔符，仅返回笔记")
        return text, None


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
