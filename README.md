# YouTube 自动学习工作流

自动监控 YouTube 订阅频道更新，一键生成中文学习笔记和知识图卡，同步到飞书文档。

## 架构

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  YouTube RSS    │────▶│  飞书通知卡片  │────▶│  用户点击     │
│  定时检查更新    │     │  「开始学习」  │     │  「开始学习」  │
└─────────────────┘     └──────────────┘     └──────┬───────┘
                                                     │
                                                     ▼
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  飞书文档       │◀────│  知识图卡     │◀────│  Gemini API   │
│  完成通知       │     │  2K 图片生成  │     │  分析视频     │
└─────────────────┘     └──────────────┘     └──────────────┘
```

**三个运行组件：**
1. **RSS Monitor**（定时任务）：每小时检查订阅频道更新，发送飞书通知
2. **Callback Server**（常驻进程）：接收飞书回调，编排学习流水线
3. **YouTube OAuth**（一次性）：授权获取订阅列表

## 前置条件

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：`pip install yt-dlp` 或 `brew install yt-dlp`
- [lark-cli](https://github.com/nicepkg/lark-cli)：飞书命令行工具
- Gemini API Key：从 [Google AI Studio](https://aistudio.google.com/apikey) 获取
- 飞书应用：从[飞书开放平台](https://open.feishu.cn/)创建
- YouTube OAuth 凭据：从 [Google Cloud Console](https://console.cloud.google.com/) 获取

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/youtube-learning-skill.git
cd youtube-learning-skill
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API 密钥
```

### 4. 配置 lark-cli

```bash
# 安装 lark-cli（如未安装）
npm install -g lark-cli

# 绑定飞书应用
lark-cli pair
# 按提示输入 App ID 和 App Secret，完成登录授权
```

### 5. 配置飞书应用

在[飞书开放平台](https://open.feishu.cn/)创建应用，需要：

1. **应用能力**：开启「机器人」
2. **事件与回调**：打开「使用长连接接收回调」，订阅「卡片回传交互」事件
3. **权限配置**：
   - `im:message` — 获取与发送消息
   - `im:message:send_as_bot` — 以机器人身份发送消息
   - `docx:document` — 读写文档
   - `docx:document:create` — 创建文档
   - `drive:drive` — 云空间文件上传
   - `drive:drive:folder` — 获取云空间文件夹信息
4. 将机器人添加到目标群聊，获取 `chat_id` 填入 `.env`
5. 在飞书云文档中创建一个文件夹用于存放学习笔记，获取 `folder_token` 填入 `.env`
   - 打开文件夹，URL 中 `/folder/` 后面的部分就是 `folder_token`

### 6. 设置 YouTube OAuth（可选）

如果想自动同步 YouTube 关注列表：

1. 在 Google Cloud Console 创建项目
2. 启用 YouTube Data API v3
3. 创建 OAuth 2.0 凭据（桌面应用类型）
4. 将 Client ID 和 Client Secret 填入 `.env`

```bash
# 首次授权
python scripts/youtube_oauth.py setup

# 同步订阅列表
python scripts/youtube_oauth.py sync
```

也可以手动添加频道：

```bash
python scripts/youtube_oauth.py add https://youtube.com/@channel_name --name "频道名"
```

### 7. 测试

```bash
# 测试发送飞书通知卡片
python scripts/rss_monitor.py test https://www.youtube.com/watch?v=VIDEO_ID

# 测试 Gemini 笔记生成
python scripts/gemini_notes.py --url https://www.youtube.com/watch?v=VIDEO_ID

# 测试知识图卡生成
python scripts/gemini_cards.py --url https://www.youtube.com/watch?v=VIDEO_ID --output ./test_output
```

### 8. 启动服务

```bash
# 启动飞书回调服务（保持运行）
python scripts/callback_server.py

# 设置定时任务（每小时检查一次）
crontab -e
# 添加：
# 17 * * * * cd /path/to/youtube-learning-skill && python3 scripts/rss_monitor.py check --hours 2 >> data/rss.log 2>&1
```

## 自定义配置

### 用户背景

编辑 `config/user_profile.md`，填入你的个人信息。Gemini 会根据这些信息生成个性化的学习笔记。

### 笔记格式

编辑 `config/output_format.md`，自定义笔记的结构和风格。

## 项目结构

```
youtube-learning-skill/
├── .env.example              # 环境变量模板
├── requirements.txt          # Python 依赖
├── config/
│   ├── user_profile.md       # 用户背景（可自定义）
│   └── output_format.md      # 笔记格式模板
├── scripts/
│   ├── youtube_oauth.py      # YouTube OAuth + 订阅管理
│   ├── rss_monitor.py        # RSS 监控 + 飞书通知
│   ├── callback_server.py    # 飞书回调 + 学习流水线
│   ├── gemini_notes.py       # Gemini 学习笔记生成
│   ├── gemini_cards.py       # Gemini 知识图卡生成
│   └── feishu_sync.py        # 飞书文档同步（lark-cli）
└── data/                     # 运行时数据（自动生成）
```

## 技术栈

- **视频分析**：Gemini 3.1 Pro（原生支持 YouTube URL）
- **图片生成**：Gemini 3 Pro Image Preview（2K 分辨率）
- **视频信息**：yt-dlp
- **飞书集成**：lark-cli + lark-oapi SDK（WebSocket 长连接）
- **RSS 解析**：YouTube Atom Feed

## License

MIT
