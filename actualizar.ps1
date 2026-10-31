# Sube a GitHub los cambios hechos en esta carpeta (uso: powershell -ExecutionPolicy Bypass -File .\actualizar.ps1)
git pull --rebase --autostash origin main
git add -A
git commit -m "futbot: actualizacion $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git push origin main
if ($LASTEXITCODE -eq 0) { Write-Host "`nACTUALIZADO OK" -ForegroundColor Green } else { Write-Host "`nAlgo fallo: manda captura" -ForegroundColor Red }
