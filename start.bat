@echo off
echo KylinGuard backend is Linux-only and must be started from WSL.
echo See README.md: run uvicorn in WSL, then run npm run dev in Windows PowerShell.
echo This script no longer launches a Windows backend or embeds example credentials.
exit /b 1
