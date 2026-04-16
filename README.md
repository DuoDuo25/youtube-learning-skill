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
                                              ┌──────────────┐
                                              │  Gemini API   │
                                              │  Streaming    │
                                              │  分析视频     │
                                              └──────┬───────┘
                                                     │
                                         ┌───────────┴───────────┐
                                         ▼                       ▼
                                  ┌──────────────┐       ┌──────────────┐
                                  │  学习笔记     │       │  图卡 Prompt  │
                                  │  Markdown     │       │  JSON 生成   │
                                  └──────┬───────┘       └──────┬───────┘
                                         │                       │
                                         │               ┌───────┴───────┐
                                         │               ▼       ▼       ▼
                                         │            ┌─────┐ ┌─────┐ ┌─────┐
                                         │            │图卡1│ │图卡2│ │图卡3│
                                         │            │并行 │ │并行 │ │并行 │
                                         │            └──┬──┘ └──┬──┘ └──┬──┘
                                         │               └───────┼───────┘
                                         ▼                       ▼
                                  ┌──────────────────────────────────┐
                                  │  飞书文档（笔记 + 知识图卡）      │
                                  │  完成通知                        │
                                  └──────────────────────────────────┘
```

**三个运行组件：**
1. **RSS Monitor**（定时任务）：每小时检查订阅频道更新，发送飞书通知
2. **Callback Server**（常驻进程）：接收飞书回调，编排学习流水线
3. **YouTube OAuth**（一次性）：授权获取订阅列表

**学习流水线（两步 Gemini 调用）：**
1. **Step 1 - 生成笔记**：Streaming 模式调用 Gemini，传入 YouTube URL 分析视频，生成中文学习笔记
2. **Step 2 - 生成图卡 Prompt**：将笔记文本传给 Gemini，生成知识图卡的图片 Prompt（不需要重新看视频）
3. **Step 3 - 并行生图**：多张知识图卡通过 ThreadPoolExecutor 并行生成，每张支持自动重试

## 前置条件

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：`pip install yt-dlp` 或 `brew install yt-dlp`
- [lark-cli](https://github.com/nicepkg/lark-cli)：飞书命令行工具
- Gemini API Key：从 [Google AI Studio](https://aistudio.google.com/apikey) 获取
- 飞书应用：从[飞书开放平台](https://open.feishu.cn/)创建
- YouTube OAuth 凭据（可选）：从 [Google Cloud Console](https://console.cloud.google.com/) 获取

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/DuoDuo25/youtube-learning-skill.git
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

### 7. 自定义学习偏好

```bash
cp config/user_profile.example.md config/user_profile.md
# 编辑 config/user_profile.md，填入你的个人信息
```

`user_profile.md` 告诉 AI 你是谁——职业、技术水平、学习目标、兴趣领域。AI 会根据这些信息调整笔记的深度和角度。这个文件越认真填，笔记质量越高。

`config/output_format.md` 定义笔记结构和写作风格，可按需调整。

### 8. 测试

```bash
# 测试发送飞书通知卡片
python scripts/rss_monitor.py test https://www.youtube.com/watch?v=VIDEO_ID

# 测试 Gemini 笔记生成（streaming 模式，支持长视频）
python scripts/gemini_notes.py --url https://www.youtube.com/watch?v=VIDEO_ID

# 测试知识图卡生成（并行生成，自动重试）
python scripts/gemini_cards.py --prompts card_prompts.json --output ./test_output
```

### 9. 启动服务

```bash
# 启动飞书回调服务（保持运行）
python scripts/callback_server.py

# 设置定时任务（每小时检查一次）
crontab -e
# 添加：
# 17 * * * * cd /path/to/youtube-learning-skill && python3 scripts/rss_monitor.py check --hours 2 >> data/rss.log 2>&1
```

## 项目结构

```
youtube-learning-skill/
├── .env.example              # 环境变量模板
├── requirements.txt          # Python 依赖
├── config/
│   ├── user_profile.example.md # 用户背景模板
│   ├── user_profile.md       # 你的个人背景（需自行创建，已 gitignore）
│   └── output_format.md      # 笔记格式模板
├── scripts/
│   ├── youtube_oauth.py      # YouTube OAuth + 订阅管理
│   ├── rss_monitor.py        # RSS 监控 + 飞书通知
│   ├── callback_server.py    # 飞书回调 + 学习流水线编排
│   ├── gemini_notes.py       # Gemini 笔记生成（streaming + 两步调用）
│   ├── gemini_cards.py       # Gemini 知识图卡生成（并行 + 重试）
│   └── feishu_sync.py        # 飞书文档同步（lark-cli）
└── data/                     # 运行时数据（自动生成）
```

## 技术细节

### 两步 Gemini 调用

传统方案是一次 API 调用同时生成笔记和图卡 Prompt，但长视频（1h+）容易超时且输出不完整。改为两步：

1. **Step 1**：Streaming 模式分析视频生成笔记，保持连接不断开，避免 HTTP 超时
2. **Step 2**：用笔记文本生成图卡 Prompt，不需要重新传视频，速度快且稳定

### 并行图卡生成

知识图卡使用 `ThreadPoolExecutor` 并行生成，4 张图卡同时请求，总耗时等于最慢的一张。每张图卡支持最多 2 次自动重试，应对偶发的超时错误。

### 技术栈

- **视频分析**：Gemini 3.1 Pro（原生支持 YouTube URL，Streaming 模式）
- **图片生成**：Gemini 3 Pro Image Preview（2K 分辨率，并行生成）
- **视频信息**：yt-dlp
- **飞书集成**：lark-cli + lark-oapi SDK（WebSocket 长连接）
- **RSS 解析**：YouTube Atom Feed

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| 飞书报错 200340 | 检查是否开启了「长连接接收回调」 |
| 飞书报错 200672 | 卡片回调响应格式问题，检查 callback_server 日志 |
| 文档创建失败 | 检查 folder_token 和飞书权限配置 |
| Gemini 超时 | 长视频已使用 Streaming 模式，确保网络稳定 |
| 图卡生成失败 | 已有自动重试机制，检查 Gemini API 配额 |
| Gemini 报 429 | 免费额度用完，等一会或升级付费 |

## 加入社群

不想自己搭？加入飞书群，直接看 AI 生成的学习笔记：

[点击加入飞书群「嗨妮好 · 学习笔记站」](https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=4d7q8aa5-bcfe-4d27-8b9a-d88943da3396)

## License

MIT
