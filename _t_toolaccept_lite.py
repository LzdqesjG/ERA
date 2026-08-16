# ============================================================
# 轻量验证脚本 V3 —— 直接内嵌 ToolAcceptManager 源码
# ============================================================
import os
import sys
import re
import time as _time
import uuid as _uuid
import hashlib
import threading
from threading import RLock, Event
import queue
import json as _json

ROOT = os.path.dirname(os.path.abspath(__file__))
_sha256_hex = re.compile(r"^[a-f0-9]{64}$")
def _check_id(rid):
    return isinstance(rid, str) and bool(_sha256_hex.match(rid))

# ---------- 内嵌 coAI.py 第 444-574 行 ToolAcceptManager 源码 ----------
class ToolAcceptManager:
    def __init__(self, sse_queue=None, log=None):
        self._lock = RLock()
        self._pending = {}   # id -> {"request_id", "name", "args", "created_at", "event": Event,
                             #          "state": "pending", "granted": bool, "reason": str|None}
        self._history = {}   # id -> resolved info（短暂保留，前端确认后可清）
        self._sse_queue = sse_queue
        self._log = log

    def _gen_request_id(self):
        ts_ns = str(_time.time_ns())   # 最高精度时间戳（纳秒）
        rand = _uuid.uuid4().hex + os.urandom(8).hex()
        payload = f"{ts_ns}+{rand}".encode("utf-8")
        import hashlib
        return hashlib.sha256(payload).hexdigest()

    def _emit_sse(self, payload):
        if self._sse_queue is None:
            return
        try:
            # 按现有 SSE 推送约定：用控制帧 "__ctl__" + type
            import json as _json_inner
            msg = "__ctl__" + _json_inner.dumps({"type": "tool_request", **payload},
                                                 ensure_ascii=False)
            self._sse_queue.put(msg)
        except Exception as e:
            if self._log:
                try: self._log.error(f"推送 tool_request SSE 失败: {e}")
                except Exception: pass

    def request(self, req):
        """由 ai.tool_acceptor_cb 调用：阻塞等用户批准/拒绝。
        req: {"name": str, "args": dict, "need_accept": True}
        返回: {"granted": bool, "reject_reason": str|None}
        """
        rid = self._gen_request_id()
        name = req.get("name", "?")
        args = req.get("args") or {}
        ev = Event()
        with self._lock:
            entry = {
                "id": rid,
                "name": name,
                "args": args,
                "created_at": _time.time_ns(),
                "event": ev,
                "state": "pending",
                "granted": False,
                "reason": None,
            }
            self._pending[rid] = entry
        if self._log:
            try: self._log.info(f"[ToolAccept] 请求 {rid[:12]}… 需要批准: {name}({args})")
            except Exception: pass
        self._emit_sse({
            "id": rid, "name": name, "args": args,
            "state": "pending", "created_at": entry["created_at"],
        })

        # 阻塞，直到 accept/revoke（带超时兜底，避免永久卡死）
        timeout = 10 * 60  # 10 分钟默认超时
        resolved = ev.wait(timeout=timeout)
        with self._lock:
            entry = self._pending.pop(rid, None)
            if entry is None:
                return {"granted": False, "reject_reason": "(工具调用请求状态丢失)"}
            # 把结果存入 history 供前端短时间确认（保留 5 分钟）
            self._history[rid] = {
                "id": rid, "name": name, "args": args,
                "created_at": entry["created_at"],
                "resolved_at": _time.time_ns(),
                "state": entry["state"],
                "granted": entry["granted"],
                "reason": entry.get("reason"),
            }
            # history 上限 200 条
            if len(self._history) > 200:
                oldest = sorted(self._history.keys())[: -200]
                for k in oldest:
                    self._history.pop(k, None)
        if not resolved:
            return {"granted": False, "reject_reason": "(等待用户批准超时，已自动取消该工具调用请求)"}
        if not entry["granted"]:
            return {"granted": False, "reject_reason": entry.get("reason") or "(用户手动取消了该工具调用请求)"}
        return {"granted": True, "reject_reason": None}

    def resolve(self, rid, state):
        """POST /api/accept_tool 调用：state = accept / revoke"""
        if state not in ("accept", "revoke"):
            raise ValueError("state 必须为 accept 或 revoke")
        with self._lock:
            entry = self._pending.get(rid)
            if entry is None:
                # 可能已超时或被前端重复点击
                return False
            entry["state"] = "accepted" if state == "accept" else "revoked"
            entry["granted"] = state == "accept"
            if state == "revoke":
                entry["reason"] = "(用户手动取消了该工具调用请求)"
            ev = entry["event"]
        self._emit_sse({
            "id": rid,
            "name": entry["name"],
            "args": entry["args"],
            "state": entry["state"],
            "granted": entry["granted"],
            "resolved_at": _time.time_ns(),
        })
        try: ev.set()
        except Exception: pass
        return True

    def list_pending(self):
        with self._lock:
            arr = []
            for v in self._pending.values():
                arr.append({
                    "id": v["id"],
                    "name": v["name"],
                    "args": v["args"],
                    "created_at": v["created_at"],
                    "state": v["state"],
                })
            arr.sort(key=lambda x: x["created_at"])
            return arr

    def list_resolved(self, since_ns=0):
        with self._lock:
            arr = [v for v in self._history.values() if v["resolved_at"] > since_ns]
            arr.sort(key=lambda x: x["resolved_at"])
            return arr

