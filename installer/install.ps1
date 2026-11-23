# ND287 分割測定アプリ インストール（「インストール.bat」から呼ばれる）
#
# このフォルダの app\ を、このPCの %LOCALAPPDATA%\ND287分割測定 に写し、
# デスクトップとスタートメニューにショートカットを作る。管理者の権限は要らない。
#
# - 2回目以降（更新）も同じ手順でよい。設定（settings.json）・測定データ・控え・ログは消さない。
# - マスタ\ は「このPCに無いファイルだけ」入れる（各PCで直したマスタを上書きしない）。
# - FOCAS 用の 32bit Python と Fwlib32.dll も app\focas\ から一緒に入る。

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$title = 'ND287 分割測定アプリ インストール'

function Say($text, $icon = 'Information') {
    [void][System.Windows.Forms.MessageBox]::Show($text, $title, 'OK', $icon)
}

try {
    $src = Join-Path $PSScriptRoot 'app'
    $exeName = 'nd287_bunkatsu.exe'
    if (-not (Test-Path (Join-Path $src $exeName))) {
        Say "入れるもの（app\$exeName）が見つかりません。`n配布セットのフォルダごとコピーしてから、もう一度ダブルクリックしてください。" 'Error'
        exit 1
    }
    $dst = Join-Path $env:LOCALAPPDATA 'ND287分割測定'

    # アプリが開いていると上書きできない
    while (Get-Process -Name 'nd287_bunkatsu' -ErrorAction SilentlyContinue) {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "ND287 分割測定アプリが開いています。`nアプリを閉じてから「OK」を押してください。",
            $title, 'OKCancel', 'Warning')
        if ($r -ne 'OK') { exit 1 }
    }

    $update = Test-Path (Join-Path $dst $exeName)
    New-Item -ItemType Directory -Force -Path $dst | Out-Null

    # 本体・ヘルパー・FOCAS 一式（マスタ以外）。古い FOCAS 一式は入れ替える
    $srcMaster = Join-Path $src 'マスタ'
    & robocopy $src $dst /E /XD $srcMaster /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "ファイルを写せませんでした（robocopy $LASTEXITCODE）" }

    # マスタは、このPCに無いファイルだけ入れる
    $added = @()
    if (Test-Path $srcMaster) {
        $dstMaster = Join-Path $dst 'マスタ'
        New-Item -ItemType Directory -Force -Path $dstMaster | Out-Null
        Get-ChildItem -Path $srcMaster -File | ForEach-Object {
            $to = Join-Path $dstMaster $_.Name
            if (-not (Test-Path $to)) {
                Copy-Item $_.FullName $to
                $added += $_.Name
            }
        }
    }

    # ショートカット（デスクトップとスタートメニュー）
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
    foreach ($p in $places) {
        $lnk = $shell.CreateShortcut((Join-Path $p 'ND287分割測定.lnk'))
        $lnk.TargetPath = Join-Path $dst $exeName
        $lnk.WorkingDirectory = $dst
        $lnk.Description = 'ND287 分割測定アプリ'
        $lnk.Save()
    }

    $focas = if ((Test-Path (Join-Path $dst 'focas\python32\python.exe')) -and (Test-Path (Join-Path $dst 'focas\Fwlib32.dll'))) {
        'FOCAS（機械と直接つなぐ）: 使えます（32bit Python と Fwlib32.dll も入れました）'
    } else {
        'FOCAS（機械と直接つなぐ）: この配布セットには入っていません'
    }
    $what = if ($update) { '更新しました' } else { '入れました' }
    $m = if ($added.Count -gt 0) { "マスタ: 新しく入れたもの " + ($added -join '・') } else { 'マスタ: このPCのものをそのまま使います' }
    Say ("ND287 分割測定アプリを$what。`n`n場所: $dst`nデスクトップの「ND287分割測定」から起動できます。`n`n$focas`n$m")
    exit 0
}
catch {
    Say ("インストールできませんでした。`n`n" + $_.Exception.Message) 'Error'
    exit 1
}
