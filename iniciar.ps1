# ─────────────────────────────────────────────────────────────
#  PLATIM Agent — arranque rapido (servidor + tunel Cloudflare)
#  Uso:  powershell -ExecutionPolicy Bypass -File .\iniciar.ps1
# ─────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$puerto = 8088
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $raiz

$py = Join-Path $raiz "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No se encontro el entorno virtual (venv). Crealo con: python -m venv venv" -ForegroundColor Red
    exit 1
}

$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    Write-Host "No se encontro cloudflared. Instalalo con: winget install --id Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}

# 1. Liberar el puerto si quedo algo colgado de una corrida anterior
try {
    (Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction Stop).OwningProcess |
        Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
} catch {}

# 2. Levantar el servidor (uvicorn) en una ventana aparte
Write-Host "Iniciando servidor en http://localhost:$puerto ..." -ForegroundColor Cyan
Start-Process -FilePath $py `
    -ArgumentList "-m", "uvicorn", "agent.main:app", "--port", "$puerto" `
    -WorkingDirectory $raiz

Start-Sleep -Seconds 3

# 3. Levantar el tunel de Cloudflare (imprime la URL publica en esta ventana)
Write-Host ""
Write-Host "Iniciando tunel de Cloudflare. La URL publica (https://...trycloudflare.com)" -ForegroundColor Cyan
Write-Host "aparecera abajo. Copiala y ponla en Meta como Callback URL + /webhook" -ForegroundColor Cyan
Write-Host "(Verify token: platim2024). NO cierres esta ventana mientras pruebas." -ForegroundColor Yellow
Write-Host ""

& $cloudflared tunnel --url "http://localhost:$puerto"
