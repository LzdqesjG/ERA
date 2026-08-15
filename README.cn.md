# ERA 终端

多功能终端 AI 助手，基于 DeepSeek API，支持对话、网络搜索、文件操作、语音输入、定时任务和自定义工具扩展。

## 功能特性

- **AI 对话** — 基于 DeepSeek API 的智能对话，支持流式输出和深度思考
- **上下文压缩** — 一键总结历史对话，节省 token
- **网络搜索** — 必应、百度、360 三引擎并行搜索，自动去重合并
- **文件操作** — 读取、写入、搜索、下载文件
- **命令执行** — 在终端运行系统命令
- **语音输入** — 长按空格键录音，自动识别为文字
- **定时任务** — 设定间隔时间自动触发 AI 对话，支持动态增删和配置持久化
- **自动更新** — 启动时后台对比 Gitee 仓库，有新版本自动提醒；输入 `/update` 一键拉取更新（ff-only 安全模式，不覆盖本地改动）
- **笔记系统** — AI 可读写笔记文件，记录和提醒待办事项
- **工具系统** — 可扩展的工具调用机制，支持 AI 主动调用工具
- **HTTP API** — 内置 HTTP 服务器（端口 8080），可通过 POST 请求远程控制
- **命令行外壳** — ECA.py 提供独立的终端外壳，支持文件编辑、目录切换、依赖检查
- **日志记录** — GreatLogger 自动记录所有关键操作到 `log.log`（UTF-8 编码，支持 emoji）

## 目录结构

```
ERA终端/
├── ECA.py             # 命令行外壳（文件操作、目录切换、依赖检查、启动 AI）
├── AI.py              # 核心 AI 库（AIChat 类）
├── coAI.py            # 主入口（终端交互 + HTTP 服务器 + 定时任务调度器）
├── yvyin.py           # 语音识别库（FunASR 封装）
├── user_tools.py      # 工具定义文件（用户自定义工具 + 笔记 + 定时任务工具）
├── findenternet.py    # 多引擎并行网络搜索
├── AIconfig.json      # AI 配置文件（自动生成）
├── history.json       # 对话历史（自动生成）
├── log.log            # 运行日志（自动生成）
├── README.md          # 本文档
├── README.en.md       # 英文版说明
└── 错误代码表.md       # 错误代码说明
```

## 快速开始

### 1. 安装依赖

```bash
pip install openai rich requests beautifulsoup4 prompt_toolkit GreatLogger arrow sounddevice soundfile numpy keyboard funasr
```

或通过 ECA.py 自动安装：

```bash
python ECA.py
# 然后输入 check，自动检查并安装所有依赖
```

### 2. 启动

**方式一：通过 ECA.py 外壳启动**

```bash
python ECA.py
```

ECA.py 提供以下命令：

| 命令 | 说明 |
|------|------|
| `ai` | 启动 coAI.py AI 对话终端 |
| `check` | 检查并自动安装所有依赖 |
| `file read <文件名>` | 读取并打印文件内容 |
| `file write <文件名>` | 逐行写入文件（输入 `//edit//` 结束） |
| `file append <文件名>` | 逐行追加文件（输入 `//edit//` 结束） |
| `cd <路径>` | 切换工作目录 |
| `dir [路径]` | 列出目录内容 |
| `exit` | 退出程序 |

**方式二：直接启动 AI 对话**

```bash
python coAI.py
```

首次运行会提示配置：
1. 输入 API 密钥
2. 设置 AI 工具存放路径
3. 选择是否开启语音功能

### 3. 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示所有可用命令 |
| `/update` | 对比 Gitee 仓库并拉取更新（git pull --ff-only，不覆盖本地改动） |
| `/tools` | 查看当前可用工具列表 |
| `/tasks` | 查看所有定时任务及运行状态 |
| `/addtask` | 交互式新增定时任务（间隔、标题、介绍） |
| `/deltask <索引/id>` | 删除指定定时任务 |
| `/clear` | 清空对话历史 |
| `/condense` | 压缩上下文（AI 总结旧对话） |
| `/save` | 保存对话历史到文件 |
| `/s` | 开启/关闭语音输入 |
| `/system <提示>` | 临时修改系统提示 |
| `/quit` | 退出程序 |

## 使用指南

### 定时任务

定时任务可以按指定间隔自动触发 AI 对话，适合周期性提醒场景。

**通过命令行管理：**

```
/addtask
间隔多少分钟？ 30
任务标题？ 喝水提醒
任务介绍/提示词？ 该喝水了，站起来活动一下

# 任务立即开始计时，每 30 分钟自动触发一次 AI 对话
```

**通过 AI 工具调用管理：**

AI 可以通过工具调用自主管理定时任务：
- `get_time_tasks` — 查看所有任务和运行状态
- `add_time_task(time, task, text)` — 新增任务，立即启动计时
- `remove_time_task(index)` — 删除任务，立即停止线程

任务配置持久化到 `AIconfig.json`，重启后自动加载恢复。

### 自动更新

远程仓库：`https://gitee.com/LBG617/era-terminal`

**启动时自动检查（后台，不阻塞）：**

启动 coAI.py 约 2 秒后会在后台拉取远程信息并对比本地版本，在下一次输入提示前打印检查结果：

```
✅ 已经是最新版本                  # 已是最新
🔔 发现新版本！落后 5 个提交，输入 /update 更新   # 有新版本，附带近 5 条 commit 摘要
ℹ️ 本地比远程领先 3 个提交，无需更新   # 你本地有新提交没 push
⚠️ 分支已分叉…请手动合并           # 本地和远程都有独立 commit
⚠️ 无法访问 Gitee                  # 没网/没权限
⚠️ 当前目录不是 Git 仓库            # 只有源码没有 .git
```

