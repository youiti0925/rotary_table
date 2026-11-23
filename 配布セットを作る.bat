@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ================================================
rem  各PCに配る「配布セット」を作る（このPCで1回ダブルクリック）
rem  できたフォルダ 配布\ND287分割測定 を共有フォルダ等に置き、
rem  各PCではその中の「インストール.bat」をダブルクリックするだけ。
rem  FOCAS 用の 32bit Python と Fwlib32.dll は、このPCに入っているものを写す。
rem ================================================

if not exist ".venv\Scripts\python.exe" (
    echo 先に ND287分割測定.bat を一度実行してセットアップしてください。
    pause
    exit /b 1
)

echo [1/2] アプリ本体（exe）を作っています…（数分かかります）
".venv\Scripts\python.exe" -m pip install -q pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name nd287_bunkatsu --paths . nd287_exe_entry.py
if errorlevel 1 (
    echo [エラー] exe を作れませんでした。
    pause
    exit /b 1
)

echo [2/2] 配布セットをまとめています…
".venv\Scripts\python.exe" -m nd287_app.deploy %*
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo 完成: 配布\ND287分割測定
echo このフォルダごと共有フォルダや USB メモリに置き、
echo 各PCで「インストール.bat」をダブルクリックしてください。
pause
