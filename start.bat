@echo off
set ROOT=%~dp0

echo Stopping old processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTEN 2^>nul') do taskkill /PID %%a /F /T >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr LISTEN 2^>nul') do taskkill /PID %%a /F /T >nul 2>&1
ping -n 3 127.0.0.1 >nul

echo Starting backend on port 8000...
start "KylinGuard-Backend" cmd /k "cd /d %ROOT%backend && .venv\Scripts\uvicorn.exe --factory kylinguard.api:create_app --host 0.0.0.0 --port 8000 --reload"
ping -n 5 127.0.0.1 >nul

echo Starting frontend on port 5174...
start "KylinGuard-Frontend" cmd /k "cd /d %ROOT%frontend && npm run dev -- --port 5174"

echo.
echo Done! Open http://localhost:5174
echo Login: admin / admin123
echo.
pause