**输入 `/update` 一键拉取：**

```
/update
🔄 正在更新 (git pull --ff-only)，请稍候...
✅ 更新成功！建议重启 coAI.py 加载新代码。
   从 xxx..yyy
   Fast-forward
    18 files changed, 562 insertions(+), 89 deletions(-)
```

安全保护机制：
- 有未提交改动（`git status` 非空）→ 中止，提示先 commit 或 stash，避免覆盖你本地代码
- `pull --ff-only`：只允许快进合并，**不会自动 merge 或产生冲突**
- 分支分叉（non-fast-forward）：给出手动 stash → reset → stash pop 三步处理方案
- origin 指向其他仓库：不擅自改配置，明确提示切换到正确仓库 URL
- 兼容 SSH (`git@gitee.com:LBG617/era-terminal.git`) 和 HTTPS 两种 remote 写法

### 语音输入

```
# 1. 启动时选择开启语音功能
# 2. 输入 /s 开启语音模式
# 3. 长按空格键录音，松开自动识别
# 4. 识别结果显示在输入行，可编辑后按回车发送
# 5. 再次 /s 关闭语音模式
```

> 注意：Windows 下使用 `keyboard` 库需要**管理员权限**运行。

### HTTP API

启动后会在 `http://localhost:8080` 开启 HTTP 服务器，发送 POST 请求：

```bash
# 发送消息给 AI
curl -X POST http://localhost:8080 -d "你好"

# 清空历史
curl -X POST http://localhost:8080 -d "/clear"

# 压缩上下文
curl -X POST http://localhost:8080 -d "/condense"

# 查看工具
curl -X POST http://localhost:8080 -d "/tools"
```

### 自定义工具

编辑 `user_tools.py`，按以下格式添加工具：

```python
def my_tool(args):
    param = args.get("param", "")
    return f"处理结果: {param}"

def get_tools():
    return {
        "my_tool": {
            "description": "工具描述（AI 根据此决定何时调用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "param": {"type": "string", "description": "参数说明"}
                },
                "required": ["param"]
            },
            "handler": my_tool,
        }
    }
```

重启 `coAI.py` 即可自动加载新工具。

### 内置工具列表

| 工具名 | 说明 |
|--------|------|
| `get_time` | 获取当前系统时间 |
| `read_file` | 读取指定文件全部内容 |
| `write_file` | 将内容写入指定文件（覆盖写入） |
| `list_files` | 列出指定目录下的文件和子目录 |
| `run_command` | 执行系统命令并返回输出 |
| `search_in_file` | 在文件中搜索包含关键词的行 |
| `get_system_info` | 获取操作系统、Python 版本等信息 |
| `find_file` | 在指定目录下查找文件 |
| `find_from_enternet` | 在互联网上搜索信息（三引擎并行） |
| `see_enternet` | 查看指定网址的内容 |
| `download_file` | 下载指定文件到指定路径 |
| `get_notes` | 获取记录的笔记 |
| `set_notes` | 在笔记中记录新的一行 |
| `clear_notes` | 清除所有笔记 |
| `rewrite_notes` | 重写所有笔记内容 |
| `remove_notes` | 删除指定行的笔记 |
| `get_time_tasks` | 获取所有定时任务及运行状态 |
| `add_time_task` | 新增定时任务（立即生效） |
| `remove_time_task` | 删除定时任务（立即停止） |

### AI 库独立使用

```python
from AI import AIChat

ai = AIChat(
    initial_messages=[{"role": "system", "content": "你是一个助手"}],
    api_key="your-api-key",
    model_name="deepseek-v4-flash",
    base_url="https://api.deepseek.com"
)

# 注册工具
ai.add_tool(
    name="get_time",
    description="获取当前时间",
    parameters={"type": "object", "properties": {}},
    handler=lambda args: "2024-01-01 12:00:00"
)

# 对话（自动处理工具调用）
reasoning, content = ai.chat("现在几点了？")

# 压缩上下文
result = ai.condense_history()

# 查看/移除工具
tools = ai.list_tools()
ai.remove_tool("get_time")
```

### 语音库独立使用

```python
from yvyin import load, start, stop, is_ready

# 加载模型（主进程，静默）
load()
while not is_ready():
    pass  # 等待就绪

# 启动热键录音
def on_text(text):
    print(f"识别结果: {text}")
start(on_text)

# 停止
stop()
```

## 配置文件 `AIconfig.json`

```json
{
    "api_key": "sk-...",
    "model_name": "deepseek-v4-flash",
    "ai_url": "https://api.deepseek.com",
    "stream_output": true,
    "deep_thinking": true,
    "AItool_path": "C:/path/to/tools",
    "open_voice": "y",
    "notes_path": "C:/path/to/notes.txt",
    "time_tasks": [
        {
            "id": "a1b2c3d4",
            "time": "30",
            "title": "喝水提醒",
            "description": "该喝水了，站起来活动一下"
        }
    ]
}
```

## 环境要求

- **操作系统**: Windows 10/11（Linux/Mac 下部分功能受限）
- **Python**: 3.9+
- **GPU**: CUDA 可选（语音识别需要，无 GPU 时可改 `device="cpu"`）
- **权限**: 语音热键需要管理员权限

## 架构说明

```
ECA.py 外壳 ──→ coAI.py 终端/HTTP ──→ AI.py AIChat ──→ DeepSeek API
                                      │
                               ┌──────┴──────┐
                               │  工具调用     │
                               └──────┬──────┘
                                      │
                    ┌────────┬────────┼────────┬────────┐
                    │        │        │        │        │
              user_tools  yvyin  findenternet  笔记  定时任务
              (文件/命令)  (语音)  (网络搜索)  (读写)  (调度器)
```

## 许可证

MIT
