@echo off
title PixCake Tiler
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] Python not found.
    echo Install Python 3.10+ from https://www.python.org/downloads/windows/
    echo and make sure "Add python.exe to PATH" is checked.
    pause
    exit /b 1
)

%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
    echo Installing Pillow, please wait ...
    %PY% -m pip install --upgrade pillow
    if errorlevel 1 (
        echo Retry with mirror ...
        %PY% -m pip install --upgrade pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [ERROR] Pillow install failed. Run manually:
            echo         %PY% -m pip install pillow
            pause
            exit /b 1
        )
    )
)

%PY% tiler_gui.py
if errorlevel 1 (
    echo.
    echo [ERROR] Exited with error, see messages above.
    pause
)
