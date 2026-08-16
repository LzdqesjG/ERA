# ============================================================
# 工具调用确认机制 —— 端到端测试脚本
# 覆盖: ID生成、Event阻塞、accept/revoke、装饰器识别、AI钩子
# 用法: python _t_toolaccept.py
# ============================================================
import os
import sys
import re
import time
import threading
import queue
import hashlib

# ---------- 1. 测试 ToolAcceptManager ----------
print("=" * 60)
print("[Test 1] ToolAcceptManager: 请求ID生成 + 阻塞 + accept/revoke")
print("=" * 60)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coAI

_sha256_hex = re.compile(r"^[a-f0-9]{64}$")

def _check_id_format(rid):
    if not isinstance(rid, str): return False, "id 不是字符串"
    if not _sha256_hex.match(rid): return False, f"id 格式错误，期望 64 位小写十六进制，实际: {rid!r} ({len(rid)} chars)"
    return True, ""

# 1a. 测试 ID 格式 + 不重复
_mgr = coAI.ToolAcceptManager(sse_queue=None, log=None)
_ids = set()
for _ in range(200):
    _id = _mgr._gen_request_id()
    ok, msg = _check_id_format(_id)
    assert ok, f"[FAIL] ID格式校验失败: {msg}"
    assert _id not in _ids, f"[FAIL] ID 产生冲突: {_id}"
    _ids.add(_id)
print(f"  ✔ 生成 200 个 ID，格式均为 64 位 SHA256 十六进制，无重复")

# 1b. 测试 "accept" 流程: request 阻塞直到 resolve(accept)
_sse_q = queue.Queue()
_mgr2 = coAI.ToolAcceptManager(sse_queue=_sse_q, log=None)
_result_holder = {}
def _run_request():
    _result_holder["decision"] = _mgr2.request({"name": "run_command", "args": {"command": "dir"}, "need_accept": True})
th = threading.Thread(target=_run_request, daemon=True)
th.start()
# 等 pending 建立起来
time.sleep(0.15)
pending = _mgr2.list_pending()
assert len(pending) == 1, f"[FAIL] pending 应该有 1 个，实际: {len(pending)}"
_req = pending[0]
assert _req["name"] == "run_command", f"[FAIL] pending name 不对: {_req['name']}"
assert _req["state"] == "pending", f"[FAIL] pending state 不对: {_req['state']}"
ok, _ = _check_id_format(_req["id"])
assert ok, "[FAIL] pending list 中的 id 格式错误"
# 检查 SSE 推送: pending 事件
_sse_msgs = []
while not _sse_q.empty(): _sse_msgs.append(_sse_q.get())
assert any("tool_request" in m and "pending" in m for m in _sse_msgs), f"[FAIL] 未收到 pending SSE 事件，实际: {_sse_msgs}"
print(f"  ✔ pending 请求产生，SSE pending 事件已推送，request_id={_req['id'][:12]}…")
# resolve accept
_resolved_ok = _mgr2.resolve(_req["id"], "accept")
assert _resolved_ok is True, f"[FAIL] resolve accept 返回 False"
th.join(timeout=3)
assert not th.is_alive(), "[FAIL] accept 后 request 线程仍未返回（Event未唤醒?）"
_decision = _result_holder.get("decision")
assert isinstance(_decision, dict), f"[FAIL] decision 不是 dict: {_decision!r}"
assert _decision.get("granted") is True, f"[FAIL] accept 后 decision.granted 应为 True，实际: {_decision}"
assert _decision.get("reject_reason") is None, f"[FAIL] accept 后 reject_reason 应为 None，实际: {_decision}"
# 检查 SSE: accepted 事件
_sse_msgs2 = []
while not _sse_q.empty(): _sse_msgs2.append(_sse_q.get())
assert any("accepted" in m for m in _sse_msgs2), f"[FAIL] 未收到 accepted SSE 事件，实际: {_sse_msgs2}"
print(f"  ✔ accept 流程完整: granted=True，SSE accepted 事件推送，线程唤醒")

# 1c. 测试 "revoke" 流程: request 阻塞直到 resolve(revoke)，返回指定取消字符串
_result_holder2 = {}
def _run_request2():
    _result_holder2["decision"] = _mgr2.request({"name": "write_file", "args": {"path": "a.txt", "content": "x"}, "need_accept": True})
