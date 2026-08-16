# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。
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
import struct
import subprocess
from datetime import datetime
import requests
import json
from web import query_balance

# 可选依赖：缺失时只跳过对应工具，不影响核心工具加载
# （Windows 不支持 crypt；findinternet 依赖 bs4；easygui/lzdqesj 可能未安装）
try:
    from findinternet import find_internet
except Exception:
    find_internet = None
try:
    import lzdqesj
except Exception:
    lzdqesj = None
try:
    import easygui
except Exception:
    easygui = None

config = {}
try:
    with open("AIconfig.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    pass


def tool_need_accept(func):
    """装饰器：标记该工具在被 AI 调用前需要用户手动批准。
    用法：
        @tool_need_accept
        def _dangerous_tool(args): ...
    该装饰器会在 handler 上加 `__tool_need_accept__ = True` 属性。
    """
    func.__tool_need_accept__ = True
    return func



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

@tool_need_accept
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

@tool_need_accept
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

# ---- Everything 全局文件搜索（基于 es.exe 命令行工具）----
def _get_es_exe():
    """根据 Python 进程位数选择合适的 es.exe；找不到时兜底扫描 es 目录。"""
    es_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "es")
    bits = struct.calcsize("P") * 8
    if bits == 64:
        candidates = ["es-x64.exe", "es-arm64.exe", "es-x86.exe", "es-arm.exe"]
    else:
        candidates = ["es-x86.exe", "es-arm.exe", "es-x64.exe", "es-arm64.exe"]
    for name in candidates:
        p = os.path.join(es_dir, name)
        if os.path.isfile(p):
            return p
    return os.path.join(es_dir, "es-x64.exe")  # 返回默认路径（即便不存在，调用时报错）

def everything_search_tool(args):
    """使用 Everything 在系统全局范围快速查找文件/文件夹。
    依赖 Everything 服务（需安装并运行 Everything）。
    注意：Everything 仅支持 Windows；非 Windows 系统会返回不可用提示。
    """
    keyword = args.get("keyword", "")
    if not keyword:
        return "错误: 未指定搜索关键词"

    # 跨平台：Everything 为 Windows 专属，非 Windows 直接告知
    if sys.platform != "win32":
        return ("⚠️ Everything 文件搜索仅支持 Windows 系统，当前系统为 "
                f"{sys.platform}，无法使用。\n"
                "建议：非 Windows 可改用 list_files 按目录浏览，或 run_command 执行 "
                "'find / -name <关键词>'（Linux/macOS）进行搜索。")

    # 结果数量限制（1~500，默认 50）
    max_results = args.get("max_results", 50)
    try:
        max_results = int(max_results)
    except (ValueError, TypeError):
        max_results = 50
    max_results = max(1, min(500, max_results))

    search_path = args.get("path", "") or ""
    folders_only = bool(args.get("folders_only", False))
    show_size = bool(args.get("show_size", True))
    show_date = bool(args.get("show_date", False))

    exe = _get_es_exe()
    if not os.path.isfile(exe):
        return ("错误: 未找到 es.exe 命令行工具，请确认项目下 es 目录存在且包含 es-x64.exe/es-x86.exe\n"
                f"查找路径: {exe}")

    # 用列表传参，避免 shell 注入风险；搜索词作为最后一个位置参数
    cmd = [exe, "-n", str(max_results), "-s"]
    if search_path:
        cmd += ["-path", search_path]
    if folders_only:
        cmd.append("/ad")
    if show_size:
        cmd.append("-size")
    if show_date:
        cmd.append("-dm")
    cmd.append(keyword)

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "搜索超时 (15秒)，Everything 服务可能未运行或无响应"
    except FileNotFoundError:
        return f"错误: 无法启动 es.exe ({exe})"
    except Exception as e:
        return f"搜索出错: {str(e)}"

    # es.exe 带 -utf8 选项时输出 UTF-8
    out = result.stdout.decode("utf-8", errors="replace").strip()
    err = result.stderr.decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        tip = "请确认 Everything 已安装且服务正在运行"
        return f"搜索失败 (exit={result.returncode}): {err or out or '无输出'}\n提示: {tip}"

    if not out:
        return f"未找到匹配 '{keyword}' 的文件"

    lines = out.splitlines()
    header = f"找到 {len(lines)} 个结果"
    if len(lines) >= max_results:
        header += f"（已限制为前 {max_results} 条，可能还有更多）"
    header += "：\n"
    return header + out


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

def find_internet_tool(args):
    """在多个搜索引擎上搜索并合并去重"""
    ci = args.get("ci", "")
    num_results = args.get("num_results", 10)
    engines = args.get("engines", None)
    resolve_redirects = args.get("resolve_redirects", True)
    max_results = args.get("max_results", 5)
    return find_internet(ci, num_results, engines, resolve_redirects, max_results)

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

@tool_need_accept
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

@tool_need_accept
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

@tool_need_accept
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

@tool_need_accept
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


@tool_need_accept
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


@tool_need_accept
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

