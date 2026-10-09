<#
.SYNOPSIS
    Aster 深度采集看门狗：保证「20 档深度」这路 WebSocket 采集常驻。

.DESCRIPTION
    ## 为什么需要它
    `aster_ws_ingest.py` 由 DSH 会话拉起（父进程链指向 `dsh-subprocess-local`），
    **会话结束进程即停**——实测该风险已导致 2026-09-17 起 ZEC/TAO 的事实性停采
    （最后一行 09-17，车道宇宙仍把它们当可用标的，于是报价永远不成交）。

    `data-center-watchdog.ps1` 只守护 `market_data_center`（9100 端口），
    管不到这个采集器 ⇒ 需要独立看门狗。

    ## 判定口径（不用进程名，用**数据新鲜度**）
    「进程活着」不等于「在写数据」：WS 半死连接会保持进程存活但不再落行。
    故本看门狗直接查 `asterdex_depth_snapshots` 的最新 event_ts_ms：
      · 最新数据 age > StaleSec ⇒ 判定停采 ⇒ 杀掉旧进程并重启
      · 进程存在但数据新鲜 ⇒ 什么都不做
      · 进程不存在 ⇒ 启动

    ## 运行模式（重要）
    **默认常驻循环**（`-Once` 只用于手动或计划任务兜底）。
    为什么不再依赖"计划任务每 2 分钟拉一次"：每次拉起都创建一个进程，而进程创建
    在交互会话里**会闪窗**（cmd/conhost）——用户实测抱怨过黑框。
    常驻 = 只在 1 次启动时可能闪一下；计划任务降到 30 分钟仅作兜底。
    循环体整体包 try/catch，单次异常不会杀死常驻进程。

    ## 覆盖的标的
    两个名单语义不同，都必须是权威值：
      `-Symbols`      = book_ticker + trades 订阅（完整采集宇宙）
      `-DepthSymbols` = 20 档深度订阅（较重，按做市评分选出）
    同步工具：`scripts/sync_watchdog_symbols.py`（单一权威源 + 一致性断言）。
#>
[CmdletBinding()]
param(
    [int]$IntervalSec = 60,
    [int]$StaleSec = 180,
    [string]$PythonExe = '',
    # ⚠️ 两个名单的**语义不同，不可混用**：
    #   `$Symbols`     = book_ticker + trades 的订阅名单（应等于**完整采集宇宙**）
    #   `$DepthSymbols`= 20 档深度订阅名单（较重，只覆盖达标标的）
    #
    #   实测事故 ①：默认值曾是 8 币（只含扩容那批），看门狗重启后
    #     HYPE/ZEC/ONDO/ARB/SEI **停采 6 分钟无人发现**。
    #   实测事故 ②：只看守深度名单、却用它去启动 book/trades，导致
    #     其余 19 个币的 book 数据**同时断供 32 分钟**（age 恒为 1926s）。
    #   ⇒ 两个名单都必须等于各自权威值，改动前先核对 `aster_ws_ingest.py` 的调用方。
    #
    # [H136 2026-09-21] 扩池：把 **USD1 合约**加进来。
    #   动机：官方费率文档（docs.asterdex.com/trading/perpetuals/fees-and-specs/fees.md）
    #     · USDT 永续  Maker 0%  Taker **0.04%** = 4.0bp   ← 我们现在用的
    #     · USD1 永续  Maker 0%  Taker **0.005%** = 0.5bp   ← **便宜 8 倍**
    #   我们成本结构里强平腿每周期 ≈10.67bp，其中 taker 费占 41%
    #   （H105/H126 实测）⇒ 换 USD1 理论上每周期省 `(4.36−0.86)bp × 强平率`。
    #   对 SOL（强平率 7.0%、USDT 版每周期仅 +0.047bp）足够翻好几倍。
    #   但**必须先量流动性**：实测 SOLUSD1 24h 成交额 $1.87M vs SOLUSDT $90.6M（1/48）、
    #   点差 3.58bp vs 0.895bp（4 倍宽）⇒ 有利有弊（书薄 ⇒ 竞争者少 ⇒ 排队靠前），
    #   所以先把数据采起来，用实测决定，不做纸面推断。
    #   `SOLUSD1` 进两个名单（它是唯一"我们候选里有点差又有 USD1 版本"的币）；
    #   `BTCUSD1/ETHUSD1` 只进 book/trades 做对照观测（点差 0.54/1.13bp，
    #   但 USDT 版只有 0.012/0.038bp ⇒ 走深度没有意义，先只看数据）。
    [string]$Symbols = 'BTCUSDT,ETHUSDT,PUMPUSDT,PLAYUSDT,USUSDT,DRAMUSDT,AVGOUSDT,HUMAUSDT,NATGASUSDT,BCHUSDT,PONSUSDT,METUSDT,ICPUSDT,TRUMPUSDT,LINKUSDT,GRAMUSDT,CASHCATUSDT,AAVEUSDT,FETUSDT,SYNUSDT,VVVUSDT,INJUSDT,DOSUSDT,MRNAUSDT,SEIUSDT,PENGUUSDT,KITEUSDT,SOLUSD1,BTCUSD1,ETHUSD1',
    [string]$DepthSymbols = 'BTCUSDT,ETHUSDT,PUMPUSDT,PLAYUSDT',
    [switch]$Once
)

