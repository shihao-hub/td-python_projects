# ============================================================
# setup.ps1 —— 一键初始化学习库 learn_pg（可重复执行 = 完全重置）
# 建库 → 建表 → 灌 100 万订单 + 200 万明细，预计 1~2 分钟
#
# 用法（在本目录打开 PowerShell）：
#   .\setup.ps1                          # 默认 postgres@localhost:5432
#   .\setup.ps1 -DbUser postgres         # 指定用户
#
# 密码读取顺序：环境变量 PGPASSWORD > %APPDATA%\postgresql\pgpass.conf > 启动时提示输入
# ============================================================
param(
    [string]$DbHost = "localhost",
    [int]   $Port   = 5432,
    [string]$DbUser = "postgres",
    [string]$DbName = "learn_pg"
)

$ErrorActionPreference = 'Stop'
chcp 65001 | Out-Null            # 控制台切 UTF-8，避免中文乱码
$env:PGCLIENTENCODING = 'UTF8'

# ---- 1. 定位 psql ----
$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql) {
    $psql = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $psql) { throw "找不到 psql。请确认已安装 PostgreSQL，并把 <安装目录>\bin 加入 PATH" }
Write-Host "使用 psql: $psql"

# ---- 2. 准备密码 ----
if (-not $env:PGPASSWORD -and -not (Test-Path "$env:APPDATA\postgresql\pgpass.conf")) {
    $sec = Read-Host "请输入 PostgreSQL 用户 $DbUser 的密码" -AsSecureString
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

# ---- 3. 依次执行三步 SQL ----
function Invoke-Sql {
    param([string]$File, [string]$Db)
    Write-Host "`n>>> 执行 $File （库: $Db）" -ForegroundColor Cyan
    & $psql -h $DbHost -p $Port -U $DbUser -d $Db -v ON_ERROR_STOP=1 -f (Join-Path $PSScriptRoot $File)
    if ($LASTEXITCODE -ne 0) { throw "$File 执行失败（退出码 $LASTEXITCODE）" }
}

Invoke-Sql '01_create_db.sql' 'postgres'   # 建库（连接默认库执行）
Invoke-Sql '02_schema.sql'    $DbName      # 建表
Invoke-Sql '03_seed.sql'      $DbName      # 灌数据

# ---- 4. 验收 ----
Write-Host "`n>>> 验收：各表行数（orders 应约 100 万）" -ForegroundColor Cyan
& $psql -h $DbHost -p $Port -U $DbUser -d $DbName -v ON_ERROR_STOP=1 -c `
    "SELECT 'users' AS tbl, count(*) FROM users UNION ALL SELECT 'products', count(*) FROM products UNION ALL SELECT 'orders', count(*) FROM orders UNION ALL SELECT 'order_items', count(*) FROM order_items;"
if ($LASTEXITCODE -ne 0) { throw "验收查询失败" }

Write-Host "`n完成！日常连接方式："
Write-Host "  psql -h $DbHost -p $Port -U $DbUser -d $DbName"
