#!/usr/bin/env python3
"""
YouTube RSS 监控

定时检查订阅频道的新视频，通过 lark-cli 发送飞书交互卡片通知。

用法：
  python scripts/rss_monitor.py check [--hours 24] [--dry-run]
  python scripts/rss_monitor.py test <youtube_url>
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
CHANNELS_FILE = DATA_DIR / "channels.json"

# Config from env
FEISHU_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")


@dataclass
class Video:
    """YouTube 视频信息"""
    video_id: str
    title: str
    channel_name: str
    channel_id: str
    published: datetime
    url: str
    thumbnail: str = ""
    description: str = ""
    duration: str = ""
    live_status: str = ""


# ==================== 频道数据 ====================

def load_channels() -> dict:
    if not CHANNELS_FILE.exists():
        return {"channels": [], "seen_videos": [], "last_check": None}
    with open(CHANNELS_FILE) as f:
        return json.load(f)


def save_channels(config: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False, default=str)


# ==================== RSS 解析 ====================

def fetch_channel_videos(channel_id: str, channel_name: str = "") -> list[Video]:
    """从频道 RSS feed 获取最近视频"""
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    logger.info(f"Fetching RSS: {rss_url}")

    try:
        resp = httpx.get(rss_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"RSS 获取失败 ({channel_id}): {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/'
        }

        videos = []
        for entry in root.findall('atom:entry', ns):
            video_id = entry.find('yt:videoId', ns)
            title = entry.find('atom:title', ns)
            published = entry.find('atom:published', ns)
            author = entry.find('atom:author/atom:name', ns)

            media_group = entry.find('media:group', ns)
            thumbnail = ""
            description = ""
            if media_group is not None:
                thumb_elem = media_group.find('media:thumbnail', ns)
                if thumb_elem is not None:
                    thumbnail = thumb_elem.get('url', '')
                desc_elem = media_group.find('media:description', ns)
                if desc_elem is not None and desc_elem.text:
                    description = desc_elem.text

            if video_id is not None and title is not None:
                vid = Video(
                    video_id=video_id.text,
                    title=title.text,
                    channel_name=author.text if author is not None else channel_name,
                    channel_id=channel_id,
                    published=datetime.fromisoformat(
                        published.text.replace('Z', '+00:00')
                    ) if published is not None else datetime.now(),
                    url=f"https://www.youtube.com/watch?v={video_id.text}",
                    thumbnail=thumbnail,
                    description=description
                )
                videos.append(vid)

        logger.info(f"Found {len(videos)} videos from {channel_name or channel_id}")
        return videos

    except Exception as e:
        logger.error(f"RSS 解析失败 ({channel_id}): {e}")
        return []


# ==================== yt-dlp ====================

def get_video_info(video_url: str) -> tuple[str, str]:
    """用 yt-dlp 获取视频时长和直播状态

    Returns: (duration, live_status)
    """
    try:
        result = subprocess.run(
            ['yt-dlp', '--ignore-no-formats-error',
             '--print', '%(live_status)s|||%(duration_string)s',
             video_url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split('|||')
            live_status = parts[0] if parts else ""
            duration_str = parts[1] if len(parts) > 1 else ""
            if duration_str and duration_str != "NA":
                duration = format_duration(duration_str)
            else:
                duration = ""
            return duration, live_status
    except Exception as e:
        logger.warning(f"yt-dlp 获取信息失败 ({video_url}): {e}")
    return "", ""


def format_duration(duration_str: str) -> str:
    """格式化时长为中文"""
    if not duration_str:
        return ""

    duration_str = duration_str.strip()

    if ':' in duration_str:
        parts = duration_str.split(':')
        if len(parts) == 2:
            mins, secs = int(parts[0]), int(parts[1])
            if mins == 0:
                return f"{secs}秒"
            elif secs == 0:
                return f"{mins}分钟"
            else:
                return f"{mins}分{secs}秒"
        elif len(parts) == 3:
            hrs, mins, secs = int(parts[0]), int(parts[1]), int(parts[2])
            if hrs > 0:
                return f"{hrs}小时{mins}分钟" if mins > 0 else f"{hrs}小时"
            else:
                return f"{mins}分{secs}秒"
    else:
        try:
            secs = int(duration_str)
            if secs < 60:
                return f"{secs}秒"
            elif secs < 3600:
                mins = secs // 60
                remaining = secs % 60
                return f"{mins}分{remaining}秒" if remaining else f"{mins}分钟"
            else:
                hrs = secs // 3600
                mins = (secs % 3600) // 60
                return f"{hrs}小时{mins}分钟" if mins else f"{hrs}小时"
        except ValueError:
            return duration_str

    return duration_str


def truncate_description(desc: str, max_len: int = 150) -> str:
    if not desc:
        return ""
    desc = desc.replace('\n', ' ').strip()
    if len(desc) <= max_len:
        return desc
    return desc[:max_len].rstrip() + "..."


# ==================== 飞书通知（lark-cli） ====================

def build_video_card(video: Video) -> dict:
    """构建视频通知交互卡片"""
    info_parts = [f"📺 频道: {video.channel_name}"]

    if video.live_status == "is_upcoming":
        info_parts.append("🔴 即将首播")
    elif video.live_status == "is_live":
        info_parts.append("🔴 正在直播")
    elif video.live_status == "was_live":
        info_parts.append("📹 直播回放")
        if video.duration:
            info_parts.append(f"⏱️ 时长: {video.duration}")
    elif video.duration:
        info_parts.append(f"⏱️ 时长: {video.duration}")

    info_parts.append(f"🕐 发布: {video.published.strftime('%Y-%m-%d %H:%M')}")
    info_line = "\n".join(info_parts)

    desc_text = truncate_description(video.description, 150)

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{video.title}**"}
        },
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": info_line}
        }
    ]

    if desc_text:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"📝 {desc_text}"}
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📚 开始学习"},
                "type": "primary",
                "value": {
                    "action": "start_learning",
                    "video_url": video.url,
                    "video_title": video.title,
                    "channel_name": video.channel_name,
                    "video_duration": video.duration,
                    "published": video.published.strftime('%Y-%m-%d %H:%M'),
                    "chat_id": FEISHU_CHAT_ID
                }
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 查看原视频"},
                "type": "default",
                "url": video.url
            }
        ]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📺 新视频提醒"},
            "template": "blue"
        },
        "elements": elements
    }


def send_video_card(video: Video) -> bool:
    """通过 lark-cli 发送视频通知卡片"""
    if not FEISHU_CHAT_ID:
        logger.error("FEISHU_CHAT_ID 未配置")
        return False

    card = build_video_card(video)

    try:
        result = subprocess.run(
            [
                'lark-cli', 'im', '+messages-send',
                '--chat-id', FEISHU_CHAT_ID,
                '--content', json.dumps(card),
                '--msg-type', 'interactive',
                '--as', 'bot'
            ],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            logger.info(f"卡片发送成功: {video.title}")
            return True
        else:
            logger.error(f"卡片发送失败: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"lark-cli 调用失败: {e}")
        return False


# ==================== 监控逻辑 ====================

def check_for_new_videos(config: dict, hours_back: int = 24) -> list[Video]:
    """检查所有频道的新视频"""
    new_videos = []
    seen_videos = set(config.get("seen_videos", []))
    cutoff_time = datetime.now().astimezone() - timedelta(hours=hours_back)

    for channel in config.get("channels", []):
        if not channel.get("enabled", True):
            continue

        channel_id = channel.get("channel_id")
        channel_name = channel.get("name", "")

        if not channel_id:
            continue

        videos = fetch_channel_videos(channel_id, channel_name)

        for video in videos:
            if video.video_id in seen_videos:
                continue

            video_time = video.published
            if video_time.tzinfo is None:
                video_time = video_time.replace(tzinfo=cutoff_time.tzinfo)
            if video_time < cutoff_time:
                continue

            logger.info(f"新视频: {video.title}")
            video.duration, video.live_status = get_video_info(video.url)

            new_videos.append(video)
            seen_videos.add(video.video_id)

    # 保留最近 1000 条记录
    config["seen_videos"] = list(seen_videos)[-1000:]
    config["last_check"] = datetime.now().isoformat()

    return new_videos


def try_sync_subscriptions():
    """尝试同步 YouTube 订阅（如果配置了 OAuth）"""
    tokens_file = DATA_DIR / "youtube_tokens.json"
    if not tokens_file.exists():
        return

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return

    logger.info("同步 YouTube 订阅列表...")
    try:
        # 延迟导入，避免循环依赖
        from youtube_oauth import cmd_sync
        cmd_sync()
    except Exception as e:
        logger.warning(f"订阅同步失败（不影响 RSS 检查）: {e}")


# ==================== CLI ====================

def cmd_check(hours_back: int = 24, dry_run: bool = False):
    """检查新视频并发送通知"""
    # 先同步订阅
    try_sync_subscriptions()

    config = load_channels()

    if not config.get("channels"):
        logger.warning("没有配置任何频道，请先运行:")
        logger.warning("  python scripts/youtube_oauth.py sync  # 从 YouTube 同步")
        logger.warning("  python scripts/youtube_oauth.py add <url>  # 手动添加")
        return

    logger.info(f"检查 {len(config['channels'])} 个频道的新视频（最近 {hours_back} 小时）...")
    new_videos = check_for_new_videos(config, hours_back)

    if not new_videos:
        logger.info("没有新视频")
        return

    logger.info(f"发现 {len(new_videos)} 个新视频")

    for video in new_videos:
        logger.info(f"  - {video.title} ({video.channel_name})")
        if not dry_run:
            success = send_video_card(video)
            if success:
                logger.info(f"    ✓ 已发送通知")
            else:
                logger.error(f"    ✗ 通知发送失败")

    if not dry_run:
        save_channels(config)


def cmd_test(url: str):
    """测试：发送指定视频的通知卡片"""
    video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]

    title = "Unknown Title"
    channel = "Unknown Channel"
    description = ""

    try:
        result = subprocess.run(
            ['yt-dlp', '--ignore-no-formats-error',
             '--print', '%(title)s', '--print', '%(channel)s',
             '--print', '%(description)s', url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            title = lines[0] if lines else "Unknown Title"
            channel = lines[1] if len(lines) > 1 else "Unknown Channel"
            description = '\n'.join(lines[2:]) if len(lines) > 2 else ""
    except Exception as e:
        logger.warning(f"获取视频信息失败: {e}")

    duration, live_status = get_video_info(url)

    video = Video(
        video_id=video_id,
        title=title,
        channel_name=channel,
        channel_id="test",
        published=datetime.now(),
        url=url,
        description=description,
        duration=duration,
        live_status=live_status
    )

    success = send_video_card(video)
    if success:
        print(f"✓ 测试卡片已发送: {title} ({channel})")
    else:
        print("✗ 卡片发送失败")


def main():
    parser = argparse.ArgumentParser(description="YouTube RSS 监控")
    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="检查新视频")
    check_parser.add_argument("--hours", type=int, default=24, help="回溯小时数")
    check_parser.add_argument("--dry-run", "-d", action="store_true", help="仅检查，不发送通知")

    test_parser = subparsers.add_parser("test", help="测试发送卡片")
    test_parser.add_argument("url", help="YouTube 视频 URL")

    args = parser.parse_args()

    if args.command == "check":
        cmd_check(args.hours, args.dry_run)
    elif args.command == "test":
        cmd_test(args.url)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
