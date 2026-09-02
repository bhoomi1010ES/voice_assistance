[CmdletBinding()]
param(
    [string]$DotnetPath,
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [string]$RuntimeIdentifier = 'win-x64'
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectPath = Join-Path $repoRoot 'backend\windows_stt\WindowsSttWorker.csproj'
$publishPath = Join-Path $repoRoot 'backend\windows_stt\publish'

if (-not $DotnetPath) {
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($dotnetCommand) {
        $DotnetPath = $dotnetCommand.Source
    }
}

if (-not $DotnetPath -or -not (Test-Path -LiteralPath $DotnetPath -PathType Leaf)) {
    throw 'A .NET SDK is required to publish the worker. Install the .NET 8 SDK or pass -DotnetPath C:\path\to\dotnet.exe. The deployed worker itself is self-contained and needs no SDK.'
}

& $DotnetPath publish $projectPath `
    --configuration $Configuration `
    --runtime $RuntimeIdentifier `
    --self-contained true `
    --output $publishPath `
    --nologo

if ($LASTEXITCODE -ne 0) {
    throw "Windows STT worker publish failed with exit code $LASTEXITCODE."
}

$workerPath = Join-Path $publishPath 'WindowsSttWorker.exe'
if (-not (Test-Path -LiteralPath $workerPath -PathType Leaf)) {
    throw "Publish reported success but worker executable is missing: $workerPath"
}

Write-Host "Windows STT worker published: $workerPath"
