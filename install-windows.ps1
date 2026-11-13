param([switch]$PrepareOnly)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Program,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Install-OfficialScript {
    param([Parameter(Mandatory = $true)][string]$Url, [Parameter(Mandatory = $true)][string]$Name)

    Write-Host "`nInstalling $Name..." -ForegroundColor Cyan
    $scriptPath = Join-Path $env:TEMP ("kernradio-" + [guid]::NewGuid().ToString('N') + '.ps1')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $scriptPath
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $scriptPath
        if ($LASTEXITCODE -ne 0) {
            throw "$Name installer exited with code $LASTEXITCODE."
        }
    }
    finally {
        Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
    }
}

try {
    if ([Environment]::OSVersion.Version.Major -lt 10) {
        throw 'Kern Radio requires Windows 10 or 11.'
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Kern Radio requires 64-bit Windows.'
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Install-OfficialScript 'https://astral.sh/uv/install.ps1' 'uv'
    }

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) {
        try {
            Install-OfficialScript 'https://chatgpt.com/codex/install.ps1' 'Codex CLI'
        }
        catch {
            Write-Host 'The first Codex download failed. Retrying from GitHub Releases...' -ForegroundColor Yellow
            $env:CODEX_INSTALLER_USE_RELEASES_OPENAI_COM = 'false'
            Install-OfficialScript 'https://chatgpt.com/codex/install.ps1' 'Codex CLI'
        }
    }

    # Installers add their bin folders to the user PATH. Refresh this process so
    # the commands installed above are available without asking the user to reopen Terminal.
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath"
    foreach ($bin in @((Join-Path $HOME '.local\bin'), (Join-Path $env:LOCALAPPDATA 'uv\bin'))) {
        if (Test-Path -LiteralPath $bin -PathType Container) {
            $env:Path = "$bin;$env:Path"
        }
    }
    $codexBin = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'
    if (Test-Path -LiteralPath (Join-Path $codexBin 'codex.exe') -PathType Leaf) {
        $env:Path = "$codexBin;$env:Path"
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $uv) { throw 'uv installed, but Windows could not find it. Restart Windows and double-click Start Kern Radio.bat again.' }
    if (-not $codex) { throw 'Codex CLI installed, but Windows could not find it. Restart Windows and double-click Start Kern Radio.bat again.' }

    Write-Host "`nPreparing Kern Radio (first run may take several minutes)..." -ForegroundColor Cyan
    Invoke-Native $uv.Source @('python', 'install', '3.13') 'Could not install the Python runtime.'
    Invoke-Native $uv.Source @('sync', '--locked') 'Could not install Kern Radio dependencies.'
    Invoke-Native $codex.Source @('--version') 'Codex CLI did not start correctly.'

    if ($PrepareOnly) {
        Write-Host "`nKern Radio dependencies are ready." -ForegroundColor Green
        return
    }

    Write-Host "`nStarting Kern Radio. Keep this window open; press Ctrl+C here to stop." -ForegroundColor Green
    $env:KERNRADIO_OPEN_BROWSER = '1'
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'The project Python environment was not created.' }
    & $python 'main.py'
    if ($LASTEXITCODE -ne 0) { throw 'Kern Radio stopped with an error.' }
}
catch {
    Write-Host "`nKern Radio could not start:`n$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
