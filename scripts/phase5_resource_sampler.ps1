[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [int]$IntervalMilliseconds = 1000
)

$frequency = [Diagnostics.Stopwatch]::Frequency
$previousTime = [Diagnostics.Stopwatch]::GetTimestamp()
$previousCpu = $null

while ($true) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $now = [Diagnostics.Stopwatch]::GetTimestamp()
    $utc = [DateTime]::UtcNow.ToString('o')
    $cpuPercent = $null
    $rssMb = $null

    if ($null -ne $process) {
        $rssMb = [Math]::Round($process.WorkingSet64 / 1MB, 2)
        if ($null -ne $previousCpu) {
            $elapsedSeconds = ($now - $previousTime) / $frequency
            if ($elapsedSeconds -gt 0) {
                $cpuSeconds = $process.CPU - $previousCpu
                $cpuPercent = [Math]::Round(($cpuSeconds / $elapsedSeconds) / [Environment]::ProcessorCount * 100, 2)
            }
        }
        $previousCpu = $process.CPU
    }

    $sample = [ordered]@{
        timestamp_utc = $utc
        monotonic_timestamp = [Math]::Round($now / $frequency, 6)
        pid = $ProcessId
        process_name = if ($null -ne $process) { $process.ProcessName } else { 'exited' }
        cpu_percent = $cpuPercent
        rss_mb = $rssMb
    }
    ($sample | ConvertTo-Json -Compress) | Add-Content -LiteralPath $OutputPath -Encoding utf8
    $previousTime = $now
    Start-Sleep -Milliseconds $IntervalMilliseconds
}
