param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [string]$ReportPath = ''
)
# Local-only ZIP diagnostic. No upload, extraction, executable execution or
# SQLite changes. The report never includes raw trade rows or API credentials.
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
$results=New-Object 'System.Collections.Generic.List[string]'
function Add-Result([string]$line) {
    if($results.Count -lt 500) { [void]$results.Add($line) }
}
$inputPath=[System.IO.Path]::GetFullPath($ZipPath)
if([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath=$inputPath+'.DIAG.txt'
}
Add-Result 'ARTITRADE_LOCAL_ZIP_DIAGNOSTIC_V1'
Add-Result 'RAW_ZIP_UPLOADED=false'
Add-Result 'ORIGINAL_ZIP_CHANGED=false'
Add-Result 'TRADE_ROWS_INCLUDED=false'
$fs=$null
$archive=$null
try {
    if(-not [System.IO.File]::Exists($inputPath)) {
        throw 'ZIP_FILE_NOT_FOUND'
    }
    $file=Get-Item -LiteralPath $inputPath
    Add-Result ('ZIP_BYTES='+$file.Length)
    Add-Result ('ZIP_SHA256='+(Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLowerInvariant())
    $fs=[System.IO.File]::Open(
        $inputPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite
    )
    $archive=New-Object System.IO.Compression.ZipArchive(
        $fs,[System.IO.Compression.ZipArchiveMode]::Read,$false
    )
    $entries=@($archive.Entries)
    Add-Result ('ZIP_DIRECTORY=READABLE')
    Add-Result ('ZIP_ENTRY_COUNT='+$entries.Count)
    $totalUncompressed=[double]0
    $totalCompressed=[double]0
    $readErrors=0
    $checkedCount=0
    $partialCount=0
    $sidecarCount=0
    $types=@{}
    # Never fully unpack an unknown ZIP or write its SQLite contents.
    # Bound the byte work. Full inspection is possible from a later
    # machine-side review, but an incomplete scan is NEVER called sound.
    [long]$readBudget=536870912
    [long]$inspectedBytes=0
    foreach($entry in $entries) {
        if([string]::IsNullOrWhiteSpace($entry.Name)){continue}
        $totalUncompressed += [double]$entry.Length
        $totalCompressed += [double]$entry.CompressedLength
        $ext=[System.IO.Path]::GetExtension($entry.Name).ToLowerInvariant()
        if([string]::IsNullOrEmpty($ext)){$ext='[none]'}
        if(-not $types.ContainsKey($ext)){$types[$ext]=0}
        $types[$ext]++
        if($entry.Name -like '*_ANALYSIS_READY.json' -or
           $entry.FullName -eq 'analysis/bundle_index.json') {
            $sidecarCount++
        }
        if($readErrors -ge 20) { $partialCount++;continue }
        if($inspectedBytes -ge $readBudget) { $partialCount++;continue }
        # Excessively expanded archive members can be malicious; do not
        # allocate their uncompressed size into PowerShell memory.
        if($entry.Length -gt 536870912 -or
           ($entry.CompressedLength -gt 0 -and
            $entry.Length -gt ($entry.CompressedLength*2000))) {
            $partialCount++
            continue
        }
        $in=$null
        try {
            $in=$entry.Open()
            $buffer=New-Object byte[] 65536
            [long]$bytesRead=0
            while(($read=$in.Read($buffer,0,$buffer.Length)) -gt 0) {
                $bytesRead += [long]$read
                $inspectedBytes += [long]$read
                if($inspectedBytes -gt $readBudget) {break}
            }
            if($bytesRead -eq [long]$entry.Length -and
               $inspectedBytes -le $readBudget) {
                $checkedCount++
            } else {
                $partialCount++
            }
        } catch {
            $readErrors++
            $nameHash=[System.Security.Cryptography.SHA256]::Create()
            try {
                $nameBytes=[System.Text.Encoding]::UTF8.GetBytes($entry.FullName)
                $fingerprint=[System.BitConverter]::ToString(
                    $nameHash.ComputeHash($nameBytes)).Replace('-','').Substring(0,12)
            } finally {$nameHash.Dispose()}
            Add-Result ('BAD_MEMBER_NAME_HASH='+$fingerprint)
            Add-Result ('BAD_MEMBER_ERROR_TYPE='+$_.Exception.GetType().Name)
        } finally {
            if($null -ne $in){$in.Dispose()}
        }
    }
    Add-Result ('UNCOMPRESSED_TOTAL_BYTES='+[string][long]$totalUncompressed)
    Add-Result ('COMPRESSED_SUM_BYTES='+[string][long]$totalCompressed)
    Add-Result ('VALIDATED_MEMBERS='+$checkedCount)
    Add-Result ('PARTIAL_OR_SKIPPED_MEMBERS='+$partialCount)
    Add-Result ('UNREADABLE_MEMBERS='+$readErrors)
    Add-Result ('ANALYSIS_SIDECARS_FOUND='+$sidecarCount)
    foreach($key in @($types.Keys|Sort-Object)) {
        Add-Result ('MEMBER_EXTENSION_COUNT_'+$key+'='+$types[$key])
    }
    if($readErrors -gt 0) {
        Add-Result 'RESULT=ZIP_MEMBER_READ_ERROR'
    } elseif($partialCount -gt 0) {
        Add-Result 'RESULT=ZIP_DIRECTORY_READABLE_CONTENT_PARTIALLY_CHECKED'
    } else {
        Add-Result 'RESULT=ZIP_MEMBERS_READABLE_WITHIN_LOCAL_CHECK_LIMIT'
    }
} catch {
    Add-Result 'RESULT=ZIP_OPEN_OR_DIRECTORY_ERROR'
    Add-Result ('ERROR_TYPE='+$_.Exception.GetType().Name)
    # Never emit complete local user filenames or secret-bearing content.
} finally {
    if($null -ne $archive){$archive.Dispose()}
    if($null -ne $fs){$fs.Dispose()}
    [System.IO.File]::WriteAllLines(
        [System.IO.Path]::GetFullPath($ReportPath),
        $results,[System.Text.UTF8Encoding]::new($false)
    )
}
Write-Output ('DIAGNOSTIC_SAVED='+[System.IO.Path]::GetFullPath($ReportPath))
Write-Output 'Upload only the small .DIAG.txt or copy and paste its text.'
