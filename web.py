# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。

"""
web.py — ERA 终端网页端服务

两种使用方式：
  1) 非 web 模式（默认）：网页通过 /api/chat 同步请求-响应。
  2) web 模式（终端输入 web 进入）：输入从网页输入框提交(/api/input)，
     输出经 stdout 重定向流式推送到网页(/api/stream, SSE)。

路由:
  GET  /             -> 聊天网页 (HTML)
  GET  /api/status   -> {"web_mode": bool}
  GET  /api/tools    -> {"tools": [...]}
  GET  /api/tasks    -> {"tasks_text": "..."}
  GET  /api/stream   -> SSE 输出流（仅 web 模式）
  POST /api/input    -> {"message":"..."} -> web 模式入队 / 非 web 模式同步返回
  POST /api/chat     -> {"message":"..."} -> {"reply","reasoning"}（非 web 模式）
  POST /             -> 兼容旧 HTTP API: 纯文本 body (/clear /tools /save /condense 或消息)
"""

import http.server
import socketserver
import json
import queue
import os
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse


# ============================================================
# DeepSeek 余额查询
# ============================================================
def _normalize_base_url(u):
    """去掉末尾斜杠并转小写，便于比较"""
    if not u:
        return ""
    return str(u).strip().rstrip("/").lower()


def _is_deepseek(base_url):
    return _normalize_base_url(base_url) == "https://api.deepseek.com"


def query_balance(api_key, base_url, timeout=8):
    """查询 DeepSeek 余额。仅当 base_url 为 api.deepseek.com 时有效。
    返回 {"ok": bool, "balance": "19.48", "currency": "CNY", "raw": {...}} 或 {"ok": False, "error": "..."}
    """
    if not _is_deepseek(base_url):
        return {"ok": False, "error": "当前 API 地址非 DeepSeek，不支持余额查询",
                "supported": False}
    if not api_key:
        return {"ok": False, "error": "未配置 API 密钥", "supported": True}
    url = "https://api.deepseek.com/user/balance"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        return {"ok": False, "error": f"HTTP {e.code}: {err_body or e.reason}",
                "supported": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "supported": True}
    if not data.get("is_available"):
        return {"ok": False, "error": "账户不可用（is_available=false）", "supported": True,
                "raw": data}
    infos = data.get("balance_infos") or []
    if not infos:
        return {"ok": False, "error": "未返回余额信息", "supported": True, "raw": data}
    info = infos[0]
    return {
        "ok": True,
        "balance": str(info.get("total_balance") or ""),
        "currency": str(info.get("currency") or ""),
        "granted_balance": str(info.get("granted_balance") or ""),
        "topped_up_balance": str(info.get("topped_up_balance") or ""),
        "raw": data,
    }



# ============================================================
# 静态资源文件（位于 web/ 目录，index.html / style.css / app.js）
# ============================================================
_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _read_static(filename, default=""):
    """读取 web/ 目录下的静态文件，失败时返回 default"""
    try:
        path = os.path.join(_WEB_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return default


_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico":  "image/x-icon",
}


