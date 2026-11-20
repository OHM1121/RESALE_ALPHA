# ARTITRADE: download the newest signed APK from the GitHub release into
#   Desktop\artitrade apk\<version>.apk   (SHA-256 verified), then open that folder
# so it can be shared to the phone with "휴대폰과 연결" (Phone Link).
# Usage (PowerShell):  powershell -ExecutionPolicy Bypass -File fetch_latest_apk.ps1
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'OHM1121/RESALE_ALPHA'
$headers = @{ 'User-Agent' = 'artitrade-fetch' }
# Releases are pre-releases, so /releases/latest does not list them: take the newest of /releases.
$release = (Invoke-RestMethod -Headers $headers "https://api.github.com/repos/$repo/releases?per_page=5" |
    Where-Object { $_.tag_name -match '^[0-9]+\.[0-9]+\.[0-9]+$' } | Select-Object -First 1)
if (-not $release) { throw 'No numeric release found.' }
$version = $release.tag_name
$apkAsset = $release.assets | Where-Object { $_.name -eq "$version.apk" }
$shaAsset = $release.assets | Where-Object { $_.name -eq "$version.sha256" }
if (-not $apkAsset -or -not $shaAsset) { throw "Release $version has no apk/sha256 asset." }
$folder = Join-Path ([Environment]::GetFolderPath('Desktop')) 'artitrade apk'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$apk = Join-Path $folder "$version.apk"
Invoke-WebRequest -Headers $headers -Uri $apkAsset.browser_download_url -OutFile $apk
$expected = ((Invoke-WebRequest -Headers $headers -Uri $shaAsset.browser_download_url).Content -split '\s+')[0].ToLower()
$actual = (Get-FileHash -Algorithm SHA256 -Path $apk).Hash.ToLower()
if ($expected -ne $actual) { Remove-Item $apk -Force; throw "SHA-256 mismatch for $version.apk (file deleted)." }
Write-Host "OK $version  sha256=$actual"
Write-Host "Saved: $apk"
Start-Process explorer.exe $folder
