# Win11 部署指南

本文用于把当前 `llm-token-router` 源码包部署到 Windows 11 机器上。

## 假设

- 部署机是 Windows 11 x64。
- 部署机可以访问 PyPI，以及你在 `.env` 中配置的上游模型供应商。
- 使用 Python 3.11 或更新版本，优先使用 python.org 安装包自带的 `py` launcher。
- 默认只监听 `127.0.0.1:8000`，只给本机调用；需要局域网访问时再改监听地址和防火墙。
- 部署包不携带本机 secrets、运行数据库、日志或虚拟环境。

## 成功标准

- `.\.venv\Scripts\python.exe -m pip check` 通过。
- `config.yaml` 能被加载。
- `GET http://127.0.0.1:8000/health` 返回 `{"status":"ok"}`。
- 首次真实模型请求后，`/admin/usage` 能看到请求次数或 token 用量变化。

## 1. 解压源码包

假设包名是 `llm-token-router-win11-YYYYMMDD.zip`，放在 `C:\Deploy`：

```powershell
New-Item -ItemType Directory -Force C:\apps | Out-Null
Expand-Archive C:\Deploy\llm-token-router-win11-YYYYMMDD.zip -DestinationPath C:\apps -Force
Set-Location C:\apps\llm-token-router
```

如果你解压到了其它目录，后续命令都在实际项目根目录执行。

## 2. 创建项目虚拟环境

先确认本机 Python：

```powershell
py -0p
```

创建项目本地 `.venv`。如果没有 3.13，可以换成 `py -3.12` 或 `py -3.11`：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip check
```

成功时 `pip check` 输出：

```text
No broken requirements found.
```

## 3. 创建本机配置

```powershell
Copy-Item .\config.example.yaml .\config.yaml
Copy-Item .\.env.example .\.env
notepad .\.env
```

按实际账号填写这些变量：

- `MIMO_TOKEN_PLAN_BASE_URL`
- `MIMO_TOKEN_PLAN_KEY`
- `MIMO_TOKEN_PLAN_MODEL`
- `ARK_BASE_URL`
- `ARK_API_KEY`
- `ARK_MODEL`
- `AGNES_BASE_URL`
- `AGNES_API_KEY`
- `AGNES_MODEL`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_API_KEY`
- `OPENROUTER_API_KEY_2`
- `OPENROUTER_FREE_MODEL`
- `TAVILY_API_KEY`

`TAVILY_API_KEY` 只影响每日模型评测；如果暂时不用每日评测，可以先保留占位值，但相关报告不会正常生成。

如需调整供应商、模型优先级、配额、并发或 fallback 级别，编辑 `config.yaml`。

加载配置做一次本地校验：

```powershell
.\.venv\Scripts\python.exe -c "from token_router.app.config import load_config; load_config('config.yaml'); print('config ok')"
```

这一步只校验配置结构和环境变量替换，不会调用外部模型 API。

## 4. 前台启动

首次部署建议先前台启动，方便直接看错误：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\win11\run-router.ps1
```

默认监听：

```text
http://127.0.0.1:8000
```

另开一个 PowerShell 窗口检查健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

成功时返回：

```text
status
------
ok
```

停止前台服务：在运行窗口按 `Ctrl+C`。

## 5. 后台运行

前台启动确认无误后，可以用 PowerShell 后台启动：

```powershell
New-Item -ItemType Directory -Force .\logs | Out-Null
$p = Start-Process powershell.exe `
  -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\scripts\win11\run-router.ps1") `
  -WorkingDirectory (Get-Location) `
  -RedirectStandardOutput ".\logs\router.out.log" `
  -RedirectStandardError ".\logs\router.err.log" `
  -PassThru
$p.Id | Set-Content .\.router.pid
```

查看日志：

```powershell
Get-Content .\logs\router.out.log -Wait
Get-Content .\logs\router.err.log -Wait
```

停止后台服务：

```powershell
Stop-Process -Id (Get-Content .\.router.pid)
Remove-Item .\.router.pid
```

## 6. 开机自启

用 Windows 任务计划程序注册登录后自启：

```powershell
$AppDir = "C:\apps\llm-token-router"
$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\scripts\win11\run-router.ps1`"" `
  -WorkingDirectory $AppDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask `
  -TaskName "LLM Token Router" `
  -Action $Action `
  -Trigger $Trigger `
  -Description "Start local LLM token router" `
  -Force
```

立即手动启动一次：

```powershell
Start-ScheduledTask -TaskName "LLM Token Router"
Invoke-RestMethod http://127.0.0.1:8000/health
```

取消开机自启：

```powershell
Unregister-ScheduledTask -TaskName "LLM Token Router" -Confirm:$false
```

## 7. 调用方式

本机 OpenAI 兼容 Chat Completions：

```powershell
$Body = @{
  model = "auto"
  messages = @(@{ role = "user"; content = "Reply with OK." })
  router = @{
    level = 1
    provider = "auto"
    fallback = $true
    debug = $true
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/chat/completions `
  -ContentType "application/json" `
  -Body $Body
```

常用管理入口：

- 健康检查：`http://127.0.0.1:8000/health`
- 模型状态：`http://127.0.0.1:8000/admin/models`
- 用量页面：`http://127.0.0.1:8000/admin/usage`
- 日评测报告：`http://127.0.0.1:8000/admin/reports/daily-eval`

## 8. 升级部署包

升级时保留本机配置和运行数据：

```powershell
Stop-Process -Id (Get-Content .\.router.pid)
Copy-Item .\.env C:\Deploy\.env.backup -Force
Copy-Item .\config.yaml C:\Deploy\config.yaml.backup -Force
New-Item -ItemType Directory -Force C:\Deploy\data.backup | Out-Null
Copy-Item .\data\* C:\Deploy\data.backup -Recurse -Force
```

解压新包后，把备份文件放回项目根目录：

```powershell
Copy-Item C:\Deploy\.env.backup .\.env -Force
Copy-Item C:\Deploy\config.yaml.backup .\config.yaml -Force
New-Item -ItemType Directory -Force .\data | Out-Null
Copy-Item C:\Deploy\data.backup\* .\data -Recurse -Force
.\.venv\Scripts\python.exe -m pip install -e .
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\win11\run-router.ps1
```

## 9. 常见问题

`py` 找不到：
安装 python.org 版本 Python，并勾选 `py launcher`。安装后重新打开 PowerShell。

`Activate.ps1 cannot be loaded`：
本指南不要求激活虚拟环境，直接调用 `.\.venv\Scripts\python.exe` 即可。

`router config is not loaded`：
确认项目根目录存在 `config.yaml`，且后台任务的 `WorkingDirectory` 是项目根目录。

配置校验报 `KeyError`：
`.env` 缺少 `config.yaml` 中引用的变量。补齐后重启服务。

端口被占用：
改端口启动，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\win11\run-router.ps1 -Port 8010
```

需要给局域网其它机器访问：
把 `-HostAddress` 改成 `0.0.0.0`，并在 Windows 防火墙中只允许可信网段访问该端口。
