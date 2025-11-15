import sys
from pathlib import Path
from collections import Counter
import math
import re

# ==== 读取/等效换算（与 focal_stats_jpg.py 同目录）====
try:
    from focal_stats_jpg import gather_rows, estimate_35mm  # noqa
except Exception as e:
    raise SystemExit("请将 photo_meta_ui.py 与 focal_stats_jpg.py 放在同一目录再运行：%s" % e)

# ==== UI / 绘图 ====
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QCheckBox, QMessageBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QSize
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# ---------- 辅助 ----------
def parse_shutter_to_stops(exposure_str):
    """把 '1/250' '0.005' '2' 等转为 (秒, EV)，EV=log2(1/t)。"""
    if exposure_str is None:
        return None, None
    s = str(exposure_str).strip()
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            val = float(a) / float(b)
        else:
            val = float(s)
        if val <= 0:
            return None, None
        stops = math.log2(1.0 / val)
        return val, stops
    except Exception:
        s2 = re.sub(r"[^0-9./]", "", s)
        try:
            if "/" in s2:
                a, b = s2.split("/", 1)
                val = float(a) / float(b)
            else:
                val = float(s2)
            if val <= 0:
                return None, None
            stops = math.log2(1.0 / val)
            return val, stops
        except Exception:
            return None, None


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def build_dataframe_like(rows):
    data = []
    for r in rows:
        f35, _ = estimate_35mm(r.get("focal_mm"), r.get("model"), r.get("focal_35mm"))
        iso = safe_float(r.get("iso"))
        shutter_s, shutter_stops = parse_shutter_to_stops(r.get("exposure"))
        data.append({
            "file": r.get("file"),
            "model": r.get("model"),
            "lens": r.get("lens"),
            "focal_mm": safe_float(r.get("focal_mm")),
            "focal_35mm": safe_float(f35),
            "iso": iso,
            "exposure_raw": r.get("exposure"),
            "shutter_s": shutter_s,
            "shutter_stops": shutter_stops,
        })
    return data


def histogram(values, bin_width, mode):
    """
    - focal: 按 mm 线性分箱
    - shutter: 按 EV 线性分箱（0.33、0.5、1.0…）
    - iso: 按 ISO 数值分箱
    """
    if not values:
        return {}
    bw = max(1e-6, float(bin_width))
    def bin_func(v): return round(v / bw) * bw
    c = Counter(bin_func(v) for v in values if v is not None)
    return dict(sorted(c.items(), key=lambda kv: kv[0]))


# ---------- 画布 ----------
class MplCanvas(FigureCanvas):
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(6, 4), dpi=100, constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def plot_bar(self, xs, counts, title, xlabel, *, numeric=False, bar_width=None):
        self.ax.clear()
        if numeric:
            if not xs:
                self.ax.set_title("No data"); self.draw(); return
            if bar_width is None:
                bar_width = 0.8
            self.ax.bar(xs, counts, width=bar_width, align='center')
            xmin = min(xs) - bar_width * 0.55
            xmax = max(xs) + bar_width * 0.55
            self.ax.set_xlim(xmin, xmax)
        else:
            pos = list(range(len(xs)))
            self.ax.bar(pos, counts, width=0.8, align='center')
            self.ax.set_xticks(pos)
            self.ax.set_xticklabels(xs, rotation=45)

        # 图内仍用英文
        self.ax.set_title(title, fontweight="bold")
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel("Count")
        self.ax.margins(x=0.02, y=0.05)
        self.draw()


