# 还在担心错过教务通知？
# SJTU 你的信息助手

[![Python](https://img.shields.io/badge/python-≥3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

自动监控 [上海交通大学教务处](https://jwc.sjtu.edu.cn/) 网站公告页面，发现新公告时通过邮件通知。

## 功能

- 🔍 同时监控多个页面（新闻通告 + 面向学生的通知），自动去重
- 📧 发现新公告自动发送 HTML 格式邮件（含标题、链接、板块、日期）
- 💾 本地 JSON 持久化存储已读公告，避免重复通知
- 🔄 支持守护模式持续运行 / 单次检查 / 仅打印
- 📬 支持任意 SMTP 邮箱（QQ / 163 / Gmail / Outlook 等）

## 监控范围

| 页面 | 板块 |
|------|------|
| [新闻通告](https://jwc.sjtu.edu.cn/xwtg.htm) | 新闻中心、质控办、教学运行、注册学务、研究办、教学质量、综合办、语言文字、工会与支部、非学历教育管理办公室 |
| [面向学生的通知](https://jwc.sjtu.edu.cn/index/mxxsdtz.htm) | 选课、考试、竞赛、助管招聘等学生相关通知 |

## 项目结构

```
Web_bugger/
├── src/web_bugger/          # 源代码包
│   ├── __init__.py          # 包元信息
│   ├── cli.py               # 命令行入口
│   ├── config.py            # 配置管理 (dataclass)
│   ├── models.py            # 数据模型 (Announcement)
│   ├── monitor.py           # 监控编排器 (Monitor)
│   ├── notifier.py          # 邮件通知 (Notifier)
│   ├── scraper.py           # 网页爬虫 (Scraper)
│   └── storage.py           # 已读存储 (Storage)
├── tests/                   # 单元测试
├── pyproject.toml           # PEP 621 包配置
├── requirements.txt         # pip 依赖清单
├── .env.example             # 环境变量模板
├── LICENSE                  # MIT 许可证
└── README.md
```

## 快速开始

### 1. 安装

```bash
# 推荐：可编辑安装（含开发依赖）
pip install -e ".[dev]"

# 或仅安装运行依赖
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env` 文件：

| 变量 | 必填 | 说明 |
|------|:----:|------|
| `SENDER_EMAIL` | ✅ | 发件人邮箱地址 |
| `SENDER_PASSWORD` | ✅ | SMTP 授权码 / 应用专用密码 |
| `RECEIVER_EMAIL` | ✅ | 收件人邮箱地址 |
| `SMTP_SERVER` | | SMTP 服务器（默认 `smtp.qq.com`） |
| `SMTP_PORT` | | 端口（默认 `465`） |
| `SMTP_USE_SSL` | | 是否使用 SSL（默认 `true`） |
| `TARGET_URLS` | | 监控页面 URL，逗号分隔 |
| `CHECK_INTERVAL` | | 检查间隔秒数（默认 `300`） |

<details>
<summary>常见邮箱 SMTP 配置</summary>

| 邮箱 | SMTP_SERVER | SMTP_PORT | SMTP_USE_SSL | 授权码获取 |
|------|-------------|-----------|:------------:|-----------|
| **QQ 邮箱** | `smtp.qq.com` | `465` | `true` | 设置 → 账户 → POP3/SMTP 服务 → 开启 → 获取授权码 |
| **163 邮箱** | `smtp.163.com` | `465` | `true` | 设置 → POP3/SMTP/IMAP → 开启 → 设置授权码 |
| **Gmail** | `smtp.gmail.com` | `587` | `false` | Google 账户 → 安全 → 应用专用密码 |
| **Outlook** | `smtp.office365.com` | `587` | `false` | 直接使用账户密码或应用密码 |

</details>

### 3. 首次初始化

将当前已有公告全部标记为已读，避免首次运行发送大量邮件：

```bash
web-bugger --init
```

### 4. 启动监控

```bash
# 持续运行（每 5 分钟检查一次）
web-bugger

# 只检查一次
web-bugger --once

# 试运行（不发邮件，只打印）
web-bugger --dry-run

# 组合使用
web-bugger --once --dry-run

# 详细日志
web-bugger -v --once

# 指定 .env 文件
web-bugger --env-file /path/to/.env
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--init` | 初始化：标记当前所有公告为已读 |
| `--once` | 单次检查后退出 |
| `--dry-run` | 不发送邮件，只在终端输出 |
| `-v, --verbose` | 输出 DEBUG 级别日志 |
| `--env-file PATH` | 指定 .env 文件路径 |

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check src/ tests/

# 类型检查
mypy src/
```

## 后台自动运行（系统托管）

为了让脚本在关闭终端甚至重启电脑后依然能自动运行，推荐将其配置为系统服务。

### 🍎 macOS - 使用 `launchd`（推荐）
系统级后台服务，开机自动启动，崩溃自动重启。

1. 在 `~/Library/LaunchAgents/` 下创建文件 `com.user.webbugger.plist`：
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key>
       <string>com.user.webbugger</string>
       <key>ProgramArguments</key>
       <array>
           <!-- 替换为你的 Python/Conda 解释器下的 web-bugger 绝对路径 -->
           <string>/path/to/venv/bin/web-bugger</string>
       </array>
       <key>WorkingDirectory</key>
       <!-- 项目根目录所在的绝对路径 -->
       <string>/path/to/Web_bugger</string>
       <key>EnvironmentVariables</key>
       <dict>
           <key>PATH</key>
           <string>/path/to/venv/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
       </dict>
       <key>RunAtLoad</key>
       <true/>
       <key>KeepAlive</key>
       <true/>
       <key>StandardOutPath</key>
       <string>/path/to/Web_bugger/bugger.log</string>
       <key>StandardErrorPath</key>
       <string>/path/to/Web_bugger/bugger_error.log</string>
   </dict>
   </plist>
   ```
2. 加载并激活服务：
   ```bash
   launchctl load ~/Library/LaunchAgents/com.user.webbugger.plist
   ```

### 🐧 Linux - 使用 `systemd`（推荐）
Ubuntu / CentOS / Debian 获取全天候挂机首选。

1. 在 `/etc/systemd/system/` 下创建 `webbugger.service` 文件：
   ```ini
   [Unit]
   Description=Web Bugger SJTU Monitor
   After=network.target

   [Service]
   Type=simple
   User=你的用户名
   WorkingDirectory=/path/to/Web_bugger
   # 替换为你的虚拟环境绝对路径
   ExecStart=/path/to/venv/bin/web-bugger
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
2. 启动并设置开机自启：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start webbugger
   sudo systemctl enable webbugger
   ```

### 临时命令挂载（全平台可用）
如果不希望编写配置文件，可以通过以下简单的命令行工具挂载：

```bash
# 方法 1：nohup (直接挂起)
# 将运行输出保存到 bugger.log 中
nohup web-bugger > bugger.log 2>&1 &

# 方法 2：screen / tmux (终端复用器)
screen -S bugger
web-bugger
# 断开当前会话：按 Ctrl+A 然后按 D
```

## 欢迎增量更新和内容扩充♥️

## 许可证

[MIT](LICENSE)
