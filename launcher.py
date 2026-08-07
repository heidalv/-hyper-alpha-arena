# -*- coding: utf-8 -*-
"""
Heidalv-Alpha-Arena Manager - Modern UI Edition
优化版启动器：无黑窗口、异步响应、现代化界面
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import threading
import queue
import time
import psutil
import os
import sys
import webbrowser
from datetime import datetime
from typing import Optional, Callable

# 高DPI支持
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except:
    pass


# =============================================================================
# 颜色主题配置
# =============================================================================
class Theme:
    # 主背景色
    BG_DARK = "#0d1117"
    BG_CARD = "#161b22"
    BG_HEADER = "#010409"
    BG_INPUT = "#0d1117"
    
    # 强调色
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#3fb950"
    ACCENT_RED = "#f85149"
    ACCENT_YELLOW = "#d29922"
    ACCENT_PURPLE = "#a371f7"
    
    # 文字色
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED = "#484f58"
    
    # 边框色
    BORDER = "#30363d"
    BORDER_LIGHT = "#21262d"
    
    # 按钮色
    BTN_PRIMARY = "#238636"
    BTN_PRIMARY_HOVER = "#2ea043"
    BTN_DANGER = "#da3633"
    BTN_DANGER_HOVER = "#f85149"
    BTN_WARNING = "#9e6a03"
    BTN_SECONDARY = "#21262d"


# =============================================================================
# 异步消息队列 - 线程安全的UI更新
# =============================================================================
class AsyncUIUpdater:
    """异步UI更新器 - 确保线程安全的UI操作"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue = queue.Queue()
        self._process_queue()
    
    def schedule(self, func: Callable, *args, **kwargs):
        """调度一个函数在主线程执行"""
        self.queue.put((func, args, kwargs))
    
    def _process_queue(self):
        """处理消息队列"""
        try:
            while True:
                func, args, kwargs = self.queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print(f"UI update error: {e}")
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._process_queue)


