@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ================================================
rem  ND287 分割測定アプリ 起動用（ダブルクリックでOK）
rem  初回はセットアップ（ライブラリの自動インストール）を行う
rem ================================================

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

if not exist ".venv\Scripts\python.exe" (
    echo ============================================
    echo  初回セットアップを実行します（数分かかります）
    echo  ※この1回だけインターネット接続が必要です
    echo ============================================
    %PY% -m venv .venv
    if errorlevel 1 goto :setup_fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :setup_fail
    echo セットアップ完了。アプリを起動します。
)

".venv\Scripts\python.exe" -m nd287_app %*
if errorlevel 1 (
    echo.
    echo アプリがエラー終了しました。上のメッセージを確認してください。
    pause
)
exit /b 0

:no_python
echo [エラー] Pythonが見つかりません。
echo   https://www.python.org/downloads/ からPythonをインストールしてください。
echo   インストール画面で「Add python.exe to PATH」に必ずチェックを入れること。
echo   インストール後、もう一度このファイルをダブルクリックしてください。
pause
exit /b 1

:setup_fail
echo [エラー] セットアップに失敗しました。
echo   インターネット接続を確認し、.venv フォルダを削除してから
echo   もう一度このファイルをダブルクリックしてください。
pause
exit /b 1
