@echo off
chcp 65001 >nul

REM ============================================================
REM Exo Windows Porting - 快速启动脚本
REM ============================================================
REM 
REM Usage: start-exo-windows-porting [options]
REM
REM Options:
REM   --model <path>    Path to GGUF model file (required)
REM   --backend cpu     CPU-only mode (default if not specified)
REM   --backend rocm    AMD ROCm GPU acceleration
REM   --backend cuda    NVIDIA CUDA acceleration
REM   --dashboard       Start Web Dashboard only
REM   --help            Show this help message

echo ============================================================
echo 🚀 Exo Windows Porting - Quick Start
echo ============================================================
echo.

REM Check Python version
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python not found! Please install Python 3.10+.
    pause
    exit /b 1
)

REM Parse arguments
set MODEL_PATH=
set BACKEND=cpu
set DASHBOARD_MODE=false

:loop
if "%~1"=="" goto :end_loop
if "%~1"=="--model" (
    set MODEL_PATH=%~2
    shift
    shift
    goto :loop
)
if "%~1"=="--backend" (
    set BACKEND=%~2
    shift
    shift
    goto :loop
)
if "%~1"=="--dashboard" (
    set DASHBOARD_MODE=true
    shift
    goto :loop
)
shift
goto :loop

:end_loop

REM Validate model path
if "%MODEL_PATH%"=="" (
    echo ❌ Model file required! Use --model <path>
    echo.
    echo Usage: start-exo-windows-porting.bat --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf --backend cpu
    pause
    exit /b 1
)

if not exist "%MODEL_PATH%" (
    echo ❌ Model file not found: %MODEL_PATH%
    pause
    exit /b 1
)

echo ✅ Model: %MODEL_PATH%
echo Backend: %BACKEND%
echo.

REM Activate virtual environment if exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Start Exo Windows Porting
echo 🚀 Starting Exo Windows Porting...
python -m exo_windows_porting --model "%MODEL_PATH%" --backend %BACKEND%

if errorlevel 1 (
    echo.
    echo ❌ Failed to start! Check error messages above.
    pause
) else (
    echo.
    echo ✅ Exo Windows Porting started successfully!
    echo Press Ctrl+C to stop.
    pause
)

exit /b %errorlevel%
