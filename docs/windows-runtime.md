# Windows 运行时：解释器被策略拦截

## 症状

定时任务到点执行，但每次都在启动子进程时失败：

```
OSError: [WinError 4551] 应用程序控制策略已阻止此文件。
```

日志里连续多个时点同一报错，蓝牙、数据源均正常——因为**子进程根本没起来**。

## 判定

先分辨是「哪个可执行文件」被拦，两个都要测：

```python
import subprocess, sys, os
base = os.path.dirname(sys.executable)
for exe in ("python.exe", "pythonw.exe"):
    try:
        subprocess.run([os.path.join(base, exe), "-c", "import sys; sys.exit(0)"],
                       timeout=20, creationflags=0x08000000)
        print(exe, "OK")
    except OSError as e:
        print(exe, e.winerror, e.strerror)
```

常见结果是 `python.exe` 通过、`pythonw.exe` 被拦。两者是独立文件，**独立判定**。

事件日志可提供策略来源：

```powershell
Get-WinEvent -LogName "Microsoft-Windows-CodeIntegrity/Operational" -MaxEvents 5 |
  Select-Object TimeCreated, Id, Message | Format-List
```

事件 **3077 / 3033** 会写明「attempted to load …pythonw.exe that did not meet the
signing level requirements or violated code integrity policy」，并给出 Policy ID。

## 根因

拦截来自 **Smart App Control**（`HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy`
的 `VerifiedAndReputablePolicyState = 1` 表示强制模式），它用 WDAC 机制实现，但策略由微软
云端托管。

两个必须知道的推论：

1. **不能加白名单。** SAC 不支持补充策略；SAC 开启时其它 App Control 策略不被评估。
   想放行只能关闭 SAC，而关闭不可逆（恢复需重装系统）。
2. **判定是动态的。** 同一文件昨天还能启动，今天重启后就被拦——云端信誉会变。
   所以「之前一直好用」不能作为「以后也没问题」的依据。

## 解法：换成有签名的解释器

python.org 的发行版有 Python Software Foundation 的代码签名，通常可直接通过 SAC。
用 winget 安装，或从官网下载：

```powershell
winget install Python.Python.3.13
```

验证签名后再使用：

```powershell
$p = "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe"
Get-AuthenticodeSignature $p | Select-Object Status, @{n='Signer';e={$_.SignerCertificate.Subject}}
# 期望 Status=Valid，Signer 含 Python Software Foundation
```

装完需重新安装依赖：`python.exe -m pip install -r requirements.txt`。

## 顺带解决「不想要控制台窗口」

用 `pythonw.exe` 启动守护进程即无窗口：

```bat
start "" "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" "%~dp0main.py" --daemon
```

若只能退回 `python.exe`（签名版不可用时），退而求其次用最小化窗口：

```bat
start "daemon" /min "%PY%" "%~dp0main.py" --daemon
```

**不要用 `taskkill /f /im pythonw.exe` 清理旧实例**——它会杀掉机器上所有无控制台
Python 进程，包括其它应用。重复实例交给程序自身处理（端口互斥 + PID 文件接管）。

## 其它注意

- `pythonw.exe` 下 `sys.stdout` 为 `None`，任何 `print()` 都会抛异常。
  日志函数必须判断：`if sys.stdout: print(...)`，并始终写文件。
- 应用控制策略拦截的文件若在 PATH 中，也可能影响 `pip`、`git` 等工具链，排查时一并测试。
