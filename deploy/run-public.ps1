# Run the full app from this machine and publish it through a free Cloudflare quick
# tunnel. No account, no card. The public URL is printed below and changes every time
# this script is restarted.
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-public.ps1
#
# Stop with Ctrl+C. That closes the tunnel and the API; nothing stays exposed.
# The API itself still listens on 127.0.0.1 only; the tunnel is the only way in.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$port = if ($env:WTD_PORT) { $env:WTD_PORT } else { '8020' }
$env:GEMMA_MODEL = if ($env:GEMMA_MODEL) { $env:GEMMA_MODEL } else { 'gemma4:e2b' }
$env:WTD_PUBLIC = '1'
$soffice = Join-Path $root '.tools\lo\program\soffice.exe'
if (Test-Path $soffice) { $env:SOFFICE_PATH = $soffice }

# 1. Ollama must be running with the model installed.
try { Invoke-RestMethod http://127.0.0.1:11434/api/version | Out-Null }
catch { Write-Host 'Starting Ollama...'; Start-Process ollama -ArgumentList serve -WindowStyle Hidden; Start-Sleep 5 }
if (-not ((ollama list) -match [regex]::Escape($env:GEMMA_MODEL))) { throw "Model $env:GEMMA_MODEL is not installed: ollama pull $env:GEMMA_MODEL" }

# 2. Build the interface if it is missing.
if (-not (Test-Path 'frontend\dist\index.html')) { Push-Location frontend; npm ci; npm run build; Pop-Location }

# 3. Keep the PC awake while this runs (restored when the script exits).
Add-Type -Namespace W -Name P -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[W.P]::SetThreadExecutionState([uint32]'0x80000001') | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

New-Item -ItemType Directory -Force work | Out-Null
$api = Start-Process -PassThru -NoNewWindow -FilePath '.venv\Scripts\python.exe' `
    -ArgumentList 'api.py', '--port', $port, '--static', 'frontend\dist', '--root', 'work\public' `
    -RedirectStandardOutput 'work\public-api.out.log' -RedirectStandardError 'work\public-api.log'
$tunnel = $null
try {
    for ($i = 0; $i -lt 30; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:$port/api/health" | Out-Null; break } catch { Start-Sleep 1 }
    }
    Write-Host "API up on 127.0.0.1:$port (public mode). Opening tunnel..."
    $tunnel = Start-Process -PassThru -NoNewWindow -FilePath 'cloudflared' `
        -ArgumentList 'tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$port" `
        -RedirectStandardError 'work\public-tunnel.log' -RedirectStandardOutput 'work\public-tunnel.out.log'
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep 1
        $m = Select-String -Path 'work\public-tunnel.log' -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
    if (-not $url) { throw 'Tunnel did not report a URL; see work\public-tunnel.log' }
    Set-Content -Path 'work\public-url.txt' -Value $url -Encoding utf8
    Write-Host ''
    Write-Host "  PUBLIC URL:  $url" -ForegroundColor Green
    Write-Host '  Keep this window open and the laptop plugged in. Ctrl+C to stop.'
    Write-Host ''
    while (-not $api.HasExited -and -not $tunnel.HasExited) { Start-Sleep 5 }
    Write-Host 'The API or the tunnel stopped; see work\public-api.log and work\public-tunnel.log.'
}
finally {
    foreach ($p in @($tunnel, $api)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
    [W.P]::SetThreadExecutionState([uint32]'0x80000000') | Out-Null
}
