# -*- coding: utf-8 -*-
"""
Hyper-Alpha-Arena 启动器
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import time
import psutil
import os
import sys

class Launcher:
    def __init__(self, root):
        self.root = root
        self.root.title("Hyper-Alpha-Arena 启动器")
        self.root.geometry("700x520")
        self.root.minsize(550, 400)
        
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.backend_dir = os.path.join(self.project_dir, "backend")
        self.frontend_dir = os.path.join(self.project_dir, "frontend")
        
        self.backend_port = 8000
        self.frontend_port = 5173
        
        self.backend_process = None
        self.frontend_process = None
        
        self.create_ui()
        self.update_status()
        
    def log(self, msg, color="white"):
        """输出日志"""
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}\n"
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        
    def get_port_from_addr(self, addr):
        """从地址获取端口号"""
        if addr is None:
            return None
        if isinstance(addr, tuple):
            if len(addr) >= 2:
                return addr[1]
            return None
        if hasattr(addr, 'port'):
            return addr.port
        return None
        
    def is_port_used(self, port):
        """检查端口是否被占用"""
        try:
            for conn in psutil.net_connections():
                if conn.status == 'LISTENING':
                    p = self.get_port_from_addr(conn.laddr)
                    if p == port:
                        return True
        except (psutil.AccessDenied, PermissionError):
            # 没有管理员权限，使用备用方案
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('', port))
                sock.close()
                return False
            except OSError:
                return True
        return False
        
    def get_port_pid(self, port):
        """获取占用端口的进程ID列表"""
        pids = []
        try:
            for conn in psutil.net_connections():
                if conn.status == 'LISTENING':
                    p = self.get_port_from_addr(conn.laddr)
                    if p == port and conn.pid and conn.pid not in pids:
                        pids.append(conn.pid)
        except (psutil.AccessDenied, PermissionError):
            # 没有管理员权限，使用 netstat 备用方案
            try:
                import subprocess
                result = subprocess.run(
                    ['netstat', '-ano'],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5
                )
                for line in result.stdout.split('\n'):
                    if f':{port}' in line and 'LISTENING' in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            try:
                                pid = int(parts[-1])
                                if pid not in pids:
                                    pids.append(pid)
                            except ValueError:
                                pass
            except Exception as e:
                self.log(f"获取端口进程失败: {e}", "orange")
        return pids if pids else None
        
    def kill_port_process(self, port):
        """杀死占用端口的所有进程"""
        pids = self.get_port_pid(port)
        if not pids:
            return False
        
        killed_count = 0
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                if proc.name() not in ['System', 'Idle']:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                        killed_count += 1
                    except:
                        os.kill(pid, 9)
                        killed_count += 1
            except:
                try:
                    os.kill(pid, 9)
                    killed_count += 1
                except:
                    pass
        
        if killed_count > 0:
            self.log(f"已终止 {killed_count} 个占用端口 {port} 的进程", "orange")
        return killed_count > 0
        
    def find_python(self):
        """查找Python可执行文件"""
        venv_python = os.path.join(self.backend_dir, "venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            return venv_python
        return sys.executable
        
    def create_ui(self):
        # 标题
        title = tk.Label(self.root, text="Hyper-Alpha-Arena", 
                        font=("Microsoft YaHei", 16, "bold"))
        title.pack(pady=15)
        
        # 服务卡片
        card_frame = tk.Frame(self.root)
        card_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # 后端卡片
        backend_card = tk.LabelFrame(card_frame, text="后端服务", padx=15, pady=10)
        backend_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        self.backend_status = tk.Label(backend_card, text="● 已停止", fg="red", font=("Microsoft YaHei", 11))
        self.backend_status.pack(anchor=tk.W)
        
        self.backend_info = tk.Label(backend_card, text="", fg="gray", font=("Microsoft YaHei", 9))
        self.backend_info.pack(anchor=tk.W)
        
        self.backend_btn = tk.Button(backend_card, text="启动", bg="#4CAF50", fg="white",
                                     command=self.start_backend, font=("Microsoft YaHei", 9))
        self.backend_btn.pack(fill=tk.X, pady=(10, 0))
        
        self.backend_kill_btn = tk.Button(backend_card, text="释放端口", bg="#FF9800", fg="white",
                                          command=self.kill_backend_port, font=("Microsoft YaHei", 8))
        self.backend_kill_btn.pack(fill=tk.X, pady=(2, 0))
        
        # 前端卡片
        frontend_card = tk.LabelFrame(card_frame, text="前端服务", padx=15, pady=10)
        frontend_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        self.frontend_status = tk.Label(frontend_card, text="● 已停止", fg="red", font=("Microsoft YaHei", 11))
        self.frontend_status.pack(anchor=tk.W)
        
        self.frontend_info = tk.Label(frontend_card, text="", fg="gray", font=("Microsoft YaHei", 9))
        self.frontend_info.pack(anchor=tk.W)
        
        self.frontend_btn = tk.Button(frontend_card, text="启动", bg="#4CAF50", fg="white",
                                      command=self.start_frontend, font=("Microsoft YaHei", 9))
        self.frontend_btn.pack(fill=tk.X, pady=(10, 0))
        
        self.frontend_kill_btn = tk.Button(frontend_card, text="释放端口", bg="#FF9800", fg="white",
                                           command=self.kill_frontend_port, font=("Microsoft YaHei", 8))
        self.frontend_kill_btn.pack(fill=tk.X, pady=(2, 0))
        
        # 快捷按钮
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=20, pady=10)
        
        tk.Button(btn_frame, text="启动所有", bg="#2196F3", fg="white",
                 command=self.start_all, font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 5))
        
        tk.Button(btn_frame, text="停止所有", bg="#f44336", fg="white",
                 command=self.stop_all, font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=5)
        
        tk.Button(btn_frame, text="重启", bg="#FF9800", fg="white",
                 command=self.restart_all, font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=5)
        
        tk.Button(btn_frame, text="打开前端", bg="#9C27B0", fg="white",
                 command=self.open_frontend, font=("Microsoft YaHei", 10)).pack(side=tk.RIGHT)
        
        # 日志
        log_label = tk.Label(self.root, text="运行日志", font=("Microsoft YaHei", 10))
        log_label.pack(anchor=tk.W, padx=20)
        
        self.log_text = scrolledtext.ScrolledText(self.root, height=10, font=("Consolas", 9),
                                                  bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 10))
        
    def start_backend(self):
        pids = self.get_port_pid(self.backend_port)
        if pids:
            count = len(pids)
            self.log(f"端口 {self.backend_port} 已被 {count} 个进程占用: {pids}", "orange")
            if messagebox.askyesno("端口冲突", f"端口 {self.backend_port} 已被 {count} 个进程占用。\n是否强制终止所有进程并启动?"):
                self.kill_backend_port()
                time.sleep(1)
            else:
                return
            
        self.log("启动后端...")
        python = self.find_python()
        
        try:
            self.backend_process = subprocess.Popen(
                [python, "-m", "uvicorn", "backend.main:app", 
                 "--host", "0.0.0.0", "--port", str(self.backend_port), "--reload"],
                cwd=self.backend_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            )
            self.log(f"后端已启动 (PID: {self.backend_process.pid})", "green")
            
            for i in range(15):
                time.sleep(1)
                if self.is_port_used(self.backend_port):
                    self.log("后端就绪!", "green")
                    break
                    
        except Exception as e:
            self.log(f"后端启动失败: {e}", "red")
            
        self.update_status()
        
    def stop_backend(self):
        if self.backend_process:
            try:
                self.backend_process.terminate()
            except:
                pass
        self.kill_port_process(self.backend_port)
        self.log("后端已停止", "orange")
        self.update_status()
        
    def start_frontend(self):
        pids = self.get_port_pid(self.frontend_port)
        if pids:
            count = len(pids)
            self.log(f"端口 {self.frontend_port} 已被 {count} 个进程占用: {pids}", "orange")
            if messagebox.askyesno("端口冲突", f"端口 {self.frontend_port} 已被 {count} 个进程占用。\n是否强制终止所有进程并启动?"):
                self.kill_frontend_port()
                time.sleep(1)
            else:
                return
            
        self.log("启动前端...")
        
        # 检查依赖
        node_modules = os.path.join(self.frontend_dir, "node_modules")
        if not os.path.exists(node_modules):
            self.log("安装依赖中...")
            try:
                subprocess.run(["npm", "install", "--no-audit", "--no-fund"], 
                              cwd=self.frontend_dir, timeout=120)
                self.log("依赖安装完成", "green")
            except Exception as e:
                self.log(f"安装依赖失败: {e}", "red")
                return
        
        try:
            self.frontend_process = subprocess.Popen(
                ["npx", "vite", "--port", str(self.frontend_port)],
                cwd=self.frontend_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self.log(f"前端已启动 (PID: {self.frontend_process.pid})", "green")
            
            for i in range(15):
                time.sleep(1)
                if self.is_port_used(self.frontend_port):
                    self.log("前端就绪!", "green")
                    break
                    
        except Exception as e:
            self.log(f"前端启动失败: {e}", "red")
            
        self.update_status()
        
    def stop_frontend(self):
        if self.frontend_process:
            try:
                self.frontend_process.terminate()
            except:
                pass
        self.kill_port_process(self.frontend_port)
        self.log("前端已停止", "orange")
        self.update_status()
        
    def kill_backend_port(self):
        """强制释放后端端口（杀死所有占用进程）"""
        pids = self.get_port_pid(self.backend_port)
        if pids:
            killed = 0
            for pid in pids:
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    proc.wait(timeout=2)
                    self.log(f"已终止进程 PID: {pid}", "orange")
                    killed += 1
                except:
                    try:
                        os.kill(pid, 9)
                        self.log(f"已强制终止进程 PID: {pid}", "red")
                        killed += 1
                    except:
                        pass
            if killed > 0:
                self.log(f"共终止 {killed} 个进程", "orange")
            self.update_status()
        
    def kill_frontend_port(self):
        """强制释放前端端口（杀死所有占用进程）"""
        pids = self.get_port_pid(self.frontend_port)
        if pids:
            killed = 0
            for pid in pids:
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    proc.wait(timeout=2)
                    self.log(f"已终止进程 PID: {pid}", "orange")
                    killed += 1
                except:
                    try:
                        os.kill(pid, 9)
                        self.log(f"已强制终止进程 PID: {pid}", "red")
                        killed += 1
                    except:
                        pass
            if killed > 0:
                self.log(f"共终止 {killed} 个进程", "orange")
            self.update_status()
        
    def start_all(self):
        self.log("="*40)
        self.start_backend()
        time.sleep(2)
        self.start_frontend()
        self.log("="*40)
        self.log("所有服务已启动!", "green")
        
    def stop_all(self):
        self.log("="*40)
        self.stop_backend()
        time.sleep(0.5)
        self.stop_frontend()
        self.log("="*40)
        
    def restart_all(self):
        self.stop_all()
        time.sleep(2)
        self.start_all()
        
    def open_frontend(self):
        import webbrowser
        webbrowser.open(f"http://localhost:{self.frontend_port}")
        
    def update_status(self):
        # 更新后端状态
        backend_pids = self.get_port_pid(self.backend_port)
        if backend_pids:
            count = len(backend_pids)
            if count == 1:
                self.backend_status.config(text="● 运行中", fg="green")
                self.backend_info.config(text=f"PID: {backend_pids[0]}  |  端口: {self.backend_port} | API: localhost:{self.backend_port}/docs")
            else:
                self.backend_status.config(text=f"⚠ 多进程冲突 ({count}个)", fg="orange")
                self.backend_info.config(text=f"PIDs: {backend_pids}  |  端口: {self.backend_port}")
            self.backend_btn.config(text="停止", bg="#f44336", command=self.stop_backend)
            self.backend_kill_btn.pack_forget()
        else:
            self.backend_status.config(text="● 已停止", fg="red")
            self.backend_info.config(text=f"端口: {self.backend_port}")
            self.backend_btn.config(text="启动", bg="#4CAF50", command=self.start_backend)
            self.backend_kill_btn.pack(fill=tk.X, pady=(2, 0))
            
        # 更新前端状态
        frontend_pids = self.get_port_pid(self.frontend_port)
        if frontend_pids:
            count = len(frontend_pids)
            if count == 1:
                self.frontend_status.config(text="● 运行中", fg="green")
                self.frontend_info.config(text=f"PID: {frontend_pids[0]}  |  端口: {self.frontend_port} | 访问: localhost:{self.frontend_port}")
            else:
                self.frontend_status.config(text=f"⚠ 多进程冲突 ({count}个)", fg="orange")
                self.frontend_info.config(text=f"PIDs: {frontend_pids}  |  端口: {self.frontend_port}")
            self.frontend_btn.config(text="停止", bg="#f44336", command=self.stop_frontend)
            self.frontend_kill_btn.pack_forget()
        else:
            self.frontend_status.config(text="● 已停止", fg="red")
            self.frontend_info.config(text=f"端口: {self.frontend_port}")
            self.frontend_btn.config(text="启动", bg="#4CAF50", command=self.start_frontend)
            self.frontend_kill_btn.pack(fill=tk.X, pady=(2, 0))
            
        # 每3秒刷新
        self.root.after(3000, self.update_status)


def main():
    root = tk.Tk()
    
    # DPI 缩放
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    
    app = Launcher(root)
    
    def on_close():
        if messagebox.askokcancel("退出", "是否停止所有服务后退出?"):
            app.stop_all()
            root.destroy()
            
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
