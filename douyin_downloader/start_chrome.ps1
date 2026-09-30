param(
    [string]$Url = "https://www.douyin.com"
)

$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$profile = 'C:\Users\29580\.chrome-automation-profile'

# Ensure preferences (Downloads folder and developer mode)
$prefDir = Join-Path $profile "Default"
if (!(Test-Path $prefDir)) {
    New-Item -ItemType Directory -Path $prefDir -Force | Out-Null
}
$prefFile = Join-Path $prefDir "Preferences"
$prefs = @{}
if (Test-Path $prefFile) {
    try { $prefs = Get-Content $prefFile -Raw | ConvertFrom-Json } catch {}
}
if (!$prefs.download) {
    $prefs | Add-Member -MemberType NoteProperty -Name "download" -Value (New-Object PSObject) -Force
}
$prefs.download | Add-Member -MemberType NoteProperty -Name "default_directory" -Value "C:\Users\29580\Downloads" -Force
$prefs.download | Add-Member -MemberType NoteProperty -Name "directory_upgrade" -Value $true -Force
$prefs.download | Add-Member -MemberType NoteProperty -Name "prompt_for_download" -Value $false -Force

$prefs | ConvertTo-Json -Depth 10 | Set-Content $prefFile -Encoding UTF8

$cmdline = "`"$chrome`" --user-data-dir=`"$profile`" --no-first-run --no-default-browser-check --remote-debugging-port=9222 --remote-allow-origins=* `"$Url`""

$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline }
Write-Output ("ReturnCode: " + $res.ReturnValue + ", ProcessId: " + $res.ProcessId)