# =============================================================================
# 服务管理器
# =============================================================================
class ServiceManager:
    """服务管理器 - 管理后端和前端进程"""
    
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.backend_dir = os.path.join(project_dir, "backend")
        self.frontend_dir = os.path.join(project_dir, "frontend")
        self.backend_port = 8000
        self.frontend_port = 5173
        self.backend_process: Optional[subprocess.Popen] = None
        self.frontend_process: Optional[subprocess.Popen] = None
        self._pipe_readers: list = []  # 管道读取线程列表
    
    def _start_pipe_reader(self, process: subprocess.Popen, name: str, log_callback: Optional[Callable] = None):
        """启动后台线程持续读取进程的stdout，防止PIPE缓冲区满导致死锁"""
        def reader():
            try:
                while process.poll() is None:
                    line = process.stdout.readline()
                    if line:
                        text = line.decode('utf-8', errors='replace').rstrip()
                        if text and log_callback:
                            # 只转发关键日志，避免刷屏
                            if any(kw in text.lower() for kw in ['error', 'warning', 'failed', 'exception', 'started', 'ready', 'listening']):
                                log_callback(f"[{name}] {text}", "info")
                    else:
                        break
            except Exception:
                pass
        t = threading.Thread(target=reader, name=f"pipe-reader-{name}", daemon=True)
        t.start()
        self._pipe_readers.append(t)
    
    def _get_port(self, addr) -> Optional[int]:
        if addr is None:
            return None
        if isinstance(addr, tuple) and len(addr) >= 2:
            return addr[1]
        return None
    
    def is_port_in_use(self, port: int) -> bool:
        try:
            for conn in psutil.net_connections():
                if conn.status == 'LISTENING':
                    if self._get_port(conn.laddr) == port:
                        return True
            return False
        except:
            return False
    
    def get_port_pid(self, port: int) -> Optional[int]:
        try:
            for conn in psutil.net_connections():
                if conn.status == 'LISTENING':
                    if self._get_port(conn.laddr) == port:
                        return conn.pid
            return None
        except:
            return None
    
    def kill_process(self, pid: int) -> bool:
        """杀死进程及其所有子进程"""
        if pid is None:
            return False
        try:
            proc = psutil.Process(pid)
            if proc.name() not in ['System', 'Idle']:
                # 首先杀死所有子进程
                children = proc.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass
                
                # 等待子进程结束
                gone, alive = psutil.wait_procs(children, timeout=3)
                
                # 强制杀死还活着的子进程
                for p in alive:
                    try:
                        p.kill()
                    except:
                        pass
                
                # 最后杀死主进程
                proc.terminate()
                proc.wait(timeout=3)
                return True
        except psutil.NoSuchProcess:
            # 进程已经不存在了
            return True
        except:
            try:
                # 尝试强制杀死
                os.kill(pid, 9)
                return True
            except:
                pass
        return False
    
    def find_python(self) -> str:
        # 真实 venv 目录是 backend\.venv（带点）；旧逻辑只找 backend\venv（无点）→
        # 永远 exists()=False 而回退到 sys.executable（可能是缺 psycopg3 的系统 Python）。
        # 这里优先 .venv，再兼容历史的 venv。
        for name in (".venv", "venv"):
            venv_python = os.path.join(self.backend_dir, name, "Scripts", "python.exe")
            if os.path.exists(venv_python):
                return venv_python
        return sys.executable
    
    def _get_startupinfo(self):
        """获取隐藏窗口的启动信息"""
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo
    
    def check_backend_health(self) -> bool:
        """检查后端健康状态（真API请求）"""
        try:
            import urllib.request
            import urllib.error
            
            url = f"http://localhost:{self.backend_port}/api/health"  # 正确的路径
            req = urllib.request.Request(url, headers={'User-Agent': 'Launcher/1.0'})
            
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status == 200
        except:
            return False
    
    def start_backend(self, log_callback: Callable, progress_callback: Callable) -> bool:
        """启动后端服务 - 不使用日志重定向，避免阻塞"""
        pid = self.get_port_pid(self.backend_port)
        if pid:
            log_callback(f"端口 {self.backend_port} 被 PID {pid} 占用", "warning")
            self.kill_process(pid)
            time.sleep(1)
        
        python = self.find_python()
        log_callback("正在启动后端服务...", "info")
        progress_callback(10, "初始化后端...")
        
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'  # 禁用Python缓冲
        
        # [2026-07-09 性能修复] 去掉 --reload：热重载导致后端每 15-30 秒重启一次
        # （日志 backend.error.log 里 10 分钟内出现 ~15 次 "BACKEND_API_KEY not set"），
        # 每次重启都会清空进程内存缓存（hyperliquid_cache / _overview_cache / _stats_cache），
        # 导致打开仪表盘时必须重新逐个账户同步调用 Hyperliquid 交易所 REST API（每账户 10-12s），
        # 多账户串行累加到 50-60s 卡顿。去掉 reload 后缓存得以保留，二次打开秒级返回。
        # 开发时如需热重载，改用 start_quick.bat 独立窗口启动。
        cmd = (
            f'cd /d "{self.project_dir}" && '
            f'"{python}" -u -m uvicorn backend.main:app '
            f'--host 0.0.0.0 --port {self.backend_port}'
        )
        
        try:
            # 使用PIPE + 读取线程，防止缓冲区满导致进程死锁
            self.backend_process = subprocess.Popen(
                cmd,
                cwd=self.project_dir,
                startupinfo=self._get_startupinfo(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=True,
                env=env,
                bufsize=0  # 无缓冲
            )
            # 关键：启动管道读取线程，持续消费stdout，防止PIPE缓冲区满导致后端卡死
            self._start_pipe_reader(self.backend_process, "后端", log_callback)
            log_callback("后端进程输出已接管", "info")
            log_callback(f"后端进程已启动 (PID: {self.backend_process.pid})", "info")
            
            # 等待端口就绪并验证API健康
            port_ready = False
            api_ready = False
            
            for i in range(60):  # 增加到60次，共30秒
                time.sleep(0.5)
                progress_callback(10 + int(i * 0.65), f"等待后端就绪... ({i+1}/60)")
                
                # 首先检查端口
                if not port_ready and self.is_port_in_use(self.backend_port):
                    port_ready = True
                    log_callback(f"端口 {self.backend_port} 已开始监听，正在初始化...", "info")
                
                # 端口就绪后检查API健康
                if port_ready and self.check_backend_health():
                    api_ready = True
                    log_callback(f"后端已就绪 http://localhost:{self.backend_port}", "success")
                    progress_callback(50, "后端就绪")
                    return True
            
            if port_ready and not api_ready:
                log_callback("后端端口已监听但API未响应", "error")
                log_callback("请检查后端进程输出或使用start_quick.bat查看实时日志", "warning")
                return False
            else:
                log_callback("后端启动超时", "error")
                log_callback("建议：使用start_quick.bat在独立窗口启动以查看详细日志", "warning")
                return False
        except Exception as e:
            log_callback(f"后端启动失败: {e}", "error")
            return False
    
    def stop_backend(self, log_callback: Callable) -> None:
        """停止后端服务 - 彻底清理所有进程"""
        killed_pids = []

        # 1. 递归杀死启动进程及其所有子进程（含 uvicorn reload 父子进程）
        if self.backend_process:
            try:
                pid = self.backend_process.pid
                # 用 kill_process 递归杀子进程，不直接用 terminate()
                # 因为 shell=True 时 self.backend_process 是 cmd.exe，
                # terminate() 只杀 cmd.exe，子进程 uvicorn 会变成孤儿继续占用端口
                self.kill_process(pid)
                killed_pids.append(pid)
                log_callback(f"已终止后端进程树 (根PID: {pid})", "info")
            except Exception as e:
                log_callback(f"终止后端主进程异常: {e}", "warning")
            self.backend_process = None

        # 2. 杀死所有占用端口的进程（兜底）
        pid = self.get_port_pid(self.backend_port)
        if pid and pid not in killed_pids:
            if self.kill_process(pid):
                killed_pids.append(pid)
                log_callback(f"已杀死端口 {self.backend_port} 占用进程 (PID: {pid})", "info")

        # 3. 扫描杀死所有残留的 uvicorn 进程（兜底）
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] or [])
                    if 'uvicorn' in cmdline and 'backend.main:app' in cmdline:
                        proc_pid = proc.info['pid']
                        if proc_pid not in killed_pids:
                            self.kill_process(proc_pid)
                            killed_pids.append(proc_pid)
                            log_callback(f"清理残留uvicorn进程 (PID: {proc_pid})", "info")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            log_callback(f"扫描uvicorn进程异常: {e}", "warning")

        # 4. 确认端口已释放
        time.sleep(1.5)
        if self.is_port_in_use(self.backend_port):
            # 最后一击：用 taskkill /F 强制杀死端口占用
            try:
                pid = self.get_port_pid(self.backend_port)
                if pid:
                    log_callback(f"端口仍被占用，强制清理 PID: {pid}", "warning")
                    self.kill_process(pid)
                    time.sleep(0.5)
            except Exception:
                pass
        if self.is_port_in_use(self.backend_port):
            log_callback(f"警告: 端口 {self.backend_port} 仍被占用，请手动检查", "warning")
        else:
            log_callback("后端已完全停止", "success")
    
    def start_frontend(self, log_callback: Callable, progress_callback: Callable) -> bool:
        """启动前端服务 - 不使用日志重定向"""
        pid = self.get_port_pid(self.frontend_port)
        if pid:
            log_callback(f"端口 {self.frontend_port} 被 PID {pid} 占用", "warning")
            self.kill_process(pid)
            time.sleep(1)
        
        log_callback("正在启动前端服务...", "info")
        progress_callback(55, "初始化前端...")
        
        env = os.environ.copy()
        cmd = f'cd /d "{self.frontend_dir}" && npx vite --port {self.frontend_port}'
        
        try:
            # 使用PIPE + 读取线程，防止缓冲区满导致进程死锁
            self.frontend_process = subprocess.Popen(
                cmd,
                cwd=self.frontend_dir,
                startupinfo=self._get_startupinfo(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=True,
                env=env,
                bufsize=0  # 无缓冲
            )
            # 关键：启动管道读取线程，持续消费stdout，防止PIPE缓冲区满导致前端卡死
            self._start_pipe_reader(self.frontend_process, "前端", log_callback)
            log_callback("前端进程输出已接管", "info")
            log_callback(f"前端进程已启动 (PID: {self.frontend_process.pid})", "info")
            
            for i in range(60):  # 增加到60次，共30秒
                time.sleep(0.5)
                progress_callback(55 + int(i * 0.75), f"等待前端就绪... ({i+1}/60)")
                if self.is_port_in_use(self.frontend_port):
                    log_callback(f"前端已就绪 http://localhost:{self.frontend_port}", "success")
                    progress_callback(100, "全部就绪")
                    return True
            
            log_callback("前端启动超时，请检查frontend进程状态", "error")
            progress_callback(100, "前端超时")
            return False
        except Exception as e:
            log_callback(f"前端启动失败: {e}", "error")
            return False
    
    def stop_frontend(self, log_callback: Callable) -> None:
        """停止前端服务 - 彻底清理所有进程"""
        killed_pids = []

        # 1. 递归杀死启动进程及其所有子进程
        if self.frontend_process:
            try:
                pid = self.frontend_process.pid
                self.kill_process(pid)
                killed_pids.append(pid)
                log_callback(f"已终止前端进程树 (根PID: {pid})", "info")
            except Exception as e:
                log_callback(f"终止前端主进程异常: {e}", "warning")
            self.frontend_process = None
            self.frontend_process = None
        
        # 2. 杀死所有占用端口的进程
        pid = self.get_port_pid(self.frontend_port)
        if pid and pid not in killed_pids:
            if self.kill_process(pid):
                killed_pids.append(pid)
                log_callback(f"已杀死端口 {self.frontend_port} 占用进程 (PID: {pid})", "info")
        
        # 3. 杀死所有node/vite相关进程
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = proc.info['name'].lower()
                    cmdline = ' '.join(proc.info['cmdline'] or [])
                    if ('node' in name or 'vite' in cmdline) and str(self.frontend_port) in cmdline:
                        proc_pid = proc.info['pid']
                        if proc_pid not in killed_pids:
                            self.kill_process(proc_pid)
                            killed_pids.append(proc_pid)
                            log_callback(f"清理vite进程 (PID: {proc_pid})", "info")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            log_callback(f"扫描vite进程异常: {e}", "warning")

        # 4. 确认端口已释放
        time.sleep(1.5)
        if self.is_port_in_use(self.frontend_port):
            try:
                pid = self.get_port_pid(self.frontend_port)
                if pid:
                    log_callback(f"端口仍被占用，强制清理 PID: {pid}", "warning")
                    self.kill_process(pid)
                    time.sleep(0.5)
            except Exception:
                pass
        if self.is_port_in_use(self.frontend_port):
            log_callback(f"警告: 端口 {self.frontend_port} 仍被占用", "warning")
        else:
            log_callback("前端已完全停止", "success")


# =============================================================================
# 现代化按钮组件
# =============================================================================
class ModernButton(tk.Canvas):
    """现代化圆角按钮"""
    
    def __init__(self, parent, text, command=None, width=120, height=36,
                 bg_color=Theme.BTN_PRIMARY, hover_color=Theme.BTN_PRIMARY_HOVER,
                 fg_color=Theme.TEXT_PRIMARY, **kwargs):
        super().__init__(parent, width=width, height=height, 
                        bg=parent.cget('bg'), highlightthickness=0, **kwargs)
        
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self.bg_color = bg_color
        self.hover_color = hover_color
        self.fg_color = fg_color
        self.current_bg = bg_color
        self.enabled = True
        
        self._draw()
        
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
    
    def _draw(self):
        self.delete("all")
        r = 8  # 圆角半径
        
        # 绘制圆角矩形
        self.create_arc(0, 0, r*2, r*2, start=90, extent=90, fill=self.current_bg, outline="")
        self.create_arc(self.width-r*2, 0, self.width, r*2, start=0, extent=90, fill=self.current_bg, outline="")
        self.create_arc(0, self.height-r*2, r*2, self.height, start=180, extent=90, fill=self.current_bg, outline="")
        self.create_arc(self.width-r*2, self.height-r*2, self.width, self.height, start=270, extent=90, fill=self.current_bg, outline="")
        
        self.create_rectangle(r, 0, self.width-r, self.height, fill=self.current_bg, outline="")
        self.create_rectangle(0, r, self.width, self.height-r, fill=self.current_bg, outline="")
        
        # 绘制文字
        text_color = self.fg_color if self.enabled else Theme.TEXT_MUTED
        self.create_text(self.width//2, self.height//2, text=self.text,
                        fill=text_color, font=("Segoe UI", 10, "bold"))
    
    def _on_enter(self, event):
        if self.enabled:
            self.current_bg = self.hover_color
            self._draw()
    
    def _on_leave(self, event):
        if self.enabled:
            self.current_bg = self.bg_color
            self._draw()
    
    def _on_click(self, event):
        if self.enabled and self.command:
            self.command()
    
    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.current_bg = self.bg_color if enabled else Theme.BTN_SECONDARY
        self._draw()
    
    def configure_btn(self, **kwargs):
        if 'text' in kwargs:
            self.text = kwargs['text']
        if 'bg_color' in kwargs:
            self.bg_color = kwargs['bg_color']
            self.current_bg = kwargs['bg_color']
        if 'hover_color' in kwargs:
            self.hover_color = kwargs['hover_color']
        self._draw()


# =============================================================================
# 进度指示器
# =============================================================================
class ProgressIndicator(tk.Canvas):
    """动画进度指示器"""
    
    def __init__(self, parent, width=400, height=6, **kwargs):
        super().__init__(parent, width=width, height=height,
                        bg=Theme.BG_CARD, highlightthickness=0, **kwargs)
        self.progress_width = width
        self.progress_height = height
        self.value = 0
        self.target = 0
        self.is_animating = False
        self.status_text = ""
        
        # 创建背景
        self.create_rectangle(0, 0, width, height, fill=Theme.BORDER_LIGHT, outline="")
        self.progress_bar = self.create_rectangle(0, 0, 0, height, fill=Theme.ACCENT_GREEN, outline="")
    
    def set_progress(self, value: int, status: str = ""):
        """设置进度值 (0-100)"""
        self.target = min(100, max(0, value))
        self.status_text = status
        if not self.is_animating:
            self._animate()
    
    def _animate(self):
        """平滑动画"""
        if abs(self.target - self.value) < 1:
            self.value = self.target
            self.is_animating = False
        else:
            self.is_animating = True
            self.value += (self.target - self.value) * 0.15
            self.after(16, self._animate)
        
        # 更新进度条
        bar_width = int((self.value / 100) * self.progress_width)
        self.coords(self.progress_bar, 0, 0, bar_width, self.progress_height)
    
    def reset(self):
        """重置进度"""
        self.value = 0
        self.target = 0
        self.coords(self.progress_bar, 0, 0, 0, self.progress_height)


# =============================================================================
# 服务状态卡片
# =============================================================================
class ServiceCard(tk.Frame):
    """服务状态卡片"""
    
    def __init__(self, parent, name: str, port: int, url_template: str, 
                 accent_color: str, on_toggle: Callable, on_kill: Callable, **kwargs):
        super().__init__(parent, bg=Theme.BG_CARD, **kwargs)
        
        self.name = name
        self.port = port
        self.url_template = url_template
        self.accent_color = accent_color
        self.on_toggle = on_toggle
        self.on_kill = on_kill
        self.is_running = False
        
        self._build_ui()
    
    def _build_ui(self):
        # 外边框效果
        self.configure(highlightbackground=Theme.BORDER, highlightthickness=1)
        
        # 头部：名称和状态指示灯
        header = tk.Frame(self, bg=Theme.BG_CARD)
        header.pack(fill=tk.X, padx=15, pady=(15, 10))
        
        # 状态指示灯
        self.status_dot = tk.Canvas(header, width=12, height=12, bg=Theme.BG_CARD, highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT)
        self.dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill=Theme.ACCENT_RED, outline="")
        
        # 服务名称
        tk.Label(header, text=self.name, font=("Segoe UI", 13, "bold"),
                bg=Theme.BG_CARD, fg=self.accent_color).pack(side=tk.LEFT, padx=(8, 0))
        
        # 状态文本
        self.status_label = tk.Label(header, text="已停止", font=("Segoe UI", 10),
                                    bg=Theme.BG_CARD, fg=Theme.ACCENT_RED)
        self.status_label.pack(side=tk.RIGHT)
        
        # 分隔线
        tk.Frame(self, height=1, bg=Theme.BORDER).pack(fill=tk.X, padx=15)
        
        # 信息区域
        info_frame = tk.Frame(self, bg=Theme.BG_CARD)
        info_frame.pack(fill=tk.X, padx=15, pady=12)
        
        # 端口信息
        port_frame = tk.Frame(info_frame, bg=Theme.BG_CARD)
        port_frame.pack(anchor=tk.W)
        
        tk.Label(port_frame, text="端口", font=("Segoe UI", 9),
                bg=Theme.BG_CARD, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT)
        tk.Label(port_frame, text=str(self.port), font=("Consolas", 10, "bold"),
                bg=Theme.BG_CARD, fg=Theme.TEXT_PRIMARY).pack(side=tk.LEFT, padx=(8, 0))
        
        # PID 信息
        self.pid_label = tk.Label(info_frame, text="", font=("Consolas", 9),
                                 bg=Theme.BG_CARD, fg=Theme.TEXT_MUTED)
        self.pid_label.pack(anchor=tk.W, pady=(4, 0))
        
        # URL
        url = self.url_template.format(self.port)
        url_label = tk.Label(info_frame, text=url, font=("Consolas", 9),
                            bg=Theme.BG_CARD, fg=Theme.ACCENT_BLUE, cursor="hand2")
        url_label.pack(anchor=tk.W, pady=(4, 0))
        url_label.bind("<Button-1>", lambda e: webbrowser.open(url))
        
        # 按钮区域
        btn_frame = tk.Frame(self, bg=Theme.BG_CARD)
        btn_frame.pack(fill=tk.X, padx=15, pady=(5, 15))
        
        self.toggle_btn = ModernButton(btn_frame, "启动", command=self.on_toggle,
                                       width=90, height=32, bg_color=Theme.BTN_PRIMARY)
        self.toggle_btn.pack(side=tk.LEFT)
        
        self.kill_btn = ModernButton(btn_frame, "终止端口", command=self.on_kill,
                                     width=80, height=32, bg_color=Theme.BTN_WARNING,
                                     hover_color=Theme.ACCENT_YELLOW)
        self.kill_btn.pack(side=tk.RIGHT)
    
    def update_status(self, running: bool, pid: Optional[int] = None):
        """更新状态显示"""
        self.is_running = running
        
        if running:
            self.status_dot.itemconfig(self.dot_id, fill=Theme.ACCENT_GREEN)
            self.status_label.config(text="运行中", fg=Theme.ACCENT_GREEN)
            self.pid_label.config(text=f"PID: {pid}" if pid else "")
            self.toggle_btn.configure_btn(text="停止", bg_color=Theme.BTN_DANGER,
                                         hover_color=Theme.BTN_DANGER_HOVER)
            self.kill_btn.pack_forget()
        else:
            self.status_dot.itemconfig(self.dot_id, fill=Theme.ACCENT_RED)
            self.status_label.config(text="已停止", fg=Theme.ACCENT_RED)
            self.pid_label.config(text=f"端口被占用 (PID: {pid})" if pid else "")
            self.toggle_btn.configure_btn(text="启动", bg_color=Theme.BTN_PRIMARY,
                                         hover_color=Theme.BTN_PRIMARY_HOVER)
            if pid:
                self.kill_btn.pack(side=tk.RIGHT)