@tool_need_accept
def _rickroll_tool(args):
    """播放rickroll视频（会弹出浏览器/桌面媒体，需用户批准）"""
    # This tool is made by @lzdqesj
    platform = args.get("type", "")
    if not platform:
        platform = "bilibili"
    if platform not in ["youtube", "bilibili"]:
        return f"❌ 错误: 未知的视频平台 {platform}"
    lzdqesj.rickroll(platform)
    return f"✅ 已打开位于 {platform} 的 Rickroll 视频。"

def _get_notify_help_tool(kwargs):
    """获取 easygui 库的方法列表或方法文档（根据 mode 参数）"""
    if easygui is None:
        return "❌ 错误: easygui 库未安装，无法获取帮助信息"
    _mode = kwargs.get("mode", "")
    if _mode == "list":
        # 优先用 __all__，缺失时回退到扫描公开属性
        methods = getattr(easygui, "__all__", None)
        if not methods:
            methods = [n for n in dir(easygui) if not n.startswith("_")]
        return {
            "msg": "✅ 已获取 easygui 库的方法列表",
            "return": methods,
        }
    elif _mode == "doc":
        _method = kwargs.get("method", "")
        if not _method:
            return "❌ 错误: mode=doc 时必须指定 method 参数"
        if not hasattr(easygui, _method):
            return f"❌ 错误: easygui 库中没有方法 {_method}"
        doc = getattr(easygui, _method).__doc__
        return {
            "msg": f"✅ 已获取 easygui 库 {_method} 方法的文档",
            "return": doc or f"(方法 {_method} 没有文档说明)"
        }
    else:
        return f"❌ 错误: 未知的 mode '{_mode}'，支持 'list'(获取方法列表) 或 'doc'(获取方法文档)"



@tool_need_accept
def _show_notify_tool(kwargs):
    """用 easygui 库显示弹窗（会打断用户操作）"""
    method = kwargs.get("method", "")
    kwargs = kwargs.get("kwargs", {})
    reval = None
    if not method:
        return "❌ 错误: 未指定 easygui 库中的方法名"
    if not hasattr(easygui, method):
        return f"❌ 错误: easygui 库中没有方法 {method}"
    try:
        reval = getattr(easygui, method)(**kwargs)
    except Exception as e:
        return f"❌ 错误: 调用 easygui 方法 {method} 时出错: {e}"
    return f"✅ 已调用 easygui 方法 {method} 并传入参数 {kwargs} | 返回:\n{reval}"



# ================================================
# 工具注册入口 —— 所有工具在此汇总
# ================================================

def get_tools():
    """返回所有工具的字典"""
    tools = {
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

        "everything_search": {
            "description": "使用 Everything 在系统全局范围秒级查找文件/文件夹（比 find_file 快得多，支持全盘搜索）。支持 Everything 搜索语法，如 *.py、ext:exe;ini、path:C:\\Windows、dupe: 等。注意：仅支持 Windows 且需要 Everything 服务正在运行；非 Windows 系统会返回不可用提示",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，支持 Everything 语法（如 *.py、config.ini、ext:pdf）"},
                    "max_results": {"type": "integer", "description": "最大返回结果数（1~500，默认50）"},
                    "path": {"type": "string", "description": "限定搜索的路径（可选）"},
                    "folders_only": {"type": "boolean", "description": "仅搜索文件夹（默认 false）"},
                    "show_size": {"type": "boolean", "description": "显示文件大小（默认 true）"},
                    "show_date": {"type": "boolean", "description": "显示修改日期（默认 false）"}
                },
                "required": ["keyword"],
            },
            "handler": everything_search_tool,
        },

        "see_internet": {
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
        },
    }

    # ===== 可选工具：依赖未安装时跳过，不影响核心工具加载 =====
    if find_internet is not None:
        tools["find_from_internet"] = {
            "description": "在互联网上查找信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "ci": {"type": "string", "description": "要查找的关键词"}
                },
                "required": ["ci"],
            },
            "handler": find_internet_tool,
        }
    if lzdqesj is not None:
        tools["rickroll"] = {
            "description": "播放指定平台的 Rickroll 视频 (提示: 该功能可用于对用户说“你被骗了”或一些类似的情况)",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "传入视频平台名 (youtube / bilibili)"}
                },
                "required": [],
            },
            "handler": _rickroll_tool,
        }
    if easygui is not None:
        tools["show_notify"] = {
            "description": "用 easygui 库显示弹窗 (用于向用户弹出提示信息、选择框、输入框、确认框等弹窗)",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "easygui 库中的方法名"},
                    "kwargs": {"type": "object", "description": "easygui 库中的方法参数"}
                },
                "required": ["method"],
            },
            "handler": _show_notify_tool,
        }
        tools["get_notify_help"] = {
            "description": "获取 easygui 库的帮助信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "description": "查询模式：'list'(列出所有方法名) 或 'doc'(查指定方法文档)"},
                    "method": {"type": "string", "description": "当 mode='doc' 时，指定要查询文档的方法名"}
                },
                "required": ["mode"],
            },
            "handler": _get_notify_help_tool,
        }
    return tools