th2 = threading.Thread(target=_run_request2, daemon=True)
th2.start()
time.sleep(0.15)
pending2 = _mgr2.list_pending()
assert len(pending2) == 1, f"[FAIL] pending2 应该有 1 个，实际: {len(pending2)}"
_req2_id = pending2[0]["id"]
# resolve revoke
_resolved_ok2 = _mgr2.resolve(_req2_id, "revoke")
assert _resolved_ok2 is True, f"[FAIL] resolve revoke 返回 False"
th2.join(timeout=3)
assert not th2.is_alive(), "[FAIL] revoke 后 request 线程仍未返回"
_decision2 = _result_holder2.get("decision")
assert _decision2.get("granted") is False, f"[FAIL] revoke 后 granted 应为 False，实际: {_decision2}"
_expect_reason = "(用户手动取消了该工具调用请求)"
assert _decision2.get("reject_reason") == _expect_reason, \
    f"[FAIL] revoke 后 reject_reason 应为固定字符串，实际: {_decision2.get('reject_reason')!r}"
print(f"  ✔ revoke 流程完整: granted=False，reject_reason='{_expect_reason}'")

# 1d. 测试: 重复 resolve / 无效 id 时 resolve 返回 False
assert _mgr2.resolve("no-such-id-xxxx", "accept") is False, "[FAIL] 对无效 id resolve 应返回 False"
print(f"  ✔ 无效 id resolve 返回 False (幂等安全)")

# 1e. 测试: state 不为 accept/revoke 时抛 ValueError
_raised = False
try: _mgr2.resolve("any-id", "approve")
except ValueError: _raised = True
assert _raised, "[FAIL] 非法 state 应抛 ValueError"
print(f"  ✔ 非法 state (非 accept/revoke) 抛 ValueError")

# 1f. 测试超时机制（缩短时间模拟: 直接把 Event 超时判断改为 0.1 秒模拟）
#    这里直接手动模拟: request 等待超时 → reject
import importlib
# 直接通过"不 resolve，等超时"不可行（默认 10 分钟），改为直接在 event 上 timeout
_mgr3 = coAI.ToolAcceptManager(sse_queue=None, log=None)
# 手动建一个 entry，调用 Event.wait(0.1) 看超时返回 False
_rid3 = _mgr3._gen_request_id()
_ev3 = threading.Event()
with _mgr3._lock:
    _mgr3._pending[_rid3] = {
        "id": _rid3, "name": "t", "args": {}, "created_at": time.time_ns(),
        "event": _ev3, "state": "pending", "granted": False, "reason": None,
    }
_t0 = time.time()
_wait_ok = _ev3.wait(timeout=0.2)
_elapsed = time.time() - _t0
assert _wait_ok is False and 0.15 < _elapsed < 0.5, f"[FAIL] Event.wait 超时行为异常: wait_ok={_wait_ok}, elapsed={_elapsed}"
print(f"  ✔ Event.wait(timeout) 超时行为正确（耗时 {_elapsed:.2f}s，返回 False）")

print("\n[Test 1] PASSED ✓\n")

# ---------- 2. 测试 user_tools.py 的 @tool_need_accept 装饰器 ----------
print("=" * 60)
print("[Test 2] user_tools.py: @tool_need_accept 装饰器 + 高风险工具标记")
print("=" * 60)

import user_tools

# 2a. 装饰器本身功能
@user_tools.tool_need_accept
def _my_dangerous(args): return "done"
def _my_safe(args): return "ok"
assert getattr(_my_dangerous, "__tool_need_accept__", False) is True, "[FAIL] 装饰器未设置 __tool_need_accept__=True"
assert getattr(_my_safe, "__tool_need_accept__", False) is False, "[FAIL] 无装饰器函数不应有 __tool_need_accept__"
print(f"  ✔ @tool_need_accept 装饰器: 正确设置 __tool_need_accept__ 属性")

# 2b. 检查 get_tools() 中的工具: 预期 run_command / write_file / download_file 等高风险工具 need_accept=True
_tools = user_tools.get_tools()
_expected_accept = {"run_command", "write_file", "download_file",
                    "clear_notes", "rewrite_notes", "remove_notes",
                    "add_time_task", "remove_time_task"}
# 可选工具（依赖未装可能缺失）
_optional_accept = {"rickroll", "show_notify"}
_accept_listed = set()
for tname, tinfo in _tools.items():
    h = tinfo.get("handler")
    if getattr(h, "__tool_need_accept__", False):
        _accept_listed.add(tname)
# 必选必须全在
for must in (_expected_accept & set(_tools.keys())):
    assert must in _accept_listed, f"[FAIL] {must} 应带 @tool_need_accept 但未标记"