$ErrorActionPreference = 'SilentlyContinue'

# ⚠️ `$PSScriptRoot` 在 `powershell -File` 下**可能为空**。这里做多重回退，
#    并在拿到空值时直接失败退出——**宁可不启动，也不能带着空路径"以为在守护"**。
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
if (-not (Test-Path $ScriptDir)) {
    Write-Host "[depth-watchdog] FATAL: cannot resolve script dir (PSScriptRoot empty)" -ForegroundColor Red
    exit 9
}
$RepoRoot = Split-Path $ScriptDir -Parent
$ResearchDir = Join-Path (Split-Path $RepoRoot -Parent) 'research_l1'
$LogDir = Join-Path $RepoRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'aster-depth-watchdog.log'
$LockFile = Join-Path $LogDir 'aster-depth-watchdog.lock'

function Write-DwLog([string]$msg) {
    $line = '{0} [depth-watchdog] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor DarkCyan
}

# 单实例锁（仅对常驻模式有意义；-Once 允许与常驻实例并存以便手动排查）
if (-not $Once) {
    try {
        if (Test-Path $LockFile) {
            $oldPid = 0
            try { $oldPid = [int](Get-Content $LockFile -Raw).Trim() } catch { $oldPid = 0 }
            if ($oldPid -gt 0 -and $oldPid -ne $PID) {
                if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
                    Write-DwLog "another resident instance running (pid=$oldPid) - exit"
                    exit 0
                }
            }
        }
        Set-Content -Path $LockFile -Value "$PID" -Encoding ASCII
    } catch { }
}

function Resolve-Py {
    if ($PythonExe -and (Test-Path $PythonExe)) { return $PythonExe }
    foreach ($c in @(
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.runtime\Python312\python.exe')
    )) { if (Test-Path $c) { return $c } }
    return 'python'
}
$Py = Resolve-Py

function Get-IngestProcs {
    # [R233] 也要认**待命替换件** `standby_ingest.py` ✓ —— 原件被误删期间（见误删事故记录），
    # 采集由替换件承担 ⇒ 若只认 `aster_ws_ingest`，看门狗会把"采集正常"误判成
    # "no ingest process" ✗ 并反复尝试启动一个**不存在的脚本**（实测每分钟刷一条日志 ✗）。
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*aster_ws_ingest*' -or $_.CommandLine -like '*standby_ingest*' }
}

function Start-Ingest([string]$reason) {
    # [h772 2026-10-03] **本函数曾是"每分钟一个黑框"的真凶**:
    #   `Start-Process -FilePath <venv python> -WindowStyle Hidden` 对控制台程序
    #   **不生效**(PowerShell 已知行为),而本看门狗 interval=60s ⇒ 每轮拉一次就弹一次。
    # 现在统一走 start-ingest-hidden.vbs(WScript.Shell.Run 的 SW_HIDE 对控制台有效,
    # 且直启基础解释器、不经 venv stub ⇒ 不会派生新的可见控制台)。
    Write-DwLog "starting ingest ($reason)"
    Write-DwLog "  book/trades symbols=$($Symbols.Split(',').Count)  depth symbols=$($DepthSymbols.Split(',').Count)"
    $vbs = 'D:\001Alpha\Hyper-Alpha-Arena\scripts\start-ingest-hidden.vbs'
    if (Test-Path $vbs) {
        Start-Process -FilePath 'wscript.exe' -ArgumentList '//B', '//Nologo', $vbs -WindowStyle Hidden | Out-Null
        return
    }
    Write-DwLog "ERR hidden launcher missing: $vbs"
}