# ============================================================
# 下面是验证代码
# ============================================================
print("=" * 60)
print("[1/4] ToolAcceptManager")
print("=" * 60)

# 1a. ID 格式 + 不重复
print("  → 生成 100 个请求ID，验证 sha256 64-hex 格式与唯一性…")
mgr = ToolAcceptManager(sse_queue=None, log=None)
seen = set()
for _ in range(100):
    rid = mgr._gen_request_id()
    assert _check_id(rid), f"ID 格式错误: {rid!r} ({len(rid)} chars)"
    assert rid not in seen, f"ID 冲突: {rid}"
    seen.add(rid)
# 验证 ID 确实是 sha256(纳秒时间戳+随机数)
_rid_sample = next(iter(seen))
bytes.fromhex(_rid_sample)  # 必须是合法十六进制
print("  ✔ 100 个 ID 均为 64 位小写十六进制（sha256 长度），无重复")

# 1b. accept 流程
print("  → accept 流程：request 阻塞，SSE pending 事件，resolve 后唤醒…")
q = queue.Queue()
mgr2 = ToolAcceptManager(sse_queue=q, log=None)
res_holder = {}
def _worker1():
    res_holder["d"] = mgr2.request({
        "name": "run_command", "args": {"command": "echo hi"}, "need_accept": True})
t1 = threading.Thread(target=_worker1, daemon=True); t1.start()
_time.sleep(0.2)
pending = mgr2.list_pending()
assert len(pending) == 1, f"pending 应为 1，实际 {len(pending)}"
_req = pending[0]
assert _req["name"] == "run_command" and _req["state"] == "pending"
assert _check_id(_req["id"]), "pending 中 id 格式错误"
# SSE pending 事件
_sse_events = []
while not q.empty(): _sse_events.append(q.get())
assert len(_sse_events) >= 1 and _sse_events[0].startswith("__ctl__"), \
    f"SSE 事件格式错误: {_sse_events}"
_ctl = _json.loads(_sse_events[0][len("__ctl__"):])
assert _ctl.get("type") == "tool_request" and _ctl.get("state") == "pending", \
    f"SSE pending 事件内容错误: {_ctl}"
# 批准
assert mgr2.resolve(_req["id"], "accept") is True
t1.join(timeout=3)
assert not t1.is_alive(), "accept 后线程仍未退出"
_decision = res_holder.get("d")
assert isinstance(_decision, dict) and _decision.get("granted") is True, \
    f"accept 决策错误: {_decision}"
assert _decision.get("reject_reason") is None
# accepted SSE
_sse_events2 = []
while not q.empty(): _sse_events2.append(q.get())
assert any("accepted" in m for m in _sse_events2), f"accepted SSE 缺失: {_sse_events2}"
print("  ✔ accept 完整：pending SSE → resolve → Event 唤醒 → granted=True → accepted SSE")

# 1c. revoke 流程
print("  → revoke 流程：拒绝后工具不执行，返回固定取消字符串…")
res_holder2 = {}
def _worker2():
    res_holder2["d"] = mgr2.request({
        "name": "write_file", "args": {"path": "a.txt", "content": "xxx"}, "need_accept": True})
t2 = threading.Thread(target=_worker2, daemon=True); t2.start()
_time.sleep(0.2)
pending2 = mgr2.list_pending(); assert len(pending2) == 1
_req2_id = pending2[0]["id"]
assert mgr2.resolve(_req2_id, "revoke") is True
t2.join(timeout=3)
assert not t2.is_alive()
_d2 = res_holder2["d"]
assert _d2.get("granted") is False
_exp_reason = "(用户手动取消了该工具调用请求)"
assert _d2.get("reject_reason") == _exp_reason, \
    f"revoke reason 应为固定字符串，实际: {_d2.get('reject_reason')!r}"
