param([string]$DeployName)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

go build -trimpath -ldflags "-s -w" -o (Join-Path $root "launcher.exe") .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "built: $(Join-Path $root 'launcher.exe')"

if ($DeployName) {
    $targetDir = Join-Path (Split-Path $root -Parent) $DeployName
    if (-not (Test-Path (Join-Path $targetDir "pyproject.toml"))) {
        throw "target project not found: $targetDir"
    }
    $dst = Join-Path $targetDir "$DeployName.exe"
    Copy-Item (Join-Path $root "launcher.exe") $dst -Force
    Write-Output "deployed: $dst"
}
