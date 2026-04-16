#!/usr/bin/env python3
"""
飞书回调服务 + 学习流水线

通过飞书 SDK 长连接（WebSocket）接收卡片回调。
用户点击「开始学习」后，编排完整的学习流水线：
1. Gemini 生成学习笔记
2. Gemini 生成知识图卡
3. 创建飞书文档并插入图片
4. 发送完成通知

用法：
  python scripts/callback_server.py
"""

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import httpx
import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

# 获取回调响应中的类型类
_resp_types = P2CardActionTriggerResponse._types
CallBackToast = _resp_types["toast"]
CallBackCard = _resp_types["card"]
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
OUTPUTS_DIR = DATA_DIR / "outputs"

# Config from env
APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")

if not APP_ID or not APP_SECRET:
    print("❌ 请在 .env 中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
    sys.exit(1)

# 确保 scripts 目录在 Python 路径中（用于导入同目录模块）
sys.path.insert(0, str(Path(__file__).parent))


# ==================== 卡片模板 ====================

def generate_processing_card(video_title: str) -> dict:
    """生成"正在学习中"状态卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📚 正在学习中..."},
            "template": "orange"
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{video_title}**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": (
                    "⏳ 正在分析视频、生成笔记和知识图卡...\n\n"
                    "完成后会自动发送飞书文档链接"
                )}
            }
        ]
    }


def generate_completed_card(
    video_title: str, doc_url: str, video_url: str = "",
    summary: str = "", channel_name: str = "", video_duration: str = "",
    published: str = ""
) -> dict:
    """生成"已完成"状态卡片"""
    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{video_title}**"}
        },
    ]

    # 笔记摘要
    if summary:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary}
        })

    # 频道名 + 视频时长 + 发布时间
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


def generate_error_card(video_title: str, error: str) -> dict:
    """生成"处理失败"状态卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "❌ 处理失败"},
            "template": "red"
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{video_title}**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"错误信息: {error[:200]}"}
            }
        ]
    }


# ==================== 卡片更新 ====================

def update_card_via_api(token: str, card: dict) -> bool:
    """通过 callback token 更新卡片状态"""
    try:
        # 获取 tenant_access_token
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET}
        )
        access_token = token_resp.json().get("tenant_access_token")

        # 更新卡片
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/interactive/v1/card/update",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"token": token, "card": card}
        )
        return resp.json().get("code") == 0
    except Exception as e:
        logger.error(f"更新卡片失败: {e}")
        return False


# ==================== 学习流水线 ====================

def sanitize_filename(title: str, max_len: int = 60) -> str:
    """清理文件名"""
    safe = re.sub(r'[<>:"/\\|?*]', '', title)
    return safe[:max_len].strip()


def extract_summary(markdown: str) -> str:
    """从笔记 markdown 中提取第一段总结文案"""
    lines = markdown.split('\n')
    # 跳过标题行和空行，找第一段正文
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!'):
            continue
        # 截取合理长度
        if len(line) > 200:
            line = line[:200] + "..."
        return line
    return ""


def process_video(video_url: str, video_title: str, callback_token: str, chat_id: str,
                  channel_name: str = "", video_duration: str = "", published: str = ""):
    """完整学习流水线（在后台线程中执行）

    1. 生成学习笔记
    2. 生成知识图卡
    3. 将图卡引用追加到笔记
    4. 创建飞书文档
    5. 插入图片
    6. 发送通知
    7. 更新原卡片状态
    """
    start_time = datetime.now()
    logger.info(f"🚀 开始处理: {video_title}")

    try:
        # 准备输出目录
        output_dir = OUTPUTS_DIR / sanitize_filename(video_title)
        output_dir.mkdir(parents=True, exist_ok=True)
        notes_path = output_dir / "output.md"

        # Step 1: 一次 API 调用生成笔记 + 图卡 prompt
        logger.info("📝 Step 1/3: 生成学习笔记和图卡 prompt...")
        from gemini_notes import generate_notes_and_card_prompts
        notes_markdown, card_prompts = generate_notes_and_card_prompts(video_url)
        notes_path.write_text(notes_markdown, encoding='utf-8')
        logger.info(f"   ✅ 笔记已保存: {notes_path}")

        # Step 2: 从 prompt 生成知识图卡图片
        image_paths = []
        if card_prompts:
            logger.info("🎨 Step 2/3: 生成知识图卡图片...")
            from gemini_cards import generate_cards_from_prompts
            image_paths = generate_cards_from_prompts(card_prompts, str(output_dir))
            logger.info(f"   ✅ 生成了 {len(image_paths)} 张图卡")
        else:
            logger.warning("   ⚠️ 未获取到图卡 prompt，跳过图卡生成")

        # Step 3: 同步到飞书
        logger.info("📄 Step 3/3: 创建飞书文档...")
        summary = extract_summary(notes_markdown)
        from feishu_sync import sync_to_feishu
        doc_url = sync_to_feishu(
            markdown_path=str(notes_path),
            title=video_title,
            video_url=video_url,
            image_paths=image_paths,
            chat_id=chat_id,
            summary=summary,
            channel_name=channel_name,
            video_duration=video_duration,
            published=published
        )

        if not doc_url:
            raise Exception("飞书文档创建失败")

        # 更新原卡片为完成状态
        logger.info("✅ 更新卡片状态")
        update_card_via_api(callback_token, generate_completed_card(
            video_title, doc_url, video_url,
            summary=summary, channel_name=channel_name,
            video_duration=video_duration, published=published
        ))

        logger.info(f"🎉 处理完成: {video_title} -> {doc_url}")

    except Exception as e:
        logger.exception(f"处理失败: {e}")
        update_card_via_api(callback_token, generate_error_card(video_title, str(e)))
    finally:
        processing_tasks.discard(video_url)