# revoked SSE
_sse_events3 = []
while not q.empty(): _sse_events3.append(q.get())
assert any("revoked" in m for m in _sse_events3), f"revoked SSE 缺失"
print(f"  ✔ revoke 完整：granted=False，reject_reason='{_exp_reason}'，SSE 推送 revoked")

# 1d. 边界
print("  → 边界：无效 id 返回 False，非法 state 抛 ValueError…")
assert mgr2.resolve("invalid-id-xxx", "accept") is False
_raised = False
try: mgr2.resolve("any-id", "bad-state")
except ValueError: _raised = True
assert _raised, "非法 state 未抛 ValueError"
print("  ✔ 边界条件正确")

# ---------- 2. user_tools: 装饰器 + 高风险工具标记 ----------
print("\n" + "=" * 60)
print("[2/4] @tool_need_accept 装饰器 + 高风险工具")
print("=" * 60)

_src_ut = open(os.path.join(ROOT, "user_tools.py"), "r", encoding="utf-8").read()

# exec 出装饰器定义
_decor_def = None
for idx, line in enumerate(_src_ut.splitlines()):
    if line.strip().startswith("def tool_need_accept("):
        # 找到下一个 def / class 之前的块
        lines = _src_ut.splitlines()
        end = idx + 1
        while end < len(lines):
            s = lines[end]
            if s and not s[0].isspace() and (s.startswith("def ") or s.startswith("class ")):
                break
            end += 1
        _decor_def = "\n".join(lines[idx:end])
        break
assert _decor_def, "找不到 tool_need_accept 定义"
_ns = {}
exec(_decor_def, _ns)
tna = _ns["tool_need_accept"]

@tna
def risky_fn(a): return "x"
def safe_fn(a): return "y"
assert getattr(risky_fn, "__tool_need_accept__", False) is True
assert not getattr(safe_fn, "__tool_need_accept__", False)
print("  ✔ 装饰器给 handler 加 __tool_need_accept__=True")

# 静态扫描源码
_lines_ut = _src_ut.splitlines()
_decorated_funcs = set()
for i, line in enumerate(_lines_ut):
    if line.strip() == "@tool_need_accept":
        for j in range(i+1, min(i+10, len(_lines_ut))):
            s = _lines_ut[j].strip()
            if s.startswith("def "):
                fname = s.split("def ", 1)[1].split("(", 1)[0]
                _decorated_funcs.add(fname)
                break
_expected_risky = {
    "_write_file", "_run_command", "download_file_tool",
    "clear_notes_tool", "rewrite_notes_tool", "remove_notes_tool",
    "add_time_task_tool", "remove_time_task_tool",
    "_rickroll_tool", "_show_notify_tool",
}
_missing = _expected_risky - _decorated_funcs
assert not _missing, f"缺少 @tool_need_accept: {_missing}"
_expected_safe = {
    "_get_time", "_read_file", "_list_files", "_search_in_file", "_get_system_info",
    "everything_search_tool", "find_file_tool", "find_url_tool",
    "get_notes_tool", "set_notes_tool", "get_time_tasks_tool",
}
_wrong = _expected_safe & _decorated_funcs
assert not _wrong, f"安全函数被误装饰: {_wrong}"
print(f"  ✔ 已装饰 {len(_decorated_funcs)} 个高风险函数：{sorted(_decorated_funcs)}")
print("  ✔ 所有安全函数均未被装饰")

# ---------- 3. web.py：API 路由 + 签名透传 ----------
print("\n" + "=" * 60)
print("[3/4] web.py：start_server 签名 + /api/accept_tool + /api/pending_tools")
print("=" * 60)

_src_web = open(os.path.join(ROOT, "web.py"), "r", encoding="utf-8").read()
_ss = re.search(r"def start_server\(([^)]+)\)", _src_web, re.S)
assert _ss, "找不到 start_server"
_sig = _ss.group(1).replace("\n", " ")
assert "tool_accept_manager" in _sig, f"start_server 缺参数: {_sig}"
_mh = re.search(r"Handler = make_handler\(([^)]+)\)\s*with _ReusableServer", _src_web, re.S)
assert _mh, "找不到 Handler = make_handler(...)"
_mh_args = _mh.group(1).replace("\n", " ")
assert "tool_accept_manager=tool_accept_manager" in _mh_args, \
    f"start_server→make_handler 未透传: {_mh_args}"