Write-DwLog "watchdog start  python=$Py  interval=${IntervalSec}s  stale=${StaleSec}s  once=$Once"

do {
    try {
        $procs = @(Get-IngestProcs)
        $n = $procs.Count

        # ---- 深度年龄探针（**内联，不封装成函数**）----
        #
        # ⚠️ 这段**故意内联**：封装成 `function Get-DepthAgeSec` 时，函数体内的
        #    路径变量恒为空串（实测：顶层同样写法正常、函数体内 Join-Path 与
        #    字符串拼接与写死字面量**全部**返回空），导致探针静默不执行
        #    ⇒ 拿不到年龄 ⇒ 曾被当成"停采 999999s" ⇒ **看门狗每 2 分钟杀掉健康采集**。
        #    该现象无法用变量遮蔽/属性访问/注释截断解释（函数体完整、括号平衡）。
        #    ⇒ 内联后行为正确。**不要"整理"回函数**，除非先验证过。
        $age = $null
        $probe = 'D:\001Alpha\Hyper-Alpha-Arena\scripts\depth_age_probe.py'
        if (Test-Path $probe) {
            $out = ("$(& $Py $probe 2>&1)").Trim()
            $rc = $LASTEXITCODE
            if ($rc -eq 0 -and $out -notmatch '^ERR') {
                $tmpv = 0
                if ([int]::TryParse($out, [ref]$tmpv)) { $age = $tmpv }
                else { Write-DwLog "probe unparsable: '$out' -> UNKNOWN" }
            } else {
                Write-DwLog "probe FAILED (exit=$rc): $out -> UNKNOWN"
            }
        } else {
            Write-DwLog "probe missing: $probe -> UNKNOWN"
        }

        if ($null -eq $age) {
            # 取数失败 ⇒ 状态未知 ⇒ **绝不动进程**（宁可不作为，也不误杀）
            Write-DwLog "skip: depth age UNKNOWN, procs=$n - no action"
        } elseif ($n -eq 0) {
            Write-DwLog "no ingest process (depth age=${age}s) -> start"
            Start-Ingest "no process"
        } elseif ($age -gt $StaleSec) {
            Write-DwLog "depth STALE ${age}s > ${StaleSec}s with $n process(es) -> restart"
            # [R99 2026-09-29] **脚本不存在时绝不杀进程**：本看门狗的原则是
            # "宁可不作为，也不误杀"（见上面 age=UNKNOWN 分支），但"先杀后启"在
            # `aster_ws_ingest.py` 缺失时会退化成**只杀不启** ✗ —— 而采集器**在 WS
            # 重连时会自愈**，杀掉它是纯损失（且会让全链路永久断流）。
            # ⇒ 杀之前先确认脚本在；不在就只记日志、不动进程。
            $ingestScript = Join-Path $ResearchDir 'services\aster_ws_ingest.py'
            if (-not (Test-Path $ingestScript)) {
                Write-DwLog ("skip restart: ingest script MISSING ($ingestScript) - " +
                             "NOT killing live ingesters (they self-heal on WS reconnect)")
            } else {
                foreach ($p in $procs) {
                    Write-DwLog "  killing PID $($p.ProcessId)"
                    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
                }
                Start-Sleep -Seconds 5
                Start-Ingest "stale ${age}s"
            }
        } else {
            Write-DwLog "ok  procs=$n  depth_age=${age}s"
        }
    } catch {
        # 常驻进程**不能**被单次异常杀死
        Write-DwLog ("loop error (continuing): " + $_.Exception.Message)
    }

    if (-not $Once) { Start-Sleep -Seconds $IntervalSec }
} while (-not $Once)

Write-DwLog "watchdog exit (once mode)"
