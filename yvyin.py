# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。

"""
yvyin.py — 语音识别库（基于 FunASR）

功能：长按空格键录音，松开后自动识别并回调返回文本。
用法：
    import yvyin
    yvyin.load()                # 加载模型（主进程）
    yvyin.start(on_text)        # 启用热键录音
    yvyin.stop()                # 停止热键录音
"""

import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import keyboard
import os
import atexit
import logging
import contextlib
import io
import sys

# 录音文件临时路径（与脚本同目录）
_AUDIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio.wav")

# === 全局状态 ===
_model = None
"FunASR 模型实例"
_ready = False         
"模型是否加载完成"
_active = False        
"热键录音是否已启用（start/stop 控制）"
_recording = False     
"当前是否正在录音"
_audio_data = []       
"录音数据块列表（每块是 numpy 数组）"
_stream = None         
"sounddevice 输入流实例"
_lock = threading.Lock()       
"保护 _recording 和 _audio_data 的线程锁"
_callback = None       
"识别完成后的回调函数 callback(text)"
_press_hook = None     
"空格按下的事件钩子"
_release_hook = None   
"空格松开的事件钩子"


def _audio_callback(indata, frames, time_info, status):
    """sounddevice 录音回调，每次将数据块追加到 _audio_data"""
    with _lock:
        if _recording:
            _audio_data.append(indata.copy())


def _on_space_press():
    """空格按下：开始录音，创建输入流"""
    global _recording, _audio_data, _stream
    # 模型未就绪、未激活、或已在录音中 → 跳过
    if not _ready or not _active or _recording:
        return
    with _lock:
        _audio_data = []          # 清空之前的录音数据
        _recording = True
    # 创建 16kHz 单声道 float32 输入流
    _stream = sd.InputStream(
        samplerate=16000, channels=1, dtype='float32', callback=_audio_callback
    )
    _stream.start()


def _on_space_release():
    """空格松开：停止录音，保存音频文件，异步识别"""
    global _recording, _stream
    if not _recording:
        return
    with _lock:
        _recording = False
    # 关闭输入流
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None
    # 合并数据块并写入 wav 文件，然后异步识别
    if _audio_data:
        audio = np.concatenate(_audio_data, axis=0)
        sf.write(_AUDIO_PATH, audio, 16000)
        # 在后台线程执行识别，避免阻塞键盘事件循环
        threading.Thread(target=_do_recognize, daemon=True).start()


def _do_recognize():
    """执行语音识别，完成后调用回调函数"""
    try:
        result = _model.generate(input=_AUDIO_PATH)
        text = result[0]["text"]
        # 仅在仍处于激活状态时回调（stop 后丢弃结果）
        if _active and _callback:
            _callback(text)
    except Exception as e:
        if _active and _callback:
            _callback(f"[识别错误: {e}]")


def _cleanup():
    """程序退出时清理资源：关闭录音流、注销热键"""
    global _recording, _stream
    _recording = False
    if _stream:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
    if _press_hook is not None:
        try:
            keyboard.unhook(_press_hook)
        except Exception:
            pass
    if _release_hook is not None:
        try:
            keyboard.unhook(_release_hook)
        except Exception:
            pass

# 注册退出清理函数
atexit.register(_cleanup)


# === 公开 API ===

_load_lock = threading.Lock()

class _FilteredStream:
    """过滤 funasr/torch/modelscope 日志的 stdout/stderr 包装器，其他内容正常输出"""
    _block_keywords = ("funasr", "modelscope", "Downloading", "it/s]", "it/s)", "file/s]",
                        "Loading", "rank:", "ckpt:", "scope_map", "excludes", "trust_remote_code")
    _real_stdout = sys.stdout
    _real_stderr = sys.stderr

    def __init__(self, is_stderr=False):
        self._real = sys.stderr if is_stderr else sys.stdout

    def write(self, text):
        if not text or not text.strip():
            self._real.write(text)
            return
        for kw in _FilteredStream._block_keywords:
            if kw in text:
                return  # 丢弃
        self._real.write(text)

    def flush(self):
        self._real.flush()


def load():
    """加载 ASR 模型，静默加载不输出控制台信息。可在任意线程安全调用。"""
    global _model, _ready
    if _ready:
        return
    with _load_lock:
        if _ready:
            return
        # 屏蔽 funasr 的 logging 输出
        logging.disable(logging.CRITICAL)
        # 用过滤流替换全局 stdout/stderr（只过滤 funasr 相关，主线程 print 正常）
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = _FilteredStream()
        sys.stderr = _FilteredStream(is_stderr=True)
        try:
            from funasr import AutoModel
            _model = AutoModel(
                model="FunAudioLLM/Fun-ASR-Nano-2512",  # 语音识别模型
                device="cuda",                          # GPU 加速
                backend="vllm",                         # 推理后端
                disable_update=True,                    # 禁用更新检查
            )
        except Exception:
            raise
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            logging.disable(logging.NOTSET)
        _ready = True


def is_ready():
    """检查模型是否已加载完成"""
    return _ready


def start(callback):
    """启用长按空格录音

    长按空格录音，松开自动识别，识别完成后调用 callback(text)。
    suppress=True 会拦截空格键事件，不传递给终端 input()。
    """
    global _callback, _active, _press_hook, _release_hook
    _callback = callback
    _active = True
    # 注册空格按下/松开热键（仅注册一次）
    if _press_hook is None:
        _press_hook = keyboard.on_press_key('space', lambda e: _on_space_press(), suppress=True)
    if _release_hook is None:
        _release_hook = keyboard.on_release_key('space', lambda e: _on_space_release(), suppress=True)


def stop():
    """停止热键录音，取消当前录音

    注销空格热键、关闭录音流、丢弃识别结果（_active=False 使回调不触发）。
    """
    global _recording, _stream, _active, _press_hook, _release_hook
    _active = False
    with _lock:
        _recording = False
    # 关闭录音流
    if _stream:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None
    # 注销热键
    if _press_hook is not None:
        try:
            keyboard.unhook(_press_hook)
        except Exception:
            pass
        _press_hook = None
    if _release_hook is not None:
        try:
            keyboard.unhook(_release_hook)
        except Exception:
            pass
        _release_hook = None
