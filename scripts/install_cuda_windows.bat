@echo off
:: CUDA for Windows Installation Script for Exo Windows Porting
:: This script installs NVIDIA CUDA Toolkit and llama-cpp-python with CUDA support

echo ========================================
echo   CUDA for Windows - Exo Installer
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

:: Check CUDA compatibility
nvidia-smi >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARNING] nvidia-smi not found!
    echo Please ensure NVIDIA GPU drivers are installed.
    echo Download from: https://www.nvidia.com/Download/index.aspx
    echo.
    
    set /p confirm="Continue anyway? (Y/N): "
    if /i not "%confirm%"=="Y" exit /b 1
) else (
    echo [INFO] NVIDIA GPU detected:
    nvidia-smi | findstr "CUDA Version"
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

:: Install CUDA-enabled llama-cpp-python
echo [INFO] Installing llama-cpp-python with CUDA support...
echo This may take several minutes...
pip install "llama-cpp-python>=0.2.85" --index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

if %errorLevel% neq 0 (
    echo [ERROR] Failed to install llama-cpp-python!
    echo Try installing Visual Studio Build Tools first:
    echo winget install Microsoft.VisualStudio.2022.BuildTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64
    pause
    exit /b 1
)

echo [INFO] Successfully installed llama-cpp-python with CUDA support!
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
