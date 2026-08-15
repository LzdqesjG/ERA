# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。

import os
import sys

# ===== 在任何库 import 之前，强制启用 UTF-8 模式 =====
# 环境变量会影响 Rich 等库在 import 时对控制台编码的判断
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')

# 强制 stdout/stderr 使用 UTF-8，支持 emoji 等 Unicode 字符（errors=replace 避免崩溃）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

import AI
import json
import re
import queue
import warnings
import threading
import subprocess
from threading import Lock, Thread, Event, RLock
import time as _time
import uuid as _uuid
import yvyin
import web
from prompt_toolkit import prompt as pt_prompt
from GreatLogger import Logger as _Logger
try:
    from multiprocessing import Process as MpProcess
except Exception:
    MpProcess = None

# 初始化日志记录器
# 先读出旧日志（__init__ 的 imm 模式会清空文件，这里提前备份）
_old_log = ""
if os.path.exists('log.log'):
    try:
        with open('log.log', 'r', encoding='utf-8', errors='replace') as _f:
            _old_log = _f.read().rstrip()
    except Exception:
        pass

log = _Logger('log.log', method='imm', lang='zh')

# ===== 修复 GreatLogger 日志文件编码：GBK → UTF-8 =====
# GreatLogger 内部 open() 未指定 encoding，Windows 默认 GBK，遇到 emoji 会崩。
# 直接 patch Logger 类的所有写日志方法，把 open() 改为 encoding='utf-8'，emoji 原样保存到日志文件。
import arrow as _arrow

def _make_utf8_log_method(level):
    """生成使用 UTF-8 编码写文件的日志方法（替代原 GBK 默认编码）"""
    def _method(self, info):
        self.recording.append(f"[{level}] {info}")
        self.lock.acquire()
        try:
            _t = _arrow.now().format("HH:mm:ss")
            if self.Method == "imm":
                with open(self.logname, "a", encoding="utf-8") as f:
                    f.write(f"[{level} {_t}] {info}\n")
            elif self.Method == "alw":
                self.f.write(f"[{level} {_t}] {info}\n")
            elif self.Method == "beh":
                self.recording_beh.append(f"[{level} {_t}] {info}")
        finally:
            self.lock.release()
    return _method

for _level in ('info', 'warning', 'error', 'debug'):
    setattr(_Logger, _level, _make_utf8_log_method(_level))

# patch log() 方法（自定义级别）
def _patched_log(self, level, info):
    self.recording.append(f"[{level}] {info}")
    self.lock.acquire()
    try:
        _t = _arrow.now().format("HH:mm:ss")
        if self.Method == "imm":
            with open(self.logname, "a", encoding="utf-8") as f:
                f.write(f"[{level} {_t}] {info}\n")
        elif self.Method == "alw":
            self.f.write(f"[{level} {_t}] {info}\n")
        elif self.Method == "beh":
            self.recording_beh.append(f"[{level} {_t}] {info}")
    finally:
        self.lock.release()
_Logger.log = _patched_log

# patch save() 方法（beh 模式批量保存到文件）
def _patched_save(self):
    if self.Method == "beh":
        with open(self.logname, "w", encoding="utf-8") as f:
            for record in self.recording_beh:
                f.write(record + "\n")
    else:
        warnings.warn("Save method is only available in 'beh' mode")
_Logger.save = _patched_save

# 重写日志文件：保留旧日志 + UTF-8 头（__init__ 用 GBK 清空并写了头，这里恢复旧内容并统一编码）
with open('log.log', 'w', encoding='utf-8') as _f:
    if _old_log:
        _f.write(_old_log + "\n")
    _f.write(f"[info {_arrow.now().format('HH:mm:ss')}] 日志初始化完成\n")