# ============================================================
# HTTP Handler 构造器
# ============================================================
def make_handler(*, ai, lock, chat_fn, save_history_fn, scheduler=None, log=None,
                 is_web_mode_fn=None, web_input_queue=None, web_output_queue=None,
                 history_path=None, conv_manager=None, config_path=None):
    """
    is_web_mode_fn:  无参回调，返回当前是否 web 模式
    web_input_queue: 网页提交的输入队列（web 模式由主循环消费）
    web_output_queue: 推送给网页的输出队列（SSE 消费）
    history_path:    history.json 的路径；None 时按常见位置兜底搜索（仅 conv_manager=None 时使用）
    conv_manager:    ConversationManager 实例（启用多会话后，/api/convs 系列 + history 都走它）
    config_path:     AIconfig.json 路径（用于 /api/config 读写）
    """
    import os as _os

    def _resolve_history_path():
        if conv_manager is not None:
            return None  # 多会话模式：不需要单文件 history_path
        if history_path and _os.path.isfile(history_path):
            return history_path
        for base in (".", _os.path.dirname(_os.path.abspath(__file__))):
            cand = _os.path.join(base, "history.json")
            if _os.path.isfile(cand):
                return cand
        return None

    # 工具函数：把原始 messages(含 role:tool 行) 转换为前端 user/assistant 结构，工具调用折叠进 assistant.tool_calls
    def _normalize_messages_for_frontend(raw_msgs):
        tool_results_by_id = {}
        for m in raw_msgs:
            if m.get("role") == "tool":
                cid = m.get("tool_call_id")
                if cid:
                    tool_results_by_id[cid] = m.get("content") or ""
        out = []
        for m in raw_msgs:
            role = m.get("role")
            if role == "system":
                continue
            if role == "tool":
                continue
            if role == "user":
                out.append({"role": "user", "content": m.get("content") or ""})
            elif role == "assistant":
                tcs = m.get("tool_calls") or []
                tc_list = []
                for tc in tcs:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name", "")
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args_obj = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except Exception:
                        args_obj = {"_raw": args_raw}
                    tc_list.append({
                        "name": name,
                        "args": args_obj,
                        "result": tool_results_by_id.get(tc.get("id"), ""),
                    })
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "reasoning": m.get("reasoning_content") or m.get("reasoning") or "",
                    "tool_calls": tc_list,
                })
        return out

    class WebHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if log is not None:
                try:
                    log.info(f"HTTP {self.address_string()} {fmt % args}")
                except Exception:
                    pass

        # ---------- 响应辅助 ----------
        def _send(self, body, status=200, content_type="text/plain; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, obj, status=200):
            self._send(json.dumps(obj, ensure_ascii=False), status,
                       "application/json; charset=utf-8")

        def _in_web_mode(self):
            return bool(is_web_mode_fn and is_web_mode_fn())

        # ---------- GET ----------
        def do_GET(self):
            path = urlparse(self.path).path
            # ---- 根路径：加载 index.html ----
            if path in ("/", "/index.html"):
                body = _read_static("index.html")
                if not body:
                    body = "<h1>500</h1><p>web/index.html 缺失，请从仓库恢复。</p>"
                    self._send(body, 500, "text/html; charset=utf-8")
                else:
                    self._send(body, 200, "text/html; charset=utf-8")
                return
            # ---- 静态文件：/style.css /app.js 等 ----
            if path.startswith("/") and path.count("/") == 1:
                fname = path.lstrip("/")
                ext = os.path.splitext(fname)[1].lower()
                if ext in _MIME and os.path.isfile(os.path.join(_WEB_DIR, fname)):
                    self._send(_read_static(fname), 200, _MIME[ext])
                    return
            if path == "/api/status":
                self._send_json({"web_mode": self._in_web_mode()})
                return
            if path == "/api/conversations" or path == "/api/convs":
                # 列出所有会话
                if conv_manager is None:
                    self._send_json({"error": "多会话未启用"}, 503)
                else:
                    self._send_json({"conversations": conv_manager.list_conversations()})
                return
            if path == "/api/conversations/current" or path == "/api/convs/current":
                # 返回当前会话完整信息（含 messages 已归一化）
                if conv_manager is None:
                    self._send_json({"error": "多会话未启用"}, 503)
                else:
                    try:
                        conv = conv_manager.current()
                        msgs = _normalize_messages_for_frontend(conv.get("messages") or [])
                        self._send_json({
                            "id": conv.get("id"),
                            "title": conv.get("title"),
                            "created_at": conv.get("created_at"),
                            "updated_at": conv.get("updated_at"),
                            "messages": msgs,
                        })
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                return
            if path == "/api/history":
                # 取当前会话历史（兼容旧端点）
                if conv_manager is not None:
                    try:
                        conv = conv_manager.current()
                        msgs = _normalize_messages_for_frontend(conv.get("messages") or [])
                        self._send_json({"messages": msgs, "loaded": True,
                                         "conv_id": conv.get("id"),
                                         "conv_title": conv.get("title")})
                    except Exception as e:
                        self._send_json({"messages": [], "loaded": False, "error": str(e)}, 500)
                    return
                # 旧模式：单文件 history.json
                hp = _resolve_history_path()
                if hp is None:
                    self._send_json({"messages": [], "loaded": False})
                    return
                try:
                    with open(hp, "r", encoding="utf-8") as f:
                        raw_msgs = json.load(f)
                except Exception as e:
                    self._send_json({"messages": [], "loaded": False, "error": str(e)}, 500)
                    return
                out = _normalize_messages_for_frontend(raw_msgs)
                self._send_json({"messages": out, "loaded": True, "path": hp})
                return
            if path == "/api/tools":
                tools = ai.list_tools()
                data = [{"name": n, "description": t["description"]}
                        for n, t in tools.items()]
                self._send_json({"tools": data})
                return
            if path == "/api/config":
                # 返回 AIconfig.json 内容供网页设置页编辑
                cp = config_path
                if not cp:
                    cp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                       "AIconfig.json")
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    self._send_json({"ok": True, "config": cfg, "path": cp})
                except FileNotFoundError:
                    self._send_json({"ok": False, "error": "配置文件不存在", "config": {}, "path": cp}, 404)
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e), "config": {}, "path": cp}, 500)
                return
            if path == "/api/balance":
                # 查询 DeepSeek 余额：从配置文件读最新的 api_key / ai_url
                cp = config_path
                if not cp:
                    cp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                       "AIconfig.json")
                _ak = ""
                _bu = ""
                try:
                    if _os.path.isfile(cp):
                        with open(cp, "r", encoding="utf-8") as f:
                            _cfg = json.load(f)
                        _ak = _cfg.get("api_key") or ""
                        _bu = _cfg.get("ai_url") or ""
                except Exception:
                    pass
                if not _is_deepseek(_bu):
                    self._send_json({"ok": False, "supported": False,
                                     "error": "当前 API 地址非 DeepSeek，不支持余额查询"})
                    return
                self._send_json(query_balance(_ak, _bu))
                return
            if path == "/api/tasks":
                if scheduler is not None and hasattr(scheduler, "list_all"):
                    self._send_json({"tasks_text": scheduler.list_all()})
                else:
                    self._send_json({"tasks_text": "调度器未初始化"})
                return
            if path == "/api/stream":
                self._handle_sse()
                return
            self._send("Not Found", 404)

        # ---------- SSE 输出流 ----------
        def _handle_sse(self):
            if not self._in_web_mode():
                # 非 web 模式：直接 404，网页会降级到同步模式
                self._send("web 模式未启用", 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # 先刷一条连接确认
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            q = web_output_queue
            while True:
                try:
                    msg = q.get(timeout=15)
                    # 支持两种入队内容：
                    # 1) 纯文本：_WebTee 推过来的 stdout 碎片 -> 包成 {type:"text",text}
                    # 2) 已带 __sse_type 的控制帧 JSON 字符串 -> 直接原样发（chat_with_ai 直接入队）
                    if isinstance(msg, str) and msg.startswith("{") and "\"__sse_type\"" in msg \
                            and msg.rstrip().endswith("}"):
                        # 已是结构化控制帧 JSON：直接用（仍兼容解析时 __sse_type 字段）
                        payload = msg
                    else:
                        payload = json.dumps({"text": str(msg)}, ensure_ascii=False)
                    # 写入 data: 行。payload 可能含换行，按 SSE 规范拆成多行 data:
                    lines = payload.split("\n")
                    for ln in lines:
                        self.wfile.write(f"data: {ln}\n".encode("utf-8"))
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                except queue.Empty:
                    # 心跳保活
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                except (BrokenPipeError, ConnectionResetError):
                    return

        # ---------- POST ----------
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except (TypeError, ValueError):
                length = 0
            raw_body = self.rfile.read(length) if length > 0 else b""

            # ---- 会话 CRUD（多会话模式下） ----
            if conv_manager is not None:
                # POST /api/conversations  — 新建会话，body 可选 {title,switch:true}
                if path in ("/api/conversations", "/api/convs"):
                    try:
                        payload = json.loads(raw_body.decode("utf-8") or "{}")
                    except Exception:
                        payload = {}
                    title = (payload.get("title") or "").strip() or "新对话"
                    with lock:  # 避免和正在 chat_with_ai 同时修改 ai.messages
                        conv = conv_manager.create(title=title)
                        conv_manager.apply_current_to_ai(ai)
                        if save_history_fn:
                            try: save_history_fn()
                            except Exception: pass
                    msgs = _normalize_messages_for_frontend(conv.get("messages") or [])
                    self._send_json({"ok": True,
                                     "id": conv["id"], "title": conv["title"],
                                     "created_at": conv["created_at"],
                                     "messages": msgs})
                    return
                # POST /api/conversations/<id>/load — 切换到指定会话（把 ai.messages 替换为该会话内容）
                if path.startswith("/api/conversations/") and path.endswith("/load") \
                   or path.startswith("/api/convs/") and path.endswith("/load"):
                    _prefix = "/api/conversations/" if path.startswith("/api/conversations/") else "/api/convs/"
                    conv_id = path[len(_prefix):-len("/load")]
                    try:
                        with lock:
                            conv = conv_manager.load(conv_id)
                            conv_manager.apply_current_to_ai(ai)
                        msgs = _normalize_messages_for_frontend(conv.get("messages") or [])
                        self._send_json({"ok": True, "id": conv["id"],
                                         "title": conv["title"], "messages": msgs})
                    except ValueError as e:
                        self._send_json({"error": str(e)}, 404)
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                    return
                # POST /api/conversations/<id>/rename — 重命名
                if path.startswith("/api/conversations/") and path.endswith("/rename") \
                   or path.startswith("/api/convs/") and path.endswith("/rename"):
                    _prefix = "/api/conversations/" if path.startswith("/api/conversations/") else "/api/convs/"
                    conv_id = path[len(_prefix):-len("/rename")]
                    try:
                        payload = json.loads(raw_body.decode("utf-8") or "{}")
                        title = (payload.get("title") or "").strip()
                        meta = conv_manager.rename(conv_id, title)
                        self._send_json({"ok": True, **meta})
                    except ValueError as e:
                        self._send_json({"error": str(e)}, 404)
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                    return
                # POST /api/conversations/<id>/delete — 删除
                if path.startswith("/api/conversations/") and path.endswith("/delete") \
                   or path.startswith("/api/convs/") and path.endswith("/delete"):
                    _prefix = "/api/conversations/" if path.startswith("/api/conversations/") else "/api/convs/"
                    conv_id = path[len(_prefix):-len("/delete")]
                    try:
                        with lock:
                            conv_manager.delete(conv_id)
                            conv_manager.apply_current_to_ai(ai)
                            if save_history_fn:
                                try: save_history_fn()
                                except Exception: pass
                        # 返回新的当前会话
                        curr = conv_manager.current()
                        msgs = _normalize_messages_for_frontend(curr.get("messages") or [])
                        self._send_json({"ok": True,
                                         "current_id": curr.get("id"),
                                         "current_title": curr.get("title"),
                                         "messages": msgs,
                                         "conversations": conv_manager.list_conversations()})
                    except ValueError as e:
                        self._send_json({"error": str(e)}, 404)
                    except Exception as e:
                        self._send_json({"error": str(e)}, 500)
                    return

            # ---- POST /api/config：保存 AIconfig.json 并热应用到 AI 实例 ----
            if path == "/api/config":
                cp = config_path
                if not cp:
                    cp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                       "AIconfig.json")
                try:
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    new_cfg = payload.get("config") if isinstance(payload, dict) else None
                    if not isinstance(new_cfg, dict):
                        self._send_json({"error": "缺少 config 字段"}, 400)
                        return
                except Exception:
                    self._send_json({"error": "无效的 JSON"}, 400)
                    return
                # 类型规范化
                def _to_bool(v):
                    if isinstance(v, bool):
                        return v
                    return str(v).strip().lower() in ("1", "true", "y", "yes", "on", "是")
                try:
                    # 先读旧配置做合并（保留前端未提交的字段）
                    old_cfg = {}
                    if _os.path.isfile(cp):
                        with open(cp, "r", encoding="utf-8") as f:
                            old_cfg = json.load(f)
                    merged = dict(old_cfg)
                    merged.update(new_cfg)
                    # 关键字段类型校正
                    if "stream_output" in merged:
                        merged["stream_output"] = _to_bool(merged["stream_output"])
                    if "deep_thinking" in merged:
                        merged["deep_thinking"] = _to_bool(merged["deep_thinking"])
                    if "port" in merged and merged["port"] not in (None, ""):
                        try:
                            merged["port"] = int(merged["port"])
                        except (TypeError, ValueError):
                            pass
                    for _k in ("api_key", "model_name", "ai_url", "AItool_path",
                               "notes_path", "open_voice", "check_update"):
                        if _k in merged and merged[_k] is not None:
                            merged[_k] = str(merged[_k])
                    # 写回文件
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump(merged, f, ensure_ascii=False, indent=4)
                    # 热应用到 AI 实例（接口信息 + 行为开关）
                    applied = {}
                    try:
                        with lock:
                            _ak = merged.get("api_key") or ""
                            _mn = merged.get("model_name") or ""
                            _bu = merged.get("ai_url") or ""
                            if hasattr(ai, "reconfigure") and (_ak or _mn or _bu):
                                ai.reconfigure(api_key=_ak or None,
                                               model_name=_mn or None,
                                               base_url=_bu or None)
                            if hasattr(ai, "update_config"):
                                ai.update_config(
                                    stream_output=merged.get("stream_output"),
                                    deep_thinking=merged.get("deep_thinking"),
                                )
                        applied = {"api_key": bool(_ak), "model_name": _mn,
                                   "ai_url": _bu,
                                   "stream_output": merged.get("stream_output"),
                                   "deep_thinking": merged.get("deep_thinking")}
                    except Exception as e:
                        applied = {"apply_error": str(e)}
                    # port / 路径类需重启才生效
                    need_restart = []
                    if "port" in new_cfg:
                        need_restart.append("port")
                    self._send_json({"ok": True, "config": merged, "applied": applied,
                                     "need_restart": need_restart,
                                     "note": "接口信息已热更新；port/路径类改动需重启程序生效" if need_restart
                                             else "配置已保存并应用"})
                except Exception as e:
                    self._send_json({"error": f"保存配置失败: {e}"}, 500)
                return

            # ---- /api/input：web 模式入队 + 等完成 / 非 web 模式同步 ----
            if path == "/api/input":
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    message = (payload.get("message") or "").strip()
                except Exception:
                    self._send_json({"error": "无效的 JSON"}, 400)
                    return
                if not message:
                    self._send_json({"error": "消息不能为空"}, 400)
                    return
                if self._in_web_mode():
                    if web_input_queue is None:
                        self._send_json({"error": "输入队列未初始化"}, 500)
                        return
                    import threading as _th
                    done_evt = _th.Event()
                    web_input_queue.put((message, done_evt))
                    # 等主循环处理完毕（AI 输出经 SSE 流式推送，这里只等结束信号）
                    done = done_evt.wait(timeout=600)
                    if not done:
                        self._send_json({"error": "AI 处理超时"}, 504)
                    else:
                        self._send_json({"done": True})
                    return
                # 非 web 模式：同步调用（等价于 /api/chat）
                try:
                    with lock:
                        reasoning, content = chat_fn(message)
                except Exception as e:
                    self._send_json({"error": f"AI 调用失败: {e}"}, 500)
                    return
                self._send_json({"queued": False, "reply": content or "",
                                 "reasoning": reasoning or ""})
                return

            # ---- /api/chat：同步请求-响应（非 web 模式主用）----
            if path == "/api/chat":
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    message = (payload.get("message") or "").strip()
                except Exception:
                    self._send_json({"error": "无效的 JSON"}, 400)
                    return
                if not message:
                    self._send_json({"error": "消息不能为空"}, 400)
                    return
                try:
                    with lock:
                        reasoning, content = chat_fn(message)
                except Exception as e:
                    self._send_json({"error": f"AI 调用失败: {e}"}, 500)
                    return
                self._send_json({"reply": content or "", "reasoning": reasoning or ""})
                return

            # ---- 兼容旧 API: POST /，纯文本 ----
            if path in ("/", "/api"):
                raw = raw_body.decode("utf-8", errors="replace")
                if log is not None:
                    log.info(f"HTTP请求: {raw[:200]}")
                if not raw:
                    self._send("Bad Request", 400)
                    return
                if raw == "/clear":
                    ai.clear_history()
                    save_history_fn()
                    self._send("✅ 对话历史已清空")
                    return
                if raw == "/tools":
                    tools = ai.list_tools()
                    if tools:
                        msg = f"\n📋 已注册的工具 ({len(tools)} 个):"
                        for tname, tinfo in tools.items():
                            msg += f"\n   · {tname}: {tinfo['description']}"
                    else:
                        msg = "当前无可用工具"
                    self._send(msg)
                    return
                if raw == "/save":
                    save_history_fn()
                    self._send("✅ 历史已保存")
                    return
                if raw == "/condense":
                    try:
                        result = ai.condense_history()
                        save_history_fn()
                        self._send(result)
                    except Exception as e:
                        self._send(f"❌ 压缩失败: {e}", 500)
                    return
                try:
                    with lock:
                        reasoning, content = chat_fn(raw)
                except Exception as e:
                    self._send(f"❌ AI 调用失败: {e}", 500)
                    return
                self._send(content or "")
                return

            self._send("Not Found", 404)

    return WebHandler


# ============================================================
# 多线程 TCP Server（SSE 长连接需并发处理请求）
# ============================================================
class _ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(port, *, ai, lock, chat_fn, save_history_fn, scheduler=None, log=None,
                 is_web_mode_fn=None, web_input_queue=None, web_output_queue=None,
                 history_path=None, conv_manager=None, config_path=None):
    """启动网页 + API 服务器。阻塞调用，应放在 daemon 线程里。"""
    Handler = make_handler(
        ai=ai, lock=lock, chat_fn=chat_fn,
        save_history_fn=save_history_fn, scheduler=scheduler, log=log,
        is_web_mode_fn=is_web_mode_fn,
        web_input_queue=web_input_queue,
        web_output_queue=web_output_queue,
        history_path=history_path,
        conv_manager=conv_manager,
        config_path=config_path,
    )
    with _ReusableServer(("", port), Handler) as httpd:
        if log is not None:
            log.info(f"网页服务器启动，端口: {port}")
        print(f"[网页服务] 已启动: http://localhost:{port}")
        httpd.serve_forever()
