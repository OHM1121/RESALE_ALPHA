# ARTITRADE: download the newest signed APK from the GitHub release into
#   Desktop\artitrade apk\<version>.apk   (SHA-256 verified), then open that folder
# so it can be shared to the phone with "휴대폰과 연결" (Phone Link).
# Works in Windows PowerShell 5.1 and PowerShell 7.
# Usage:  powershell -ExecutionPolicy Bypass -File fetch_latest_apk.ps1
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'OHM1121/RESALE_ALPHA'
$headers = @{ 'User-Agent' = 'artitrade-fetch' }

# Releases are pre-releases, so /releases/latest does not list them: use /releases.
# NOTE: in Windows PowerShell 5.1 a JSON array piped straight into Where-Object arrives as ONE
# object, so the list is assigned to a variable and walked with foreach instead.
$all = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$repo/releases?per_page=10"
$release = $null
foreach ($candidate in $all) {
    $tag = [string]$candidate.tag_name
    if ($tag -match '^[0-9]+\.[0-9]+\.[0-9]+$') { $release = $candidate; break }
}
if ($null -eq $release) { throw 'No numeric-version release found.' }
$version = [string]$release.tag_name

$apkUrl = $null
$shaUrl = $null
foreach ($asset in $release.assets) {
    if ($asset.name -eq "$version.apk")    { $apkUrl = [string]$asset.browser_download_url }
    if ($asset.name -eq "$version.sha256") { $shaUrl = [string]$asset.browser_download_url }
}
if (-not $apkUrl -or -not $shaUrl) { throw "Release $version has no $version.apk / $version.sha256 asset." }

$folder = Join-Path ([Environment]::GetFolderPath('Desktop')) 'artitrade apk'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$apk = Join-Path $folder "$version.apk"
$shaFile = Join-Path $env:TEMP "$version.sha256"
Invoke-WebRequest -UseBasicParsing -Headers $headers -Uri $apkUrl -OutFile $apk
Invoke-WebRequest -UseBasicParsing -Headers $headers -Uri $shaUrl -OutFile $shaFile
$expected = ((Get-Content -Raw -Path $shaFile).Trim() -split '\s+')[0].ToLower()
$actual = (Get-FileHash -Algorithm SHA256 -Path $apk).Hash.ToLower()
Remove-Item $shaFile -Force -ErrorAction SilentlyContinue
if ($expected -ne $actual) {
    Remove-Item $apk -Force
    throw "SHA-256 mismatch for $version.apk (file deleted). expected=$expected actual=$actual"
}
Write-Host ""
Write-Host "OK  version=$version  sha256=$actual" -ForegroundColor Green
Write-Host "Saved file : $apk" -ForegroundColor Green
Write-Host "Folder     : $folder" -ForegroundColor Green
# The folder name contains a space: Start-Process would split it into two arguments and open the
# wrong place, so open it with Invoke-Item (and select the file when explorer allows it).
try { Invoke-Item -LiteralPath $folder } catch { Write-Host "Open the folder manually: $folder" -ForegroundColor Yellow }
try { Start-Process -FilePath 'explorer.exe' -ArgumentList ('/select,"' + $apk + '"') } catch { }