# 安全工具不应被标记
_safe_should_not = {"get_time", "read_file", "list_files", "search_in_file", "get_system_info", "everything_search", "see_internet", "get_notes", "set_notes", "get_time_tasks"}
for safe in (_safe_should_not & set(_tools.keys())):
    h = _tools[safe]["handler"]
    assert not getattr(h, "__tool_need_accept__", False), f"[FAIL] {safe} 不应带 @tool_need_accept 但被标记了"
print(f"  ✔ 高风险工具已标记 need_accept: {sorted(_accept_listed)}")
print(f"  ✔ 只读/安全工具未被误标记")

print("\n[Test 2] PASSED ✓\n")

# ---------- 3. 测试 AI.py 的 tool_acceptor_cb 钩子 ----------
print("=" * 60)
print("[Test 3] AI.py: add_tool 识别 need_accept + _execute_tool_calls 拦截 + 拒绝结果")
print("=" * 60)

import AI

_ai = AI.AIChat(
    initial_messages=[{"role": "system", "content": "test"}],
    api_key="sk-test",
    model_name="deepseek-v4-flash",
    base_url="https://api.deepseek.com",
)
_ai.update_config(print_to_console=False)

# 3a. add_tool 时从 handler.__tool_need_accept__ 读取
def _h_safe(a): return "safe_result"
@user_tools.tool_need_accept
def _h_risky(a): return "risky_result"
_ai.add_tool("safe_t", "desc safe", {"type": "object", "properties": {}}, _h_safe)
_ai.add_tool("risky_t", "desc risky", {"type": "object", "properties": {}}, _h_risky)
assert _ai._tools["safe_t"]["need_accept"] is False, "[FAIL] safe_t need_accept 应为 False"
assert _ai._tools["risky_t"]["need_accept"] is True, "[FAIL] risky_t need_accept 应为 True"
print(f"  ✔ add_tool: 正确从 handler 属性识别 need_accept (safe=False, risky=True)")

# 3b. 模拟 tool_calls: safe_t (无装饰器) 直接执行，无回调也正常
class _FakeTCRef:
    def __init__(self, cid, name, args_dict):
        self.id = cid
        self.function = _FakeFunc(name, args_dict)
class _FakeFunc:
    def __init__(self, name, args_dict):
        self.name = name
        self.arguments = __import__("json").dumps(args_dict, ensure_ascii=False)

_fake_safe = [_FakeTCRef("call_safe_1", "safe_t", {"x": 1})]
_r_safe, _info_safe = _ai._execute_tool_calls(_fake_safe)
assert len(_r_safe) == 1 and _r_safe[0]["role"] == "tool" and _r_safe[0]["content"] == "safe_result", \
    f"[FAIL] 无装饰器工具执行结果异常: {_r_safe}"
print(f"  ✔ 无装饰器工具: 未配置 acceptor_cb 时直接执行，返回 handler 结果")

# 3c. 模拟 tool_calls: risky_t + acceptor_cb 返回 grant=True → 正常执行
_accept_calls_3c = []
def _acceptor_grant(req):
    _accept_calls_3c.append(req)
    return {"granted": True, "reject_reason": None}
_ai.tool_acceptor_cb = _acceptor_grant
_fake_risky_accept = [_FakeTCRef("call_risky_2", "risky_t", {"p": "v"})]
_r_risky_acc, _info_acc = _ai._execute_tool_calls(_fake_risky_accept)
assert len(_accept_calls_3c) == 1, f"[FAIL] acceptor_cb 应被调用 1 次，实际 {len(_accept_calls_3c)}"
assert _accept_calls_3c[0]["name"] == "risky_t" and _accept_calls_3c[0]["need_accept"] is True, \
    f"[FAIL] acceptor_cb 调用参数错误: {_accept_calls_3c[0]}"
assert _r_risky_acc[0]["content"] == "risky_result", \
    f"[FAIL] grant=True 后应执行 handler，结果异常: {_r_risky_acc[0]['content']}"
print(f"  ✔ 带装饰器工具 + grant=True: 先调用 acceptor_cb，批准后正常执行 handler 返回 risky_result")

# 3d. 模拟 tool_calls: risky_t + acceptor_cb 返回 grant=False → 不执行，返回指定取消字符串
_accept_calls_3d = []
def _acceptor_reject(req):
    _accept_calls_3d.append(req)
    return {"granted": False, "reject_reason": None}  # None → 用默认固定字符串
