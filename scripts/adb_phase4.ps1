[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

# The installed platform-tools build ignores ANDROID_USER_HOME and reads HOME
# when locating adbkey. Keep the compatibility correction scoped to this child
# process; no user or machine environment variable is changed.
$phase4HomePath = $env:USERPROFILE
$sdkPath = $env:ANDROID_SDK_ROOT
if (-not $sdkPath) {
    $sdkPath = $env:ANDROID_HOME
}
$realAdbPath = Join-Path $sdkPath 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $realAdbPath -PathType Leaf)) {
    throw "ADB executable not found at $realAdbPath"
}
if (-not $phase4HomePath -or -not (Test-Path -LiteralPath $phase4HomePath -PathType Container)) {
    throw 'USERPROFILE must identify a writable Windows user directory for ADB.'
}

$env:HOME = $phase4HomePath
& $realAdbPath @Arguments
exit $LASTEXITCODE