# ---------- 主窗 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo Meta Analyzer")
        self.setMinimumSize(QSize(1120, 700))

        # 数据
        self.rows = []
        self.data = []
        self.current_folder = None
        self._last_plot = None  # 保存复合图时使用

        # 顶部条
        self.ed_path = QLineEdit()
        self.btn_browse = QPushButton("选择文件夹")
        self.btn_read = QPushButton("读取")
        self.lbl_status = QLabel("状态：未读取")
        self.lbl_status.setStyleSheet("color:#555;")

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("文件夹："))
        top_layout.addWidget(self.ed_path, 1)
        top_layout.addWidget(self.btn_browse)
        top_layout.addWidget(self.btn_read)
        top_layout.addWidget(self.lbl_status)

        # ===== 三列：左=参数  中=相机  右=镜头 =====
        self.cmb_analysis = QComboBox()
        self.cmb_analysis.addItems(["焦距（35mm等效）", "焦距（物理mm）", "快门速度", "ISO"])

        self.spn_bin = QDoubleSpinBox()
        self.spn_bin.setRange(0.01, 200.0)
        self.spn_bin.setValue(5.0)
        self.spn_bin.setDecimals(2)
        self.spn_bin.setSingleStep(1.0)
        self.spn_bin.setSuffix("（单位随模式变化）")

        self.spn_dpi = QDoubleSpinBox()
        self.spn_dpi.setRange(72, 600)
        self.spn_dpi.setValue(150)
        self.spn_dpi.setDecimals(0)

        self.chk_autosave = QCheckBox("更新时自动保存PNG（复合图）")
        self.btn_save_png = QPushButton("另存当前复合图")

        self.tbl_crop = QTableWidget(0, 2)
        self.tbl_crop.setHorizontalHeaderLabels(["相机型号", "裁切系数"])
        self.tbl_crop.horizontalHeader().setStretchLastSection(True)
        self.btn_apply_crop = QPushButton("应用裁切表并刷新")

        left = QVBoxLayout()
        left.addWidget(QLabel("分析类型"))
        left.addWidget(self.cmb_analysis)
        left.addWidget(QLabel("分箱宽度（单位随模式变化）"))
        left.addWidget(self.spn_bin)
        left.addWidget(QLabel("图像DPI"))
        left.addWidget(self.spn_dpi)
        left.addWidget(self.chk_autosave)
        left.addWidget(self.btn_save_png)
        left.addWidget(QLabel("裁切系数（可编辑）"))
        left.addWidget(self.tbl_crop, 1)
        left.addWidget(self.btn_apply_crop)

        # 中列：相机
        self.lst_camera = QListWidget()
        self.lst_camera.setSelectionMode(QAbstractItemView.MultiSelection)
        mid = QVBoxLayout()
        mid.addWidget(QLabel("相机（可多选）"))
        mid.addWidget(self.lst_camera)

        # 右列：镜头
        self.lst_lens = QListWidget()
        self.lst_lens.setSelectionMode(QAbstractItemView.MultiSelection)
        right = QVBoxLayout()
        right.addWidget(QLabel("镜头（可多选）"))
        right.addWidget(self.lst_lens)

        filter_layout = QHBoxLayout()
        filter_layout.addLayout(left, 3)
        filter_layout.addLayout(mid, 3)
        filter_layout.addLayout(right, 3)

        # 图 + 概览
        self.canvas = MplCanvas(self)
        self.sel_label = QLabel("")
        self.sel_label.setWordWrap(True)
        self.sel_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.sel_label.setStyleSheet("color:#444;")

        self.sel_container = QWidget()
        cont_layout = QVBoxLayout(self.sel_container)
        cont_layout.addWidget(self.sel_label)
        cont_layout.addStretch(1)

        sel_area = QScrollArea()
        sel_area.setWidgetResizable(True)
        sel_area.setMinimumWidth(260)
        sel_area.setWidget(self.sel_container)

        chart_row = QHBoxLayout()
        chart_row.addWidget(self.canvas, 1)
        chart_row.addWidget(sel_area)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(top_layout)
        layout.addLayout(filter_layout)
        layout.addLayout(chart_row, 1)
        self.setCentralWidget(root)

        # 信号
        self.btn_browse.clicked.connect(self.on_browse)
        self.btn_read.clicked.connect(self.on_read)
        self.cmb_analysis.currentIndexChanged.connect(self.on_mode_changed)
        self.cmb_analysis.currentIndexChanged.connect(self.update_plot)
        self.lst_camera.itemSelectionChanged.connect(self.update_plot)
        self.lst_lens.itemSelectionChanged.connect(self.update_plot)
        self.btn_save_png.clicked.connect(self.save_png)
        self.btn_apply_crop.clicked.connect(self.update_plot)
        self.spn_bin.valueChanged.connect(self.update_plot)
        self.spn_dpi.valueChanged.connect(self.update_plot)

        # 初始根据模式设置分箱控件
        self.on_mode_changed()

    # ---- 事件 ----
    def on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含照片的根目录")
        if d:
            self.ed_path.setText(d)

    def on_read(self):
        folder = self.ed_path.text().strip()
        if not folder:
            QMessageBox.warning(self, "提示", "请先选择文件夹。")
            return
        p = Path(folder)
        if not p.exists():
            QMessageBox.critical(self, "错误", "路径不存在。")
            return
        self.lbl_status.setText("状态：读取中…")
        QApplication.processEvents()

        try:
            self.rows = gather_rows(p, use_exiftool=True)
            if not self.rows:
                self.lbl_status.setText("状态：未找到 JPG/EXIF")
                return
            self.data = build_dataframe_like(self.rows)
            self.current_folder = p
            self.fill_filters()
            self.fill_crop_table()
            self.cmb_analysis.setCurrentIndex(0)
            self.on_mode_changed()
            self.update_plot()
            self.lbl_status.setText(f"状态：已更新（{len(self.data)} 条）")
        except Exception as e:
            self.lbl_status.setText("状态：读取失败")
            QMessageBox.critical(self, "错误", f"读取失败：\n{e}")

    def fill_filters(self):
        cams = sorted({d["model"] for d in self.data if d.get("model")})
        lens = sorted({d["lens"] for d in self.data if d.get("lens")})

        self.lst_camera.clear()
        for c in cams:
            it = QListWidgetItem(c)
            self.lst_camera.addItem(it)
            it.setSelected(True)

        self.lst_lens.clear()
        for l in lens:
            it = QListWidgetItem(l)
            self.lst_lens.addItem(it)
            it.setSelected(True)

    def fill_crop_table(self):
        cams = sorted({d["model"] for d in self.data if d.get("model")})
        self.tbl_crop.setRowCount(len(cams))
        for i, cam in enumerate(cams):
            self.tbl_crop.setItem(i, 0, QTableWidgetItem(str(cam)))
            s = (cam or "").upper()
            cf = 1.0
            if any(k in s for k in ["ILCE-6", "A6", "X-T", "X-S", "X-H", "ZV-E", "ALPHA 6"]):
                cf = 1.5
            if "EOS R" in s and any(k in s for k in ["R7", "R10", "R50"]):
                cf = 1.6
            if any(k in s for k in ["OM-", "E-M", "DMC-G", "DC-G", "GH", "GX"]):
                cf = 2.0
            self.tbl_crop.setItem(i, 1, QTableWidgetItem(str(cf)))

    def read_crop_table(self):
        d = {}
        for r in range(self.tbl_crop.rowCount()):
            cam_item = self.tbl_crop.item(r, 0)
            cf_item  = self.tbl_crop.item(r, 1)
            if not cam_item or not cf_item:
                continue
            cam = cam_item.text().strip()
            try:
                cf = float(cf_item.text().strip())
            except Exception:
                cf = 1.0
            if cam:
                d[cam] = cf
        return d

    # ---- 模式切换时动态调整分箱控件 ----
    def on_mode_changed(self):
        idx = self.cmb_analysis.currentIndex()
        if idx in (0, 1):  # 焦距（等效/物理）
            self.spn_bin.blockSignals(True)
            self.spn_bin.setDecimals(0)
            self.spn_bin.setRange(1, 200)
            self.spn_bin.setSingleStep(1)
            if self.spn_bin.value() < 1:
                self.spn_bin.setValue(5)
            self.spn_bin.setSuffix(" mm")
            self.spn_bin.blockSignals(False)
        elif idx == 2:  # 快门（EV）
            self.spn_bin.blockSignals(True)
            self.spn_bin.setDecimals(2)
            self.spn_bin.setRange(0.01, 10.0)
            self.spn_bin.setSingleStep(0.33)  # 常用 1/3 EV
            if self.spn_bin.value() < 0.01 or self.spn_bin.value() > 10:
                self.spn_bin.setValue(1.00)
            self.spn_bin.setSuffix(" EV")
            self.spn_bin.blockSignals(False)
        else:  # ISO
            self.spn_bin.blockSignals(True)
            self.spn_bin.setDecimals(0)
            self.spn_bin.setRange(10, 2000)
            self.spn_bin.setSingleStep(10)
            if self.spn_bin.value() < 10:
                self.spn_bin.setValue(100)
            self.spn_bin.setSuffix(" ISO")
            self.spn_bin.blockSignals(False)

    def _sel_summary_text(self, cams, lens):
        def summarise(items, n=30):
            if not items:
                return "(none)"
            if len(items) <= n:
                return ", ".join(items)
            return ", ".join(items[:n]) + f" … (total {len(items)})"
        return f"相机: {summarise(cams)}\n\n镜头: {summarise(lens)}"

    def save_png(self):
        if not self.data or not self._last_plot:
            QMessageBox.information(self, "提示", "还没有可保存的图。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存复合图", "hist.png", "PNG Files (*.png)")
        if path:
            self._save_composite(path, dpi=int(self.spn_dpi.value()))
            self.lbl_status.setText("状态：已保存 → " + Path(path).name)

    def _save_composite(self, path, dpi=150):
        """左图右文复合保存。"""
        lp = self._last_plot
        fig = Figure(figsize=(8, 4.5), dpi=dpi, constrained_layout=True)
        gs = fig.add_gridspec(ncols=2, nrows=1, width_ratios=[3.0, 1.3])
        ax = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[0, 1])
        # 左
        if lp["numeric"]:
            xs = lp["xs"]; ys = lp["ys"]; bw = lp["bar_width"]
            ax.bar(xs, ys, width=bw, align='center')
            ax.set_xlim(min(xs)-bw*0.55, max(xs)+bw*0.55)
        else:
            pos = list(range(len(lp["xs"])))
            ax.bar(pos, lp["ys"], width=0.8, align='center')
            ax.set_xticks(pos); ax.set_xticklabels(lp["xs"], rotation=45)
        ax.set_title(lp["title"], fontweight="bold")
        ax.set_xlabel(lp["xlabel"]); ax.set_ylabel("Count"); ax.margins(x=0.02, y=0.05)
        # 右
        ax2.axis("off")
        txt = self._sel_summary_text(lp["cams"], lp["lens"])
        ax2.text(0.02, 0.98, "Selection summary", fontsize=11, weight="bold", va="top")
        ax2.text(0.02, 0.92, txt, fontsize=10, va="top", wrap=True)
        fig.savefig(path, dpi=dpi)

    def update_plot(self):
        if not self.data:
            return

        dpi = int(self.spn_dpi.value())
        self.canvas.fig.set_dpi(dpi)

        mode_idx = self.cmb_analysis.currentIndex()  # 0等效 1物理 2快门 3ISO
        keep_cams = [i.text() for i in self.lst_camera.selectedItems()]
        keep_lens = [i.text() for i in self.lst_lens.selectedItems()]
        keep_cams_set = set(keep_cams); keep_lens_set = set(keep_lens)
        bin_w = float(self.spn_bin.value())
        crop_override = self.read_crop_table()

        # 过滤
        filt = []
        for d in self.data:
            if keep_cams_set and d.get("model") and d["model"] not in keep_cams_set:
                continue
            if keep_lens_set and d.get("lens") and d["lens"] not in keep_lens_set:
                continue
            filt.append(d)

        self.sel_label.setText(self._sel_summary_text(keep_cams, keep_lens))

        if not filt:
            self.canvas.plot_bar([], [], "No data", "", numeric=False)
            self.lbl_status.setText("状态：筛选后无数据")
            return

        # 分布 + 绘图（图内英文）
        if mode_idx == 0:  # 35mm等效
            def focal35_with_override(x):
                mm = x.get("focal_mm"); model = x.get("model")
                if mm is None: return None
                if model in crop_override:
                    return float(mm) * float(crop_override[model])
                f35, _ = estimate_35mm(mm, model, x.get("focal_35mm"))
                return f35
            vals = [focal35_with_override(x) for x in filt if focal35_with_override(x) is not None]
            dist = histogram(vals, bin_width=bin_w, mode="focal")
            xs = list(dist.keys()); ys = list(dist.values())
            title = f"Focal length (35mm eq) | bin={bin_w:g}"
            xlabel = "Focal (mm, 35mm eq)"
            self.canvas.plot_bar(xs, ys, title, xlabel, numeric=True, bar_width=bin_w*0.9)
            self._last_plot = dict(xs=xs, ys=ys, title=title, xlabel=xlabel,
                                   numeric=True, bar_width=bin_w*0.9, cams=keep_cams, lens=keep_lens)

        elif mode_idx == 1:  # 物理
            vals = [x["focal_mm"] for x in filt if x.get("focal_mm") is not None]
            dist = histogram(vals, bin_width=bin_w, mode="focal")
            xs = list(dist.keys()); ys = list(dist.values())
            title = f"Focal length (physical) | bin={bin_w:g}"
            xlabel = "Focal (mm)"
            self.canvas.plot_bar(xs, ys, title, xlabel, numeric=True, bar_width=bin_w*0.9)
            self._last_plot = dict(xs=xs, ys=ys, title=title, xlabel=xlabel,
                                   numeric=True, bar_width=bin_w*0.9, cams=keep_cams, lens=keep_lens)

        elif mode_idx == 2:  # 快门（EV 空间）
            vals = [x["shutter_stops"] for x in filt if x.get("shutter_stops") is not None]
            dist = histogram(vals, bin_width=max(0.01, bin_w), mode="shutter")
            xs_ev = list(dist.keys()); ys = list(dist.values())
            labels = []
            for ev in xs_ev:
                sec = 1.0 / (2 ** ev)
                if sec >= 1:
                    labels.append(f"{int(round(sec))}s")
                else:
                    denom = int(round(1/sec)); labels.append(f"1/{denom}")
            title = f"Shutter speed (grouped by {bin_w:g} EV)"
            xlabel = "Shutter"
            self.canvas.plot_bar(labels, ys, title, xlabel, numeric=False)
            self._last_plot = dict(xs=labels, ys=ys, title=title, xlabel=xlabel,
                                   numeric=False, bar_width=None, cams=keep_cams, lens=keep_lens)

        else:  # ISO
            vals = [x["iso"] for x in filt if x.get("iso") is not None]
            dist = histogram(vals, bin_width=max(10.0, bin_w), mode="iso")
            xs = list(dist.keys()); ys = list(dist.values())
            self.canvas.plot_bar(xs, ys,
                                 f"ISO distribution | bin={max(10.0, bin_w):g}",
                                 "ISO",
                                 numeric=True, bar_width=max(10.0, bin_w)*0.9)
            self._last_plot = dict(xs=xs, ys=ys, title=f"ISO distribution | bin={max(10.0, bin_w):g}",
                                   xlabel="ISO", numeric=True, bar_width=max(10.0, bin_w)*0.9,
                                   cams=keep_cams, lens=keep_lens)

        if self.chk_autosave.isChecked() and self._last_plot:
            out = Path.cwd() / "hist.png"
            self._save_composite(out, dpi=int(self.spn_dpi.value()))
            self.lbl_status.setText("状态：已保存 → hist.png")
        else:
            self.lbl_status.setText(f"状态：已更新（{len(filt)} 条）")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()

    sys.exit(app.exec())
