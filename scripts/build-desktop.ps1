# 一键打包 Windows 版
#
# 为什么要有这个脚本：Windows 打包必须在 Windows 上做（PyInstaller 不能交叉编译），
# 手工装环境有十几步、每步都可能踩坑。这个脚本把「装 Python、建虚拟环境、装依赖、
# 跑测试、打包、压缩」串起来，出错会停下并告诉你卡在哪一步。
#
# 用法：双击仓库根目录的 一键打包-Windows.bat

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Windows PowerShell 5.1 默认按 ANSI 代码页读脚本，中文会乱码甚至吃掉引号。
# 脚本本身存成 UTF-8 BOM（见文件头），控制台再设一次编码，两头都对齐。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Step($n, $text) { Write-Host ""; Write-Host "== $n. $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "失败：$text" -ForegroundColor Red; exit 1 }
function PyVersion($cand) {
    try {
        $code = "import sys;print(sys.version_info[0],sys.version_info[1])"
        return & cmd /c "$cand -c ""$code""" 2>$null
    } catch { return "" }
}

Step 1 "检查 Python 3.11"
$py = $null
foreach ($cand in @("py -3.11", "python3.11", "python")) {
    if ((PyVersion $cand) -match "^3 11") { $py = $cand; break }
}
if (-not $py) {
    Write-Host "没找到 Python 3.11，尝试用 winget 安装…" -ForegroundColor Yellow
    try { winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements } catch {}
    foreach ($cand in @("py -3.11", "python3.11")) {
        if ((PyVersion $cand) -match "^3 11") { $py = $cand; break }
    }
}
if (-not $py) { Fail "装不上 Python 3.11。请到 python.org 下载 3.11（安装时勾选 Add to PATH），然后重跑。" }
Write-Host "使用：$py" -ForegroundColor Green

Step 2 "创建虚拟环境 .venv-win"
if (-not (Test-Path ".venv-win")) { & cmd /c "$py -m venv .venv-win" }
$vpy = ".venv-win\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Fail "虚拟环境没建起来" }

Step 3 "安装依赖"
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install -e . -q
& $vpy -m pip install pywebview pyinstaller -q
if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败（看上面的报错，多半是网络）" }

Step 4 "跑测试"
& $vpy -m pytest -q
if ($LASTEXITCODE -ne 0) { Fail "测试没过，先别打包" }

Step 5 "打包"
& $vpy -m PyInstaller packaging\jancode-desktop.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "打包失败（看上面的报错）" }

Step 6 "压缩产物"
$out = Get-ChildItem dist -Directory | Where-Object { $_.Name -like "*JanCode*" } | Select-Object -First 1
if (-not $out) { Fail "dist 里没找到打包结果" }
$zip = "dist\JanCode-Agent-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out.FullName -DestinationPath $zip -Force

Write-Host ""
Write-Host "完成！产物：$zip" -ForegroundColor Green
Write-Host "把控制台最后 20 行发我，就能确认 Windows 版是不是真能跑。" -ForegroundColor Green
