#!/usr/bin/env python3
"""
YouTube OAuth & 订阅管理

支持两种方式管理频道：
1. OAuth 授权同步 YouTube 关注列表（默认）
2. 手动添加频道 URL

用法：
  python scripts/youtube_oauth.py setup       # 首次 OAuth 授权
  python scripts/youtube_oauth.py sync        # 同步订阅列表
  python scripts/youtube_oauth.py add <url>   # 手动添加频道
  python scripts/youtube_oauth.py remove <id> # 移除频道
  python scripts/youtube_oauth.py list        # 列出所有频道
"""

import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Paths
PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
TOKENS_FILE = DATA_DIR / "youtube_tokens.json"
CHANNELS_FILE = DATA_DIR / "channels.json"

# YouTube API
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SUBSCRIPTIONS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
REDIRECT_URI = "http://localhost:8888"


# ==================== OAuth ====================

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """接收 OAuth 回调"""
    auth_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if 'code' in query:
            OAuthCallbackHandler.auth_code = query['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(
                b'<html><body style="font-family:sans-serif;text-align:center;padding-top:50px;">'
                b'<h1>Authorization Successful!</h1>'
                b'<p>You can close this window now.</p>'
                b'<script>window.close();</script>'
                b'</body></html>'
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed")

    def log_message(self, format, *args):
        pass


def get_oauth_tokens(client_id: str, client_secret: str) -> dict:
    """执行 OAuth 流程获取 access_token 和 refresh_token"""
    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent"
    }
    auth_url = f"{GOOGLE_AUTH_URL}?" + "&".join(f"{k}={v}" for k, v in auth_params.items())

    print(f"\n🔐 正在打开浏览器进行 YouTube 授权...")
    print(f"如果浏览器未自动打开，请手动访问：\n{auth_url}\n")

    # 启动本地服务器接收回调
    server = HTTPServer(('localhost', 8888), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.start()

    webbrowser.open(auth_url)

    server_thread.join(timeout=120)
    server.server_close()

    if not OAuthCallbackHandler.auth_code:
        raise Exception("授权失败 - 未收到授权码")

    # 用授权码换取 token
    resp = httpx.post(GOOGLE_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": OAuthCallbackHandler.auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    })
    tokens = resp.json()

    if 'error' in tokens:
        raise Exception(f"Token 交换失败: {tokens}")

    return tokens


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """用 refresh_token 刷新 access_token"""
    resp = httpx.post(GOOGLE_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    })
    tokens = resp.json()

    if 'error' in tokens:
        raise Exception(f"Token 刷新失败: {tokens}")

    return tokens['access_token']


# ==================== Token 管理 ====================

def load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        return {}
    with open(TOKENS_FILE) as f:
        return json.load(f)


def save_tokens(tokens: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOKENS_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)


# ==================== 频道管理 ====================

def load_channels() -> dict:
    if not CHANNELS_FILE.exists():
        return {"channels": [], "seen_videos": [], "last_sync": None}
    with open(CHANNELS_FILE) as f:
        return json.load(f)


def save_channels(config: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False, default=str)


def get_channel_id_from_url(url: str) -> str | None:
    """从 YouTube URL 提取频道 ID 或 handle"""
    patterns = [
        r'youtube\.com/channel/([^/?]+)',
        r'youtube\.com/@([^/?]+)',
        r'youtube\.com/c/([^/?]+)',
        r'youtube\.com/user/([^/?]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


# ==================== YouTube API ====================

def get_subscriptions(access_token: str) -> list:
    """获取用户的 YouTube 订阅列表"""
    subscriptions = []
    next_page_token = None

    while True:
        params = {
            "part": "snippet",
            "mine": "true",
            "maxResults": 50
        }
        if next_page_token:
            params["pageToken"] = next_page_token

        resp = httpx.get(
            YOUTUBE_SUBSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params
        )
        data = resp.json()

        if 'error' in data:
            raise Exception(f"API 错误: {data['error']}")

        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            resource = snippet.get('resourceId', {})
            subscriptions.append({
                "channel_id": resource.get('channelId'),
                "name": snippet.get('title'),
                "description": snippet.get('description', '')[:100],
            })

        next_page_token = data.get('nextPageToken')
        if not next_page_token:
            break

    return subscriptions


# ==================== CLI 命令 ====================

def cmd_setup():
    """首次 OAuth 授权"""
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ 请先在 .env 中配置 YOUTUBE_CLIENT_ID 和 YOUTUBE_CLIENT_SECRET")
        print("   获取方式：https://console.cloud.google.com/")
        print("   1. 创建项目（或使用已有项目）")
        print("   2. 启用 YouTube Data API v3")
        print("   3. 创建 OAuth 2.0 凭据（桌面应用类型）")
        sys.exit(1)

    tokens = load_tokens()
    if tokens.get('refresh_token'):
        print("✓ OAuth 已配置")
        choice = input("重新授权？(y/N): ").strip().lower()
        if choice != 'y':
            return

    try:
        result = get_oauth_tokens(client_id, client_secret)
        tokens = {
            "access_token": result.get('access_token'),
            "refresh_token": result.get('refresh_token'),
            "authorized_at": datetime.now().isoformat()
        }
        save_tokens(tokens)
        print("\n✅ OAuth 授权成功！")
        print("运行 'python scripts/youtube_oauth.py sync' 同步订阅列表")
    except Exception as e:
        print(f"\n❌ 授权失败: {e}")
        sys.exit(1)


def cmd_sync():
    """从 YouTube 同步订阅列表到本地"""
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    tokens = load_tokens()

    if not tokens.get('refresh_token'):
        print("❌ 尚未授权，请先运行: python scripts/youtube_oauth.py setup")
        sys.exit(1)

    print("🔄 正在同步 YouTube 订阅列表...")

    try:
        access_token = refresh_access_token(client_id, client_secret, tokens['refresh_token'])
        tokens['access_token'] = access_token
        save_tokens(tokens)

        subscriptions = get_subscriptions(access_token)
        print(f"✓ 获取到 {len(subscriptions)} 个订阅频道")

        channels_config = load_channels()
        existing_ids = {ch['channel_id'] for ch in channels_config.get('channels', [])}

        new_count = 0
        for sub in subscriptions:
            if sub['channel_id'] not in existing_ids:
                channels_config.setdefault('channels', []).append({
                    "channel_id": sub['channel_id'],
                    "name": sub['name'],
                    "enabled": True,
                    "source": "youtube_subscription",
                    "added_at": datetime.now().isoformat()
                })
                new_count += 1
                print(f"  + {sub['name']}")

        channels_config['last_sync'] = datetime.now().isoformat()
        save_channels(channels_config)

        print(f"\n✅ 同步完成！")
        print(f"   总频道数: {len(channels_config['channels'])}")
        print(f"   新增: {new_count}")

    except Exception as e:
        print(f"\n❌ 同步失败: {e}")
        sys.exit(1)


def cmd_add(url: str, name: str = ""):
    """手动添加频道"""
    channel_id = get_channel_id_from_url(url)
    if not channel_id:
        channel_id = url  # 可能直接传的是 channel ID

    channels_config = load_channels()

    # 检查是否已存在
    for ch in channels_config.get('channels', []):
        if ch.get('channel_id') == channel_id:
            print(f"⚠️ 频道已存在: {ch.get('name', channel_id)}")
            return

    channels_config.setdefault('channels', []).append({
        "channel_id": channel_id,
        "name": name or channel_id,
        "enabled": True,
        "source": "manual",
        "added_at": datetime.now().isoformat()
    })

    save_channels(channels_config)
    print(f"✅ 已添加频道: {name or channel_id} ({channel_id})")


def cmd_remove(channel_id: str):
    """移除频道"""
    channels_config = load_channels()
    channels = channels_config.get('channels', [])

    original_count = len(channels)
    channels_config['channels'] = [
        ch for ch in channels if ch.get('channel_id') != channel_id
    ]

    if len(channels_config['channels']) == original_count:
        print(f"⚠️ 未找到频道: {channel_id}")
        return

    save_channels(channels_config)
    print(f"✅ 已移除频道: {channel_id}")


def cmd_list():
    """列出所有频道"""
    channels_config = load_channels()
    channels = channels_config.get('channels', [])

    if not channels:
        print("\n暂无频道")
        print("运行 'python scripts/youtube_oauth.py sync' 从 YouTube 同步")
        print("或 'python scripts/youtube_oauth.py add <url>' 手动添加")
        return

    print(f"\n📺 监控频道列表（共 {len(channels)} 个）")
    print("-" * 65)
    print(f"{'状态':<4} {'名称':<30} {'来源':<12} {'频道ID'}")
    print("-" * 65)

    for ch in channels:
        status = "✓" if ch.get('enabled', True) else "✗"
        name = ch.get('name', 'Unknown')[:28]
        source = ch.get('source', 'unknown')[:10]
        cid = ch.get('channel_id', 'N/A')
        print(f" {status}   {name:<30} {source:<12} {cid}")

    enabled = sum(1 for ch in channels if ch.get('enabled', True))
    print("-" * 65)
    print(f"已启用: {enabled}/{len(channels)}")

    last_sync = channels_config.get('last_sync')
    if last_sync:
        print(f"上次同步: {last_sync}")


def main():
    parser = argparse.ArgumentParser(description="YouTube OAuth & 订阅管理")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("setup", help="首次 OAuth 授权")
    subparsers.add_parser("sync", help="同步 YouTube 订阅列表")

    add_parser = subparsers.add_parser("add", help="手动添加频道")
    add_parser.add_argument("url", help="频道 URL 或 ID")
    add_parser.add_argument("--name", "-n", default="", help="频道名称")

    remove_parser = subparsers.add_parser("remove", help="移除频道")
    remove_parser.add_argument("channel_id", help="频道 ID")

    subparsers.add_parser("list", help="列出所有频道")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "sync":
        cmd_sync()
    elif args.command == "add":
        cmd_add(args.url, args.name)
    elif args.command == "remove":
        cmd_remove(args.channel_id)
    elif args.command == "list":
        cmd_list()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
