# 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
# 许可证: AGPL-3.0  (见 LICENSE)
# 本项目基于 ERA 终端，衍生修改须保留本署名并遵循 AGPL-3.0。
# ============================================================
# ECA.py — ERA 终端简易命令行外壳
# 功能：提供交互式命令行，支持文件读写、目录切换、列表、启动 AI 助手
# 运行环境：仅支持 Windows（os.name == "nt"）
# ============================================================

import random
import os
import time
import warnings
import subprocess
warnings.filterwarnings("ignore")

# ---------- 全局状态变量 ----------
puth = os.getcwd()          # 当前工作目录（用户看到的 cwd，命令 cd 会改它）
Os = os.name                # 操作系统类型（nt=Windows / posix=Linux/macOS）
inp = ""                    # 用户输入的原始字符串（全局复用，文件读取后会覆盖）
lug = ""                    # 命令行左侧提示符（如 "ERA [C:\xxx\ERA终端]"）


def relug():
    """刷新提示符：将当前工作目录拼到 ERA [ ] 里并写回全局变量 lug"""
    global lug, Os, puth
    lug = f"ERA [{puth}]"


def luck(a):
    """
    生成提示符前缀：a 个感叹号 + 1 个空格
    例：luck(1) -> "! ", luck(2) -> "!! "
    作用：区分主命令行（!）和文件编辑子模式（!!）
    """
    return "!" * a + " "


def error(msg, ino=False):
    """
    打印红字加粗错误提示
    :param msg: 错误文本
    :param ino: True=报错后暂停等用户按回车再继续（exit 前的 fatal error 用）
    """
    print(f"\033[31m\033[1mError: {msg}\033[0m")
    if ino:
        input("Press any key to continue...")

def look_import(mport):
    """检查依赖库是否已安装，未安装则自动尝试安装（含阿里云镜像兜底）"""
    print(f"开始检查库:{mport}")
    a = subprocess.run(['pip', 'show', mport], capture_output=True).returncode
    if a == 0:
        print(f"{mport} 已安装")
        return True
    else:
        warnings.warn(f"{mport} 未安装")
        print(f"尝试自主安装")
        subprocess.run(['pip', 'install', mport], capture_output=True)
        a = subprocess.run(['pip', 'show', mport], capture_output=True).returncode
        if a == 0:
            print(f"{mport} 已安装")
            return True
        else:
            warnings.warn(f"{mport} 安装失败")
            print("尝试切换阿里云镜像")
            subprocess.run(['pip', 'install', mport, '-i', 'https://mirrors.aliyun.com/pypi/simple/'], capture_output=True)
            a = subprocess.run(['pip', 'show', mport], capture_output=True).returncode
            if a == 0:
                print(f"{mport} 已安装")
                return True
            else:
                error(f"{mport} 安装失败")
                error(f"请检查网络连接后自主安装")
                error(f"Failed to install {mport}. Please check your internet connection and try again.", ino=True)
            return False


## ---------- 启动时环境检查 ----------
# 本程序部分逻辑依赖 Windows API（如 coAI.py、控制台颜色），非 Windows 直接退出
if Os != "nt":
    error("Only Windows is supported, please use Windows to run this program.(001)", ino=True)
    exit()

# 初始化提示符并打印欢迎信息
relug()
print(lug)

puth = os.path.dirname(os.path.abspath(__file__))
os.chdir(puth)
# ---------- 主命令循环 ----------
while True:
    # 取用户输入（用 luck(1) = "! " 作为提示符）
    inp = input(luck(1))
    # 按空格拆分命令（例："file read a.txt" -> ["file","read","a.txt"]）
    comm = inp.strip().split()
    # 空行跳过
    if len(comm) == 0:
        continue
    first = comm[0]  # 命令动词：exit / file / cd / dir / ai

    # ===== 命令 1：exit 退出程序 =====
    if first == "exit":
        break

    # ===== 命令 2：file 读/写/追加 文件 =====
    # 用法：file read    <文件名>        直接打印文件内容
    #       file write   <文件名>        进入 !! 模式逐行写入，输入 //edit// 结束
    #       file append  <文件名>        进入 !! 模式逐行追加，输入 //edit// 结束
    elif first == "file":
        if len(comm) < 3:
            error("Invalid command, please use file read/write/append [filename](012)")
            continue
        if comm[1] == "read":
            # 读模式：一次性读整个文件并打印
            with open(comm[2], "r") as f:
                inp = f.read()
                print(inp)
        elif comm[1] == "write":
            # 写模式：覆盖写，逐行读取直到输入 //edit// 停止
            with open(comm[2], "w") as f:
                while True:
                    a = input(luck(2))  # 子提示符 "!! "
                    if a == "//edit//":
                        break
                    f.write(a + "\n")
        elif comm[1] == "append":
            # 追加模式：在文件末尾继续写，逐行读取直到输入 //edit// 停止
            with open(comm[2], "a") as f:
                while True:
                    a = input(luck(2))
                    if a == "//edit//":
                        break
                    f.write(a + "\n")
        else:
            error("Invalid command(013)")

    # ===== 命令 3：cd 切换目录（更新 puth 提示符 + os.chdir 真正切换工作目录）=====
    elif first == "cd":
        if len(comm) < 2:
            error("Invalid command, please use cd [path](012)")
            continue
        if not os.path.exists(comm[1]):
            error("Invalid path, please use a valid path.(020)")
            continue
        puth = comm[1]
        os.chdir(puth)   # 真正改变进程工作目录，确保后续 file 等命令能找到目标文件
        relug()   # 刷新提示符显示新路径
        print(lug)

    # ===== 命令 4：dir 列目录内容 =====
    # 用法：dir        -> 列出 puth 当前目录
    #       dir <路径>  -> 列出指定目录
    elif first == "dir":
        if len(comm) == 2:
            if not os.path.exists(comm[1]):
                error("Invalid path, please use a valid path.(020)")
                continue
            print(os.listdir(comm[1]))
        elif len(comm) == 1:
            print(os.listdir(puth))

    # ===== 命令 5：ai 启动 coAI.py AI 对话终端 =====
    elif first == "ai":
        # 注意：当前用 os.system 子进程方式启动
        #       如果后续要打成 exe 单文件部署，这里需要改成 import 方式直接调 coAI 入口
        #       （避免 exe 里再去找 python + coAI.py 物理文件）
        os.system(f"python {os.path.dirname(os.path.abspath(__file__))}\\coAI.py")

    # ===== 命令 6：web 以网页模式启动 coAI.py（启动即进 web 模式）=====
    elif first == "web":
        os.system(f"python {os.path.dirname(os.path.abspath(__file__))}\\coAI.py --web")
    
    elif first == "check":
        # 需要检查的全部依赖（含语音所需的 funasr）
        required = [
            "openai", "rich", "requests", "beautifulsoup4",
            "prompt_toolkit", "GreatLogger", "arrow",
            "sounddevice", "soundfile", "numpy", "keyboard", "funasr",
        ]
        installed = []
        missing = []
        for m in required:
            # look_import 返回 True=已安装(含自动安装成功)，False=安装失败
            if look_import(m):
                installed.append(m)
            else:
                missing.append(m)
        if not missing:
            print(f"\033[92m所有模块已安装（{len(installed)}/{len(required)}）\033[0m")
        else:
            print(f"\033[91m缺失模块：{missing}\033[0m")
            print(f"\033[92m已安装：{installed}\033[0m")
       