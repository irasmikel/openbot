# Sube el proyecto a https://github.com/irasmikel/openbot
# Uso: PowerShell en esta carpeta ->  powershell -ExecutionPolicy Bypass -File .\subir.ps1
$ErrorActionPreference = "Continue"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "Falta Git. Instalalo desde https://git-scm.com/download/win y vuelve a ejecutar." -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force .github\workflows | Out-Null
if (Test-Path workflows) { Move-Item workflows\*.yml .github\workflows\ -Force; Remove-Item workflows -Recurse -Force }
if (-not (Test-Path .git)) { git init -b main }
git config user.name  > $null 2>&1; if ($LASTEXITCODE -ne 0) { git config user.name "irasmikel" }
git config user.email > $null 2>&1; if ($LASTEXITCODE -ne 0) { git config user.email "irasmikel@users.noreply.github.com" }
git add .
git commit -m "futbot: scout rojas + favorito pierde + registro de apuestas"
git remote remove origin 2>$null
git remote add origin https://github.com/irasmikel/openbot.git
git push -u origin main
if ($LASTEXITCODE -ne 0) {
  Write-Host "El repo ya tenia contenido (p.ej. README). Lo fusiono y reintento..." -ForegroundColor Yellow
  git pull origin main --allow-unrelated-histories --no-edit
  git push -u origin main
}
if ($LASTEXITCODE -eq 0) { Write-Host "`nSUBIDO OK -> https://github.com/irasmikel/openbot" -ForegroundColor Green }
