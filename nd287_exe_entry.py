# -*- coding: utf-8 -*-
"""exe（PyInstaller）の入口。

nd287_app\\__main__.py を直接 PyInstaller に渡すと、パッケージとして読まれず
相対 import（from .nd287 import …）が「attempted relative import with no known parent package」で
落ちる（2026-11-23 CR0579 で確認。それまで作っていた exe は起動しなかった）。
ここから nd287_app を普通に import して起動する。
"""
from nd287_app.__main__ import main

if __name__ == "__main__":
    main()
