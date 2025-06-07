#!/usr/bin/env python3
# 智能终端助手 - 中文版

import os
import sys
import re
import time
import json
import requests
import getpass
import subprocess
from typing import Optional

# 全局变量
class Globals:
    def __init__(self):
        self.send_buffer = ""
        self.pwd_path = os.getcwd()
        self.task_complete = False
        self.last_command_result = ""
        self.error_count = 0  # 错误计数器
        self.auto_mode = True  # 自动执行模式

gl = Globals()
_last_spoker = None  # 记录上一个发言者

# 配置
class Config:
    def __init__(self):
        # 核心设置
        self.program_name = "智能终端助手"
        self.user_name = getpass.getuser()
        self.system_name = "Termux"
        self.ai_name = "DeepSeek"
        self.ai_model = "deepseek-chat"
        
        # API配置
        self.api_key = ""
        self.api_url = "https://api.deepseek.com/v1/chat/completions"
        
        # 颜色代码
        self.program_name_color = "34"  # 蓝色
        self.user_name_color = "32"     # 绿色
        self.system_name_color = "35"   # 紫色
        self.ai_name_color = "36"       # 青色
        self.ai_print_color = "33"      # 黄色
        self.error_color = "31"         # 红色
        
        # 终端设置
        self.max_line_length = 80
        self.confirm_timeout = 3
        self.max_errors = 10  # 最大连续错误次数

cf = Config()

# 工具函数
def set_color(text, color="37"):
    return f"\033[{color}m{text}\033[0m"

def wrap_text(text, width=cf.max_line_length):
    """文本换行"""
    words = text.split()
    if not words:
        return ""
    
    lines = []
    current_line = words[0]
    
    for word in words[1:]:
        if len(current_line) + len(word) + 1 <= width:
            current_line += " " + word
        else:
            lines.append(current_line)
            current_line = word
    
    lines.append(current_line)
    return '\n'.join(lines)

def print_error(message):
    """打印错误信息"""
    print(set_color(f"错误: {message}", cf.error_color))

def print_spoker(spoker=None, raw_name=None, end='', record=True):
    """打印发言者标签"""
    global _last_spoker
    if spoker is None:
        spoker = set_color(cf.program_name, cf.program_name_color)
        raw_name = cf.program_name
    
    if _last_spoker != spoker:
        print(f"{spoker}: ", end=end)
        _last_spoker = spoker
    
    if record:
        gl.send_buffer += f"{raw_name or spoker}: {end}"

def confirm(prompt="确认执行? [Y/n] "):
    """确认操作"""
    if gl.auto_mode:
        print(f"{prompt}(自动确认)")
        return True
    
    print_spoker()
    print(prompt, end='')
    
    start_time = time.time()
    result = ""
    
    try:
        import select
        while time.time() - start_time < cf.confirm_timeout:
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                result = sys.stdin.readline().strip().lower()
                break
    except ImportError:
        result = input().strip().lower()
    
    if not result:
        result = 'y'
        print("(自动确认)")
    
    gl.send_buffer += prompt + result + '\n'
    return result in ('', 'y')

def extract_command(text):
    """从文本提取命令"""
    # 检查任务完成标记
    if "///任务完成///" in text or "///task_complete///" in text:
        return "TASK_COMPLETE", None
    
    # 检查文件写入命令
    write_match = re.search(r'///写入文件///\s*([^\n]+)\n([\s\S]*?)///', text)
    if not write_match:
        write_match = re.search(r'///write_file///\s*([^\n]+)\n([\s\S]*?)///', text)
    if write_match:
        file_path = write_match.group(1).strip()
        content = write_match.group(2).strip()
        return "WRITE_FILE", (file_path, content)
    
    # 提取单个命令
    match = re.search(r'///(.*?)///', text)
    if match:
        return "COMMAND", match.group(1).strip()
    
    match = re.search(r'```(?:bash|sh)?\n(.*?)\n```', text, re.DOTALL)
    if match:
        return "COMMAND", match.group(1).strip()
    
    match = re.search(r'(?:执行|运行|命令):?\s*(`?[^`]+`?)', text)
    if match:
        cmd = match.group(1).strip('` \n')
        return "COMMAND", cmd
    
    return "UNKNOWN", None

