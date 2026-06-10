@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ================================================
rem  配布用exeのビルド（Python無しの検査PCに配る場合）
rem  先に ND287分割測定.bat を一度実行してセットアップしておくこと
rem ================================================

if not exist ".venv\Scripts\python.exe" (
    echo 先に ND287分割測定.bat を一度実行してセットアップしてください。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --onefile --windowed --name nd287_bunkatsu --paths . nd287_app\__main__.py
if errorlevel 1 (
    echo [エラー] ビルドに失敗しました。
    pause
    exit /b 1
)

echo.
echo 完成: dist\nd287_bunkatsu.exe
echo このexeを検査PCにコピーすれば、Pythonなしでダブルクリック起動できます。
pause
