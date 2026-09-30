# 还在担心错过教务通知？

# SJTU 你的信息助手

[![Python](https://img.shields.io/badge/python-≥3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

自动监控 [上海交通大学教务处](https://jwc.sjtu.edu.cn/)、[计算机学院](https://cs.sjtu.edu.cn/)、[致远学院](https://zhiyuan.sjtu.edu.cn/) 的公告页面，发现新公告时通过邮件通知。

## 功能

- 🔍 同时监控 7 个页面（教务处 + 计算机学院 4 个板块 + 致远学院 2 个板块），自动去重
- 📧 发现新公告自动发送 HTML 邮件：按来源（教务处 / 计算机学院 / 致远学院）分组，每条带分类、日期和正文摘要；讲座活动另附时间段、地点、主讲人，当天 / 次日举行的活动会醒目标出
- 🚨 某个页面持续抓取失败（网络故障、页面改版）时发告警邮件，同一页面每天最多提醒一次
- 💾 本地 JSON 持久化存储已读公告，避免重复通知
- 🔄 支持守护模式持续运行 / 单次检查 / 仅打印
- 📬 支持任意 SMTP 邮箱（QQ / 163 / Gmail / Outlook 等）

## 监控范围

| 页面 | 板块 |
| ------ | ------ |
| [教务处 · 面向学生的通知](https://jwc.sjtu.edu.cn/index/mxxsdtz.htm) | 选课、考试、竞赛、助管招聘等学生相关通知 |
| [计算机学院 · 党建德育](https://cs.sjtu.edu.cn/xsgz-tzgg-djdy.html) | 党建德育 |
| [计算机学院 · 团学工作](https://cs.sjtu.edu.cn/xsgz-tzgg-txgz.html) | 团学工作 |
| [计算机学院 · 学生事务](https://cs.sjtu.edu.cn/xsgz-tzgg-xssw.html) | 学生事务 |
| [计算机学院 · 职业发展](https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html) | 职业发展 |
| [致远学院 · 学术活动](https://zhiyuan.sjtu.edu.cn/event) | ZY-INS 沙龙、ChalkTalk、全球系列讲座等 |
| [致远学院 · 通知动态](https://zhiyuan.sjtu.edu.cn/announcement) | 学院全部通知（综合 / 招生 / 教学 / 学生事务、合作交流、团学工作等） |

> 致远学院官网 2026 年 9 月改版后，两个列表都是服务端渲染的 HTML（`?page=N` 翻页，每页 12 条）。
> 列表历史很深（通知 1100+ 条、活动 400+ 条），新条目排在最前，但活动列表偶尔会把补录的旧活动
> 插到前面，因此只抓取最近 3 页，保留 `MAX_PAGES` 作为安全上限。

## 项目结构

```
SJTU_InformationGrabber/
├── src/web_bugger/          # 源代码包
│   ├── __init__.py          # 包元信息
│   ├── cli.py               # 命令行入口
│   ├── config.py            # 配置管理 (dataclass + 校验)
│   ├── models.py            # 数据模型 (Announcement)
│   ├── monitor.py           # 监控编排器 (Monitor)
│   ├── notifier.py          # 邮件通知 (Notifier)
│   ├── scraper.py           # 网页爬虫 (Scraper，三种布局)
│   └── storage.py           # 已读存储 (Storage，原子写入)
├── tests/                   # 单元测试（含抓取/存储/CLI 回归测试）
├── .github/workflows/ci.yml # CI（ruff + mypy + pytest）
├── pyproject.toml           # PEP 621 包配置
├── pyrightconfig.json       # 编辑器/类型检查解释器配置
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
| ------ | :----: | ------ |
| `SENDER_EMAIL` | ✅ | 发件人邮箱地址 |
| `SENDER_PASSWORD` | ✅ | SMTP 授权码 / 应用专用密码 |
| `RECEIVER_EMAIL` | ✅ | 收件人邮箱地址 |
| `SMTP_SERVER` | | SMTP 服务器（默认 `smtp.qq.com`） |
| `SMTP_PORT` | | 端口（默认 `465`） |
| `SMTP_USE_SSL` | | 是否使用 SSL（默认 `true`；`false` 时走 STARTTLS） |
| `SMTP_TIMEOUT` | | SMTP 超时秒数（默认 `20`） |
| `TARGET_URLS` | | 监控页面 URL，逗号分隔（默认 6 个交大页面） |
| `CHECK_INTERVAL` | | 检查间隔秒数（默认 `300`，最小 `10`） |
| `BASE_URL` | | 相对链接的兜底解析基准（默认 `https://jwc.sjtu.edu.cn/`） |
| `DATA_DIR` | | 已读状态文件所在目录（默认项目根目录） |
| `REQUEST_TIMEOUT` | | 单次 HTTP 超时秒数（默认 `15`） |
| `MAX_RETRIES` | | 单页面失败重试次数（默认 `3`） |
| `MAX_WORKERS` | | 并发抓取页面数（默认 `4`） |
| `MAX_PAGES` | | 单个板块最大翻页数（默认 `50`，防止死循环；致远学院固定只取最近 3 页） |
| `FAILURE_ALERT_THRESHOLD` | | 某个页面连续抓取失败多少次后发告警邮件（默认 `3`） |
| `FAILURE_ALERT_INTERVAL` | | 同一页面持续失败时重复提醒的最小间隔秒数（默认 `86400` = 1 天；新出问题的页面会立即提醒） |

> 所有数值型配置都会做范围校验：写错（例如 `SMTP_PORT=abc`）会得到一条带变量名的
> 明确报错，而不是 Python 堆栈。

<details>
<summary>常见邮箱 SMTP 配置</summary>

| 邮箱 | SMTP_SERVER | SMTP_PORT | SMTP_USE_SSL | 授权码获取 |
| ------ | ------------- | ----------- | :------------: | ----------- |
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
| ------ | ------ |
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

### 🪟 Windows - 使用任务计划程序

让程序在 Windows 开机时自动在后台静默运行。

1. 创建一个批处理文件 `run_webbugger.bat` 放在项目目录下（例如 `D:\Web_bugger\run_webbugger.bat`）：

   ```bat
   @echo off
   cd /d D:\Web_bugger
   :: 替换为你自己的虚拟环境 Python 的绝对路径
   C:\Users\YourName\miniconda3\envs\web_bugger\python.exe -m web_bugger.cli
   ```

2. 创建一个 VBS 脚本 `start_hidden.vbs` 用来隐藏黑色的终端窗口：

   ```vbs
   Set WshShell = CreateObject("WScript.Shell")
   ' 替换为你的 bat 文件绝对路径
   WshShell.Run chr(34) & "D:\Web_bugger\run_webbugger.bat" & Chr(34), 0
   Set WshShell = Nothing
   ```

3. 按 `Win+R` 输入 `taskschd.msc` 打开**任务计划程序**。
4. 点击右侧 **创建基本任务...**：
   - 名称填写：`Web Bugger Monitor`
   - 触发器选择：**计算机启动时**（或“当当前用户登录时”）
   - 操作选择：**启动程序**
   - 程序或脚本：浏览选择刚创建的 `start_hidden.vbs` 文件
   - 完成后保存即可实现开机后台自动运行。

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

# 3.8 更新

新增了计算机学院网站 [xsgz-tzgg-zyfz](https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html) 中四个通知模块的抓取。

## 许可证

[MIT](LICENSE)
