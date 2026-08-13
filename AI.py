# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。

import sys
import os
import json
import logging
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown

# 屏蔽 openai/httpx 的 HTTP 请求日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# ===== 控制台 UTF-8 安全配置 =====
# 若 coAI.py 已经设置过，就不要覆盖 encoding / errors 参数，只补 line_buffering
try:
    # 先尝试获取现有编码，如果不是 UTF-8 就强制重置
    stdout_enc = getattr(sys.stdout, 'encoding', None) or ''
    stderr_enc = getattr(sys.stderr, 'encoding', None) or ''
    _enc = 'utf-8'
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding=_enc, errors='replace', line_buffering=True)
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding=_enc, errors='replace', line_buffering=True)
except Exception:
    pass

# 创建 Rich Console：指定 force_interactive 避免它自己判断控制台编码时走 GBK 分支
try:
    console = Console(force_terminal=True, color_system='auto')
except Exception:
    console = Console()


def _safe_print(*args, **kwargs):
    """Rich console.print 的安全包装：遇到 UnicodeEncodeError 时 fallback 到普通 print(replace)"""
    try:
        return console.print(*args, **kwargs)
    except UnicodeEncodeError:
        # 把参数转成字符串后 replace 掉不可编码字符，再走 sys.stdout
        parts = []
        for a in args:
            try:
                s = str(a)
            except Exception:
                s = repr(a)
            parts.append(s.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
        try:
            print(' '.join(parts), file=sys.stdout)
            sys.stdout.flush()
        except Exception:
            pass


class AIChat:
    def __init__(self, initial_messages, api_key, model_name="deepseek-v4-flash", base_url="https://api.deepseek.com"):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name
        self.messages = initial_messages
        self._print_to_console = True
        self._stream_output = False
        self._deep_thinking = True
        self._tools = {}

    def add_tool(self, name, description, parameters, handler):
        """注册一个工具供AI调用

        Args:
            name: 工具名称（唯一标识）
            description: 工具描述，AI根据描述决定是否调用
            parameters: JSON Schema格式的参数定义，例如:
                {"type": "object", "properties": {"query": {"type": "string", "description": "搜索内容"}}, "required": ["query"]}
            handler: 可调用对象，接收一个dict参数，返回字符串结果
        """
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler
        }

    def remove_tool(self, name):
        """移除已注册的工具"""
        if name in self._tools:
            del self._tools[name]

    def list_tools(self):
        """列出所有已注册的工具"""
        return {name: {"description": info["description"], "parameters": info["parameters"]}
                for name, info in self._tools.items()}

    def _get_tools_schema(self):
        tools = []
        for name, tool in self._tools.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                }
            })
        return tools

    def _build_api_messages(self):
        """构建发送给API的消息列表，过滤掉reasoning_content等非标准字段，并截断过长历史"""
        api_messages = []
        # 收集所有有效的 tool_call_id（来自 assistant 消息的 tool_calls）
        valid_call_ids = set()
        for msg in self.messages:
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    valid_call_ids.add(tc["id"])

        for msg in self.messages:
            # 跳过孤儿 tool 消息（没有对应 tool_calls 的）
            if msg["role"] == "tool":
                call_id = msg.get("tool_call_id", "")
                if call_id not in valid_call_ids:
                    continue
            api_msg = {"role": msg["role"]}
            if "content" in msg:
                api_msg["content"] = msg["content"] or ""
            if "tool_calls" in msg:
                api_msg["tool_calls"] = msg["tool_calls"]
            if msg["role"] == "tool" and "tool_call_id" in msg:
                api_msg["tool_call_id"] = msg["tool_call_id"]
            api_messages.append(api_msg)

        # 估算token数，超过上限时截断旧消息（保留system消息）
        total_chars = sum(len(m.get("content", "") or "") for m in api_messages)
        for m in api_messages:
            if "tool_calls" in m:
                total_chars += len(json.dumps(m["tool_calls"], ensure_ascii=False))
        max_chars = 800000  # 约20万token的安全上限

        if total_chars > max_chars and len(api_messages) > 2:
            system_msgs = [m for m in api_messages if m["role"] == "system"]
            non_system = [m for m in api_messages if m["role"] != "system"]
            while non_system and total_chars > max_chars:
                removed = non_system.pop(0)
                total_chars -= len(removed.get("content", "") or "")
                if "tool_calls" in removed:
                    total_chars -= len(json.dumps(removed["tool_calls"], ensure_ascii=False))
                    # 删除该 assistant 消息对应的所有 tool 响应，避免孤儿 tool 消息
                    removed_ids = {tc["id"] for tc in removed["tool_calls"]}
                    while non_system and non_system[0]["role"] == "tool" and non_system[0].get("tool_call_id") in removed_ids:
                        tr = non_system.pop(0)
                        total_chars -= len(tr.get("content", "") or "")
            # 如果截断后第一条是 tool 消息（孤儿），继续删除
            while non_system and non_system[0]["role"] == "tool":
                tr = non_system.pop(0)
                total_chars -= len(tr.get("content", "") or "")
            api_messages = system_msgs + non_system

        return api_messages

    def _execute_tool_calls(self, tool_calls):
        tool_results = []
        for tool_call in tool_calls:
            call_id = tool_call.id
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            if func_name in self._tools:
                try:
                    result = self._tools[func_name]["handler"](func_args)
                except Exception as e:
                    result = f"工具 {func_name} 执行出错: {str(e)}"
            else:
                result = f"工具 {func_name} 不存在"

            tool_results.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result)
            })
        return tool_results

    def chat(self, user_input=None):
        if user_input:
            self.messages.append({'role': 'user', 'content': user_input})

        has_tools = len(self._tools) > 0
        stream = self._stream_output and not has_tools
        reasoning_effort = "high" if self._deep_thinking else None
        extra_body = {"thinking": {"type": "enabled"}} if self._deep_thinking else None

        api_messages = self._build_api_messages()

        request_kwargs = {
            "model": self.model_name,
            "messages": api_messages,
            "stream": stream,
            "reasoning_effort": reasoning_effort,
        }
        if extra_body:
            request_kwargs["extra_body"] = extra_body
        if has_tools:
            request_kwargs["tools"] = self._get_tools_schema()

        response = self.client.chat.completions.create(**request_kwargs)

        while True:
            if stream:
                reasoning_content = ""
                content = ""
                last_delta = False
                for chunk in response:
                    delta = chunk.choices[0].delta
                    if delta.reasoning_content:
                        reasoning_content += delta.reasoning_content
                        if self._print_to_console:
                            _safe_print(Markdown(f"\033[91m{delta.reasoning_content}\033[0m", end=""))
                            sys.stdout.flush()
                        last_delta = False
                    elif delta.content:
                        if not last_delta and reasoning_content:
                            if self._print_to_console:
                                _safe_print(Markdown("\n--------------------"))
                                sys.stdout.flush()
                        content += delta.content
                        if self._print_to_console:
                            _safe_print(Markdown(f"\033[94m{delta.content}\033[0m", end=""))
                            sys.stdout.flush()
                        last_delta = True
                if self._print_to_console:
                    _safe_print(Markdown("\n--------------------"))
                    sys.stdout.flush()

                self.messages.append({
                    "role": "assistant",
                    "reasoning_content": reasoning_content,
                    "content": content
                })
                return reasoning_content, content
            else:
                message = response.choices[0].message

                if hasattr(message, 'tool_calls') and message.tool_calls:
                    assistant_msg = {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    }
                    self.messages.append(assistant_msg)

                    if self._print_to_console:
                        if message.content:
                            _safe_print(Markdown(message.content))
                        for tc in message.tool_calls:
                            _safe_print(Markdown(f"\n🔧 调用工具: {tc.function.name}({tc.function.arguments})"))
                        sys.stdout.flush()

                    tool_results = self._execute_tool_calls(message.tool_calls)

                    self.messages.extend(tool_results)

                    api_messages = self._build_api_messages()

                    request_kwargs = {
                        "model": self.model_name,
                        "messages": api_messages,
                        "stream": False,
                        "reasoning_effort": reasoning_effort,
                    }
                    if extra_body:
                        request_kwargs["extra_body"] = extra_body
                    if has_tools:
                        request_kwargs["tools"] = self._get_tools_schema()

                    response = self.client.chat.completions.create(**request_kwargs)
                    continue
                else:
                    reasoning_content = ""
                    if hasattr(message, 'reasoning_content'):
                        reasoning_content = message.reasoning_content or ""
                    content = message.content or ""

                    if self._print_to_console:
                        if reasoning_content:
                            _safe_print(Markdown(f"\033[91m{reasoning_content}\033[0m"))
                            _safe_print(Markdown("--------------------"))
                        _safe_print(Markdown(f"\033[94m{content}\033[0m"))

                    self.messages.append({
                        "role": "assistant",
                        "reasoning_content": reasoning_content,
                        "content": content
                    })
                    return reasoning_content, content

    def update_config(self, print_to_console=None, stream_output=None, deep_thinking=None):
        if print_to_console is not None:
            self._print_to_console = bool(print_to_console)
        if stream_output is not None:
            self._stream_output = bool(stream_output)
        if deep_thinking is not None:
            self._deep_thinking = bool(deep_thinking)

    def get_config(self):
        return {
            "print_to_console": self._print_to_console,
            "stream_output": self._stream_output,
            "deep_thinking": self._deep_thinking
        }

    def condense_history(self):
        """用AI总结对话历史，将旧消息压缩为一条摘要，减少token占用"""
        if len(self.messages) <= 2:
            return "历史消息太少，无需压缩"

        system_msgs = [m for m in self.messages if m.get("role") == "system"]
        non_system = [m for m in self.messages if m.get("role") != "system"]

        if len(non_system) <= 2:
            return "对话内容太少，无需压缩"

        # 临时关闭打印、流式、深度思考和工具，避免总结时卡住
        old_print = self._print_to_console
        old_stream = self._stream_output
        old_thinking = self._deep_thinking
        old_tools = self._tools
        self._print_to_console = False
        self._stream_output = False
        self._deep_thinking = False
        self._tools = {}

        # 构建总结请求
        summary_prompt = "请用中文简洁总结以下对话的关键信息，不超过300字，保留重要主题、决策和事实。\n\n"
        for m in non_system[:-2]:
            role = "用户" if m.get("role") == "user" else "AI"
            content = m.get("content", "")
            if content:
                summary_prompt += f"{role}: {content}\n"

        temp_messages = [{"role": "system", "content": "你是一个对话摘要助手。"}, {"role": "user", "content": summary_prompt}]
        original_messages = self.messages[:]
        try:
            self.messages = temp_messages
            _, summary = self.chat()
            # 重建消息：system + 摘要 + 最近2轮
            new_non_system = non_system[-2:]
            if summary:
                summary_msg = {
                    "role": "assistant",
                    "content": f"[之前对话摘要]\n{summary}"
                }
                self.messages = system_msgs + [summary_msg] + new_non_system
            else:
                self.messages = system_msgs + new_non_system
            return f"✅ 已压缩上下文，保留最近{len(new_non_system)//2}轮对话，摘要约{len(summary)}字"
        except Exception as e:
            self.messages = original_messages
            return f"压缩失败: {e}"
        finally:
            self._print_to_console = old_print
            self._stream_output = old_stream
            self._deep_thinking = old_thinking
            self._tools = old_tools

    def clear_history(self):
        a = self.messages[0]
        self.messages = []
        self.messages.append(a)

    def add_message(self, role, content, reasoning_content=None):
        msg = {"role": role, "content": content}
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.messages.append(msg)

    def get_history(self):
        return self.messages


if __name__ == "__main__":
    _safe_print(Markdown("=== AI Chat ==="))