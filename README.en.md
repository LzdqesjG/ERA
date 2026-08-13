# ERA Terminal

A multi-functional terminal AI assistant based on the DeepSeek API, supporting conversation, web search, file operations, voice input, scheduled tasks, and custom tool extensions.

## Features

- **AI Conversation** — Smart chat based on DeepSeek API with streaming output and deep thinking
- **Context Compression** — One-click summary of conversation history to save tokens
- **Web Search** — Multi-engine parallel search (Bing, Baidu, 360) with automatic deduplication and merging
- **File Operations** — Read, write, search, and download files
- **Command Execution** — Run system commands in the terminal
- **Voice Input** — Hold spacebar to record, auto-transcribe to text
- **Scheduled Tasks** — Set interval to auto-trigger AI conversation, with dynamic add/remove and config persistence
- **Auto Update** — Background compare with Gitee repo at startup, auto-notifies when new version exists; type `/update` to pull updates safely (ff-only mode, never overwrites local changes)
- **Notes System** — AI can read/write notes files for reminders and to-do tracking
- **Tool System** — Extensible tool-calling mechanism for AI to actively invoke tools
- **HTTP API** — Built-in HTTP server (port 8080) for remote control via POST requests
- **Command Shell** — ECA.py provides a standalone shell with file editing, directory navigation, and dependency checking
- **Logging** — GreatLogger automatically records all key operations to `log.log` (UTF-8 encoding, emoji-safe)

## Directory Structure

```
ERA Terminal/
├── ECA.py             # Command shell (file ops, directory nav, dependency check, AI launcher)
├── AI.py              # Core AI library (AIChat class)
├── coAI.py            # Main entry (terminal interaction + HTTP server + task scheduler)
├── yvyin.py           # Voice recognition library (FunASR wrapper)
├── user_tools.py      # Tool definitions (custom tools + notes + scheduled task tools)
├── findenternet.py    # Multi-engine parallel web search
├── AIconfig.json      # AI configuration (auto-generated)
├── history.json       # Conversation history (auto-generated)
├── log.log            # Runtime logs (auto-generated)
├── README.md          # Chinese documentation
├── README.en.md       # This file
└── 错误代码表.md       # Error code reference
```

## Quick Start

### 1. Install Dependencies

```bash
pip install openai rich requests beautifulsoup4 prompt_toolkit GreatLogger arrow sounddevice soundfile numpy keyboard funasr
```

Or auto-install via ECA.py:

```bash
python ECA.py
# Then type "check" to automatically check and install all dependencies
```

### 2. Launch

**Option A: Via ECA.py shell**

```bash
python ECA.py
```

ECA.py commands:

| Command | Description |
|---------|-------------|
| `ai` | Launch coAI.py AI chat terminal |
| `check` | Check and auto-install all dependencies |
| `file read <filename>` | Read and print file contents |
| `file write <filename>` | Write file line by line (type `//edit//` to finish) |
| `file append <filename>` | Append to file line by line (type `//edit//` to finish) |
| `cd <path>` | Change working directory |
| `dir [path]` | List directory contents |
| `exit` | Exit program |

**Option B: Direct AI chat**

```bash
python coAI.py
```

On first run, you will be prompted to configure:
1. Enter your API key
2. Set the AI tools storage path
3. Choose whether to enable voice features

### 3. Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/update` | Compare with Gitee repo and pull updates (git pull --ff-only, never overwrites local changes) |
| `/tools` | List all available tools |
| `/tasks` | View all scheduled tasks and their status |
| `/addtask` | Interactively add a scheduled task (interval, title, description) |
| `/deltask <index/id>` | Delete a scheduled task |
| `/clear` | Clear conversation history |
| `/condense` | Compress context (AI summarizes old conversation) |
| `/save` | Save conversation history to file |
| `/s` | Toggle voice input on/off |
| `/key` | Modify API key |
| `/config` | Modify tool path, voice, key, and other settings |
| `/system <prompt>` | Temporarily modify the system prompt |
| `/quit` | Exit the program |

## Usage Guide

### Scheduled Tasks

Scheduled tasks auto-trigger AI conversations at specified intervals, ideal for periodic reminders.

**Via command line:**

```
/addtask
Interval in minutes? 30
Task title? Water Reminder
Task description/prompt? Time to drink water, stand up and stretch

# Task starts immediately, triggers AI conversation every 30 minutes
```

**Via AI tool calls:**

AI can manage scheduled tasks autonomously through tool calls:
- `get_time_tasks` — View all tasks and their running status
- `add_time_task(time, task, text)` — Add a task, starts immediately
- `remove_time_task(index)` — Remove a task, stops its thread immediately

Task configs persist to `AIconfig.json` and auto-load on restart.

### Auto Update

Remote repo: `https://gitee.com/LBG617/era-terminal`

**Background check at startup (non-blocking):**

About 2 seconds after launching coAI.py, the app fetches remote info in the background and compares it with the local version, then prints the result before your next input prompt:

```
✅ Already up to date
🔔 New version available! Behind by 5 commits, type /update to update  (last 5 commit summaries attached)
ℹ️ Local is ahead of remote by 3 commits, no update needed
⚠️ Branches have diverged... merge manually
⚠️ Unable to reach Gitee
⚠️ Current directory is not a Git repository
```