# =============================================================================
# 主应用程序
# =============================================================================
class LauncherApp:
    """现代化启动器应用"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Heidalv-Alpha-Arena")
        self.root.geometry("800x640")
        self.root.minsize(700, 550)
        self.root.configure(bg=Theme.BG_DARK)
        
        # 设置图标（如果存在）
        icon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)
        
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.manager = ServiceManager(self.project_dir)
        self.ui_updater = AsyncUIUpdater(root)
        self.refresh_job = None
        self.is_operating = False
        
        self._build_ui()
        self._log_system("启动器就绪", "info")
        self._update_status()
    
    def _build_ui(self):
        """构建UI"""
        # 顶部标题栏
        self._build_header()
        
        # 主内容区
        main = tk.Frame(self.root, bg=Theme.BG_DARK)
        main.pack(fill=tk.BOTH, expand=True, padx=24, pady=16)
        
        # 服务卡片区域
        self._build_cards(main)
        
        # 操作按钮区域
        self._build_actions(main)
        
        # 进度条
        self._build_progress(main)
        
        # 控制台日志区域
        self._build_console(main)
    
    def _build_header(self):
        """构建顶部标题栏"""
        header = tk.Frame(self.root, bg=Theme.BG_HEADER, height=64)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # Logo 和标题
        title_frame = tk.Frame(header, bg=Theme.BG_HEADER)
        title_frame.pack(side=tk.LEFT, padx=24)
        
        # Logo 图标 (使用 Unicode)
        tk.Label(title_frame, text="◆", font=("Segoe UI", 20),
                bg=Theme.BG_HEADER, fg=Theme.ACCENT_PURPLE).pack(side=tk.LEFT)
        
        tk.Label(title_frame, text="Heidalv-Alpha-Arena", font=("Segoe UI", 16, "bold"),
                bg=Theme.BG_HEADER, fg=Theme.TEXT_PRIMARY).pack(side=tk.LEFT, padx=(8, 0))
        
        tk.Label(title_frame, text="量化交易系统", font=("Segoe UI", 10),
                bg=Theme.BG_HEADER, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(12, 0))
        
        # 状态指示器
        status_frame = tk.Frame(header, bg=Theme.BG_HEADER)
        status_frame.pack(side=tk.RIGHT, padx=24)
        
        self.main_status = tk.Label(status_frame, text="● 就绪", font=("Segoe UI", 11),
                                   bg=Theme.BG_HEADER, fg=Theme.TEXT_MUTED)
        self.main_status.pack(side=tk.RIGHT)
    
    def _build_cards(self, parent):
        """构建服务卡片"""
        cards_frame = tk.Frame(parent, bg=Theme.BG_DARK)
        cards_frame.pack(fill=tk.X, pady=(0, 16))
        
        # 配置网格权重使卡片等宽
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)
        
        self.backend_card = ServiceCard(
            cards_frame, "后端服务", self.manager.backend_port,
            "http://localhost:{}/docs", Theme.ACCENT_BLUE,
            on_toggle=self._toggle_backend, on_kill=self._kill_backend
        )
        self.backend_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        
        self.frontend_card = ServiceCard(
            cards_frame, "前端服务", self.manager.frontend_port,
            "http://localhost:{}", Theme.ACCENT_PURPLE,
            on_toggle=self._toggle_frontend, on_kill=self._kill_frontend
        )
        self.frontend_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    
    def _build_actions(self, parent):
        """构建操作按钮"""
        actions = tk.Frame(parent, bg=Theme.BG_DARK)
        actions.pack(fill=tk.X, pady=(0, 12))
        
        # 左侧操作按钮
        left_btns = tk.Frame(actions, bg=Theme.BG_DARK)
        left_btns.pack(side=tk.LEFT)
        
        self.start_all_btn = ModernButton(left_btns, "▶ 全部启动", command=self._start_all,
                                         width=110, bg_color=Theme.BTN_PRIMARY)
        self.start_all_btn.pack(side=tk.LEFT, padx=(0, 8))
        
        self.stop_all_btn = ModernButton(left_btns, "■ 全部停止", command=self._stop_all,
                                        width=110, bg_color=Theme.BTN_DANGER,
                                        hover_color=Theme.BTN_DANGER_HOVER)
        self.stop_all_btn.pack(side=tk.LEFT, padx=(0, 8))
        
        self.restart_btn = ModernButton(left_btns, "↻ 重启", command=self._restart_all,
                                       width=90, bg_color=Theme.BTN_WARNING,
                                       hover_color=Theme.ACCENT_YELLOW)
        self.restart_btn.pack(side=tk.LEFT)
        
        # 右侧快捷操作
        right_btns = tk.Frame(actions, bg=Theme.BG_DARK)
        right_btns.pack(side=tk.RIGHT)
        
        self.open_btn = ModernButton(right_btns, "🌐 打开应用", command=self._open_frontend,
                                    width=100, bg_color=Theme.BTN_SECONDARY,
                                    hover_color=Theme.BORDER)
        self.open_btn.pack(side=tk.RIGHT)
    
    def _build_progress(self, parent):
        """构建进度条区域"""
        progress_frame = tk.Frame(parent, bg=Theme.BG_DARK)
        progress_frame.pack(fill=tk.X, pady=(0, 12))
        
        self.progress = ProgressIndicator(progress_frame, width=752)
        self.progress.pack(fill=tk.X)
        
        self.progress_label = tk.Label(progress_frame, text="", font=("Segoe UI", 9),
                                      bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        self.progress_label.pack(anchor=tk.W, pady=(4, 0))
    
    def _build_console(self, parent):
        """构建控制台日志区域"""
        console_frame = tk.Frame(parent, bg=Theme.BG_CARD, highlightbackground=Theme.BORDER,
                                highlightthickness=1)
        console_frame.pack(fill=tk.BOTH, expand=True)
        
        # 控制台标题栏
        console_header = tk.Frame(console_frame, bg=Theme.BG_CARD)
        console_header.pack(fill=tk.X, padx=12, pady=(8, 0))
        
        tk.Label(console_header, text="📋 控制台输出", font=("Segoe UI", 10, "bold"),
                bg=Theme.BG_CARD, fg=Theme.TEXT_SECONDARY).pack(side=tk.LEFT)
        
        clear_btn = tk.Label(console_header, text="清除", font=("Segoe UI", 9),
                           bg=Theme.BG_CARD, fg=Theme.ACCENT_BLUE, cursor="hand2")
        clear_btn.pack(side=tk.RIGHT)
        clear_btn.bind("<Button-1>", lambda e: self._clear_log())
        
        # 日志文本区域
        self.log_text = scrolledtext.ScrolledText(
            console_frame, font=("Consolas", 10),
            bg=Theme.BG_INPUT, fg=Theme.ACCENT_GREEN,
            insertbackground=Theme.TEXT_PRIMARY,
            selectbackground=Theme.ACCENT_BLUE,
            borderwidth=0, highlightthickness=0,
            padx=12, pady=8
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=1, pady=(8, 1))
        self.log_text.configure(state="disabled")
        
        # 配置标签颜色
        self.log_text.tag_configure("info", foreground=Theme.TEXT_PRIMARY)
        self.log_text.tag_configure("success", foreground=Theme.ACCENT_GREEN)
        self.log_text.tag_configure("warning", foreground=Theme.ACCENT_YELLOW)
        self.log_text.tag_configure("error", foreground=Theme.ACCENT_RED)
        self.log_text.tag_configure("timestamp", foreground=Theme.TEXT_MUTED)
    
    def _log(self, msg: str, level: str = "info"):
        """线程安全的日志输出"""
        self.ui_updater.schedule(self._log_impl, msg, level)
    
    def _log_impl(self, msg: str, level: str):
        """实际的日志输出实现"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.log_text.insert(tk.END, f"{msg}\n", level)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
    
    def _log_system(self, msg: str, level: str = "info"):
        """系统日志（直接调用）"""
        self._log_impl(msg, level)
    
    def _clear_log(self):
        """清除日志"""
        self.log_text.configure(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state="disabled")
    
    def _update_progress(self, value: int, status: str):
        """线程安全的进度更新"""
        self.ui_updater.schedule(self._update_progress_impl, value, status)
    
    def _update_progress_impl(self, value: int, status: str):
        """实际的进度更新实现"""
        self.progress.set_progress(value, status)
        self.progress_label.config(text=status)
    
    def _set_operating(self, operating: bool):
        """设置操作状态"""
        self.is_operating = operating
        self.ui_updater.schedule(self._set_buttons_enabled, not operating)
    
    def _set_buttons_enabled(self, enabled: bool):
        """设置按钮启用状态"""
        self.start_all_btn.set_enabled(enabled)
        self.stop_all_btn.set_enabled(enabled)
        self.restart_btn.set_enabled(enabled)
    
    def _is_running(self, name: str) -> bool:
        port = getattr(self.manager, f"{name}_port")
        return self.manager.is_port_in_use(port)
    
    def _toggle_backend(self):
        if self.is_operating:
            return
        if self._is_running("backend"):
            self._stop_backend_async()
        else:
            self._start_backend_async()
    
    def _toggle_frontend(self):
        if self.is_operating:
            return
        if self._is_running("frontend"):
            self._stop_frontend_async()
        else:
            self._start_frontend_async()
    
    def _start_all(self):
        if self.is_operating:
            return
        self._set_operating(True)
        self.progress.reset()
        self._log_system("═" * 50, "info")
        self._log_system("开始启动所有服务...", "info")
        threading.Thread(target=self._start_all_thread, daemon=True).start()
    
    def _start_all_thread(self):
        """后台线程启动所有服务"""
        try:
            self.manager.start_backend(self._log, self._update_progress)
            time.sleep(1)
            self.manager.start_frontend(self._log, self._update_progress)
            self._log("═" * 50, "info")
            self._log("所有服务启动完成！", "success")
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _stop_all(self):
        if self.is_operating:
            return
        self._set_operating(True)
        self._log_system("═" * 50, "info")
        self._log_system("正在停止所有服务...", "info")
        threading.Thread(target=self._stop_all_thread, daemon=True).start()
    
    def _stop_all_thread(self):
        """后台线程停止所有服务"""
        try:
            self.manager.stop_backend(self._log)
            time.sleep(0.5)
            self.manager.stop_frontend(self._log)
            self._log("═" * 50, "info")
            self._log("所有服务已停止", "info")
            self._update_progress(0, "")
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _restart_all(self):
        if self.is_operating:
            return
        self._set_operating(True)
        self.progress.reset()
        self._log_system("正在重启服务...", "info")
        threading.Thread(target=self._restart_all_thread, daemon=True).start()
    
    def _restart_all_thread(self):
        """后台线程重启所有服务"""
        try:
            self.manager.stop_backend(self._log)
            self.manager.stop_frontend(self._log)
            time.sleep(2)
            self.manager.start_backend(self._log, self._update_progress)
            time.sleep(1)
            self.manager.start_frontend(self._log, self._update_progress)
            self._log("服务重启完成！", "success")
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _start_backend_async(self):
        self._set_operating(True)
        self.progress.reset()
        threading.Thread(target=self._start_backend_thread, daemon=True).start()
    
    def _start_backend_thread(self):
        try:
            self.manager.start_backend(self._log, self._update_progress)
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _stop_backend_async(self):
        self._set_operating(True)
        threading.Thread(target=self._stop_backend_thread, daemon=True).start()
    
    def _stop_backend_thread(self):
        try:
            self.manager.stop_backend(self._log)
            self._update_progress(0, "")
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _start_frontend_async(self):
        self._set_operating(True)
        self.progress.reset()
        threading.Thread(target=self._start_frontend_thread, daemon=True).start()
    
    def _start_frontend_thread(self):
        try:
            self.manager.start_frontend(self._log, self._update_progress)
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _stop_frontend_async(self):
        self._set_operating(True)
        threading.Thread(target=self._stop_frontend_thread, daemon=True).start()
    
    def _stop_frontend_thread(self):
        try:
            self.manager.stop_frontend(self._log)
            self._update_progress(0, "")
        finally:
            self._set_operating(False)
            self.ui_updater.schedule(self._update_status)
    
    def _kill_backend(self):
        pid = self.manager.get_port_pid(self.manager.backend_port)
        if pid:
            if messagebox.askyesno("终止进程", f"确定要强制终止 PID {pid}?"):
                self.manager.kill_process(pid)
                self._log_system(f"已终止进程 PID {pid}", "warning")
                self._update_status()
    
    def _kill_frontend(self):
        pid = self.manager.get_port_pid(self.manager.frontend_port)
        if pid:
            if messagebox.askyesno("终止进程", f"确定要强制终止 PID {pid}?"):
                self.manager.kill_process(pid)
                self._log_system(f"已终止进程 PID {pid}", "warning")
                self._update_status()
    
    def _open_frontend(self):
        webbrowser.open(f"http://localhost:{self.manager.frontend_port}")
    
    def _update_status(self):
        """更新状态显示"""
        b_running = self._is_running("backend")
        b_pid = self.manager.get_port_pid(self.manager.backend_port)
        self.backend_card.update_status(b_running, b_pid)
        
        f_running = self._is_running("frontend")
        f_pid = self.manager.get_port_pid(self.manager.frontend_port)
        self.frontend_card.update_status(f_running, f_pid)
        
        if b_running and f_running:
            self.main_status.config(text="● 全部运行中", fg=Theme.ACCENT_GREEN)
        elif b_running or f_running:
            self.main_status.config(text="● 部分运行", fg=Theme.ACCENT_YELLOW)
        else:
            self.main_status.config(text="● 已停止", fg=Theme.TEXT_MUTED)
        
        # 定时刷新
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(2000, self._update_status)


# =============================================================================
# 入口点
# =============================================================================
def main():
    root = tk.Tk()
    app = LauncherApp(root)
    
    def on_close():
        if messagebox.askyesno("退出", "是否停止所有服务并退出？"):
            app.manager.stop_backend(lambda msg, level="info": None)
            app.manager.stop_frontend(lambda msg, level="info": None)
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