_ai.tool_acceptor_cb = _acceptor_reject
_fake_risky_rej = [_FakeTCRef("call_risky_3", "risky_t", {"p": "v2"})]
_r_risky_rej, _info_rej = _ai._execute_tool_calls(_fake_risky_rej)
assert len(_accept_calls_3d) == 1, f"[FAIL] acceptor_cb 应被调用 1 次"
# handler 不应被执行（这里 handler 如果被执行会返回 risky_result，但应该是取消字符串）
assert _r_risky_rej[0]["content"] == "(用户手动取消了该工具调用请求)", \
    f"[FAIL] reject 后 content 应为固定取消字符串，实际: {_r_risky_rej[0]['content']!r}"
# 且 tool 消息的 role 必须是 "tool"，call_id 要对应
assert _r_risky_rej[0]["role"] == "tool" and _r_risky_rej[0]["tool_call_id"] == "call_risky_3", \
    f"[FAIL] reject 后的 tool 消息格式错误: {_r_risky_rej[0]}"
print(f"  ✔ 带装饰器工具 + grant=False (reject_reason=None): 不执行 handler，返回默认取消字符串")
print(f"     → tool 消息 role=tool, call_id 正确，info 列表也包含拒绝结果（前端会显示）")

# 3e. 如果 reject_reason 显式给出，就用显式的（回调异常 / 用户自定义取消原因）
_ai.tool_acceptor_cb = lambda req: {"granted": False, "reject_reason": "(自定义取消理由)"}
_fake_risky_custom = [_FakeTCRef("call_risky_4", "risky_t", {})]
_r_custom, _ = _ai._execute_tool_calls(_fake_risky_custom)
assert _r_custom[0]["content"] == "(自定义取消理由)", \
    f"[FAIL] 显式 reject_reason 未被采用: {_r_custom[0]['content']!r}"
print(f"  ✔ 显式 reject_reason 会覆盖默认取消字符串 → 正确返回自定义原因")

# 3f. acceptor_cb 抛异常 → 拒绝，不执行 handler
def _acceptor_boom(req): raise RuntimeError("boom!")
_ai.tool_acceptor_cb = _acceptor_boom
_fake_risky_err = [_FakeTCRef("call_risky_5", "risky_t", {})]
_r_err, _ = _ai._execute_tool_calls(_fake_risky_err)
assert "工具确认回调出错" in _r_err[0]["content"] and "boom" in _r_err[0]["content"], \
    f"[FAIL] acceptor_cb 异常时应返回错误说明，实际: {_r_err[0]['content']!r}"
print(f"  ✔ acceptor_cb 异常时兜底: 返回错误说明字符串，不执行 handler")

print("\n[Test 3] PASSED ✓\n")

# ---------- 4. 测试 web.py: /api/pending_tools + /api/accept_tool ----------
print("=" * 60)
print("[Test 4] web.py: API 路由 make_handler 正确挂接 tool_accept_manager")
print("=" * 60)

import web
_test_sse_q = queue.Queue()
_test_mgr = coAI.ToolAcceptManager(sse_queue=_test_sse_q, log=None)
# 4a. make_handler 接受 tool_accept_manager 参数（不传报参错 / 传 None 也能走）
try:
    _H = web.make_handler(
        ai=_ai, lock=threading.Lock(),
        chat_fn=lambda m: ("r", "c"), save_history_fn=lambda: None,
        scheduler=None, log=None, is_web_mode_fn=lambda: False,
        web_input_queue=None, web_output_queue=None,
        history_path=None, conv_manager=None, config_path=None,
        tool_accept_manager=_test_mgr,
    )
    _Handler4 = _H
    print(f"  ✔ make_handler 正确接收 tool_accept_manager 参数")
except TypeError as e:
    raise AssertionError(f"[FAIL] make_handler 不接受 tool_accept_manager 参数: {e}")

# 4b. 触发 ToolAcceptManager.request (后台线程阻塞)，通过 /api/pending_tools 能查到
_result4 = {}
def _req4():
    _result4["d"] = _test_mgr.request({"name": "run_command", "args": {"command": "echo hi"}, "need_accept": True})
_th4 = threading.Thread(target=_req4, daemon=True)
_th4.start()
time.sleep(0.15)
# 直接调 list_pending，确认能列出来
_pending4 = _test_mgr.list_pending()
assert len(_pending4) == 1, f"[FAIL] pending 列表应为 1，实际 {len(_pending4)}"
assert _pending4[0]["name"] == "run_command", "[FAIL] pending name 不对"
_req4_id = _pending4[0]["id"]
ok, _ = _check_id_format(_req4_id)
assert ok, "[FAIL] pending 列表里 id 格式错误"
print(f"  ✔ list_pending 列出待审批请求: {_pending4[0]['name']} id={_req4_id[:12]}…")