**Type `/update` to pull with one command:**

```
/update
🔄 Updating (git pull --ff-only), please wait...
✅ Update successful! Please restart coAI.py to load the new code.
   From xxx..yyy
   Fast-forward
    18 files changed, 562 insertions(+), 89 deletions(-)
```

Safety protections:
- Uncommitted changes (non-empty `git status`) → Aborts with message to commit or stash first, avoiding overwrite of your local code
- `pull --ff-only`: Fast-forward merges only, **no auto-merge, no surprise conflicts**
- Diverged branches (non-fast-forward): Provides a 3-step manual guide (stash → reset → stash pop)
- Wrong origin URL: Never silently rewrites your remote config; clearly prompts to switch to the correct repo URL
- Supports both SSH (`git@gitee.com:LBG617/era-terminal.git`) and HTTPS remote formats

### Voice Input

```
# 1. Choose to enable voice at startup
# 2. Type /s to enter voice mode
# 3. Hold spacebar to record, release to auto-transcribe
# 4. Result appears on the input line, editable, press Enter to send
# 5. Type /s again to disable voice mode
```

> Note: The `keyboard` library requires **administrator privileges** on Windows.

### HTTP API

After launch, an HTTP server runs at `http://localhost:8080`. Send POST requests:

```bash
# Send message to AI
curl -X POST http://localhost:8080 -d "Hello"

# Clear history
curl -X POST http://localhost:8080 -d "/clear"

# Compress context
curl -X POST http://localhost:8080 -d "/condense"

# List tools
curl -X POST http://localhost:8080 -d "/tools"
```

### Custom Tools

Edit `user_tools.py` and add tools in the following format:

```python
def my_tool(args):
    param = args.get("param", "")
    return f"Result: {param}"

def get_tools():
    return {
        "my_tool": {
            "description": "Tool description (AI uses this to decide when to call)",
            "parameters": {
                "type": "object",
                "properties": {
                    "param": {"type": "string", "description": "Parameter description"}
                },
                "required": ["param"]
            },
            "handler": my_tool,
        }
    }
```

Restart `coAI.py` to automatically load the new tool.

### Built-in Tools

| Tool | Description |
|------|-------------|
| `get_time` | Get current system time |
| `read_file` | Read entire contents of a file |
| `write_file` | Write content to a file (overwrite) |
| `list_files` | List files and subdirectories in a directory |
| `run_command` | Execute a system command and return output |
| `search_in_file` | Search for lines containing a keyword in a file |
| `get_system_info` | Get OS, Python version, and other system info |
| `find_file` | Find a specific file in a directory |
| `find_from_enternet` | Search the internet (parallel multi-engine) |
| `see_enternet` | View contents of a specified URL |
| `download_file` | Download a file to a specified path |
| `get_notes` | Get all recorded notes |
| `set_notes` | Append a new line to notes |
| `clear_notes` | Clear all notes |
| `rewrite_notes` | Rewrite all notes content |
| `remove_notes` | Remove a specific line from notes |
| `get_time_tasks` | Get all scheduled tasks and their status |
| `add_time_task` | Add a scheduled task (takes effect immediately) |
| `remove_time_task` | Remove a scheduled task (stops immediately) |

### Using the AI Library Independently

```python
from AI import AIChat

ai = AIChat(
    initial_messages=[{"role": "system", "content": "You are an assistant"}],
    api_key="your-api-key",
    model_name="deepseek-v4-flash",
    base_url="https://api.deepseek.com"
)

# Register a tool
ai.add_tool(
    name="get_time",
    description="Get current time",
    parameters={"type": "object", "properties": {}},
    handler=lambda args: "2024-01-01 12:00:00"
)

# Chat (automatically handles tool calls)
reasoning, content = ai.chat("What time is it?")

# Compress context
result = ai.condense_history()

# List/remove tools
tools = ai.list_tools()
ai.remove_tool("get_time")
```

### Using the Voice Library Independently

```python
from yvyin import load, start, stop, is_ready

# Load model (main process, silent)
load()
while not is_ready():
    pass  # Wait for readiness

# Start hotkey recording
def on_text(text):
    print(f"Recognition result: {text}")
start(on_text)

# Stop
stop()
```

## Configuration File `AIconfig.json`

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
            "title": "Water Reminder",
            "description": "Time to drink water, stand up and stretch"
        }
    ]
}
```

## Requirements

- **OS**: Windows 10/11 (some features limited on Linux/Mac)
- **Python**: 3.9+
- **GPU**: CUDA optional (required for voice recognition; change `device="cpu"` if no GPU)
- **Permissions**: Administrator privileges required for voice hotkeys

## Architecture

```
ECA.py Shell ──→ coAI.py Terminal/HTTP ──→ AI.py AIChat ──→ DeepSeek API
                                      │
                               ┌──────┴──────┐
                               │  Tool Calls  │
                               └──────┬──────┘
                                      │
                    ┌────────┬────────┼────────┬────────┐
                    │        │        │        │        │
              user_tools  yvyin  findenternet  Notes  Scheduler
              (file/cmd)  (voice)  (web search) (r/w)  (tasks)
```

## License

MIT
