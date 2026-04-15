#!/usr/bin/env python3
"""
Gemini 学习笔记生成

调用 Gemini API，直接传入 YouTube 视频 URL，生成中文学习笔记。
Gemini 原生支持读取 YouTube URL，不需要下载字幕。

用法：
  python scripts/gemini_notes.py --url <youtube_url> [--output <path>]
"""

import argparse
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
    return genai.Client(api_key=api_key)


def load_prompts() -> tuple[str, str]:
    """读取用户背景和输出格式配置"""
    user_profile_path = PROJECT_DIR / "config" / "user_profile.md"
    output_format_path = PROJECT_DIR / "config" / "output_format.md"

    user_profile = user_profile_path.read_text(encoding='utf-8') if user_profile_path.exists() else ""
    output_format = output_format_path.read_text(encoding='utf-8') if output_format_path.exists() else ""

    return user_profile, output_format


def generate_notes(youtube_url: str) -> str:
    """生成学习笔记

    Args:
        youtube_url: YouTube 视频链接

    Returns:
        str: Markdown 格式的学习笔记
    """
    client = init_client()
    user_profile, output_format = load_prompts()

    prompt = f"""你是一个专业的视频学习助手。请仔细观看这个 YouTube 视频，然后根据以下要求生成中文学习笔记。

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

    print(f"🎬 正在分析视频: {youtube_url}")
    print(f"🤖 使用模型: {MODEL}")

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

    return text


def main():
    parser = argparse.ArgumentParser(description="Gemini 学习笔记生成")
    parser.add_argument("--url", required=True, help="YouTube 视频 URL")
    parser.add_argument("--output", "-o", help="输出文件路径（默认打印到终端）")

    args = parser.parse_args()

    try:
        notes = generate_notes(args.url)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(notes, encoding='utf-8')
            print(f"\n✅ 笔记已保存: {args.output}")
        else:
            print("\n" + "=" * 60)
            print(notes)
            print("=" * 60)

    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
