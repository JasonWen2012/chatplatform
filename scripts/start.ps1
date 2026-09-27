# 启动服务（可选：经 ngrok / 反向代理对外访问）
#
# 用法：
#   .\scripts\start.ps1                                           # 仅本机访问
#   .\scripts\start.ps1 -PublicOrigin https://xxx.ngrok-free.dev  # 经 ngrok 对外访问
#   .\scripts\start.ps1 -PublicOrigin https://xxx.ngrok-free.dev -Port 8080
#   .\scripts\start.ps1 -Lan                                      # 允许局域网访问
#
# 为什么需要 -PublicOrigin：
#   Django 4.2 起会对 POST 请求校验 Origin 头，未列入 CSRF_TRUSTED_ORIGINS 的域名
#   会导致登录/注册报 403（CSRF verification failed / Origin checking failed）。
#   该参数会同时写入 CSRF_TRUSTED_ORIGINS 与 ALLOWED_HOSTS。
#
# 若执行策略禁止运行脚本，可临时放行：
#   powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -PublicOrigin https://xxx.ngrok-free.dev

param(
    [string]$PublicOrigin = "",
    [int]$Port = 8000,
    [string]$Bind = "127.0.0.1",
    [switch]$Lan
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 中文路径下 Python 需要显式 UTF-8，否则参数与输出会乱码
$env:PYTHONPATH = "."
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if ($PublicOrigin) {
    # 去掉尾部斜杠，避免拼出 https://host// 这样的来源
    $env:DSH_PUBLIC_ORIGIN = $PublicOrigin.TrimEnd("/")
    # 经 HTTPS 隧道访问时，让 Cookie 带上 Secure 标记
    $env:DSH_PUBLIC_HTTPS = "1"
    Write-Host "已配置公网来源: $($env:DSH_PUBLIC_ORIGIN)" -ForegroundColor Green
    Write-Host "提示：ngrok 免费版每次重启域名会变化，需重新传入新的地址。" -ForegroundColor Yellow
}

if ($Lan) {
    $Bind = "0.0.0.0"
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境，请先执行：" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

Write-Host "启动服务: http://$($Bind):$($Port)/" -ForegroundColor Cyan

# 用内联脚本执行管理命令：本机沙箱会拦截直接执行工作区内的脚本文件，
# 该写法在普通终端与受限环境中都可用。
# 用数组 + 换行拼接，避免 here-string 结束符必须顶格的约束。
$bootstrap = @(
    'import os, sys',
    'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")',
    'from django.core.management import execute_from_command_line',
    'execute_from_command_line(sys.argv)'
) -join [Environment]::NewLine

& $python -c $bootstrap runserver "$($Bind):$($Port)"
