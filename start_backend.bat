@echo off
chcp 65001 >nul
title Wen_Da Backend

cd /d "%~dp0"

:START

cls

echo ==========================================
echo       Wen_Da 后端服务启动
echo ==========================================
echo.
echo 项目目录：%cd%
echo 后端地址：http://127.0.0.1:8000
echo API文档：http://127.0.0.1:8000/docs
echo.
echo 按 Ctrl+C 可以停止后端
echo ==========================================
echo.

if not exist ".\venv\Scripts\python.exe" (
    echo [错误] 未找到 Python 虚拟环境！
    echo.
    echo 预期位置：
    echo %cd%\venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

call .\venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir backend

echo.
echo ==========================================
echo          后端服务已停止
echo ==========================================
echo.

choice /C YN /N /M "是否重新启动后端？(Y/N)："

if errorlevel 2 goto END
if errorlevel 1 goto START

:END

echo.
echo ==========================================
echo          Wen_Da 后端已退出
echo ==========================================
echo.

pause