def load_tools(ai_instance):
    """从 user_tools.py 加载所有工具"""
    global _user_tools_module
    if not os.path.isfile("user_tools.py"):
        log.warning("未找到 user_tools.py，无可用工具")
        print("⚠️ 未找到 user_tools.py，无可用工具")
        return

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("user_tools", os.path.abspath("user_tools.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _user_tools_module = module  # 保存给后面注入调度器

        if not hasattr(module, "get_tools"):
            log.warning("user_tools.py 中未找到 get_tools() 函数")
            print("⚠️ user_tools.py 中未找到 get_tools() 函数")
            return

        tools = module.get_tools()
        if not isinstance(tools, dict):
            log.warning("get_tools() 必须返回一个字典")
            print("⚠️ get_tools() 必须返回一个字典")
            return

        for name, tool in tools.items():
            ai_instance.add_tool(
                name=name,
                description=tool["description"],
                parameters=tool["parameters"],
                handler=tool["handler"],
            )
        log.info(f"已加载 {len(tools)} 个工具")
        print(f"✅ 已加载 {len(tools)} 个工具")
    except Exception as e:
        log.error(f"加载工具失败: {e}")
        print(f"⚠️ 加载工具失败: {e}")


# ============================================================
# 多会话管理器：history/<datetime>.json
# - 每个会话一个 JSON 文件：{id, title, created_at, updated_at, messages}
# - 首次启动若存在旧 history.json：导入为第一个会话
# - 提供 list/create/load/delete/rename/current API 供 web.py 调用
# ============================================================
class ConversationManager:
    def __init__(self, project_dir, default_system_prompt_fn):
        """
        project_dir: 项目根目录（history/ 目录和旧 history.json 都在这里）
        default_system_prompt_fn: 无参可调用，返回默认 system prompt 列表（用于新建空会话）
        """
        self.project_dir = os.path.abspath(project_dir)
        self.history_dir = os.path.join(self.project_dir, "history")
        os.makedirs(self.history_dir, exist_ok=True)
        self.default_system_prompt_fn = default_system_prompt_fn
        # 当前选中会话（终端/网页共享）
        self._current_id = None
        # 元数据缓存（避免每次 list 都遍历所有文件 stat）: id -> meta dict
        self._meta_cache = {}  # 仅存 id/title/created_at/updated_at
        self._lock = RLock()  # 可重入锁：apply_current_to_ai 会调用 current()
        self._load_meta_cache()
        # 旧 history.json 首次导入
        self._migrate_old_history()

    # ---- 底层文件/缓存 ----
    def _file_of(self, conv_id):
        return os.path.join(self.history_dir, f"{conv_id}.json")

    def _load_meta_cache(self):
        """扫描 history/ 目录，重建元数据缓存（id/title/created_at/updated_at）"""
        self._meta_cache = {}
        for fn in os.listdir(self.history_dir):
            if not fn.endswith(".json"):
                continue
            conv_id = fn[:-5]
            fp = os.path.join(self.history_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._meta_cache[conv_id] = {
                    "id": data.get("id", conv_id),
                    "title": data.get("title", conv_id),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                }
            except Exception:
                continue

    def _migrate_old_history(self):
        old_path = os.path.join(self.project_dir, "history.json")
        if not os.path.isfile(old_path):
            return
        try:
            with open(old_path, "r", encoding="utf-8") as f:
                old_msgs = json.load(f)
        except Exception:
            return
        # 若当前没有任何会话且无当前会话，导入为第一个
        if self._meta_cache:
            # 已有会话：仅把旧文件重命名留档，不主动覆盖
            try:
                backup = os.path.join(self.project_dir, "history.old.bak.json")
                if not os.path.exists(backup):
                    os.rename(old_path, backup)
                    log.info("旧 history.json 已备份为 history.old.bak.json")
            except Exception:
                pass
            return
        created_at = self._now_dt()
        conv_id = created_at
        payload = {
            "id": conv_id,
            "title": "新对话（导入历史）",
            "created_at": created_at,
            "updated_at": created_at,
            "messages": old_msgs,
        }
        try:
            with open(self._file_of(conv_id), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._meta_cache[conv_id] = {
                "id": conv_id, "title": payload["title"],
                "created_at": created_at, "updated_at": created_at,
            }
            self._current_id = conv_id
            try:
                backup = os.path.join(self.project_dir, "history.old.bak.json")
                if not os.path.exists(backup):
                    os.rename(old_path, backup)
            except Exception:
                pass
            log.info(f"旧 history.json 已导入为会话 {conv_id}")
        except Exception as e:
            log.error(f"导入旧 history.json 失败: {e}")

    @staticmethod
    def _now_dt():
        import datetime as _dt
        return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- 公开 API（全部带锁）----
    def list_conversations(self):
        """返回列表 [{id,title,created_at,updated_at,current}]，按 updated_at 倒序"""
        with self._lock:
            arr = list(self._meta_cache.values())
            # 对缓存排序；同时把 current 标记带上
            arr.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
            return [
                {**x, "current": (x["id"] == self._current_id)}
                for x in arr
            ]

    def current(self):
        """返回当前会话的完整 payload（含 messages），无则自动创建一个"""
        with self._lock:
            if self._current_id and self._current_id in self._meta_cache:
                return self._read_full(self._current_id)
            # 否则创建默认会话
            return self._create_locked(title="新对话")

    def create(self, title="新对话", system_prompt=None):
        with self._lock:
            return self._create_locked(title=title, system_prompt=system_prompt)

    def _create_locked(self, title="新对话", system_prompt=None):
        """必须在 self._lock 内调用"""
        created_at = self._now_dt()
        # 避免重名：同一秒创建多个就加 _1/_2
        conv_id = created_at
        counter = 1
        while conv_id in self._meta_cache:
            conv_id = f"{created_at}_{counter}"
            counter += 1
        messages = list(system_prompt) if system_prompt else list(self.default_system_prompt_fn())
        payload = {
            "id": conv_id,
            "title": title,
            "created_at": conv_id,
            "updated_at": conv_id,
            "messages": messages,
        }
        try:
            with open(self._file_of(conv_id), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._meta_cache[conv_id] = {
                "id": conv_id, "title": title,
                "created_at": conv_id, "updated_at": conv_id,
            }
            self._current_id = conv_id
            return payload
        except Exception as e:
            log.error(f"创建会话失败: {e}")
            raise

    def load(self, conv_id):
        """切换到指定会话，返回完整 payload。不存在抛 ValueError"""
        with self._lock:
            if conv_id not in self._meta_cache:
                raise ValueError(f"会话不存在: {conv_id}")
            self._current_id = conv_id
            return self._read_full(conv_id)

    def rename(self, conv_id, new_title):
        new_title = (new_title or "").strip() or "未命名对话"
        with self._lock:
            if conv_id not in self._meta_cache:
                raise ValueError(f"会话不存在: {conv_id}")
            fp = self._file_of(conv_id)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["title"] = new_title
                now = self._now_dt()
                data["updated_at"] = now
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._meta_cache[conv_id]["title"] = new_title
                self._meta_cache[conv_id]["updated_at"] = now
                return self._meta_cache[conv_id]
            except Exception as e:
                log.error(f"重命名会话失败: {e}")
                raise

    def delete(self, conv_id):
        with self._lock:
            if conv_id not in self._meta_cache:
                raise ValueError(f"会话不存在: {conv_id}")
            try:
                fp = self._file_of(conv_id)
                if os.path.isfile(fp):
                    os.remove(fp)
                self._meta_cache.pop(conv_id, None)
                if self._current_id == conv_id:
                    # 删除的是当前会话：如果还剩其他会话则用最新的，否则创建一个新的
                    if self._meta_cache:
                        arr = sorted(
                            self._meta_cache.values(),
                            key=lambda x: x.get("updated_at") or "",
                            reverse=True,
                        )
                        self._current_id = arr[0]["id"]
                    else:
                        # 保持 _current_id 为空，下次 current() 会自动创建
                        self._current_id = None
                        # 确保还有一个默认会话
                        self._create_locked(title="新对话")
                return True
            except Exception as e:
                log.error(f"删除会话失败: {e}")
                raise

    def save_from_ai(self, ai_instance):
        """保存当前会话：把 ai.messages 写入当前会话文件并更新 updated_at"""
        with self._lock:
            if self._current_id is None:
                self._create_locked(title="新对话")
            conv_id = self._current_id
            now = self._now_dt()
            title = self._meta_cache.get(conv_id, {}).get("title", "新对话")
            payload = {
                "id": conv_id,
                "title": title,
                "created_at": self._meta_cache.get(conv_id, {}).get("created_at") or now,
                "updated_at": now,
                "messages": list(ai_instance.get_history()),
            }
            try:
                with open(self._file_of(conv_id), "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                self._meta_cache[conv_id] = {
                    "id": conv_id, "title": title,
                    "created_at": payload["created_at"],
                    "updated_at": now,
                }
            except Exception as e:
                log.error(f"保存会话失败: {e}")

    def apply_current_to_ai(self, ai_instance):
        """把当前会话 messages 加载到 ai_instance.messages（替换）"""
        with self._lock:
            conv = self.current()  # 若不存在则会自动创建
            ai_instance.messages = list(conv.get("messages") or [])
            return conv

    def _read_full(self, conv_id):
        """必须在锁内调用：读 JSON 完整文件"""
        fp = self._file_of(conv_id)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            # 文件意外丢失：从缓存移除
            self._meta_cache.pop(conv_id, None)
            raise ValueError(f"会话文件缺失: {conv_id}")
        except Exception as e:
            raise ValueError(f"读取会话 {conv_id} 失败: {e}")


# 会话管理器单例（__main__ 阶段初始化）
_conv_manager = None  # type: ConversationManager | None


def save_history(ai_instance):
    """兼容旧入口：转发给 ConversationManager"""
    global _conv_manager
    if _conv_manager is None:
        # 会话管理器未初始化时兜底写旧 history.json，避免丢数据
        try:
            with open("history.json", "w", encoding="utf-8") as f:
                json.dump(ai_instance.get_history(), f, ensure_ascii=False, indent=4)
        except Exception as e:
            log.error(f"保存历史失败: {e}")
        return
    try:
        _conv_manager.save_from_ai(ai_instance)
    except Exception as e:
        log.error(f"保存会话失败: {e}")


# AIconfig.json 绝对路径（避免 CWD 变化读错/写错不同的配置文件）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AIconfig.json")


def _save_config(config):
    """统一写回 AIconfig.json（保证 UTF-8、相对项目根目录写文件）"""
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def _normalize_voice_input(raw):
    """把 y/yes/Y/N/no/n 统一成 'y' 或 'n'；其他返回原值不做强改"""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    if s in ("y", "yes", "是", "开", "on", "1", "true"):
        return "y"
    if s in ("n", "no", "否", "关", "off", "0", "false"):
        return "n"
    return s


def _ensure_dir_for_file(filepath):
    """若文件父目录不存在则创建，返回 (成功?, 错误信息)"""
    try:
        parent = os.path.dirname(os.path.abspath(filepath))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        return True, ""
    except Exception as e:
        return False, str(e)


def load_config():
    if os.path.isfile(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {
            "api_key": "",
            "model_name": "deepseek-v4-flash",
            "ai_url": "https://api.deepseek.com",
            "stream_output": True,
            "deep_thinking": True,
            "AItool_path": "",
            "open_voice": "",
            "notes_path": "",
            "port": 8080,
        }
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
    log.info(f"配置加载完成，模型: {config.get('model_name', 'unknown')}")
    return config


def load_history(path):
    if os.path.isfile("history.json"):
        with open("history.json", "r", encoding="utf-8") as f:
            return json.load(f)
    log.info("未找到 history.json，使用默认系统提示")
    return [{"role": "system", "content": f"你是一个电脑助手，专门帮助用户。你可以在{path}里创建你自己做的工具。格式：工具文件夹>工具.py，工具.md。 你可以把工具做成cmd命令。这样就不用再写代码了。"}]

# === 全局状态 ===
ai = None
user_input = ""
reasoning = ""
content = ""
_voice_text = ""
_voice_mode = False
initial_messages = None
lock = Lock()
_task_scheduler = None  # TimedTaskScheduler 实例
_user_tools_module = None  # 动态加载的 user_tools 模块对象，用于注入 _scheduler

# === 网页模式（终端输入 web 后进入：输入从网页输入框来，输出重定向到网页） ===
_web_mode = False
_web_output = queue.Queue()   # 推送给网页的输出流（SSE 消费）
_web_input = queue.Queue()    # 网页提交的输入（主循环消费），元素为 (message, done_event)
_web_done_pending = None      # 上一次请求的完成信号 Event，下一轮循环开头 set
_real_stdout = sys.stdout     # 进入 web 模式前保存真实 stdout
_ansi_re = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# === 全局退出控制（让 Ctrl+C / SIGINT 一定能把程序停下来） ===
# signal handler 只做一件事：设标志。真正退出逻辑在主循环轮询到标志后走 _cleanup_and_exit。
# 这样避免在 signal 栈里做复杂清理（锁 / 线程 join / Rich 刷新等）可能把 Ctrl+C 卡死。
_stop_flag = Event()
# 全局锁的快速释放占位：程序退出时如果 lock 被持有，给它一个"放开路径"
_lock_owner = None


class _WebTee:
    """stdout 重定向包装：web 模式下把输出同时写给真实终端和网页输出队列。
    终端侧保留可见性（便于调试/双屏），网页侧剥离 ANSI 颜色码取纯文本。"""

    def __init__(self, real):
        self._real = real

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        try:
            self._real.write(s)
        except Exception:
            pass
        if s:
            # 剥离 ANSI 转义后推给网页；保留换行等空白以便网页分段显示
            clean = _ansi_re.sub("", s)
            if clean:
                _web_output.put(clean)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    def reconfigure(self, *args, **kwargs):
        try:
            self._real.reconfigure(*args, **kwargs)
        except Exception:
            pass

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._real, "errors", "replace")


def _web_on_assistant_block(reasoning, content, tool_calls_info):
    """一轮 AI 回复回调（web 模式）：把 reasoning + content + 工具调用列表
    作为一个完整"消息块"推送到 SSE 队列。前端据此渲染一个气泡，
    工具调用嵌在气泡内 content 之后，顺序与 AI 输出一致。"""
    try:
        _web_output.put_nowait(json.dumps({
            "__sse_type": "block",
            "reasoning": reasoning or "",
            "content": content or "",
            "tool_calls": tool_calls_info or [],
        }, ensure_ascii=False))
    except Exception:
        pass


def _enter_web_mode():
    """进入网页模式：重定向 stdout + Rich console.file 到 _WebTee。
    流式分支（无工具）仍走 stdout 实时输出 + done 帧整理；
    非流式分支（有工具）走 on_assistant_block 回调推送完整消息块，不走 stdout。"""
    global _web_mode, _real_stdout
    if _web_mode:
        return
    _real_stdout = sys.stdout
    _tee = _WebTee(_real_stdout)
    sys.stdout = _tee
    # 关键：AI 内部用模块级 Rich console 输出，它在 import 时绑定了真实 stdout，
    # 单独改 sys.stdout 抓不到 Rich 输出。这里把 console.file 也指到 _WebTee。
    try:
        AI.console.file = _tee
    except Exception:
        pass
    # 非流式分支走 on_assistant_block 回调推结构化消息块（含工具调用）
    try:
        ai.on_assistant_block = _web_on_assistant_block
    except Exception:
        pass
    _web_mode = True


def _exit_web_mode():
    """退出网页模式：恢复 stdout 和 Rich console.file，清掉回调。"""
    global _web_mode
    if not _web_mode:
        return
    sys.stdout = _real_stdout
    try:
        AI.console.file = _real_stdout
    except Exception:
        pass
    # 清掉回调，恢复终端模式
    try:
        ai.on_assistant_block = None
    except Exception:
        pass
    _web_mode = False


_sig_int_count = 0  # Ctrl+C 次数计数，第二次直接 os._exit 防止锁死


def _install_signal_handlers():
    """在主循环开始前安装信号处理：
    第一次 Ctrl+C：设 _stop_flag 走正常退出。
    第二次 Ctrl+C：直接 os._exit(1)，防止 AI.chat/锁/子进程卡死导致退不出来。
    """
    import signal as _signal
    import os as _os

    def _handler(sig, frame):
        global _sig_int_count
        _stop_flag.set()
        _sig_int_count += 1
        if _sig_int_count >= 2:
            # 连点两次 Ctrl+C：不再做任何清理，直接杀进程
            try:
                out = sys.stdout if sys.stdout is not None else _real_stdout
                out.write("\n💀 强制退出\n")
                out.flush()
            except Exception:
                pass
            _os._exit(1)
        try:
            out = sys.stdout if sys.stdout is not None else _real_stdout
            out.write("\n🛑 正在退出...（再按一次 Ctrl+C 可强制退出）\n")
            out.flush()
        except Exception:
            pass
    try:
        _signal.signal(_signal.SIGINT, _handler)
        _signal.signal(_signal.SIGTERM, _handler)
    except (ValueError, OSError):
        # Windows 下非主线程调用 signal 会 ValueError，忽略即可
        pass


def _cleanup_and_exit(code=0):
    """唯一退出路径：按顺序停一切，sys.exit 收尾。"""
    global _web_done_pending
    # 1) 停所有定时任务（每个任务在 sleep 分块里会立即响应 stop_event）
    if _task_scheduler is not None and hasattr(_task_scheduler, "shutdown_all"):
        try:
            _task_scheduler.shutdown_all()
        except Exception:
            pass
    # 2) 完成所有 pending 的网页请求 done 信号，避免 /api/input 等超时
    if _web_done_pending is not None:
        try:
            _web_done_pending.set()
        except Exception:
            pass
        _web_done_pending = None
    # 3) 恢复 stdout / Rich 输出
    try:
        _exit_web_mode()
    except Exception:
        pass
    # 4) 停语音
    try:
        if _voice_mode:
            yvyin.stop()
    except Exception:
        pass
    # 5) 关日志
    try:
        log.close()
    except Exception:
        pass
    # 给个告别，给点时间让 daemon 线程看到标志后退出（不需要 join）
    try:
        out = sys.stdout if sys.stdout is not None else _real_stdout
        out.write("\n\n👋 再见！\n")
        out.flush()
    except Exception:
        pass
    # 最终退出
    sys.exit(code)


# ==========================================================
# 定时任务调度器：每个任务一条独立线程，循环 等待→触发AI→重新计时
# 触发时自动加 lock，避免和用户正常交互抢 ai.messages
# ==========================================================
class TimedTaskScheduler:
    def __init__(self, ai_ref, chat_lock, go_ai_fn, config_path=None):
        self._ai = ai_ref
        self._lock = chat_lock
        self._go_ai = go_ai_fn
        self._config_path = config_path or _CONFIG_PATH
        self._tasks = {}                  # task_id -> {"task", "thread", "stop_event"}
        self._sched_lock = Lock()         # 保护 _tasks / 并发增删

    # ---------- 内部辅助 ----------
    def _build_prompt(self, task):
        title = task.get("task") or task.get("title") or ""
        intro = task.get("text") or task.get("intro") or ""
        msg = f"🔔 提醒定时任务：{title}"
        if intro:
            msg += f"\n{intro}"
        return msg

    def _load_config(self):
        try:
            if not os.path.isfile(self._config_path):
                return []
            with open(self._config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("time_tasks") or []
        except Exception as e:
            log.error(f"[定时任务] 读取配置失败: {e}")
            return []

    def _save_config(self, new_tasks):
        try:
            cfg = {}
            if os.path.isfile(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["time_tasks"] = new_tasks
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            log.error(f"[定时任务] 保存配置失败: {e}")
            return False

    def _worker(self, task_id, stop_event, snapshot):
        """单个任务线程的主循环"""
        try:
            interval_min = float(snapshot.get("time", 5))
        except Exception:
            interval_min = 5.0
        interval_sec = max(5.0, interval_min * 60.0)
        title = snapshot.get("task") or "(无标题)"

        log.info(f"[定时任务] 启动 id={task_id} 标题='{title}' 间隔={interval_min}分钟")
        while not stop_event.is_set():
            # 分段 sleep，支持快速响应停止
            slept = 0.0
            chunk = 5.0
            while slept < interval_sec and not stop_event.is_set():
                step = min(chunk, interval_sec - slept)
                _time.sleep(step)
                slept += step
            if stop_event.is_set():
                break

            msg = self._build_prompt(snapshot)
            log.info(f"[定时任务] 触发 id={task_id} 标题='{title}' -> {msg[:80]}")
            try:
                print(f"\n⏰ [定时任务触发: {title}]", flush=True)
                with self._lock:
                    global user_input, content
                    user_input = msg
                    # 补打"你: xxx"输入回显行，对齐正常交互视觉
                    print(f"你: {msg}", flush=True)
                    self._go_ai()
            except Exception as e:
                log.error(f"[定时任务] 触发失败 id={task_id}: {e}")
                try:
                    print(f"\n⚠️  [定时任务触发失败: {e}]", flush=True)
                except Exception:
                    pass
        log.info(f"[定时任务] 停止 id={task_id} 标题='{title}'")

    # ---------- 公共 API ----------
    def load_all(self):
        """启动时从配置加载所有任务并启动其线程"""
        cfg_tasks = self._load_config()
        started = 0
        need_save_back = False
        with self._sched_lock:
            for t in cfg_tasks:
                tid = t.get("id")
                if not tid:
                    tid = _uuid.uuid4().hex[:8]
                    t["id"] = tid
                    need_save_back = True
                if tid in self._tasks:
                    continue
                ev = Event()
                th = Thread(
                    target=self._worker,
                    args=(tid, ev, dict(t)),
                    daemon=True,
                    name=f"TimedTask-{tid}"
                )
                self._tasks[tid] = {"task": t, "thread": th, "stop_event": ev}
                th.start()
                started += 1
        # 回写一下（把补生成的 id 真正固化到磁盘）
        if need_save_back:
            self._save_config(cfg_tasks)
        log.info(f"[定时任务] 初始化完成，启动了 {started} 个任务")
        return started

    def add_task(self, time_min, title, intro):
        """新增任务（AI 工具 / 命令行调用）：写配置 + 立即启动线程"""
        try:
            f_min = float(time_min)
        except Exception:
            return "❌ 错误: time(分钟) 必须是数字"
        if f_min <= 0:
            return "❌ 错误: time(分钟) 必须大于 0"
        if not title:
            return "❌ 错误: task(标题) 不能为空"

        tid = _uuid.uuid4().hex[:8]
        new_task = {"id": tid, "time": str(time_min), "task": title, "text": intro or ""}

        # 在 _sched_lock 里完成：读配置 → append → 写配置 → 启动线程
        # 避免并发写配置时 lost update
        with self._sched_lock:
            tasks = self._load_config()
            tasks.append(new_task)
            if not self._save_config(tasks):
                return "❌ 错误: 保存配置失败"

            ev = Event()
            th = Thread(
                target=self._worker,
                args=(tid, ev, dict(new_task)),
                daemon=True,
                name=f"TimedTask-{tid}"
            )
            self._tasks[tid] = {"task": new_task, "thread": th, "stop_event": ev}
            th.start()

        log.info(f"[定时任务] 新增 id={tid} 间隔={time_min}分 标题={title}")
        return (
            f"✅ 添加成功\n"
            f"  id    : {tid}\n"
            f"  间隔  : {time_min} 分钟\n"
            f"  标题  : {title}\n"
            f"  介绍  : {intro}"
        )

    def remove_task(self, index_or_id):
        """删除任务（支持列表索引int 或 id字符串）"""
        removed_thread = None
        removed_title = ""
        removed_id = ""
        # 整个"读配置→查→停线程→删→写配置"都放锁里，避免并发下 lost update
        with self._sched_lock:
            tasks = self._load_config()
            if not tasks:
                return "❌ 当前没有任何定时任务"

            target_idx = None
            target_id = None
            try:
                idx = int(index_or_id)
                if 0 <= idx < len(tasks):
                    target_idx = idx
                    target_id = tasks[idx].get("id")
            except Exception:
                s = str(index_or_id)
                for i, t in enumerate(tasks):
                    if t.get("id") == s:
                        target_idx = i
                        target_id = s
                        break

            if target_idx is None:
                return f"❌ 未找到索引/id='{index_or_id}'的定时任务"

            removed = tasks[target_idx]
            removed_title = removed.get("task", "")
            removed_id = removed.get("id", "")

            # 1. 停线程（set stop_event，同时把 thread 引用带出锁用于后续 join）
            if removed_id and removed_id in self._tasks:
                self._tasks[removed_id]["stop_event"].set()
                removed_thread = self._tasks[removed_id]["thread"]
                self._tasks.pop(removed_id, None)

            # 2. 写配置
            tasks.pop(target_idx)
            if not self._save_config(tasks):
                return "❌ 错误: 保存配置失败"

        # 3. 出锁后再 join（带超时），防止线程卡 AI.chat 期间长时间占锁
        if removed_thread is not None and removed_thread.is_alive():
            try:
                removed_thread.join(timeout=0.5)
            except Exception:
                pass

        log.info(f"[定时任务] 删除 id={removed_id} 标题={removed_title}")
        return f"✅ 删除定时任务成功: [{removed_title}] (id={removed_id})"

    def shutdown_all(self, join_timeout=1.0):
        """停止所有定时任务。不写配置，仅停止线程；带超时 join，不阻塞。"""
        with self._sched_lock:
            task_ids = list(self._tasks.keys())
            threads_to_join = []
            for tid in task_ids:
                try:
                    entry = self._tasks[tid]
                    entry["stop_event"].set()
                    threads_to_join.append(entry["thread"])
                except Exception:
                    pass
            self._tasks.clear()
        for t in threads_to_join:
            if t is not None and t.is_alive():
                try:
                    t.join(timeout=join_timeout)
                except Exception:
                    pass

    def list_all(self):
        """返回格式化字符串，用于 /tasks 命令或 get_time_tasks 工具"""
        cfg_tasks = self._load_config()
        if not cfg_tasks:
            return "当前没有任何定时任务"
        lines = [f"📋 定时任务列表 (共 {len(cfg_tasks)} 个):"]
        with self._sched_lock:
            for i, t in enumerate(cfg_tasks):
                tid = t.get("id", "")
                alive = tid in self._tasks and self._tasks[tid]["thread"].is_alive()
                status = "🟢 运行中" if alive else "⚪ 未启动"
                title = t.get("task", "")
                tm = t.get("time", "?")
                text = t.get("text", "")
                lines.append(f"  [{i}] {status} 间隔{tm}分 | id={tid} | {title}")
                if text:
                    preview = text[:80] + ("…" if len(text) > 80 else "")
                    lines.append(f"        介绍: {preview}")
        return "\n".join(lines)


# ============================================================
# Git 自动更新检查 / 执行更新
# 远程仓库: https://gitee.com/LBG617/era-terminal
# 设计原则:
#   1. 没有 git.exe / 不是 git 仓库 / 网络不通 → 全部静默降级，不影响主程序
#   2. 启动时后台线程做 fetch + 对比，5 秒后出结果不阻塞欢迎信息
#   3. 只告诉用户"有新版本 / 已是最新 / 检查失败"，不自动拉，用户输入 /update 才执行
# ============================================================

_GITEE_REPO_HTTPS = "https://gitee.com/LBG617/era-terminal.git"


def _git(*args, timeout=15, capture=True):
    """封装 subprocess 调用 git，捕获所有异常，返回 (ok, stdout, stderr)"""
    try:
        r = subprocess.run(
            ['git'] + list(args),
            capture_output=capture,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return (r.returncode == 0, r.stdout or "", r.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        return (False, "", str(e))


def _ensure_repo_and_remote():
    """前置检查：是不是 git 仓库；origin 对不对；不对就改成目标仓库 URL。
    返回: (ok, msg)  ok=True 才能继续后续 git 操作"""
    # 1. 目录是不是 git 仓库？
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return (False, "当前目录不是 Git 仓库（没有 .git 文件夹）")

    # 2. 有 origin 吗？URL 对吗？
    ok, out, err = _git('remote', '-v')
    if not ok:
        return (False, f"读取 remote 失败: {(err or out).strip()[:120]}")

    origin_fetch = ""
    for line in out.splitlines():
        if line.startswith("origin") and "(fetch)" in line:
            parts = line.split()
            if len(parts) >= 2:
                origin_fetch = parts[1]
                break

    # 同一仓库有两种合法写法：SSH (git@gitee.com:LBG617/era-terminal.git)
    #                        HTTPS (https://gitee.com/LBG617/era-terminal.git)
    # 任一命中都算配置正确
    def _match_repo(url):
        """把 URL 归一化成 'owner/repo' 形式再比较，兼容 SSH/HTTPS/.git 后缀"""
        try:
            u = url.strip()
            if not u:
                return False
            # 去掉协议头 https:// http:// git:// ssh://
            for proto in ("https://", "http://", "git://", "ssh://", "ftps://", "ftp://"):
                if u.lower().startswith(proto):
                    u = u[len(proto):]
                    break
            # 去掉认证信息 user:pass@ / user@ （SSH 写法 git@gitee.com:xxx 的 @ 这里不处理，后面按 @ 拆 host）
            # 处理格式：[user@]host[:port]/owner/repo(.git)  或  [user@]host:owner/repo(.git)
            if "@" in u:
                u = u.split("@", 1)[1]          # 去掉 user@，剩 host:xxx 或 host/xxx
            # 现在两种格式：
            #   HTTPS: gitee.com/LBG617/era-terminal.git
            #   SSH  : gitee.com:LBG617/era-terminal.git  (注意这里的 host 之后是冒号)
            # 把 host:path → host/path 统一成斜杠
            # 更稳健：取最后两段 owner/repo
            # 先判断分隔符：SSH 格式是 host:owner/repo（':' 出现在 '/' 之前）
            if ":" in u and ("/" not in u or u.index(":") < u.index("/")):
                host, rest = u.split(":", 1)
                u = host + "/" + rest
            # 现在 u 里全是 '/' 分隔，去掉末尾 .git，取最后 2 段
            parts = [p for p in u.split("/") if p]
            if parts and parts[-1].lower().endswith(".git"):
                parts[-1] = parts[-1][:-4]
            if len(parts) >= 2:
                owner_repo = f"{parts[-2]}/{parts[-1]}"
            else:
                return False
            return owner_repo.lower() == "LBG617/era-terminal".lower()
        except Exception:
            return False

    if not origin_fetch:
        # 没有 origin → 加一个
        ok, out, err = _git('remote', 'add', 'origin', _GITEE_REPO_HTTPS)
        if not ok:
            return (False, f"添加 origin 失败: {(err or out).strip()[:120]}")
    else:
        # origin 已存在但 URL 不是目标仓库 → 不改，提醒用户
        if not _match_repo(origin_fetch):
            return (False, f"origin 指向其他仓库 ({origin_fetch})，如需自动更新请手动切换到 {_GITEE_REPO_HTTPS}")

    return (True, "OK")


def _get_remote_default_branch():
    """先 git remote show origin，拿到 HEAD branch；拿不到就猜 master/main。"""
    ok, out, _ = _git('remote', 'show', 'origin', timeout=20)
    if ok:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:") or line.startswith("HEAD 分支:"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    b = parts[1].strip()
                    if b and b != "(unknown)":
                        return b
    # fallback 依次尝试
    ok, out, _ = _git('symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD')
    if ok and out.strip():
        return out.strip().split('/')[-1]
    for guess in ("master", "main"):
        ok, _, _ = _git('rev-parse', '--verify', f'refs/remotes/origin/{guess}')
        if ok:
            return guess
    return "master"


def _check_update():
    """返回 (状态字符串, 需不需要更新)
    状态字符串直接打印给用户看；need_update=True 表示可以执行 /update"""
    ok_pre, msg = _ensure_repo_and_remote()
    if not ok_pre:
        if config["check_update"] == "y":
            return (f"⚠️ 无法检查更新: {msg}", False)

    # 1. fetch origin（不 pull，不修改工作区）
    ok, out, err = _git('fetch', 'origin', timeout=30)
    if not ok:
        tip = (err or out).strip()
        # 403 / Permission denied → 网络或权限
        if "denied" in tip.lower() or "403" in tip or "could not resolve" in tip.lower():
            return ("⚠️ 无法访问 Gitee：网络不通或需要登录，请稍后手动执行 git fetch/pull", False)
        return (f"⚠️ git fetch 失败: {tip[:180]}", False)

    # 2. 找到远程默认分支
    branch = _get_remote_default_branch()
    local_ref = "HEAD"
    remote_ref = f"origin/{branch}"

    # 3. 判断本地是否落后 / 领先 / 已最新 / 分叉
    # rev-list --left-right --count <remote>...<local>
    # output: "behind    ahead"
    ok, out, err = _git('rev-list', '--left-right', '--count', f'{remote_ref}...{local_ref}')
    if not ok:
        return (f"⚠️ 无法对比版本: {(err or out).strip()[:180]}", False)
    try:
        parts = out.strip().split()
        behind = int(parts[0]) if parts else 0
        ahead = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        behind = ahead = 0

    if behind == 0 and ahead == 0:
        return ("✅ 已经是最新版本", False)
    if behind > 0 and ahead == 0:
        # 落后 N 个 commit，给 commit 摘要
        ok, log_out, _ = _git('log', '--oneline', '-n', '5', f'{local_ref}..{remote_ref}')
        tips = []
        if ok:
            for ln in log_out.strip().splitlines():
                if ln.strip():
                    tips.append("    · " + ln.strip())
        extra = "\n" + "\n".join(tips) if tips else ""
        return (f"🔔 发现新版本！落后 {behind} 个提交，输入 /update 更新{extra}", True)
    if ahead > 0 and behind == 0:
        return (f"ℹ️ 本地比远程领先 {ahead} 个提交，无需更新", False)
    return (f"⚠️ 分支已分叉（本地领先 {ahead} / 远程领先 {behind}），请手动合并后再更新", False)


# 启动时后台检查结果，主线程可随时读取（用 list 包装避免 top-level 需要 global 声明）
_update_state = {
    "result": None,    # (status_msg, need_update)
    "done": False,
}


def _bg_check_update():
    """后台线程：等 2 秒避免阻塞欢迎语，然后检查更新，结果写到全局"""
    try:
        _time.sleep(2)
        _update_state["result"] = _check_update()
    except Exception as e:
        _update_state["result"] = (f"⚠️ 检查更新时出错: {e}", False)
    finally:
        _update_state["done"] = True


def do_update():
    """用户输入 /update 时调用：执行 git pull"""
    ok_pre, msg = _ensure_repo_and_remote()
    if not ok_pre:
        return f"❌ 更新失败: {msg}"

    # 先确认有没有本地修改 → 有未提交改动就不让硬 pull（避免冲突）
    ok, status_out, _ = _git('status', '--porcelain')
    if ok and status_out.strip():
        count = len([l for l in status_out.splitlines() if l.strip()])
        return (f"⚠️ 检测到 {count} 处本地未提交改动，为避免覆盖你的代码已中止更新。\n"
                f"   请先 git commit / git stash，再执行 /update")

    # 执行 pull --ff-only：只允许快进合并，拒绝自动 merge（更安全）
    branch = _get_remote_default_branch()
    ok, out, err = _git('pull', '--ff-only', 'origin', branch, timeout=120)
    if not ok:
        tip = (err or out).strip() or "未知错误"
        # 如果是本地 commit 导致 fast-forward 失败，提示用户自己手动处理
        if "non-fast-forward" in tip.lower() or "not possible to fast-forward" in tip.lower():
            return (f"❌ 更新失败：远程与本地已产生分叉（non-fast-forward）。\n"
                    f"   推荐方案：\n"
                    f"   1. git stash (保存你的本地改动)\n"
                    f"   2. git reset --hard origin/{branch}\n"
                    f"   3. git stash pop (恢复改动，再解决冲突)")
        return f"❌ git pull 失败:\n{tip[:500]}"

    # pull 成功后对比输出，给用户一句总结
    out_all = (out or "").strip()
    if "Already up to date." in out_all or "Already up-to-date." in out_all:
        return "✅ 已是最新版本，无需更新"
    return f"✅ 更新成功！建议重启 coAI.py 加载新代码。\n{out_all[:500]}"


def go_AI():
    """主循环入口：根据当前输入是否来自网页队列决定是否传 done_event。"""
    global user_input, _web_done_pending
    chat_with_ai(user_input, done_event=_web_done_pending)

def chat_with_ai(text, done_event=None):
    """供网页/外部调用：传入文本 -> 返回 (reasoning, content)，并保存历史。
    注意：调用方需自行持有 lock（与终端交互互斥）。
    done_event: 若传入（来自 /api/input），则处理完立即 set，让 HTTP 请求及时返回。"""
    global reasoning, content, user_input
    user_input = text
    log.info(f"发送给AI: {text[:200]}")
    if _web_mode:
        # web 模式：网页本地已显示用户输入，不重复打印 "你: xxx"。
        # AI 的回复由 AI 内部 Rich console 输出，经 _WebTee 自动流到网页，无需手动 print。
        reasoning, content = ai.chat(text)
        # 直接向 SSE 队列推入结构化完成事件（不走 stdout，避免 NUL/Rich 换行污染）
        # 载荷带 __sse_type = "done"，前端拿到后替换流式气泡为折叠样式
        try:
            _web_output.put_nowait(json.dumps({
                "__sse_type": "done",
                "reasoning": reasoning or "",
                "content": content or "",
            }, ensure_ascii=False))
        except Exception:
            pass
    else:
        print(f"\n你: {text}", flush=True)
        print("AI: ", end="", flush=True)
        reasoning, content = ai.chat(text)
    log.info(f"AI回复: {content[:200] if content else '(空)'}")
    save_history(ai)
    # done_event 立即 set，让 /api/input 线程不用等主循环下一轮开头
    if done_event is not None:
        try:
            done_event.set()
        except Exception:
            pass
    return reasoning, content

def on_voice(text):
    """语音识别回调：保存文本到 _voice_text，去除换行和多余空白"""
    global _voice_text
    _voice_text = " ".join(text.split())  # 去除换行和多余空白
    log.info(f"语音识别: {_voice_text}")


if __name__ == "__main__":
    log.info("程序启动")

    # 解析命令行参数：--web 启动即进入网页模式
    _start_in_web = "--web" in sys.argv

    config = load_config()

    if not config.get("api_key"):
        key = input("请输入您的 API 密钥: ").strip()
        config["api_key"] = key
        _save_config(config)
        log.info("API 密钥已保存")
        print("✅ API 密钥已保存")

    # AI 工具存放目录：若已配置且目录不存在 → 自动创建；只有完全未配置才询问
    _ai_tool = config.get("AItool_path") or ""
    if not _ai_tool:
        raw = input("请设置AI制作的工具存放路径：").strip()
        if raw:
            config["AItool_path"] = os.path.abspath(raw)
            os.makedirs(config["AItool_path"], exist_ok=True)
            _save_config(config)
            log.info(f"工具路径已保存: {config['AItool_path']}")
            print(f"✅ 工具路径已保存（已自动创建目录: {config['AItool_path']}）")
    elif not os.path.isdir(_ai_tool):
        # 已配过但目录不存在（例如迁移后），自动重建
        try:
            os.makedirs(_ai_tool, exist_ok=True)
            print(f"ℹ️ 工具目录不存在，已自动重建: {_ai_tool}")
            log.info(f"工具目录不存在，已自动重建: {_ai_tool}")
        except Exception as e:
            print(f"⚠️ 工具目录不存在且无法创建: {_ai_tool} ({e})")
            raw = input("请重新设置AI制作的工具存放路径：").strip()
            if raw:
                config["AItool_path"] = os.path.abspath(raw)
                os.makedirs(config["AItool_path"], exist_ok=True)
                _save_config(config)
                print(f"✅ 工具路径已更新: {config['AItool_path']}")

    if not config.get("open_voice"):
        raw = input("是否开启语音功能？（y/n）：")
        config["open_voice"] = _normalize_voice_input(raw)
        _save_config(config)
        log.info(f"语音功能设置: {config['open_voice']}")
        print("✅ 语音功能已保存")

    # 笔记文件路径：已配置但文件不存在 → 自动建父目录 + 空文件；只有完全未配置才询问
    _notes = config.get("notes_path") or ""
    if not _notes:
        raw = input("请设置AI的笔记存放路径：").strip()
        if raw:
            config["notes_path"] = os.path.abspath(raw)
            ok, err = _ensure_dir_for_file(config["notes_path"])
            if ok:
                if not os.path.exists(config["notes_path"]):
                    try:
                        with open(config["notes_path"], "w", encoding="utf-8") as _nf:
                            pass
                    except Exception as e:
                        print(f"⚠️ 无法创建笔记文件: {e}")
                _save_config(config)
                log.info(f"笔记路径已保存: {config['notes_path']}")
                print(f"✅ 笔记路径已保存: {config['notes_path']}")
            else:
                print(f"⚠️ 无法创建笔记目录: {err}")
    elif not os.path.exists(_notes):
        # 已配过但文件/父目录不存在 → 自动重建，不重问
        ok, err = _ensure_dir_for_file(_notes)
        if ok:
            try:
                with open(_notes, "w", encoding="utf-8") as _nf:
                    pass
                print(f"ℹ️ 笔记文件不存在，已自动重建空文件: {_notes}")
                log.info(f"笔记文件不存在，已自动重建空文件: {_notes}")
            except Exception as e:
                print(f"⚠️ 笔记文件不存在且无法创建: {_notes} ({e})")
        else:
            print(f"⚠️ 笔记父目录不存在且无法创建: {err}")

    # ===== 初始化多会话管理器（替代旧的单 history.json 模式）=====
    _tool_path_for_prompt = config.get("AItool_path") or "."
    _conv_manager = ConversationManager(
        project_dir=os.path.dirname(os.path.abspath(__file__)),
        default_system_prompt_fn=lambda: load_history(_tool_path_for_prompt),
    )
    # 以当前会话的 messages 作为 AIChat 初始消息
    current_conv = _conv_manager.current()
    initial_messages = current_conv.get("messages") or load_history(_tool_path_for_prompt)

    ai = AI.AIChat(
        initial_messages=initial_messages,
        api_key=config["api_key"],
        model_name=config["model_name"],
        base_url=config["ai_url"],
    )
    ai.update_config(
        print_to_console=True,
        stream_output=config.get("stream_output", True),
        deep_thinking=config.get("deep_thinking", True),
    )
    log.info(f"AIChat初始化完成，模型: {config['model_name']}")

    # ===== 定时任务调度器 + 工具加载 =====
    _task_scheduler = TimedTaskScheduler(ai_ref=ai, chat_lock=lock, go_ai_fn=go_AI)

    load_tools(ai)
    # 把运行时调度器注入 user_tools 模块，AI 工具调用时可以动态启停任务
    if _user_tools_module is not None:
        _user_tools_module._scheduler = _task_scheduler

    # 启动所有已配置的定时任务
    started = _task_scheduler.load_all()
    if started > 0:
        print(f"⏰ 已自动启动 {started} 个定时任务（输入 /tasks 查看）")

    # ===== 启动网页 + API 服务器（后台线程，端口可配置） =====
    try:
        _web_port = int(config.get("port", 8080))
    except (TypeError, ValueError):
        _web_port = 8080

    # 端口预检：若已被占用（多半是另一个 coAI 实例在跑），给出清晰提示而不是静默失败
    _port_ok = True
    import socket as _socket
    _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        _probe.bind(("127.0.0.1", _web_port))
        _probe.close()
    except OSError:
        _port_ok = False
        _probe.close()
        print(f"⚠️ 端口 {_web_port} 已被占用，网页服务未启动。")
        print(f"   可能是另一个 coAI.py 实例正在运行（AI 曾用 start 启动过额外实例）。")
        print(f"   终端对话不受影响；如需网页，请先关闭占用 {_web_port} 的进程，或在 AIconfig.json 改 port。")

    if _port_ok:
        _server_thread = threading.Thread(
            target=web.start_server,
            args=(_web_port,),
            kwargs=dict(
                ai=ai, lock=lock, chat_fn=chat_with_ai,
                save_history_fn=lambda: save_history(ai),
                scheduler=_task_scheduler, log=log,
                is_web_mode_fn=lambda: _web_mode,
                web_input_queue=_web_input,
                web_output_queue=_web_output,
                history_path=None,  # 会话模式下不再按单文件读取
                conv_manager=_conv_manager,
                config_path=_CONFIG_PATH,
            ),
            daemon=True,
        )
        _server_thread.start()

    # --web 启动参数：启动即进入网页模式（输入从网页来，输出到网页）
    if _start_in_web and _port_ok:
        _enter_web_mode()
        print(f"已启动 http://localhost:{_web_port}/")
        print("💡 网页模式下输入从网页输入框来，输出显示在网页。再输入 web 切回终端。")

    all_tools = ai.list_tools()
    if all_tools:
        print(f"\n📋 可用工具列表 ({len(all_tools)} 个):")
        for tname, tinfo in all_tools.items():
            print(f"   · {tname}: {tinfo['description']}")

    if config["open_voice"] == "y":
        print("\n正在后台加载语音模型...")
        log.info("开始后台加载语音模型")
        def _load_voice():
            try:
                yvyin.load()
                log.info("语音模型加载完成")
                print("✅ 语音模型已就绪，输入 /s 开启语音")
            except Exception as e:
                log.error(f"语音模型加载失败: {e}")
                print(f"❌ 语音模型加载失败: {e}")
        threading.Thread(target=_load_voice, daemon=True).start()

    # ===== 启动后台更新检查（不阻塞） =====
    threading.Thread(target=_bg_check_update, daemon=True).start()
    log.info("后台更新检查已启动")

    print("\n💬 命令: /help 显示此文本 | web 切换网页模式 | /update 检查更新并拉取 | /tools 查看工具 | /tasks 定时任务 | /addtask 新增定时 | /deltask 删除定时 | /clear 清空历史 | /condense 压缩上下文 | /save 保存历史 | /s 语音开关 | /quit 退出")
    print("=" * 50)

    # ===== Ctrl+C 统一走信号 -> 设标志 -> 主循环轮询退出 =====
    _install_signal_handlers()

    while not _stop_flag.is_set():
        try:
            # 完成上一次网页请求的 done 信号（若有），让 /api/input 返回
            if _web_done_pending is not None:
                _web_done_pending.set()
                _web_done_pending = None

            # ===== 网页模式：输入来自网页输入框，用 get(timeout) 轮询，保证 Ctrl+C 能打断 =====
            if _web_mode:
                try:
                    _web_in, _web_done_pending = _web_input.get(timeout=0.5)
                    user_input = _web_in
                except queue.Empty:
                    # 每 0.5s 轮询一次，以便检测 _stop_flag
                    if _stop_flag.is_set():
                        break
                    continue
            else:
                # ===== 若后台更新检查刚完成，在用户输入前打印一次提示 =====
                if _update_state["done"] and _update_state["result"] is not None:
                    msg, _ = _update_state["result"]
                    _update_state["result"] = None
                    log.info(f"[更新检查] {msg}")
                    print(f"\n{msg}")
                    print("=" * 50)

                prompt = "\n你: "
                if _voice_text:
                    prompt = f"\n你: [🎤 {_voice_text}] "
                try:
                    raw = input(prompt).strip()
                except KeyboardInterrupt:
                    # input() 阻塞时的 Ctrl+C：立即走退出
                    raise

                # 处理语音文本
                if _voice_text and not raw:
                    user_input = pt_prompt("你🎤： ", default=_voice_text)
                    _voice_text = ""
                elif _voice_text and raw:
                    user_input = pt_prompt("你+🎤： ", default=_voice_text + " " + raw)
                    _voice_text = ""
                else:
                    user_input = raw

            if not user_input:
                continue

            # ===== web 命令：切换网页模式（终端<->网页） =====
            if user_input == "web":
                if _web_mode:
                    _exit_web_mode()
                    print("🖥️ 已切换回终端模式")
                else:
                    _enter_web_mode()
                    print(f"已启动 http://localhost:{_web_port}/")
                    print("💡 网页模式下输入从网页输入框来，输出显示在网页。再输入 web 切回终端。")
                continue

            if user_input == "/quit":
                log.info("用户退出程序")
                _cleanup_and_exit(0)
            elif user_input == "/update":
                print("\n🔄 正在更新 (git pull --ff-only)，请稍候...")
                log.info("用户请求执行更新")
                result = do_update()
                log.info(f"[更新结果] {result}")
                print(result)
                continue
            elif user_input == "/s":
                if not yvyin.is_ready():
                    log.warning("语音模型尚未就绪")
                    print("⚠️ 语音模型尚未就绪")
                elif _voice_mode:
                    yvyin.stop()
                    _voice_mode = False
                    log.info("语音已关闭")
                    print("🔇 语音已关闭")
                else:
                    yvyin.start(on_voice)
                    _voice_mode = True
                    log.info("语音已开启")
                    print("🎤 语音已开启，长按空格说话")
            elif user_input == "/clear":
                ai.clear_history()
                save_history(ai)
                log.info("对话历史已清空")
                print("✅ 对话历史已清空")
            elif user_input == "/tools":
                tools = ai.list_tools()
                if tools:
                    print(f"\n📋 已注册的工具 ({len(tools)} 个):")
                    for tname, tinfo in tools.items():
                        print(f"   · {tname}: {tinfo['description']}")
                else:
                    print("当前无可用工具")
            elif user_input == "/tasks":
                print(_task_scheduler.list_all() if _task_scheduler else "⚠️ 调度器未初始化")
            elif user_input == "/addtask":
                print("\n--- 新增定时任务 ---")
                t = input("间隔多少分钟（正整数/小数）？ ").strip()
                title = input("任务标题？ ").strip()
                intro = input("任务介绍/提示词？ ").strip()
                res = _task_scheduler.add_task(t, title, intro) if _task_scheduler else "⚠️ 调度器未初始化"
                print(res)
            elif user_input.startswith("/deltask"):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    idx = input("要删除的任务索引(如 0) 或 id？ ").strip()
                else:
                    idx = parts[1].strip()
                res = _task_scheduler.remove_task(idx) if _task_scheduler else "⚠️ 调度器未初始化"
                print(res)
            elif user_input == "/save":
                save_history(ai)
                log.info("历史已保存")
                print("✅ 历史已保存")
            elif user_input == "/condense":
                if len(ai.messages) <= 2:
                    print("对话内容太少，无需压缩")
                    log.info("对话内容太少，无需压缩")
                    continue
                print("正在压缩上下文...")
                log.info("开始压缩上下文")
                result = ai.condense_history()
                save_history(ai)
                log.info(f"压缩完成: {result}")
                print(result)
            elif user_input == "/help":
                print("\n💬 命令: /help 显示此文本 | web 切换网页模式 | /update 检查并更新 (Gitee) | /tools 查看工具 | /tasks 定时任务 | /addtask 新增定时 | /deltask <索引/id> 删除定时 | /clear 清空历史 | /condense 压缩上下文 | /save 保存历史 | /s 语音开关 | /quit 退出")
            else:
                with lock:
                    go_AI()

        except KeyboardInterrupt:
            # 第一次 Ctrl+C：设标志（一般 signal handler 已设，这里双保险），走统一退出
            log.info("用户中断退出 (Ctrl+C)")
            _stop_flag.set()
            _cleanup_and_exit(0)
        except Exception as e:
            if _stop_flag.is_set():
                # 异常由退出信号触发（如被 interrupt 的 EOFError / OSError），不等了，直接退
                _cleanup_and_exit(0)
            log.error(f"主循环出错: {e}")
            try:
                print(f"\n❌ 出错: {e}")
            except Exception:
                pass

    # while 正常结束（_stop_flag 被设），走统一退出
    _cleanup_and_exit(0)
