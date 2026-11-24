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

".venv\Scripts\python.exe" -m pip install pyinstaller xlrd
".venv\Scripts\python.exe" -m PyInstaller --onefile --windowed --name nd287_bunkatsu --hidden-import xlrd --paths . nd287_exe_entry.py
if errorlevel 1 (
    echo [エラー] ビルドに失敗しました。
    pause
    exit /b 1
)

rem FOCAS（機械と直接つなぐ）の「機械と話す小さなプログラム」は exe の中に入らない。
rem 32bit の Python で別に動かすので、exe の隣へ focas_helper.py を写す。
copy /y "nd287_app\focas_helper.py" "dist\focas_helper.py" >nul
if errorlevel 1 (
    echo [エラー] focas_helper.py を dist に写せませんでした。
    pause
    exit /b 1
)

echo.
echo 完成: dist\nd287_bunkatsu.exe
echo 同じフォルダに dist\focas_helper.py も作りました。exeと一緒に検査PCへコピーしてください。
echo （FOCASを使わない検査PCでは、focas_helper.py が無くてもアプリは動きます。）
echo このexeを検査PCにコピーすれば、Pythonなしでダブルクリック起動できます。
pause
