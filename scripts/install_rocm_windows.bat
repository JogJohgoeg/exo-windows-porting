@echo off
:: ROCm for Windows Installation Script for Exo Windows Porting
:: This script installs AMD ROCm SDK and llama-cpp-python with ROCm support

echo ========================================
echo   ROCm for Windows - Exo Installer
echo ========================================
echo.

:: Check if running as administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] This script must be run as Administrator!
    echo Please right-click and "Run as administrator"
    pause
    exit /b 1
)

:: Check Python installation
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.12+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [INFO] Python detected:
python --version
echo.

:: Check Windows version (Windows 11 required for ROCm)
powershell -Command "[int]$env:OSBuildNumber; if ($LASTEXITCODE -ne 0) { exit 1 }" >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Failed to get Windows build number!
    pause
    exit /b 1
)

:: Check GPU support (AMD RX 7900/6950 series recommended)
dxdiag >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARNING] dxdiag not found!
    echo Please ensure DirectX is installed.
    echo Download from: https://www.microsoft.com/en-us/download/details.aspx?id=35
    echo.
    
    set /p confirm="Continue anyway? (Y/N): "
    if /i not "%confirm%"=="Y" exit /b 1
) else (
    echo [INFO] DirectX detected:
    dxdiag | findstr /C:"DirectX Version"
    echo.
)

:: Check for AMD GPU
echo [INFO] Checking for AMD GPU...
wmic path win32_videocontroller get name > C:\temp\gpu_list.txt 2>&1
findstr /i "AMD Radeon RX" C:\temp\gpu_list.txt >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARNING] No AMD Radeon RX GPU detected!
    echo ROCm for Windows requires compatible AMD GPUs:
    echo   - RX 7900 XTX/XT
    echo   - PRO W7900
    echo   - RX 6950 XT
    echo.
    
    set /p confirm="Continue anyway? (Y/N): "
    if /i not "%confirm%"=="Y" exit /b 1
) else (
    echo [INFO] AMD Radeon RX GPU detected!
    wmic path win32_videocontroller get name | findstr /i "AMD Radeon RX"
    echo.
)

:: Create virtual environment (recommended)
echo [INFO] Creating virtual environment...
if exist .venv (
    echo [WARNING] Virtual environment already exists!
    set /p overwrite="Overwrite? (Y/N): "
    if /i "%overwrite%"=="Y" rmdir /s /q .venv
    else exit /b 1
)

python -m venv .venv
if %errorLevel% neq 0 (
    echo [ERROR] Failed to create virtual environment!
    pause
    exit /b 1
)

echo [INFO] Virtual environment created: .venv
echo.

:: Activate virtual environment
call .venv\Scripts\activate.bat
if %errorLevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment!
    pause
    exit /b 1
)

:: Upgrade pip
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip
echo.

:: Install ROCm-enabled llama-cpp-python
echo [INFO] Installing llama-cpp-python with ROCm support...
echo This may take several minutes...
pip install "llama-cpp-python>=0.2.85" --index-url https://abetlen.github.io/llama-cpp-python/whl/rocm6.2

if %errorLevel% neq 0 (
    echo [ERROR] Failed to install llama-cpp-python!
    echo Try installing Visual Studio Build Tools first:
    echo winget install Microsoft.VisualStudio.2022.BuildTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64
    pause
    exit /b 1
)

echo [INFO] Successfully installed llama-cpp-python with ROCm support!
echo.

:: Verify installation
echo [INFO] Verifying installation...
python -c "import llama_cpp; print(f'llama.cpp version: {llama_cpp.__version__}')"
if %errorLevel% neq 0 (
    echo [ERROR] Verification failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Next steps:
echo   1. Test the installation with: python -m exo_windows_porting --help
echo   2. Run a sample inference: python scripts/benchmark_performance.py
echo   3. Start distributed inference: start-exo-windows-porting.bat
echo.
pause