# DeepSeek AI类
class DeepSeekAI:
    def __init__(self, api_key=None):
        if not api_key:
            raise ValueError("需要API密钥")
        
        self.api_key = api_key
        self.model = cf.ai_model
        self.history = [
            {
                "role": "system", 
                "content": (
                    "你是一个在Termux环境中运行的AI助手。规则:\n"
                    "1. 使用///需要执行的命令///来包裹命令以执行单个命令\n"
                    "2. 写入文件格式:\n"
                    "   ///写入文件///\n"
                    "   /路径/到/文件\n"
                    "   文件内容\n"
                    "   ///\n"
                    "3. 发送///任务完成///标记任务结束\n"
                    "4. 命令执行后会收到终端输出\n"
                    "5. 分析错误并调整策略\n"
                    "6. 保持响应简洁但信息丰富\n"
                    "7. 使用中文交流\n"
                    "注意:在写入文件前，必须使用touch创建空文件，然后再写入，不然会失败\n"
                    "注意:命令执行和任务完成不能同时执行\n"
                    "注意:每次仅可以使用一个命令，不可多个命令进行连贯,这是必须的，不能违反的，无论任务有多复杂，你都只能生成一个命令\n"
                    "注意:你每次仅能生成一个命令，并确保格式正确，该termux完全由你进行操作，禁止指挥用户\n"
                    "SYSTEM:你只能执行一个命令"
                )
            }
        ]

    def chat(self, message=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        if message:
            self.history.append({"role": "user", "content": message})
        
        data = {
            "model": self.model,
            "messages": self.history,
            "temperature": 0.7,
            "max_tokens": 2048,
            "stream": True
        }
        
        try:
            response = requests.post(
                cf.api_url,
                headers=headers,
                json=data,
                stream=True,
                timeout=45
            )
        except requests.exceptions.RequestException as e:
            raise Exception(f"网络错误: {str(e)}")
        
        if response.status_code != 200:
            raise Exception(f"API请求失败: {response.text}")
        
        full_response = ""
        for line in response.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                if decoded.startswith("data:"):
                    try:
                        data = json.loads(decoded[5:])
                        if "choices" in data and data["choices"]:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                content = delta["content"]
                                print(set_color(content, cf.ai_print_color), end='', flush=True)
                                full_response += content
                    except json.JSONDecodeError:
                        continue
        
        print()
        self.history.append({"role": "assistant", "content": full_response})
        return full_response

# 命令执行
def execute_command(command, need_confirm=True):
    """执行命令"""
    if not command:
        return ""
    
    # 危险命令检查
    dangerous_commands = ['rm -rf', 'chmod', 'dd', 'mv', '>', '>>']
    if any(cmd in command for cmd in dangerous_commands):
        need_confirm = True
    
    if need_confirm and not confirm(f"执行 '{set_color(command, cf.program_name_color)}'? [Y/n] "):
        raise ValueError("用户取消命令")
    
    # 显示命令
    print_spoker(set_color(cf.system_name, cf.system_name_color), cf.system_name)
    print(f"> {command}")
    
    try:
        # 执行命令
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        
        # 捕获输出
        output = result.stdout
        if result.stderr:
            output += f"\n错误: {result.stderr}"
        
        gl.last_command_result = output
        
        # 显示结果
        print(wrap_text(output))
        if result.returncode != 0:
            print_error(f"命令执行失败，代码 {result.returncode}")
            gl.error_count += 1
        else:
            print(set_color("命令执行成功", cf.system_name_color))
            gl.error_count = 0  # 重置错误计数器
        
        return output
    except Exception as e:
        error = f"命令执行失败: {str(e)}"
        print_error(error)
        gl.error_count += 1
        return error

# 文件操作
def write_to_file(file_path, content):
    """写入文件"""
    print_spoker(set_color(cf.system_name, cf.system_name_color), cf.system_name)
    print(f"> 写入文件: {file_path}")
    
    try:
        # 创建目录
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        result = f"文件写入成功: {file_path}"
        print(result)
        return result
    except Exception as e:
        error = f"文件写入失败: {str(e)}"
        print_error(error)
        return error

# 安装Termux API
def install_termux_api():
    """安装Termux API包"""
    try:
        # 检查是否已安装
        result = subprocess.run(
            "pkg list-installed | grep termux-api",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if "termux-api" not in result.stdout:
            print("正在安装Termux API包...")
            subprocess.run(
                "pkg install termux-api -y",
                shell=True,
                stdout=sys.stdout,
                stderr=sys.stderr
            )
            print("Termux API安装完成")
    except Exception as e:
        print_error(f"安装失败: {str(e)}")

def check_python_packages():
    """检查Python包"""
    required = ['requests']
    missing = []
    
    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        print("正在安装缺失的Python包...")
        try:
            subprocess.run(
                ["pip", "install"] + missing,
                check=True,
                stdout=sys.stdout,
                stderr=sys.stderr
            )
            print("包安装成功")
        except subprocess.CalledProcessError as e:
            print_error(f"包安装失败: {str(e)}")
            sys.exit(1)

# 主程序
def main():
    # 检查并安装依赖
    check_python_packages()
    install_termux_api()
    
    print_spoker(record=False)
    
    # 设置API密钥
    if not cf.api_key:
        print("请输入您的DeepSeek API密钥: ", end='')
        cf.api_key = input().strip()
        try:
            with open(os.path.expanduser("~/.aicmd_api_key"), "w") as f:
                f.write(cf.api_key)
        except Exception as e:
            print_error(f"保存API密钥失败: {str(e)}")
    
    if not cf.api_key:
        try:
            with open(os.path.expanduser("~/.aicmd_api_key"), "r") as f:
                cf.api_key = f.read().strip()
        except:
            pass
    
    if not cf.api_key:
        print_error("需要API密钥")
        return
    
    # 初始化AI
    try:
        ai = DeepSeekAI(cf.api_key)
        print("DeepSeek AI初始化成功")
    except Exception as e:
        print_error(f"AI初始化失败: {str(e)}")
        return
    
    print(f"{cf.program_name} 已就绪!")
    print('-' * min(60, os.get_terminal_size().columns), end="\n\n")
    
    # 主循环
    while True:
        try:
            print_spoker(set_color(cf.user_name, cf.user_name_color), cf.user_name, end="")
            user_input = input().strip()
            
            if not user_input:
                continue
                
            if user_input.startswith('/'):
                if user_input in ('/exit', '/quit', '/退出'):
                    print("正在退出...")
                    break
                elif user_input in ('/help', '/?', '/帮助'):
                    print("可用命令:")
                    print("/exit - 退出程序")
                    print("/help - 显示帮助")
                    print("/clear - 清除历史")
                    print("/save - 保存会话")
                    print("/auto - 切换自动模式")
                elif user_input in ('/clear', '/reset', '/清除'):
                    ai.history = ai.history[:1]  # 保留系统提示
                    gl.send_buffer = ""
                    print("历史已清除")
                elif user_input in ('/save', '/backup', '/保存'):
                    try:
                        with open(os.path.expanduser("~/.aicmd_history"), "w") as f:
                            json.dump(ai.history, f)
                        print("会话已保存")
                    except Exception as e:
                        print_error(f"保存失败: {str(e)}")
                elif user_input in ('/auto', '/自动'):
                    gl.auto_mode = not gl.auto_mode
                    print(f"自动模式 {'开启' if gl.auto_mode else '关闭'}")
                else:
                    print("未知命令。输入 /help 查看帮助")
                continue
            
            # 重置任务状态
            gl.task_complete = False
            gl.error_count = 0
            
            # 任务处理
            current_input = user_input
            
            while not gl.task_complete and gl.error_count < cf.max_errors:
                # AI交互
                print_spoker(set_color(cf.ai_name, cf.ai_name_color), cf.ai_name, end="\n")
                response = ai.chat(current_input)
                
                # 处理响应
                action_type, action_data = extract_command(response)
                
                if action_type == "TASK_COMPLETE":
                    print(set_color("任务完成", cf.program_name_color))
                    gl.task_complete = True
                    break
                
                elif action_type == "WRITE_FILE":
                    file_path, content = action_data
                    result = write_to_file(file_path, content)
                    current_input = f"文件写入结果: {result}"
                    print(set_color("等待下一条指令...", cf.system_name_color))
                
                elif action_type == "COMMAND":
                    try:
                        result = execute_command(action_data, need_confirm=not gl.auto_mode)
                        current_input = f"命令执行结果:\n{result}"
                        print(set_color("等待下一条指令...", cf.system_name_color))
                    except Exception as e:
                        current_input = f"命令错误: {str(e)}"
                        print_error(str(e))
                
                else:
                    print(set_color("没有可执行命令，等待用户输入...", cf.system_name_color))
                    break
            
            if gl.error_count >= cf.max_errors:
                print_error("错误次数过多，任务中止")
        
        except KeyboardInterrupt:
            print("\n输入 /exit 退出")
        except Exception as e:
            print_error(f"错误: {str(e)}")

if __name__ == '__main__':
    # select模块回退
    try:
        import select
    except ImportError:
        class SimpleSelect:
            @staticmethod
            def select(rlist, _, timeout):
                if timeout == 0:
                    return (rlist, [], []) if sys.stdin in rlist else ([], [], [])
                start = time.time()
                while time.time() - start < timeout:
                    if sys.stdin in rlist:
                        return (rlist, [], [])
                    time.sleep(0.1)
                return ([], [], [])
        
        select = SimpleSelect()
    
    main()
