[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$venvPath = Join-Path $repositoryRoot ".venv"
$venvPythonPath = Join-Path $venvPath "Scripts\python.exe"
$envExamplePath = Join-Path $repositoryRoot ".env.example"
$envPath = Join-Path $repositoryRoot ".env"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter()][string[]]$ArgumentList = @()
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($ArgumentList -join ' ')"
    }
}

function Invoke-Python312 {
    param(
        [Parameter()][string[]]$ArgumentList = @()
    )

    if ($pythonLauncher) {
        Invoke-Checked -FilePath $pythonLauncher -ArgumentList (@("-3.12") + $ArgumentList)
    } else {
        Invoke-Checked -FilePath $pythonCommand -ArgumentList $ArgumentList
    }
}

$pythonLauncher = $null
$pythonCommand = $null
$pyCommandInfo = Get-Command py -ErrorAction SilentlyContinue
if ($pyCommandInfo) {
    try {
        $pythonVersion = (& $pyCommandInfo.Source -3.12 --version 2>&1 | Out-String).Trim()
    } catch {
        $pythonVersion = ""
    }
    if ($pythonVersion -match "^Python 3\.12\.") {
        $pythonLauncher = $pyCommandInfo.Source
    }
}

if (-not $pythonLauncher) {
    $python312CommandInfo = Get-Command python3.12 -ErrorAction SilentlyContinue
    if ($python312CommandInfo) {
        $pythonVersion = (& $python312CommandInfo.Source --version 2>&1 | Out-String).Trim()
        if ($pythonVersion -match "^Python 3\.12\.") {
            $pythonCommand = $python312CommandInfo.Source
        }
    }
}

if (-not $pythonLauncher -and -not $pythonCommand) {
    $pythonCommandInfo = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommandInfo) {
        $pythonVersion = (& $pythonCommandInfo.Source --version 2>&1 | Out-String).Trim()
        if ($pythonVersion -match "^Python 3\.12\.") {
            $pythonCommand = $pythonCommandInfo.Source
        }
    }
}

if (-not $pythonLauncher -and -not $pythonCommand) {
    throw "Python 3.12 is required. Install Python 3.12 and rerun this script."
}

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
    Write-Host "Created .env from .env.example. Review it before sharing credentials."
} else {
    Write-Host ".env already exists; leaving it unchanged."
}

if (-not (Test-Path -LiteralPath $venvPythonPath)) {
    Invoke-Python312 -ArgumentList @("-m", "venv", $venvPath)
}

$venvVersion = (& $venvPythonPath --version 2>&1 | Out-String).Trim()
if ($venvVersion -notmatch "^Python 3\.12\.") {
    throw "Existing .venv is not Python 3.12 ($venvVersion). Recreate it manually and rerun this script."
}

Invoke-Checked -FilePath $venvPythonPath -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Checked -FilePath $venvPythonPath -ArgumentList @("-m", "pip", "install", "-e", (Join-Path $repositoryRoot "backend[dev]"))

Push-Location $repositoryRoot
try {
    Push-Location (Join-Path $repositoryRoot "backend")
    try {
        $dependencyCheckScript = @'
import asyncio
import json

from app.core.config import Settings
from app.services.infrastructure import Infrastructure


async def main() -> int:
    infrastructure = None
    try:
        infrastructure = Infrastructure(Settings())
        result = await infrastructure.check_readiness()
    except Exception as error:  # noqa: BLE001 - bootstrap must report a safe error
        result = {
            "status": "not_ready",
            "dependencies": {
                "postgres": {"status": "error", "error": type(error).__name__},
                "redis": {"status": "error", "error": type(error).__name__},
            },
        }
    finally:
        if infrastructure is not None:
            await infrastructure.close()
    print(json.dumps(result, sort_keys=True))
    return 0


raise SystemExit(asyncio.run(main()))
'@
        $dependencyOutput = & $venvPythonPath -c $dependencyCheckScript 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to check PostgreSQL and Redis using DATABASE_URL and REDIS_URL."
        }

        try {
            $dependencyReport = ($dependencyOutput | Select-Object -Last 1 | ConvertFrom-Json)
        } catch {
            throw "The dependency check returned an invalid status response."
        }

        $postgresReady = $dependencyReport.dependencies.postgres.status -eq "ok"
        $redisReady = $dependencyReport.dependencies.redis.status -eq "ok"
        Write-Host "PostgreSQL: $($dependencyReport.dependencies.postgres.status)"
        Write-Host "Redis: $($dependencyReport.dependencies.redis.status)"
        if ($dependencyReport.dependencies.postgres.error) {
            Write-Host "PostgreSQL error: $($dependencyReport.dependencies.postgres.error)"
        }
        if ($dependencyReport.dependencies.redis.error) {
            Write-Host "Redis error: $($dependencyReport.dependencies.redis.error)"
        }

        if ($postgresReady) {
            Invoke-Checked -FilePath $venvPythonPath -ArgumentList @("-m", "alembic", "upgrade", "head")
        } else {
            Write-Warning "PostgreSQL is unavailable; Alembic migrations were skipped."
        }

        if (-not $redisReady) {
            Write-Warning "Redis is unavailable; readiness cannot pass."
        }

        if (-not $postgresReady -or -not $redisReady) {
            throw "Local services are not ready. Start PostgreSQL and Redis, verify DATABASE_URL and REDIS_URL, then rerun bootstrap."
        }

        Write-Host "Bootstrap completed: local PostgreSQL, pgvector migration, and Redis are verified."
    } finally {
        Pop-Location
    }
} finally {
    Pop-Location
}
