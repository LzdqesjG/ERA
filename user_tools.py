# =====================================================
# 工具定义文件 — 在此文件中定义所有AI可用的工具
# =====================================================
#
# 如何添加新工具:
# 1. 定义一个处理函数，接收 args 字典参数，返回字符串结果
# 2. 在 get_tools() 函数中注册工具
# 3. 重启 coAI.py 即可使用新工具
#
# 工具格式:
#     def my_tool(args):
#         param = args.get("param", "")
#         return "结果"
#
#     def get_tools():
#         return {
#             "my_tool": {
#                 "description": "给AI看的描述，AI据此决定何时调用",
#                 "parameters": {
#                     "type": "object",
#                     "properties": {
#                         "param": {"type": "string", "description": "参数说明"}
#                     },
#                     "required": ["param"]
#                 },
#                 "handler": my_tool,
#             }
#         }
# =====================================================

import os
import sys
import subprocess
from datetime import datetime
from findenternet import find_enternet
import requests
import json
config = {}
try:
    with open("AIconfig.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    pass


# ================================================
# 内置工具 —— 可随意修改、删除或增加
# ================================================

def _get_time(args):
    """获取当前系统时间"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _read_file(args):
    """读取指定文件的内容"""
    filepath = args.get("path", "")
    if not filepath:
        return "错误: 未指定文件路径"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"读取文件出错: {str(e)}"

def _write_file(args):
    """将内容写入指定文件"""
    filepath = args.get("path", "")
    content = args.get("content", "")
    if not filepath:
        return "错误: 未指定文件路径"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"文件 {filepath} 写入成功 ({len(content)} 字节)"
    except Exception as e:
        return f"写入文件出错: {str(e)}"

def _list_files(args):
    """列出指定目录下的文件和子目录"""
    dirpath = args.get("path", ".")
    try:
        items = os.listdir(dirpath)
        result = f"目录 {os.path.abspath(dirpath)} 内容 ({len(items)} 项):\n"
        for item in sorted(items):
            full_path = os.path.join(dirpath, item)
            if os.path.isdir(full_path):
                result += f"  📁 {item}/\n"
            else:
                size = os.path.getsize(full_path)
                result += f"  📄 {item} ({size} 字节)\n"
        return result
    except Exception as e:
        return f"列出文件出错: {str(e)}"

def _run_command(args):
    """执行系统命令并返回输出"""
    cmd = args.get("command", "")
    if not cmd:
        return "错误: 未指定命令"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n[stderr]\n" if output else "") + result.stderr
        return output or "(命令执行成功，无输出)"
    except subprocess.TimeoutExpired:
        return "命令执行超时 (30秒)"
    except Exception as e:
        return f"命令执行出错: {str(e)}"

def _search_in_file(args):
    """在文件中搜索关键词"""
    filepath = args.get("path", "")
    keyword = args.get("keyword", "")
    if not filepath or not keyword:
        return "错误: 需要指定 path 和 keyword"
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        matches = []
        for i, line in enumerate(lines, 1):
            if keyword.lower() in line.lower():
                matches.append(f"  第{i}行: {line.rstrip()}")
        if matches:
            return f"在 {filepath} 中找到 {len(matches)} 处匹配:\n" + "\n".join(matches[:50])
        return f"在 {filepath} 中未找到 '{keyword}'"
    except Exception as e:
        return f"搜索出错: {str(e)}"

def _get_system_info(args):
    """获取系统基本信息"""
    info = {
        "操作系统": os.name,
        "当前目录": os.getcwd(),
        "Python版本": sys.version.split()[0],
        "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return "\n".join(f"{k}: {v}" for k, v in info.items())


# ================================================
# 自定义工具 —— 在这里添加你自己的工具
# ================================================

def find_file_tool(args):
    """在指定目录下查找指定文件"""
    path = args.get("path", "")
    filename = args.get("filename", "")
    if not path or not filename:
        return "错误: 需要指定 path 和 filename"
    try:
        files = os.listdir(path)
        found = False
        for file in files:
            if file == filename:
                found = True
                break
        if found:
            return f"找到文件: {os.path.join(path, filename)}"
        else:
            return f"未找到文件: {filename}"
    except Exception as e:
        return f"查找文件出错: {str(e)}"

def find_enternet_tool(args):
    """在多个搜索引擎上搜索并合并去重"""
    ci = args.get("ci", "")
    num_results = args.get("num_results", 10)
    engines = args.get("engines", None)
    resolve_redirects = args.get("resolve_redirects", True)
    max_results = args.get("max_results", 5)
    return find_enternet(ci, num_results, engines, resolve_redirects, max_results)

def find_url_tool(args):
    """查看指定网址的内容"""
    url = args.get("url", "")
    if not url:
        return "错误: 未指定网址"
    try:
        response = requests.get(url)
        return response.text
    except Exception as e:
        return f"查看网址出错: {str(e)}"

def download_file_tool(args):
    """下载指定文件到指定路径"""
    url = args.get("url", "")
    path = args.get("path", "")
    if not url:
        return "错误: 未指定文件URL"
    if not path:
        return "错误: 未指定保存路径"
    try:
        response = requests.get(url, stream=True)
        if response.status_code != 200:
            return f"下载文件出错: {response.status_code}"
        else:
            with open(path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return f"文件下载成功: {path}"
    except Exception as e:
        return f"下载文件出错: {str(e)}"

def get_notes_tool(args=None):
    """获取记录的笔记"""
    notes_path = config.get("notes_path", "")
    if not notes_path:
        return "错误: 未指定笔记路径"
    try:
        with open(notes_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"读取笔记出错: {str(e)}"

def set_notes_tool(args):
    """在笔记记录新的一行"""
    notes = args.get("notes", "")
    notes_path = config.get("notes_path", "")
    if not notes:
        return "错误: 不允许空笔记"
    try:
        with open(notes_path, "a", encoding="utf-8") as f:
            f.write(notes + "\n")
        return f"记录笔记成功: {notes}"
    except Exception as e:
        return f"记录笔记出错: {str(e)}"

def clear_notes_tool(args=None):
    """清除记录的笔记"""
    notes_path = config.get("notes_path", "")
    if not notes_path:
        return "错误: 未指定笔记路径"
    try:
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write("")
        return f"清除笔记成功"
    except Exception as e:
        return f"清除笔记出错: {str(e)}"

def rewrite_notes_tool(args):
    """重写所有的笔记"""
    notes_path = config.get("notes_path", "")
    notes = args.get("notes", "")
    if not notes:
        return "错误: 不允许空笔记"
    try:
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(notes)
        return f"重写笔记成功: {notes}"
    except Exception as e:
        return f"重写笔记出错: {str(e)}"

def remove_notes_tool(args):
    """删除指定行的笔记"""
    index = args.get("index", "")
    notes_path = config.get("notes_path", "")
    if not index:
        return "错误: 未指定笔记索引"
    try:
        with open(notes_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"读取笔记出错: {str(e)}"

    # 显式校验索引类型和范围，给出用户友好的提示（与 remove_time_task_tool 风格一致）
    try:
        idx = int(index)
    except (ValueError, TypeError):
        return f"错误: 索引必须是整数，当前传入: '{index}'"

    if idx < 0 or idx >= len(lines):
        return f"错误: 索引 {idx} 超出范围 (共 {len(lines)} 行，索引从 0 开始)"

    try:
        lines.pop(idx)
        with open(notes_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"删除笔记成功: {index}"
    except Exception as e:
        return f"写入笔记出错: {str(e)}"


# ============================================================
# 定时任务工具（会被 coAI.py 注入运行时调度器引用 _scheduler）
# - 有 _scheduler 时：AI 调用立即启停线程 + 写配置
# - 无 _scheduler 时：只写配置（兼容单独导入 user_tools 的场景）
# ============================================================

_scheduler = None


def get_time_tasks_tool(args=None):
    """获取所有定时任务（带运行状态/间隔/标题/介绍）"""
    global _scheduler
    if _scheduler is not None and hasattr(_scheduler, "list_all"):
        try:
            return _scheduler.list_all()
        except Exception as e:
            return f"读取定时任务失败: {e}"
    tasks = config.get("time_tasks", []) or []
    if not tasks:
        return "当前没有任何定时任务"
    lines = [f"📋 定时任务列表 (共 {len(tasks)} 个):"]
    for i, t in enumerate(tasks):
        lines.append(f"  [{i}] 间隔{t.get('time','?')}分 | {t.get('task','')}")
        if t.get("text"):
            lines.append(f"      介绍: {t['text'][:80]}{'…' if len(t['text'])>80 else ''}")
    return "\n".join(lines)


def add_time_task_tool(args):
    """添加一个定时任务（立刻生效并启动线程）"""
    global _scheduler
    time = args.get("time", "")
    task = args.get("task", "")
    text = args.get("text", "")
    if not time or not task:
        return "❌ 错误: time(分钟) 和 task(标题) 必填"

    if _scheduler is not None and hasattr(_scheduler, "add_task"):
        try:
            return _scheduler.add_task(time, task, text)
        except Exception as e:
            return f"添加定时任务出错: {e}"

    # 兜底：无运行时调度器时，直接读最新配置改 time_tasks 再写回
    # 注意：每次都从磁盘重讀，不用模块级 config 缓存，防止覆盖 coAI.py 已写入的修改
    try:
        cfg_path = "AIconfig.json"
        current_cfg = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                current_cfg = json.load(f)
        tasks = current_cfg.get("time_tasks", []) or []
        tasks.append({"time": str(time), "task": task, "text": text or ""})
        current_cfg["time_tasks"] = tasks
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(current_cfg, f, ensure_ascii=False, indent=4)
        return f"✅ 添加成功（下次启动生效）: {time}分 {task}"
    except Exception as e:
        return f"添加定时任务出错: {e}"


def remove_time_task_tool(args):
    """删除指定定时任务（索引或id），并自动停止其线程"""
    global _scheduler
    index = args.get("index", "")
    if index == "" or index is None:
        return "❌ 错误: 未指定索引或 id"

    if _scheduler is not None and hasattr(_scheduler, "remove_task"):
        try:
            return _scheduler.remove_task(index)
        except Exception as e:
            return f"删除定时任务出错: {e}"

    # 兜底：无运行时调度器时，每次从磁盘重讀最新配置再修改写回
    try:
        cfg_path = "AIconfig.json"
        current_cfg = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                current_cfg = json.load(f)
        tasks = current_cfg.get("time_tasks", []) or []

        # 兜底路径暂只支持按整数索引删除（id 删除依赖运行时调度器提供全量查找）
        idx = int(index)
        if idx < 0 or idx >= len(tasks):
            return f"错误: 索引 {index} 超出范围 (共 {len(tasks)} 个)"
        removed = tasks.pop(idx)
        current_cfg["time_tasks"] = tasks
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(current_cfg, f, ensure_ascii=False, indent=4)
        return f"✅ 删除成功（下次启动生效）: {removed.get('task')}"
    except ValueError:
        return f"错误: 索引必须是整数（当前无运行时调度器，暂不支持按 id 删除）"
    except Exception as e:
        return f"删除定时任务出错: {e}"


# ================================================
# 工具注册入口 —— 所有工具在此汇总
# ================================================

def get_tools():
    """返回所有工具的字典"""
    return {
        # --- 内置工具 ---
        "get_time": {
            "description": "获取当前系统时间",
            "parameters": {"type": "object", "properties": {}},
            "handler": _get_time,
        },
        "read_file": {
            "description": "读取指定文件的全部内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"],
            },
            "handler": _read_file,
        },
        "write_file": {
            "description": "将内容写入指定文件（覆盖写入）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
            "handler": _write_file,
        },
        "list_files": {
            "description": "列出指定目录下的所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认为当前目录"}
                },
                "required": ["path"],
            },
            "handler": _list_files,
        },
        "run_command": {
            "description": "执行一个系统命令并返回其输出结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"}
                },
                "required": ["command"],
            },
            "handler": _run_command,
        },
        "search_in_file": {
            "description": "在指定文件中搜索包含关键词的行",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "keyword": {"type": "string", "description": "要搜索的关键词"},
                },
                "required": ["path", "keyword"],
            },
            "handler": _search_in_file,
        },
        "get_system_info": {
            "description": "获取操作系统、Python版本等基本信息",
            "parameters": {"type": "object", "properties": {}},
            "handler": _get_system_info,
        },

        # --- 自定义工具 ---
        "find_file": {
            "description": "在指定目录下查找指定文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"},
                    "filename": {"type": "string", "description": "要查找的文件名"}
                },
                "required": ["path", "filename"],
            },
            "handler": find_file_tool,
        },

        "find_from_enternet": {
            "description": "在互联网上查找信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "ci": {"type": "string", "description": "要查找的关键词"}
                },
                "required": ["ci"],
            },
            "handler": find_enternet_tool,
        },
        "see_enternet": {
            "description": "查看指定网址的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要查看的网址"}
                },
                "required": ["url"],
            },
            "handler": find_url_tool,
        },
        "download_file": {
            "description": "下载指定文件到指定路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要下载的文件URL"},
                    "path": {"type": "string", "description": "下载后的文件路径，默认为当前目录"}
                },
                "required": ["url", "path"],
            },
            "handler": download_file_tool,
        },
        "get_notes": {
            "description": "获取记录的笔记",
            "parameters": {"type": "object", "properties": {}},
            "handler": get_notes_tool,
        },
        "set_notes": {
            "description": "在笔记记录新的一行",
            "parameters": {
                "type": "object",
                "properties": {"notes": {"type": "string", "description": "要记录的笔记内容"}},
                "required": ["notes"],
            },
            "handler": set_notes_tool,
        },
        "clear_notes": {
            "description": "清除所有记录的笔记",
            "parameters": {"type": "object", "properties": {}},
            "handler": clear_notes_tool,
        },
        "rewrite_notes": {
            "description": "重写所有的笔记",
            "parameters": {"type": "object", "properties": {"notes": {"type": "string", "description": "要重写的所有笔记内容"}}},
            "handler": rewrite_notes_tool
        },
        "remove_notes": {
            "description": "删除指定行的笔记",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "string", "description": "要删除的笔记索引"}},
                "required": ["index"],
            },
            "handler": remove_notes_tool,
        },
        "get_time_tasks": {
            "description": "获取所有定时任务（包含运行状态、间隔、标题、介绍）",
            "parameters": {"type": "object", "properties": {}},
            "handler": get_time_tasks_tool,
        },
        "add_time_task": {
            "description": "新增一个定时任务：指定间隔分钟数、标题、介绍内容。新增后立即开始计时并自动循环触发",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "定时任务的间隔时间，单位为分钟（正整数或小数）"},
                    "task": {"type": "string", "description": "定时任务的标题，显示在提示词最前面"},
                    "text": {"type": "string", "description": "定时任务的详细介绍或提示词，作为发送给AI的内容"},
                },
                "required": ["time", "task", "text"],
            },
            "handler": add_time_task_tool,
        },
        "remove_time_task": {
            "description": "删除指定的定时任务，可以传列表索引(0,1,2...) 也可以传任务id。删除后立即停止其计时线程",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "要删除的定时任务索引（从0开始）或任务id"}
                },
                "required": ["index"],
            },
            "handler": remove_time_task_tool,
        }
    }