# 正在处理的任务（防止重复）
processing_tasks: set[str] = set()


# ==================== 卡片回调处理 ====================

def handle_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """处理飞书卡片按钮点击回调

    使用 lark_oapi 的 P2CardActionTrigger 事件模型。
    """
    logger.info("收到卡片回调")

    resp = P2CardActionTriggerResponse()

    try:
        # 从事件中提取 action 信息
        event = data.event
        if not event:
            logger.warning("事件数据为空")
            return resp

        # event 是一个 dict-like 对象，提取 action.value
        action = event.get("action", {}) if isinstance(event, dict) else getattr(event, "action", {})
        if isinstance(action, dict):
            action_value = action.get("value", {})
        else:
            action_value = getattr(action, "value", {})

        # 解析 action value
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
            except (json.JSONDecodeError, TypeError):
                action_value = {"action": action_value}

        logger.info(f"Action: {action_value}")

        # 获取 callback token（用于后续更新卡片）
        callback_token = None
        if isinstance(event, dict):
            callback_token = event.get("token")
        else:
            callback_token = getattr(event, "token", None)

        # 处理"开始学习"
        if action_value.get("action") == "start_learning":
            video_url = action_value.get("video_url")
            video_title = action_value.get("video_title", "Unknown Video")
            channel_name = action_value.get("channel_name", "")
            video_duration = action_value.get("video_duration", "")
            published = action_value.get("published", "")
            chat_id = action_value.get("chat_id", FEISHU_CHAT_ID)

            if not video_url:
                toast = CallBackToast()
                toast.type = "error"
                toast.content = "视频链接无效"
                resp.toast = toast
                return resp

            # 检查是否正在处理
            if video_url in processing_tasks:
                toast = CallBackToast()
                toast.type = "warning"
                toast.content = "该视频正在处理中..."
                resp.toast = toast
                return resp

            # 标记为处理中
            processing_tasks.add(video_url)

            # 启动后台线程
            thread = threading.Thread(
                target=process_video,
                args=(video_url, video_title, callback_token, chat_id,
                      channel_name, video_duration, published),
                daemon=True
            )
            thread.start()

            # 立即返回"处理中"卡片
            toast = CallBackToast()
            toast.type = "info"
            toast.content = "开始学习，请稍候..."
            resp.toast = toast

            card = CallBackCard()
            card.type = "raw"
            card.data = generate_processing_card(video_title)
            resp.card = card
            return resp

        return resp

    except Exception as e:
        logger.exception(f"回调处理错误: {e}")
        toast = CallBackToast()
        toast.type = "error"
        toast.content = f"处理错误: {str(e)[:50]}"
        resp.toast = toast
        return resp


# ==================== 主入口 ====================

def main():
    logger.info("🚀 启动飞书回调服务（长连接）...")
    logger.info(f"   App ID: {APP_ID}")
    logger.info(f"   Chat ID: {FEISHU_CHAT_ID or '(未配置)'}")

    # 创建事件处理器，注册卡片回调
    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_card_action_trigger(handle_card_action) \
        .build()

    # 创建 WebSocket 客户端
    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO
    )

    logger.info("✅ 长连接已建立，等待卡片回调...")
    logger.info("按 Ctrl+C 停止服务")

    # 启动（阻塞）
    ws_client.start()


if __name__ == "__main__":
    main()
