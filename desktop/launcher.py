"""
Heidalv Alpha Arena Desktop Launcher
─────────────────────────────
Lifecycle:
  1. 单实例检测：绑定 IPC 端口 19999，若被占用则激活已有窗口后退出
  2. Check PID lock → if backend alive, reuse it; otherwise start fresh
  3. Open pywebview window (loading screen → main app)
  4. pystray tray icon runs in background thread
  5. Closing the window (X) = hide to tray, backend stays alive
  6. Tray "显示窗口" = window.show() + focus 弹出到前台
  7. Tray "退出系统" = destroy window + kill backend + delete lock + exit
"""

import sys
import os
import time
import threading
import signal
import logging
import json
import urllib.request
import subprocess
import shutil
import socket
from pathlib import Path

# pythonw.exe sets stdout/stderr to None — redirect to log file to avoid crashes
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "launcher.log"

if sys.stdout is None or sys.stderr is None:
    _log_fh = open(_LOG_FILE, "w", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fh
    if sys.stderr is None:
        sys.stderr = _log_fh

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("launcher")

# ─── Paths ───────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
BACKEND_STATIC = BACKEND_DIR / "static"
PID_FILE = Path(__file__).resolve().parent / ".alpha-arena.pid"

HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{APP_URL}/api/health"

# ─── Shared state ────────────────────────────────────
_server_process = None
_should_exit = False          # True → allow window.destroy()
_exiting = False              # True → _do_real_exit/_do_restart 正在执行，防重入
_window = None                # pywebview Window reference
_tray_icon = None             # pystray Icon reference
_we_started_backend = False   # True if we spawned the uvicorn process
_job_handle = None            # Windows Job Object — 确保子进程随父进程一起退出
_ipc_socket = None            # IPC listen socket — 单实例检测与窗口激活

# ─── IPC constants ───────────────────────────────────
IPC_HOST = "127.0.0.1"
IPC_PORT = 19999             # 独立于 8000 后端端口，用于 launcher 进程间通信

# ─── Loading HTML (shown while backend boots) ────────
LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Tahoma,'MS Sans Serif',sans-serif;font-size:11px;
background:#000080;color:#FFF;display:flex;align-items:center;
justify-content:center;height:100vh;user-select:none;overflow:hidden}
.s{text-align:center;width:380px}
.logo{width:60px;height:60px;margin:0 auto 16px;
background:linear-gradient(135deg,#F00 25%,#0F0 25%,#0F0 50%,#00F 50%,#00F 75%,#FF0 75%);
image-rendering:pixelated}
h1{font-size:20px;margin-bottom:4px;letter-spacing:2px}
.sub{font-size:12px;color:#AAA;margin-bottom:24px}
.track{width:100%;height:18px;background:#000;
border:2px solid;border-color:#808080 #FFF #FFF #808080;overflow:hidden}
.bar{height:100%;background:repeating-linear-gradient(90deg,#000080 0 8px,transparent 8px 10px);
width:5%;transition:width .3s}
.st{margin-top:8px;font-size:11px;color:#CCC}
.ver{margin-top:16px;font-size:10px;color:#666}
</style></head><body>
<div class="s"><div class="logo"></div>
<h1>Heidalv Alpha Arena</h1><div class="sub">全自动交易系统</div>
<div class="track"><div class="bar" id="bar"></div></div>
<div class="st" id="st">正在启动后端服务...</div>
<div class="ver">v0.7.0</div></div>
<script>
let pct=5;const bar=document.getElementById('bar'),st=document.getElementById('st');
function upd(p,t){pct=p;bar.style.width=p+'%';st.textContent=t;}
setInterval(()=>{if(pct<30)upd(pct+1,'正在启动后端服务...');},300);
</script></body></html>"""


# ═══════════════════════════════════════════════════════
# 1. PID lock file
# ═══════════════════════════════════════════════════════

def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID exists on Windows."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _health_ok() -> bool:
    """
    必须确认为「本仓库」后端：仅 status==200 不够，其它程序也可能占用 8000。
    此前只要端口通且返回 200 就「跳过启动」，会导致一直复用旧进程/错误服务，代码更新永不生效。
    """
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status != 200:
            return False
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if data.get("status") != "healthy":
            return False
        msg = data.get("message") or ""
        if "Trading API" not in msg:
            return False
        if not data.get("version"):
            return False
        return True
    except Exception:
        return False


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, PORT)) == 0


def read_pid_lock() -> int | None:
    """Read PID from lock file, return None if invalid."""
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            return pid if pid > 0 else None
    except (ValueError, OSError):
        pass
    return None


def write_pid_lock(pid: int):
    PID_FILE.write_text(str(pid))


def clear_pid_lock():
    PID_FILE.unlink(missing_ok=True)


def is_backend_running() -> bool:
    """Check lock file + port + health to determine if backend is alive."""
    pid = read_pid_lock()
    if pid and _pid_alive(pid) and _port_in_use() and _health_ok():
        log.info(f"Backend already running (PID {pid}), will reuse.")
        return True
    if _port_in_use() and _health_ok():
        log.info("Backend running on port (no lock file match), will reuse.")
        return True
    clear_pid_lock()
    return False


# ═══════════════════════════════════════════════════════
# 1.5. Single-instance detection (IPC socket)
# ═══════════════════════════════════════════════════════

def _try_become_primary():
    """尝试绑定 IPC 端口成为主实例。
    返回 (is_primary: bool, listener_socket | None)
    如果端口已被占用 → 已有实例在运行 → 返回 (False, None)
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((IPC_HOST, IPC_PORT))
        s.listen(1)
        s.settimeout(1.0)  # accept() 超时 1s，避免阻塞退出
        log.info(f"IPC 主实例已绑定 {IPC_HOST}:{IPC_PORT}")
        return True, s
    except OSError:
        s.close()
        log.info(f"IPC 端口 {IPC_PORT} 已被占用，检测到已有实例运行")
        return False, None


def _activate_existing() -> bool:
    """连接已有实例并发送 'activate' 命令使其弹出窗口。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((IPC_HOST, IPC_PORT))
        s.sendall(b"activate")
        s.close()
        log.info("已向已有实例发送激活命令")
        return True
    except Exception as e:
        log.warning(f"无法连接已有实例: {e}")
        return False


def _ipc_listener():
    """IPC 监听线程：接收来自新实例的 'activate' 命令并弹出窗口。"""
    global _ipc_socket
    log.info("IPC 监听线程已启动")
    while _ipc_socket and not _should_exit:
        try:
            conn, addr = _ipc_socket.accept()
            data = conn.recv(1024)
            conn.close()
            if b"activate" in data:
                log.info("IPC: 收到激活命令，弹出窗口到前台")
                if _window:
                    try:
                        _window.show()
                        # pywebview 在某些版本没有 restore/on_top，
                        # 尝试通过 evaluate_js 调用 window.focus()
                        try:
                            _window.evaluate_js("window.focus()")
                        except Exception:
                            pass
                    except Exception as e:
                        log.warning(f"激活窗口失败: {e}")
        except socket.timeout:
            continue
        except OSError:
            if _should_exit:
                break
            time.sleep(0.5)
        except Exception as e:
            log.debug(f"IPC accept error: {e}")
            time.sleep(0.5)
    log.info("IPC 监听线程退出")


def _stop_ipc():
    """关闭 IPC 监听 socket。"""
    global _ipc_socket
    if _ipc_socket:
        try:
            _ipc_socket.close()
        except Exception:
            pass
        _ipc_socket = None


# ═══════════════════════════════════════════════════════
# 2. Frontend deployment
# ═══════════════════════════════════════════════════════

def ensure_frontend_deployed(force_rebuild: bool = False) -> bool:
    """
    确保前端已构建并部署到 backend/static。
    如果 dist/ 比 static/ 新，自动重新部署（无需手动删除）。
    force_rebuild=True 时先执行 npm run build。
    """
    dist_index = FRONTEND_DIST / "index.html"
    static_index = BACKEND_STATIC / "index.html"

    need_build = force_rebuild or not dist_index.exists()
    need_deploy = False

    if need_build:
        log.info("Building frontend (npm run build) ...")
        try:
            subprocess.run(
                ["npm", "run", "build"], cwd=str(FRONTEND_DIR),
                check=True, timeout=120, shell=True,
            )
        except Exception as e:
            log.error(f"Frontend build failed: {e}")
            if not dist_index.exists():
                return False

    if not dist_index.exists():
        log.error("dist/index.html not found.")
        return False

    if not static_index.exists():
        need_deploy = True
    else:
        dist_mtime = dist_index.stat().st_mtime
        static_mtime = static_index.stat().st_mtime
        if dist_mtime > static_mtime:
            log.info("dist/ is newer than static/ → redeploying")
            need_deploy = True

    if need_deploy:
        log.info("Deploying dist → static ...")
        if BACKEND_STATIC.exists():
            shutil.rmtree(BACKEND_STATIC)
        shutil.copytree(str(FRONTEND_DIST), str(BACKEND_STATIC))
        log.info("Frontend deployed successfully.")
    else:
        log.info("Frontend static files up-to-date.")

    return True


# ═══════════════════════════════════════════════════════
# 3. Backend process management
# ═══════════════════════════════════════════════════════

def _create_job_object():
    """创建 Windows Job Object 并设置 KILL_ON_JOB_CLOSE 标志。

    Job Object 确保所有子进程（包括 multiprocessing.spawn 创建的 uvicorn worker）
    在 Job 关闭时全部被终止，彻底解决孤儿进程问题。
    """
    global _job_handle
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # CreateJobObjectW(lpJobAttributes, lpName)
        _job_handle = kernel32.CreateJobObjectW(None, None)
        if not _job_handle:
            log.warning("CreateJobObjectW failed")
            return None

        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        result = kernel32.SetInformationJobObject(
            _job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not result:
            log.warning("SetInformationJobObject failed")
            kernel32.CloseHandle(_job_handle)
            _job_handle = None
            return None

        log.info("Windows Job Object created (KILL_ON_JOB_CLOSE)")
        return _job_handle
    except Exception as e:
        log.warning(f"Job Object creation failed (non-fatal): {e}")
        _job_handle = None
        return None


def _assign_process_to_job(pid: int):
    """将指定 PID 加入 Job Object，使其子进程也受 Job 管理。"""
    global _job_handle
    if not _job_handle:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_ALL_ACCESS = 0x1F0FFF
        handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not handle:
            log.warning(f"OpenProcess failed for PID {pid}")
            return False
        result = kernel32.AssignProcessToJobObject(_job_handle, handle)
        kernel32.CloseHandle(handle)
        if result:
            log.info(f"PID {pid} assigned to Job Object")
            return True
        else:
            log.warning(f"AssignProcessToJobObject failed for PID {pid}")
            return False
    except Exception as e:
        log.warning(f"Failed to assign PID {pid} to job: {e}")
        return False


def start_backend():
    """Start uvicorn subprocess and write PID lock.

    使用 Windows Job Object 确保 uvicorn 及其所有子进程（包括 --reload 创建的 worker）
    在 launcher 退出时全部被终止，防止孤儿进程。
    """
    global _server_process, _we_started_backend

    # 真实 venv 目录是 backend\.venv（带点）；旧逻辑只找 backend\venv（无点）→ 永远
    # 回退到 sys.executable（可能是缺 psycopg3 的系统 Python，后端会起不来）。
    # 优先 .venv，再兼容历史的 venv。
    venv_python = next(
        (p for p in (
            BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
            BACKEND_DIR / "venv" / "Scripts" / "python.exe",
        ) if p.exists()),
        None,
    )
    python_exe = str(venv_python) if venv_python else sys.executable

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        python_exe, "-m", "uvicorn",
        "backend.main:app",
        "--host", HOST,
        "--port", str(PORT),
        "--log-level", "info",
    ]

    # 创建 Job Object（在启动进程之前）
    _create_job_object()

    log.info(f"Starting backend: {' '.join(cmd)}")
    _server_process = subprocess.Popen(
        cmd, cwd=str(ROOT_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    _we_started_backend = True
    write_pid_lock(_server_process.pid)

    # 将后端进程及其未来的子进程全部纳入 Job Object 管理
    _assign_process_to_job(_server_process.pid)

    def _stream():
        with open(_LOG_DIR / "backend-stdout.log", "a", encoding="utf-8", buffering=1) as _backend_log:
            for line in iter(_server_process.stdout.readline, b""):
                try:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    print(f"[backend] {text}")
                    _backend_log.write(text + "\n")
                except Exception:
                    pass

    threading.Thread(target=_stream, daemon=True).start()

def stop_backend():
    """Terminate backend: Job Object → process tree → orphan cleanup → port release.

    多层清理策略（彻底解决孤儿进程）：
    1. 关闭 Job Object — 自动杀死所有受管进程（包括 multiprocessing.spawn 子进程）
    2. taskkill /F /T — 杀进程树（兜底 Job Object 失败的情况）
    3. 搜杀 multiprocessing 孤儿 — 找到所有含 --multiprocessing-fork 的 python 进程
    4. 端口释放 — 确保 8000 端口可用
    """
    global _server_process, _job_handle

    parent_pid = None
    if _server_process and _server_process.poll() is None:
        parent_pid = _server_process.pid
    elif _we_started_backend:
        parent_pid = read_pid_lock()

    # Step 1: 关闭 Job Object（最可靠的方式）
    if _job_handle:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.TerminateJobObject(_job_handle, 1)
            kernel32.CloseHandle(_job_handle)
            log.info("Job Object terminated — all child processes killed")
        except Exception as e:
            log.warning(f"Job Object termination failed: {e}")
        _job_handle = None

    # Step 2: graceful terminate + taskkill tree
    if _server_process and _server_process.poll() is None:
        log.info("Stopping backend (terminate) ...")
        _server_process.terminate()
        try:
            _server_process.wait(timeout=3)
            log.info("Backend stopped gracefully.")
        except subprocess.TimeoutExpired:
            log.warning("Terminate timeout, using taskkill /F /T ...")
            _taskkill(_server_process.pid)
    elif parent_pid and _pid_alive(parent_pid):
        log.info(f"Killing backend PID {parent_pid} via taskkill ...")
        _taskkill(parent_pid)

    # Step 3: 搜杀 multiprocessing.spawn 孤儿进程
    _kill_multiprocessing_orphans(parent_pid)

    # Step 4: 端口释放
    _force_kill_port(timeout=5)
    clear_pid_lock()


def _kill_multiprocessing_orphans(parent_pid: int = None):
    """找到并杀死所有 multiprocessing.spawn/fork 创建的 Python 孤儿进程。

    uvicorn --reload 通过 multiprocessing.spawn 创建 worker 子进程，
    这些子进程在 Windows 上可能不在父进程树中，导致 taskkill /T 无法杀死。
    """
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId, CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return

        import json
        processes = json.loads(result.stdout or "[]")
        if isinstance(processes, dict):
            processes = [processes]

        for proc in processes:
            cmd_line = proc.get("CommandLine", "") or ""
            pid = proc.get("ProcessId", 0)
            if not pid or pid == os.getpid():
                continue

            is_orphan = False

            # 检查是否是 multiprocessing.spawn 创建的子进程
            if "--multiprocessing-fork" in cmd_line and "spawn_main" in cmd_line:
                # 如果知道父 PID，精确匹配
                if parent_pid and f"parent_pid={parent_pid}" in cmd_line:
                    is_orphan = True
                # 如果不知道父 PID，检查其父进程是否还活着
                elif not parent_pid:
                    import re
                    m = re.search(r"parent_pid=(\d+)", cmd_line)
                    if m:
                        declared_parent = int(m.group(1))
                        if not _pid_alive(declared_parent):
                            is_orphan = True

            if is_orphan:
                log.warning(f"Killing multiprocessing orphan PID {pid}")
                _taskkill(pid)

    except Exception as e:
        log.debug(f"_kill_multiprocessing_orphans failed (non-fatal): {e}")


def _taskkill(pid: int):
    """Windows taskkill that kills entire process tree."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        log.error(f"taskkill failed: {e}")


def _get_port_pids() -> list[int]:
    """Return all PIDs listening on PORT."""
    pids = []
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        stdout = result.stdout or ""
        for line in stdout.splitlines():
            if f":{PORT}" in line and "LISTEN" in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                    if pid > 0:
                        pids.append(pid)
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        log.error(f"_get_port_pids failed: {e}")
    return pids


def _force_kill_port(timeout=5) -> bool:
    """Find and kill ALL processes holding PORT, using multiple strategies."""
    # Strategy 1: fast taskkill loop (2s)
    deadline = time.time() + min(timeout, 2)
    while time.time() < deadline:
        pids = _get_port_pids()
        if not pids:
            return True
        for pid in pids:
            log.warning(f"Killing stale PID {pid} on port {PORT}")
            _taskkill(pid)
        time.sleep(0.3)

    # Still in use? Try PowerShell Stop-Process as fallback (WMIC removed in Win11 25H2)
    pids = _get_port_pids()
    if pids:
        log.warning(f"taskkill failed for {pids}, trying PowerShell Stop-Process ...")
        for pid in pids:
            try:
                subprocess.run(
                    ["powershell", "-Command", f"Stop-Process -Id {pid} -Force"],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
            except Exception as e:
                log.error(f"PowerShell kill failed for PID {pid}: {e}")
        time.sleep(0.5)

    # Final check
    pids = _get_port_pids()
    if not pids:
        log.info(f"Port {PORT} released successfully.")
        return True

    log.error(f"Port {PORT} still in use by PIDs {pids} after all kill attempts")
    return False


# ═══════════════════════════════════════════════════════
# 4. System tray (pystray)
# ═══════════════════════════════════════════════════════

def _create_tray_image():
    """Generate a 64x64 Win95-style 4-color logo via Pillow."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), "#C0C0C0")
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, 30, 30], fill="#FF0000")
    d.rectangle([34, 4, 60, 30], fill="#00FF00")
    d.rectangle([4, 34, 30, 60], fill="#0000FF")
    d.rectangle([34, 34, 60, 60], fill="#FFFF00")
    return img


def start_tray():
    """Create and start the system tray icon in a detached thread."""
    global _tray_icon
    from pystray import Icon, Menu, MenuItem

    def on_show(icon, item):
        if _window:
            _window.show()
            # 尝试将窗口弹到前台并获取焦点
            try:
                _window.evaluate_js("window.focus()")
            except Exception:
                pass
            log.info("Tray: 显示窗口 → 已弹出到前台")

    def on_restart(icon, item):
        _do_restart()

    def on_exit(icon, item):
        _do_real_exit()

    image = _create_tray_image()
    menu = Menu(
        MenuItem("Heidalv Alpha Arena", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("显示窗口", on_show, default=True),
        Menu.SEPARATOR,
        MenuItem("重启系统", on_restart),
        MenuItem("退出系统", on_exit),
    )

    _tray_icon = Icon("AlphaArena", image, "Heidalv Alpha Arena", menu)
    _tray_icon.run_detached()
    log.info("System tray icon started.")


def stop_tray():
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None


# ═══════════════════════════════════════════════════════
# 5. Window management
# ═══════════════════════════════════════════════════════

def _do_restart():
    """Tray '重启系统': kill backend → release IPC → spawn new launcher → clean exit.

    关键顺序：先杀后端再释放 IPC + 启动新进程，确保新实例不会复用即将被杀的后端。
    防重入：_exiting 标志阻止并发调用。
    """
    global _should_exit, _exiting
    if _exiting:
        log.warning("Restart already in progress, ignoring duplicate request.")
        return
    _exiting = True
    _should_exit = True
    log.info("User requested restart.")

    # ── 看门狗：15 秒后强制退出（兜底 stop_backend 卡死） ──
    _watchdog_triggered = threading.Event()

    def _restart_watchdog():
        if not _watchdog_triggered.wait(15):
            log.warning("Restart watchdog triggered — force exit!")
            try:
                _kill_multiprocessing_orphans()
            except Exception:
                pass
            os._exit(0)

    threading.Thread(target=_restart_watchdog, daemon=True, name="restart-watchdog").start()

    # 1. 先杀后端（确保新实例不会复用旧后端）
    try:
        stop_backend()
    except Exception as e:
        log.warning(f"stop_backend error (non-fatal): {e}")

    # 2. 释放 IPC 端口，以便新进程成为主实例
    _stop_ipc()

    # 3. 启动新的 launcher 进程
    launcher_path = Path(__file__).resolve()
    python_exe = sys.executable
    try:
        subprocess.Popen(
            [python_exe, str(launcher_path)],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        log.info(f"New launcher spawned: {python_exe} {launcher_path}")
    except Exception as e:
        log.error(f"Failed to spawn new launcher: {e}")

    # 4. 关闭托盘和窗口
    stop_tray()
    if _window:
        try:
            _window.destroy()
        except Exception:
            pass

    # 5. 通知看门狗正常完成，退出
    _watchdog_triggered.set()
    log.info("Restart: clean exit completed.")
    os._exit(0)


def _do_real_exit():
    """Called from tray "退出系统". Destroys everything with watchdog.

    防重入：_exiting 标志阻止并发调用。
    看门狗：使用 threading.Event 替代盲 sleep，正常完成时 set() 取消看门狗。
    """
    global _should_exit, _exiting
    if _exiting:
        log.warning("Exit already in progress, ignoring duplicate request.")
        return
    _exiting = True
    _should_exit = True
    log.info("User requested full exit.")

    # ── 看门狗：15 秒后无论如何强制退出，防止死锁 ──
    _watchdog_triggered = threading.Event()

    def _watchdog():
        if not _watchdog_triggered.wait(15):
            log.warning("Exit watchdog triggered — force exit!")
            try:
                _kill_multiprocessing_orphans()
            except Exception:
                pass
            os._exit(0)

    threading.Thread(target=_watchdog, daemon=True, name="exit-watchdog").start()

    stop_tray()

    # 关闭 IPC 监听 socket（释放端口，允许下次启动成为主实例）
    _stop_ipc()

    if _window:
        try:
            _window.destroy()
        except Exception:
            pass

    try:
        stop_backend()
    except Exception as e:
        log.warning(f"stop_backend error (non-fatal): {e}")

    # 通知看门狗正常完成
    _watchdog_triggered.set()
    log.info("Clean exit completed.")
    os._exit(0)


def _check_webview2_installed() -> bool:
    """Verify Edge WebView2 Runtime is actually available (not just registered)."""
    import ctypes
    # Check common install paths
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\EdgeWebView\Application"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\EdgeWebView\Application"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\EdgeWebView\Application"),
    ]
    for base in candidates:
        if os.path.isdir(base):
            for entry in os.listdir(base):
                exe = os.path.join(base, entry, "msedgewebview2.exe")
                if os.path.isfile(exe):
                    log.info(f"WebView2 found: {exe}")
                    return True
    return False


def clear_webview_cache():
    """清理 WebView2 浏览器缓存，确保加载最新前端资源。"""
    cache_dirs = []
    # pywebview 在 Windows 上把 EBWebView 缓存放在 %APPDATA%\pywebview
    roaming = os.environ.get("APPDATA", "")
    if roaming:
        cache_dirs.append(Path(roaming) / "pywebview")
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        cache_dirs.append(Path(local_app) / "pywebview")

    for d in cache_dirs:
        if d.exists() and d.is_dir():
            try:
                shutil.rmtree(d)
                log.info(f"Cleared webview cache: {d}")
            except Exception as e:
                log.warning(f"Cannot clear cache {d}: {e}")


def open_window():
    """
    Single-window approach:
    - Starts with inline loading HTML
    - Background thread polls backend health → navigates to APP_URL
    - Closing (X) hides to tray unless _should_exit is True
    """
    global _window, _should_exit
    import webview

    # ── 预检：WebView2 是否可用 ──
    if sys.platform == "win32" and not _check_webview2_installed():
        _err_msg = (
            "Heidalv Alpha Arena 需要 Microsoft Edge WebView2 运行时！\n\n"
            "请下载安装后重试:\n"
            "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
            "安装完成后重新运行 启动AlphaArena.bat"
        )
        log.error(_err_msg)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, _err_msg, "Heidalv Alpha Arena — 缺少 WebView2", 0x10)
        except Exception:
            pass
        # 清理已启动的后端
        _should_exit = True
        if _we_started_backend:
            try:
                stop_backend()
            except Exception:
                pass
        try:
            stop_tray()
        except Exception:
            pass
        os._exit(1)

    _window = webview.create_window(
        title="Heidalv Alpha Arena — 全自动交易系统",
        html=LOADING_HTML,
        width=1280,
        height=800,
        min_size=(1024, 600),
        text_select=True,
        zoomable=True,
    )

    # ── Closing intercept: hide to tray instead of exit ──
    def on_closing():
        if _should_exit:
            log.info("窗口关闭: _should_exit=True → 允许关闭")
            return True
        if _window:
            log.info("窗口关闭: ✕ 按钮 → 最小化到系统托盘（后台继续运行）")
            _window.hide()
        return False  # 返回 False 阻止 webview 销毁窗口

    _window.events.closing += on_closing

    # ── After window shown: poll backend then navigate ──
    def _wait_and_navigate():
        steps = [
            (15, "加载数据库..."),
            (30, "启动交易引擎..."),
            (50, "连接 Hyperliquid..."),
            (70, "初始化 AI 系统..."),
            (85, "准备界面..."),
        ]
        si = 0
        # 首次启动若 DB 较慢或后台迁移较多，60s 易误判失败；与 main 后台初始化解耦后通常很快就绪
        deadline = time.time() + 120

        while time.time() < deadline:
            if _health_ok():
                try:
                    _window.evaluate_js("upd(100,'启动完成！')")
                except Exception:
                    pass
                time.sleep(0.5)
                try:
                    _window.load_url(APP_URL)
                    log.info("Navigated to main app.")
                except Exception as e:
                    log.error(f"Navigate failed: {e}")
                return

            if si < len(steps):
                pct, msg = steps[si]
                try:
                    _window.evaluate_js(f"upd({pct},'{msg}')")
                except Exception:
                    pass
                si += 1
            time.sleep(1.5)

        log.error("Backend did not become ready in 120s — see desktop/launcher.log and backend terminal output.")
        try:
            _window.evaluate_js(
                "upd(0,'后端 120s 内未就绪：请打开 desktop/launcher.log 查看原因')"
            )
        except Exception:
            pass

    _window.events.shown += lambda: threading.Thread(
        target=_wait_and_navigate, daemon=True
    ).start()

    try:
        webview.start(
            debug=("--debug" in sys.argv),
            private_mode=False,
        )
    except Exception as _wv_err:
        log.error(f"WebView crashed: {_wv_err}")
        log.warning("WebView启动失败，正在清理后端进程...")
        _should_exit = True
        if _we_started_backend:
            try:
                stop_backend()
            except Exception as _be:
                log.warning(f"stop_backend error (non-fatal): {_be}")
        try:
            stop_tray()
        except Exception:
            pass
        _err_msg = (
            "Heidalv Alpha Arena 窗口启动失败！\n\n"
            "常见原因：\n"
            "  1. Edge WebView2 运行时未安装\n"
            "     → 下载: https://go.microsoft.com/fwlink/p/?LinkId=2124703\n"
            "  2. WebView2 缓存损坏\n"
            "     → 运行: 启动AlphaArena.bat /cleancache\n"
            "  3. GPU 驱动问题 → 尝试更新显卡驱动\n"
            "  4. 杀毒软件拦截 → 将程序目录加入白名单\n\n"
            f"完整错误: {_wv_err}\n\n"
            "详细日志请查看: logs\\launcher.log"
        )
        log.error(f"\n{'='*60}\n{_err_msg}\n{'='*60}")
        # 用 ctypes 弹出 Windows 消息框（pythonw 无控制台时也能看到）
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, _err_msg, "Heidalv Alpha Arena — 启动失败", 0x10)
        except Exception:
            pass
        os._exit(1)


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 50)
    log.info("  Heidalv Alpha Arena Desktop Launcher")
    log.info("=" * 50)

    signal.signal(signal.SIGINT, lambda *_: (_do_real_exit(), sys.exit(0)))

    # ── 单实例检测 ──
    # 通过绑定 IPC 端口判断是否已有实例在运行
    global _ipc_socket
    is_primary, listener = _try_become_primary()
    if not is_primary:
        # 已有实例在运行 → 激活其窗口并退出
        print("Heidalv Alpha Arena 已在运行中，正在激活已有窗口...")
        if _activate_existing():
            print("已激活已有窗口。")
        else:
            print("无法连接到已有实例。如确认无实例运行，请检查端口 19999 是否被占用。")
        return
    _ipc_socket = listener
    # 启动 IPC 监听线程（接收来自后续实例的激活命令）
    threading.Thread(target=_ipc_listener, daemon=True, name="ipc-listener").start()

    # ── P2-3 预清理：检测是否存在脱离 launcher 控制的 uvicorn 实例 ──
    # 当外部（PowerShell/脚本）直接 `python -m uvicorn backend.main:app` 启动时，
    # launcher 再启动会在 8000 端口形成冲突 / 重复 reload child。
    # 启动时主动扫一次，杀掉所有 PID lock 之外的同名进程。
    try:
        _locked_pid = read_pid_lock()
        import subprocess as _sp
        _res = _sp.run(
            ["wmic", "process", "where",
             "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        _stray_pids = []
        for _line in (_res.stdout or "").splitlines():
            if "uvicorn" not in _line or "backend.main:app" not in _line:
                continue
            _parts = [p.strip() for p in _line.split(",") if p.strip()]
            if not _parts:
                continue
            try:
                _pid_str = _parts[-1]
                _pid = int(_pid_str)
            except Exception:
                continue
            if _locked_pid and _pid == _locked_pid:
                continue
            _stray_pids.append(_pid)
        if _stray_pids:
            log.warning(f"检测到 {len(_stray_pids)} 个外部 uvicorn 进程 {_stray_pids}，将清理")
            for _sp_pid in _stray_pids:
                try:
                    _sp.run(["taskkill", "/F", "/T", "/PID", str(_sp_pid)], timeout=3)
                except Exception:
                    pass
    except Exception as _pre_err:
        log.debug(f"uvicorn 预扫描跳过: {_pre_err}")

    force_restart = (
        "--restart-backend" in sys.argv
        or "--fresh-backend" in sys.argv
        or os.environ.get("ALPHA_ARENA_RESTART_BACKEND", "").strip() in ("1", "true", "yes")
    )
    if force_restart:
        log.info("强制重启后端：释放端口并清除 PID 锁（加载最新代码）")
        # 先尝试通过 PID 锁杀旧进程
        old_pid = read_pid_lock()
        if old_pid and _pid_alive(old_pid):
            log.info(f"Killing locked backend PID {old_pid} ...")
            _taskkill(old_pid)
            time.sleep(0.3)
        clear_pid_lock()

        # 清理 multiprocessing.spawn 孤儿进程（之前遗留的 uvicorn worker）
        _kill_multiprocessing_orphans(old_pid)

        # 再清理所有占用端口的进程
        port_released = _force_kill_port(timeout=5)

        if not port_released:
            log.warning(f"端口 {PORT} 未能释放，但将继续启动（uvicorn --reload 会热加载最新代码）")

    # ── /cleancache: 清除 WebView2 缓存后立即退出 ──
    if "--cleancache" in sys.argv or "/cleancache" in sys.argv:
        log.info("清除 WebView2 缓存（--cleancache）...")
        clear_webview_cache()
        _msg = "WebView2 缓存已清除。请重新启动 Heidalv Alpha Arena。"
        log.info(_msg)
        print(_msg)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, _msg, "Heidalv Alpha Arena — 缓存已清除", 0x40)
        except Exception:
            pass
        return

    # Step 1: frontend — 每次强制重启时都重新部署，确保代码更新生效
    if not ensure_frontend_deployed():
        log.error("Cannot proceed without frontend.")
        return

    # Step 2: backend (reuse or start)
    if is_backend_running():
        log.info("Backend already running — reusing existing instance (reload will pick up code changes).")
    else:
        start_backend()

    # Step 3: 清理 WebView2 缓存，确保新前端立即可见
    if force_restart:
        clear_webview_cache()

    # Step 4: system tray
    start_tray()

    # Step 5: window (blocks until destroyed)
    log.info("Opening window ...")
    open_window()

    # After webview.start() returns (window destroyed):
    if _we_started_backend:
        stop_backend()
    stop_tray()
    log.info("Goodbye.")


if __name__ == "__main__":
    main()