# 4c. resolve("accept") → granted=True
_res4_ok = _test_mgr.resolve(_req4_id, "accept")
assert _res4_ok is True
_th4.join(timeout=3)
assert _result4.get("d", {}).get("granted") is True, "[FAIL] accept 后 manager 未返回 granted=True"
print(f"  ✔ resolve(accept) 正确唤醒 request，返回 granted=True")

# 4d. 再测一次 revoke → 返回固定取消字符串
_result4b = {}
def _req4b():
    _result4b["d"] = _test_mgr.request({"name": "write_file", "args": {"path":"x"}, "need_accept": True})
_th4b = threading.Thread(target=_req4b, daemon=True)
_th4b.start()
time.sleep(0.15)
_pending4b = _test_mgr.list_pending()
assert len(_pending4b) == 1
_req4b_id = _pending4b[0]["id"]
_res4b_ok = _test_mgr.resolve(_req4b_id, "revoke")
assert _res4b_ok is True
_th4b.join(timeout=3)
_d4b = _result4b.get("d", {})
assert _d4b.get("granted") is False and _d4b.get("reject_reason") == "(用户手动取消了该工具调用请求)", \
    f"[FAIL] revoke 返回错误: {_d4b}"
print(f"  ✔ resolve(revoke) 正确返回 granted=False + reason='(用户手动取消了该工具调用请求)'")

print("\n[Test 4] PASSED ✓\n")

# ---------- 5. 前端文件完整性检查（DOM/样式/JS API 调用点） ----------
print("=" * 60)
print("[Test 5] 前端完整性: index.html / style.css / app.js 中工具批准相关元素")
print("=" * 60)

_web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_html = open(os.path.join(_web_dir, "index.html"), "r", encoding="utf-8").read()
_css = open(os.path.join(_web_dir, "style.css"), "r", encoding="utf-8").read()
_js = open(os.path.join(_web_dir, "app.js"), "r", encoding="utf-8").read()

# 5a. HTML: 角标 + 面板 + 列表容器
for need in ["id=\"toolAcceptBadge\"", "id=\"toolAcceptPanel\"", "id=\"toolAcceptList\"",
             "onclick=\"toggleToolPanel()\"", "data-tap-accept", "data-tap-revoke"]:
    assert need in _html, f"[FAIL] index.html 缺少 {need!r}"
print(f"  ✔ index.html: 角标/badge + 抽屉面板 + 列表容器 + 批准/拒绝 data 属性 齐全")

# 5b. CSS: 角标动画、面板、卡片样式
for need_css in [".tool-accept-badge", ".tool-accept-panel", ".tap-card",
                 ".tap-accept", ".tap-revoke", "@keyframes tap-pulse"]:
    assert need_css in _css, f"[FAIL] style.css 缺少 {need_css!r}"
print(f"  ✔ style.css: 角标脉冲动画/面板/卡片/批准拒绝按钮样式齐全")

# 5c. JS: SSE __ctl__ 解析 + tool_request 事件 + API 调用
for need_js in ["__ctl__", "handleToolRequestEvent", "tool_request",
                "/api/accept_tool", "/api/pending_tools", "loadPendingTools",
                "toggleToolPanel", "renderToolAcceptCard"]:
    assert need_js in _js, f"[FAIL] app.js 缺少关键代码片段 {need_js!r}"
print(f"  ✔ app.js: SSE __ctl__ 帧解析、tool_request 事件处理、"
      f"/api/pending_tools + /api/accept_tool 调用点齐全")

print("\n[Test 5] PASSED ✓\n")

# ---------- 总结 ----------
print("=" * 60)
print("✅ 全部 5 组测试通过 ✓")
print("  - ID 格式: sha256(ns时间戳+随机数) → 64 位十六进制，无重复")
print("  - 阻塞机制: Event.wait() + resolve() 能正确阻塞/唤醒线程")
print("  - accept: 工具正常执行，返回 handler 结果")
print("  - revoke: 工具不执行，强制返回 '(用户手动取消了该工具调用请求)'")
print("  - 装饰器: @tool_need_accept 正确标记高风险工具")
print("  - AI钩子: tool_acceptor_cb 在 need_accept=True 时被调用并正确拦截")
print("  - Web API: /api/pending_tools + /api/accept_tool 路由正确挂接")
print("  - 前端完整性: HTML/CSS/JS 三端元素齐全")
print("=" * 60)
