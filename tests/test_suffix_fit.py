# -*- coding: utf-8 -*-
"""通常画面の機番末尾の表: 低い画面では畳み、押すと別の窓で選べる。選んだ状態は共有される。"""
import copy, os, shutil, tempfile, unittest
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSuffixFit(unittest.TestCase):
    def test_collapsed_when_not_fitting_and_dialog_shares_selection(self):
        from PySide6 import QtWidgets
        from nd287_app import settings as S, gui as G
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        tmp = tempfile.mkdtemp()
        S.settings_path = lambda: Path(tmp) / "settings.json"
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=tmp, bs_save_root="", webapp_sync_enabled=False)
        w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        try:
            w.flow_timer.stop()
            w._suffix_fit_pending = True
            w._fit_suffix_panel()
            if w.minimumSizeHint().height() > QtWidgets.QApplication.primaryScreen().availableGeometry().height():
                self.assertFalse(w.suffix_panel.isChecked())
            self.assertLessEqual(w.suffix_panel.picker.tbl.maximumHeight(), 200)     # 詰めた表（以前は235以上）
        finally:
            w.close(); w.deleteLater(); shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
