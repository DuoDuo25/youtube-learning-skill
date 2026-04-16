#!/usr/bin/env python3
"""
飞书文档同步（基于 lark-cli）

通过 lark-cli 完成所有飞书操作：
- 创建飞书文档（从 Markdown）
- 插入知识图卡图片
- 发送完成通知卡片

用法：
  python scripts/feishu_sync.py --markdown <path> --title <title> --url <video_url>
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load environment
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Config from env
FEISHU_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")
FEISHU_FOLDER_TOKEN = os.getenv("FEISHU_FOLDER_TOKEN", "")


def run_lark_cli(args: list[str], timeout: int = 60, cwd: str | None = None) -> tuple[bool, str, str]:
    """执行 lark-cli 命令

    Returns:
        (success, stdout, stderr)
    """
    cmd = ['lark-cli'] + args
    logger.debug(f"执行: {' '.join(cmd)}" + (f" (cwd={cwd})" if cwd else ""))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"lark-cli 超时 ({timeout}s)")
        return False, "", "timeout"
    except FileNotFoundError:
        logger.error("lark-cli 未安装，请先安装: https://github.com/nicepkg/lark-cli")
        return False, "", "lark-cli not found"


def create_doc(title: str, markdown_path: str, folder_token: str = "") -> str | None:
    """创建飞书文档

    Args:
        title: 文档标题
        markdown_path: Markdown 文件路径
        folder_token: 存放文件夹 token（可选）

    Returns:
        str | None: 文档 URL，失败返回 None
    """
    folder = folder_token or FEISHU_FOLDER_TOKEN

    # lark-cli 要求 --markdown 使用相对路径，需要 cd 到文件所在目录
    md_path = Path(markdown_path).resolve()
    md_dir = str(md_path.parent)

    # 清理 markdown 并提取中文标题
    content = md_path.read_text(encoding='utf-8')
    # 从首行 # 标题提取中文标题，用作飞书文档标题
    title_match = re.match(r'^# (.+)', content)
    doc_title = title_match.group(1).strip() if title_match else title
    # 去掉首行 # 标题（避免与 --title 重复）
    cleaned = re.sub(r'^# .+\n+', '', content)
    # 去掉本地图片引用（图片通过 media-insert 单独插入）
    cleaned = re.sub(r'!\[.*?\]\([^)]+\)\n*', '', cleaned)
    temp_md = md_path.parent / f".feishu_{md_path.name}"
    temp_md.write_text(cleaned, encoding='utf-8')
    md_name = temp_md.name

    cmd = [
        'docs', '+create',
        '--title', doc_title,
        '--markdown', f'@{md_name}',
        '--as', 'user'
    ]

    if folder:
        cmd.extend(['--folder-token', folder])

    logger.info(f"📄 创建飞书文档: {title}")
    success, stdout, stderr = run_lark_cli(cmd, timeout=120, cwd=md_dir)

    if not success:
        logger.error(f"文档创建失败: {stderr}")
        return None

    # 从输出中提取文档 URL
    doc_url = extract_doc_url(stdout)
    if doc_url:
        logger.info(f"   ✅ 文档创建成功: {doc_url}")
    else:
        logger.warning(f"   ⚠️ 文档已创建但无法提取 URL")
        logger.debug(f"   stdout: {stdout[:500]}")

    return doc_url


def extract_doc_url(output: str) -> str | None:
    """从 lark-cli 输出中提取文档 URL"""
    # 匹配飞书文档 URL
    match = re.search(r'https://[^\s]*feishu\.cn/docx/[^\s"\']+', output)
    if match:
        return match.group(0)

    # 尝试从 JSON 输出提取
    try:
        data = json.loads(output)
        # lark-cli docs +create 可能返回 document_id 或 url
        if 'url' in data:
            return data['url']
        doc_id = data.get('document', {}).get('document_id') or data.get('document_id')
        if doc_id:
            return f"https://feishu.cn/docx/{doc_id}"
    except (json.JSONDecodeError, TypeError):
        pass

    return None


def insert_images(doc_url: str, image_paths: list[str]) -> int:
    """向文档插入知识图卡图片

    Args:
        doc_url: 文档 URL 或 document_id
        image_paths: 图片文件路径列表

    Returns:
        int: 成功插入的图片数量
    """
    if not image_paths:
        return 0

    success_count = 0
    for i, image_path in enumerate(image_paths, 1):
        img_path = Path(image_path).resolve()
        if not img_path.exists():
            logger.warning(f"   ⚠️ 图片不存在: {image_path}")
            continue

        img_dir = str(img_path.parent)
        img_name = img_path.name

        logger.info(f"   🖼️ 插入图片 [{i}/{len(image_paths)}]: {img_name}")

        success, stdout, stderr = run_lark_cli([
            'docs', '+media-insert',
            '--doc', doc_url,
            '--file', img_name,
            '--as', 'user'
        ], timeout=120, cwd=img_dir)

        if success:
            success_count += 1
            logger.info(f"      ✅ 插入成功")
        else:
            logger.error(f"      ❌ 插入失败: {stderr}")

    return success_count


def build_completion_card(
    title: str, doc_url: str, video_url: str,
    summary: str = "", channel_name: str = "", video_duration: str = "",
    published: str = ""
) -> dict:
    """构建完成通知卡片"""
    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{title}**"}
        },
    ]

    if summary:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary}
        })

    if channel_name or video_duration or published:
        fields = []
        if channel_name:
            fields.append({"is_short": True, "text": {"tag": "lark_md", "content": f"**频道**\n{channel_name}"}})
        if video_duration:
            fields.append({"is_short": True, "text": {"tag": "lark_md", "content": f"**时长**\n{video_duration}"}})
        if published:
            fields.append({"is_short": True, "text": {"tag": "lark_md", "content": f"**发布时间**\n{published}"}})
        elements.append({"tag": "div", "fields": fields})

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📄 查看完整笔记"},
                "type": "primary",
                "url": doc_url
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 查看原视频"},
                "type": "default",
                "url": video_url
            }
        ]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 学习笔记已生成"},
            "template": "green"
        },
        "elements": elements
    }


def send_notification(
    chat_id: str, title: str, doc_url: str, video_url: str,
    summary: str = "", channel_name: str = "", video_duration: str = "",
    published: str = ""
) -> bool:
    """发送完成通知卡片"""
    target_chat = chat_id or FEISHU_CHAT_ID
    if not target_chat:
        logger.warning("未配置 FEISHU_CHAT_ID，跳过通知发送")
        return False

    card = build_completion_card(
        title, doc_url, video_url,
        summary=summary, channel_name=channel_name,
        video_duration=video_duration, published=published
    )

    logger.info("📨 发送完成通知...")
    success, stdout, stderr = run_lark_cli([
        'im', '+messages-send',
        '--chat-id', target_chat,
        '--content', json.dumps(card),
        '--msg-type', 'interactive',
        '--as', 'bot'
    ])

    if success:
        logger.info("   ✅ 通知发送成功")
    else:
        logger.error(f"   ❌ 通知发送失败: {stderr}")

    return success


def sync_to_feishu(
    markdown_path: str,
    title: str,
    video_url: str,
    image_paths: list[str] | None = None,
    chat_id: str = "",
    summary: str = "",
    channel_name: str = "",
    video_duration: str = "",
    published: str = ""
) -> str | None:
    """完整的飞书同步流程"""
    logger.info(f"🚀 开始同步到飞书: {title}")

    # 1. 创建文档
    doc_url = create_doc(title, markdown_path)
    if not doc_url:
        logger.error("文档创建失败，终止同步")
        return None

    # 2. 插入图片
    if image_paths:
        logger.info(f"🖼️ 插入 {len(image_paths)} 张知识图卡...")
        inserted = insert_images(doc_url, image_paths)
        logger.info(f"   图片插入完成: {inserted}/{len(image_paths)}")

    # 3. 发送通知
    send_notification(
        chat_id, title, doc_url, video_url,
        summary=summary, channel_name=channel_name,
        video_duration=video_duration, published=published
    )

    logger.info(f"✅ 同步完成: {doc_url}")
    return doc_url


def main():
    parser = argparse.ArgumentParser(description="飞书文档同步")
    parser.add_argument("--markdown", required=True, help="Markdown 文件路径")
    parser.add_argument("--title", required=True, help="文档标题")
    parser.add_argument("--url", default="", help="YouTube 视频 URL")
    parser.add_argument("--images", nargs="*", help="知识图卡图片路径")
    parser.add_argument("--chat-id", default="", help="飞书群聊 ID")
    parser.add_argument("--folder-token", default="", help="飞书文件夹 token")
    parser.add_argument("--channel-name", default="", help="YouTube 频道名")
    parser.add_argument("--video-duration", default="", help="视频时长")
    parser.add_argument("--published", default="", help="发布时间")

    args = parser.parse_args()

    if not os.path.exists(args.markdown):
        print(f"❌ 文件不存在: {args.markdown}")
        sys.exit(1)

    if args.folder_token:
        global FEISHU_FOLDER_TOKEN
        FEISHU_FOLDER_TOKEN = args.folder_token

    doc_url = sync_to_feishu(
        markdown_path=args.markdown,
        title=args.title,
        video_url=args.url,
        image_paths=args.images,
        chat_id=args.chat_id,
        channel_name=args.channel_name,
        video_duration=args.video_duration,
        published=args.published
    )

    if doc_url:
        print(f"\n✅ 飞书文档: {doc_url}")
    else:
        print("\n❌ 同步失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