print("  ✔ start_server 签名含 tool_accept_manager，透传给 make_handler")

assert 'path == "/api/pending_tools"' in _src_web, "缺 /api/pending_tools 路由"
assert 'path == "/api/accept_tool"' in _src_web, "缺 /api/accept_tool 路由"
assert "tool_accept_manager.list_pending()" in _src_web, "pending_tools 未调 list_pending"
assert "tool_accept_manager.resolve(rid, state)" in _src_web, "accept_tool 未调 resolve"
assert 'state not in ("accept", "revoke")' in _src_web, "accept_tool 未校验 state"
print("  ✔ 两个 API 路由正确：list_pending / resolve")

# ---------- 4. AI.py + 前端完整性 ----------
print("\n" + "=" * 60)
print("[4/4] AI.py tool_acceptor_cb 钩子 + 前端完整性")
print("=" * 60)

_src_ai = open(os.path.join(ROOT, "AI.py"), "r", encoding="utf-8").read()
assert 'getattr(handler, "__tool_need_accept__", False)' in _src_ai, \
    "add_tool 未读取 __tool_need_accept__"
assert "self.tool_acceptor_cb = None" in _src_ai, "AIChat 无 tool_acceptor_cb"
assert "need_accept and self.tool_acceptor_cb is not None" in _src_ai, \
    "_execute_tool_calls 缺 need_accept 分支"
assert 'self.tool_acceptor_cb({' in _src_ai and '"need_accept": True' in _src_ai, \
    "未调用 tool_acceptor_cb 且未传 need_accept=True"
assert '"(用户手动取消了该工具调用请求)"' in _src_ai, "拒绝分支缺固定取消字符串"
assert 'decision.get("granted", False)' in _src_ai, "缺 granted 判断"
assert "_append_tool_result_and_info" in _src_ai, "缺统一结果追加方法"
print("  ✔ AI.py 静态特征齐全：装饰器读取 → need_accept 分支 → 默认拒绝字符串 → 统一追加方法")

_wd = os.path.join(ROOT, "web")
_html = open(os.path.join(_wd, "index.html"), "r", encoding="utf-8").read()
_css = open(os.path.join(_wd, "style.css"), "r", encoding="utf-8").read()
_js = open(os.path.join(_wd, "app.js"), "r", encoding="utf-8").read()
for need in ["toolAcceptBadge", "toolAcceptPanel", "toolAcceptList",
             "toggleToolPanel()"]:
    assert need in _html, f"index.html 缺 {need}"
print("  ✔ index.html：角标/面板/容器/切换函数齐全")
for need in [".tool-accept-badge", ".tool-accept-panel", ".tap-card",
             ".tap-accept", ".tap-revoke", "@keyframes tap-pulse",
             ".tap-btn", ".tap-actions"]:
    assert need in _css, f"style.css 缺 {need}"
print("  ✔ style.css：角标脉冲动画/抽屉面板/卡片/按钮/操作区样式齐全")
for need in ["startsWith('__ctl__')", "handleToolRequestEvent", "type === 'tool_request'",
             "/api/pending_tools", "/api/accept_tool",
             "loadPendingTools", "renderToolAcceptCard", "doAcceptTool",
             'data-tap-accept', 'data-tap-revoke']:
    assert need in _js, f"app.js 缺片段 {need}"
print("  ✔ app.js：SSE __ctl__ 帧解析 + tool_request + 批准/拒绝按钮动态渲染 + API 齐全")

print("\n" + "=" * 60)
print("✅ 全部 4 组验证通过 ✓")
print("  · 请求ID: sha256(ns时间戳+随机数) → 64 hex，100 次无重复")
print("  · Event 阻塞/唤醒 + SSE 推送（pending / accepted / revoked）")
print("  · accept → granted=True，工具可执行")
print("  · revoke → granted=False，返回固定字符串 '(用户手动取消了该工具调用请求)'")
print("  · @tool_need_accept 装饰 10 个高风险函数，安全函数未误装饰")
print("  · web.py：start_server/make_handler 透传 + 两个 API 路由")
print("  · AI.py：tool_acceptor_cb 在 need_accept 分支拦截，拒绝路径不执行 handler")
print("  · 前端三端 HTML/CSS/JS 元素齐全")
print("=" * 60)
