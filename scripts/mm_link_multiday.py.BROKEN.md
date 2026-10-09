# scripts/mm_link_multiday.py 已损坏 —— 请勿运行

**状态**：❌ `py_compile` 失败（SyntaxError），**不可执行**
**发现时间**：2026-09-20 19:27 之后
**处置**：保留原文件不删（历史留痕），另留本说明。**不要修复它，用替代脚本。**

---

## 1. 坏了什么

| 位置 | 问题 |
|---|---|
| 第 2–5 行 | 顶部 docstring 中文全部乱码（`LINK 澶氫氦鏄撴棩澶嶆牳`） |
| **第 87 行** | **f-string 未闭合**：`print(f"{day:<12}{a3-a2:>7}  鏁版嵁涓嶈冻锛岃烦杩?)` —— 缺收尾引号与右括号 |
| 第 118 / 125 行 | 含 `\u20ac`(€) / `\uFFFD` 等替换字符，第 125 行的 f-string 同样语法错误 |
| 全文件 | 8 处乱码区段，共 12 个字符**已永久丢失** |

## 2. 根因

文件本应是 UTF-8。某个工具把它的字节按 **CP936(GBK)** 解码成字符串，再以 UTF-8 写回
⇒ 磁盘内容变成 mojibake。还原时：

```
bytes.fromhex(磁盘) → decode('utf-8') → encode('cp936') → decode('utf-8')
```

我用 `scripts/_fix_mojibake.py` 跑了这个逆运算，**成功还原 56 个字符**
（例如 `鈬?→ ⇒`、`脳 → ×`、`鈮?→ ≥`）。

**但仍有 12 个字符不可逆**：原始解码把无法映射的字节替换成了 `?`
（乱码里 `锛?` 的那个 `?`），信息已经丢了。所以**不能声称修好了**。

## 3. 为什么第 87 行是最重要的证据

那一行**语法都不成立**。而 Python 是**先编译整个文件再执行**的 ⇒
`py_compile` 失败意味着这个脚本**从未成功跑过一次**。

⇒ 如果有人引用过它的输出，那个输出不可能来自本文件。
（同类教训：本仓库多次出现"脚本坏了但没人发现"，因为从没有人对它做过编译检查。）

## 4. 用什么替代

| 用途 | 用哪个 |
|---|---|
| LINK 多交易日复核（7 日 × w ∈ {25,30,35}） | **`scripts/mm_link_multiday2.py`** |
| 全仓 .py 编译体检（找同类问题） | 见下方一行命令 |

`mm_link_multiday2.py`（F299，2026-09-16）`py_compile` **通过**，功能相同，
且额外使用**注册表锚定的** `vol_baseline_bp`（与线上车道同口径），比本文件更可信。

## 5. 附：全仓编译体检（可复用）

```powershell
$files = Get-ChildItem "scripts" -Filter "*.py" -Recurse | Where-Object { $_.FullName -notmatch '__pycache__' }
$bad = foreach ($f in $files) {
  $out = & ".venv\Scripts\python.exe" -m py_compile $f.FullName 2>&1
  if ($LASTEXITCODE -ne 0) { $f.Name }
}
$bad
```

**2026-09-20 首跑结果：699 个脚本中 6 个编译失败**，其中：

- `mm_link_multiday.py`（本文件）—— 真 bug，会误导人 ✗
- `_fix126b_quotes.py` —— 一次性修复脚本
- `archive/analyze_db.py`、`archive/analyze_logs.py`、`archive/create_doc.py`、
  `archive/tmp_analyze.py` —— 归档目录，不影响运行

⇒ **`scripts/` 下只有本文件一个真问题**，其余 5 个可忽略。
