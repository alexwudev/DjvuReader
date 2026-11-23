# -*- coding: utf-8 -*-
"""DjvuReader：精簡的 DjVu／FB2／EPUB／PDF 閱讀器。

DjVu 以 DjVuLibre 命令列工具（ddjvu、djvused、djvudump）解碼；
這些工具只吃 ANSI 路徑，所以檔名含中文、俄文時先建硬連結到純英數路徑。
FB2、EPUB、PDF 以 PyMuPDF 排版與渲染。
所有開檔與渲染都在背景執行緒，介面不會卡住。
"""
import os
import re
import sys
import json
import uuid
import bisect
import ctypes
import shutil
import hashlib
import zipfile
import threading
import subprocess
from collections import OrderedDict

from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal, QRectF, QPointF, QTimer
from PyQt6.QtGui import (QImage, QPixmap, QPainter, QColor, QAction, QKeySequence, QPalette,
                         QIntValidator, QFont, QIcon, QActionGroup)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QAbstractScrollArea, QToolBar, QLineEdit,
                             QLabel, QComboBox, QFileDialog, QDockWidget, QTreeWidget, QTreeWidgetItem,
                             QMenu, QToolButton, QWidget, QSizePolicy)

APP = "DjvuReader"
APP_TITLE = "DjVu 閱讀器"
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
TMP_DIR = os.path.join(DATA_DIR, "tmp")
LINK_DIR = os.path.join(DATA_DIR, "links")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
NO_WINDOW = 0x08000000
OPEN_FILTER = "可閱讀的檔案 (*.djvu *.djv *.fb2 *.fb2.zip *.epub *.pdf *.xps *.cbz *.mobi);;所有檔案 (*.*)"
PAGE_GAP = 10
MARGIN = 10
CACHE_LIMIT = 600 * 1024 * 1024


# ---------------------------------------------------------------- DjVuLibre

def find_djvulibre():
    cands = []
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    cands.append(os.path.join(base, "djvulibre"))
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(base, "_internal", "djvulibre"))
    cands.append(os.path.expanduser(r"~\scoop\apps\djvulibre\current"))
    cands += [r"C:\Program Files (x86)\DjVuLibre", r"C:\Program Files\DjVuLibre"]
    w = shutil.which("ddjvu")
    if w:
        cands.append(os.path.dirname(w))
    for c in cands:
        if os.path.isfile(os.path.join(c, "ddjvu.exe")):
            return c
    return None


DJVU_DIR = find_djvulibre()


def run_tool(name, args, timeout=60):
    if not DJVU_DIR:
        raise RuntimeError("找不到 DjVuLibre（ddjvu.exe），無法開啟 DjVu。")
    exe = os.path.join(DJVU_DIR, name + ".exe")
    r = subprocess.run([exe] + args, capture_output=True, timeout=timeout,
                       creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def dec(b):
    return b.decode("utf-8", "replace").lstrip("\ufeff")


def _pid_alive(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def _link_dir_for(path):
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    if drive == os.path.splitdrive(LINK_DIR)[0].upper() and LINK_DIR.isascii():
        return LINK_DIR
    if re.fullmatch(r"[A-Z]:", drive):
        return drive + "\\.djvureader_links"
    return None


def _fixed_drives():
    out = []
    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            d = chr(65 + i) + ":\\"
            if k.GetDriveTypeW(d) == 3:  # DRIVE_FIXED
                out.append(d)
    return out


def cleanup_links():
    """刪掉已結束的程序留下的硬連結與暫存檔。"""
    dirs = [LINK_DIR, TMP_DIR] + [d + ".djvureader_links" for d in _fixed_drives()]
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            m = re.match(r"(\d+)_", n)
            if m and not _pid_alive(int(m.group(1))):
                try:
                    os.remove(os.path.join(d, n))
                except OSError:
                    pass


def ascii_path(path):
    """回傳 (DjVuLibre 可用的路徑, 需在關檔時刪除的暫時檔或 None)。"""
    if path.isascii():
        return path, None
    h = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    name = f"{os.getpid()}_{h}_{uuid.uuid4().hex[:6]}.djvu"
    d = _link_dir_for(path)
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            if d.endswith(".djvureader_links"):
                ctypes.windll.kernel32.SetFileAttributesW(d, 0x2)  # 隱藏
            p = os.path.join(d, name)
            if os.path.exists(p):
                os.remove(p)
            os.link(path, p)
            return p, p
        except OSError:
            pass
    # 不同磁碟、網路磁碟或雲端佔位檔：退而複製一份
    for d in (LINK_DIR, os.environ.get("SystemDrive", "C:") + "\\.djvureader_links"):
        if not d.isascii():
            continue
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, name)
            shutil.copyfile(path, p)
            return p, p
        except OSError:
            continue
    raise RuntimeError("檔名含非英數字元，且無法建立暫時連結或複本。")


def parse_sexpr(s):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', s)
    stack = [[]]
    for t in tokens:
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) > 1:
                x = stack.pop()
                stack[-1].append(x)
        elif t.startswith('"'):
            stack[-1].append(re.sub(r'\\([0-7]{3}|.)',
                                    lambda m: chr(int(m.group(1), 8)) if len(m.group(1)) == 3
                                    else {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)),
                                    t[1:-1]))
        else:
            stack[-1].append(t)
    return stack[0]


class DjvuDoc:
    def __init__(self, path):
        self.path = path
        self.apath, self.temp = ascii_path(path)
        try:
            rc, out, err = run_tool("djvused", [self.apath, "-e", "n; select 1; size"], 120)
            txt = dec(out)
            m = re.match(r"\s*(\d+)", txt)
            if rc != 0 or not m:
                raise RuntimeError((dec(err).strip() or "無法讀取此 DjVu 檔").splitlines()[0])
            self.n = int(m.group(1))
            if self.n < 1:
                raise RuntimeError("此 DjVu 檔沒有頁面")
            ms = re.search(r"width=(\d+)\s+height=(\d+)", txt)
            w, h = (int(ms.group(1)), int(ms.group(2))) if ms else (2480, 3508)
        except Exception:
            self.close()
            raise
        self.native = [(w, h)] * self.n
        self.sizes = [(w * 72 / 300, h * 72 / 300)] * self.n
        self.outline = []

    def load_details(self):
        native, dpis = {}, {}
        rc, out, _ = run_tool("djvudump", [self.apath], 300)
        cur = None
        for line in dec(out).splitlines():
            if "FORM:DJVU" in line:
                m = re.search(r"\[P(\d+)\]", line)
                cur = int(m.group(1)) if m else (1 if self.n == 1 else None)
                continue
            if cur and cur not in native:
                m = re.search(r"DjVu (\d+)x(\d+),.*?(\d+) dpi", line)
                if m:
                    native[cur] = (int(m.group(1)), int(m.group(2)))
                    dpis[cur] = int(m.group(3)) or 300
        if len(native) < self.n:  # 非 bundled（indirect）文件：逐頁問尺寸
            script = "".join(f"select {i}; size;" for i in range(1, self.n + 1))
            rc, out, _ = run_tool("djvused", [self.apath, "-e", script], 600)
            for i, m in enumerate(re.finditer(r"width=(\d+)\s+height=(\d+)", dec(out)), 1):
                native.setdefault(i, (int(m.group(1)), int(m.group(2))))
        dflt = sorted(dpis.values())[len(dpis) // 2] if dpis else 300
        res_native, res_sizes = [], []
        for i in range(1, self.n + 1):
            w, h = native.get(i, self.native[0])
            dpi = dpis.get(i, dflt)
            res_native.append((w, h))
            res_sizes.append((w * 72 / dpi, h * 72 / dpi))
        outline = []
        try:
            rc, out, _ = run_tool("djvused", ["-u", self.apath, "-e", "print-outline"], 60)
            tree = parse_sexpr(dec(out))
            if tree and isinstance(tree[0], list) and tree[0] and tree[0][0] == "bookmarks":
                ids = None
                if re.search(r'"#[^0-9"+-]', dec(out)):
                    rc2, out2, _ = run_tool("djvused", ["-u", self.apath, "-e", "ls"], 60)
                    ids = {}
                    for ln in dec(out2).splitlines():
                        mm = re.match(r"\s*(\d+)\s+P\s+\d+\s+(\S+)", ln)
                        if mm:
                            ids[mm.group(2)] = int(mm.group(1)) - 1

                def conv(node):
                    title = node[0] if node and isinstance(node[0], str) else ""
                    url = node[1] if len(node) > 1 and isinstance(node[1], str) else ""
                    page = None
                    if url.startswith("#"):
                        t = url[1:]
                        if t.isdigit():
                            page = int(t) - 1
                        elif ids:
                            page = ids.get(t)
                    kids = [conv(k) for k in node[2:] if isinstance(k, list)]
                    return (title, page, kids)
                outline = [conv(k) for k in tree[0][1:] if isinstance(k, list)]
        except Exception:
            outline = []
        return {"native": res_native, "sizes": res_sizes, "outline": outline}

    def apply_details(self, d):
        self.native = d["native"]
        self.sizes = d["sizes"]
        self.outline = d["outline"]

    def max_px(self, i):
        return self.native[i][0]

    def render(self, i, width):
        nw, nh = self.native[i]
        height = max(1, round(width * nh / nw)) + 2
        os.makedirs(TMP_DIR, exist_ok=True)
        out = os.path.join(TMP_DIR, f"{os.getpid()}_{uuid.uuid4().hex}.pnm")
        try:
            rc, _, err = run_tool("ddjvu", ["-format=pnm", f"-page={i + 1}", f"-size={width}x{height}",
                                            self.apath, out], 180)
            with open(out, "rb") as f:
                data = f.read()
        except OSError:
            raise RuntimeError("第 %d 頁解碼失敗" % (i + 1))
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        img = QImage.fromData(data)
        if img.isNull():
            raise RuntimeError("第 %d 頁解碼失敗" % (i + 1))
        return img

    def close(self):
        if self.temp:
            try:
                os.remove(self.temp)
            except OSError:
                pass
            self.temp = None


# ---------------------------------------------------------------- PyMuPDF（FB2／EPUB／PDF）

class MuDoc:
    def __init__(self, path):
        import pymupdf
        self.pymupdf = pymupdf
        self.path = path
        self.lock = threading.Lock()
        low = path.lower()
        if low.endswith(".zip") or low.endswith(".fbz"):
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith((".fb2", ".epub", ".pdf"))]
                if not names:
                    raise RuntimeError("壓縮檔內沒有 fb2／epub／pdf")
                data = z.read(names[0])
            self.doc = pymupdf.open(stream=data, filetype=os.path.splitext(names[0])[1][1:].lower())
        else:
            self.doc = pymupdf.open(path)
        if self.doc.needs_pass:
            raise RuntimeError("此檔案有密碼保護")
        if self.doc.is_reflowable:
            self.doc.layout(width=595, height=842, fontsize=12)
        self.n = self.doc.page_count
        if self.n < 1:
            raise RuntimeError("此檔案沒有頁面")
        r = self.doc[0].rect
        self.sizes = [(r.width, r.height)] * self.n
        self.outline = []

    def load_details(self):
        sizes = list(self.sizes)
        if not self.doc.is_reflowable:
            for i in range(self.n):
                with self.lock:
                    r = self.doc.load_page(i).rect
                sizes[i] = (r.width, r.height)
        with self.lock:
            toc = self.doc.get_toc(simple=True)
        root = []
        stack = [(0, root)]
        for lvl, title, page in toc:
            node = (title, page - 1 if page and page > 0 else None, [])
            while len(stack) > 1 and stack[-1][0] >= lvl:
                stack.pop()
            stack[-1][1].append(node)
            stack.append((lvl, node[2]))
        return {"sizes": sizes, "outline": root}

    def apply_details(self, d):
        self.sizes = d["sizes"]
        self.outline = d["outline"]

    def max_px(self, i):
        return 6000

    def render(self, i, width):
        with self.lock:
            page = self.doc.load_page(i)
            s = width / max(1.0, page.rect.width)
            pix = page.get_pixmap(matrix=self.pymupdf.Matrix(s, s), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        return img

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass


def open_doc(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"AT&TFORM") or path.lower().endswith((".djvu", ".djv")):
        return DjvuDoc(path)
    return MuDoc(path)


# ---------------------------------------------------------------- 背景工作

class Bridge(QObject):
    done = pyqtSignal(object, object, object)  # token, value, error


class Task(QRunnable):
    def __init__(self, bridge, token, fn, should_run=None):
        super().__init__()
        self.bridge, self.token, self.fn, self.should_run = bridge, token, fn, should_run
        self.setAutoDelete(True)

    def run(self):
        if self.should_run and not self.should_run():
            self.bridge.done.emit(self.token, None, "skipped")
            return
        try:
            v = self.fn()
            self.bridge.done.emit(self.token, v, None)
        except subprocess.TimeoutExpired:
            self.bridge.done.emit(self.token, None, "逾時")
        except Exception as e:  # noqa: BLE001
            self.bridge.done.emit(self.token, None, str(e) or type(e).__name__)


# ---------------------------------------------------------------- 頁面檢視

LAYOUTS = {"single": "單頁檢視", "single_cont": "啟用捲動", "two": "雙頁檢視", "two_cont": "雙頁捲動"}
ZOOM_NAMES = {"width": "符合寬度", "page": "符合頁面", "visible": "符合可見區域"}


class PageView(QAbstractScrollArea):
    pageChanged = pyqtSignal(int)
    zoomChanged = pyqtSignal()
    autoScrollChanged = pyqtSignal(bool)

    def __init__(self, pool, parent=None):
        super().__init__(parent)
        self.pool = pool
        self.bridge = Bridge()
        self.bridge.done.connect(self._on_render)
        self.doc = None
        self.doc_id = 0
        self.message = "拖曳檔案到這裡，或按 Ctrl+O 開啟"
        self.mode = "width"            # 縮放：width／page／visible／custom
        self.layout_mode = "single_cont"
        self.cover = True              # 雙頁檢視時封面頁單獨一頁
        self.gaps = True               # 頁面之間的間隙
        self.rot = 0                   # 旋轉檢視角度
        self.z = 1.0
        self.rows, self.row_of = [], []
        self.disp_rows, self.row_tops, self.row_hs = [], [], []
        self.geo = {}
        self.crop = {}
        self.cur = 0
        self.total_h = self.total_w = 0
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.pending = {}
        self.wanted = frozenset()
        self.drag = None
        self.flip_block = 0.0
        self.auto_timer = QTimer(self, interval=16, timeout=self._auto_step)
        self.auto_speed = 2.0
        self.auto_acc = 0.0
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(48)
        self.horizontalScrollBar().setSingleStep(48)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.relayout_timer = QTimer(self, singleShot=True, interval=30, timeout=lambda: self.relayout(True))

    # ---- 文件
    def set_message(self, text):
        self.stop_auto()
        self.doc = None
        self.message = text
        self._clear_cache()
        self.relayout(False)

    def set_doc(self, doc, doc_id):
        self.doc = doc
        self.doc_id = doc_id
        self.message = ""
        self.rot = 0
        self.crop = {}
        self._clear_cache()
        self.cur = 0
        self.build_rows()
        self.relayout(False)
        self.verticalScrollBar().setValue(0)

    def sizes_changed(self):
        self._clear_cache(keep_images=True)
        self.relayout(True)

    def _clear_cache(self, keep_images=False):
        if not keep_images:
            self.cache.clear()
            self.cache_bytes = 0
        self.pending.clear()
        self.wanted = frozenset()

    def page_count(self):
        return self.doc.n if self.doc else 0

    # ---- 顯示方式
    def two(self):
        return self.layout_mode in ("two", "two_cont")

    def continuous(self):
        return self.layout_mode in ("single_cont", "two_cont")

    def gap(self):
        return PAGE_GAP if self.gaps else 0

    def dsize(self, i):
        w, h = self.doc.sizes[i]
        return (h, w) if self.rot % 180 else (w, h)

    def build_rows(self):
        if not self.doc:
            self.rows, self.row_of = [], []
            return
        n = self.doc.n
        if self.two():
            rows = [[0]] if self.cover else []
            start = 1 if self.cover else 0
            rows += [list(range(i, min(i + 2, n))) for i in range(start, n, 2)]
        else:
            rows = [[i] for i in range(n)]
        self.rows = rows
        self.row_of = [0] * n
        for r, pages in enumerate(rows):
            for i in pages:
                self.row_of[i] = r

    def cur_row(self):
        return self.row_of[self.cur] if self.row_of else 0

    def set_layout(self, layout_mode=None, cover=None, gaps=None):
        page = self.cur
        if layout_mode is not None:
            self.layout_mode = layout_mode
        if cover is not None:
            self.cover = cover
        if gaps is not None:
            self.gaps = gaps
        if not self.doc:
            return
        self.build_rows()
        self.relayout(False)
        self.goto(page)

    def rotate(self, deg):
        self.rot = (self.rot + deg) % 360
        if self.doc:
            self.relayout(True)

    # ---- 版面
    def _vp(self):
        return self.viewport().width(), self.viewport().height()

    def _crop_of(self, i):
        """回傳頁面內容的左右邊界比例（以未旋轉的頁面計），尚未渲染則 None。"""
        if i in self.crop:
            return self.crop[i]
        ent = self.cache.get(i)
        if not ent or not ent[2]:
            return None
        img = ent[0].toImage().scaledToWidth(300).convertToFormat(QImage.Format.Format_Grayscale8)
        bpl, h = img.bytesPerLine(), img.height()
        ptr = img.constBits()
        ptr.setsize(bpl * h)
        data = bytes(ptr)
        cols = [min(data[x::bpl]) for x in range(img.width())]
        ink = [x for x, v in enumerate(cols) if v < 200]
        if not ink:
            res = (0.0, 1.0)
        else:
            pad = 0.02
            res = (max(0.0, ink[0] / img.width() - pad), min(1.0, (ink[-1] + 1) / img.width() + pad))
        self.crop[i] = res
        return res

    def _visible_frac(self):
        if self.two() or self.rot % 180 or not self.doc:
            return None
        c = self._crop_of(self.cur)
        if not c:
            return None
        l, r = c
        return (1 - r, 1 - l) if self.rot == 180 else (l, r)

    def compute_zoom(self):
        if not self.doc or not self.rows:
            return
        vw, vh = self._vp()
        g = self.gap()
        avail = max(50, vw - 2 * MARGIN)
        if self.mode in ("width", "visible"):
            k = max(len(r) for r in self.rows)
            if self.two():
                half = sorted(max(self.dsize(i)[0] for i in r) for r in self.rows)
                sw = half[len(half) // 2] * 2
            else:
                ws = sorted(self.dsize(r[0])[0] for r in self.rows)
                sw = ws[len(ws) // 2]
            self.z = max(0.05, (avail - g * (k - 1)) / sw)
            if self.mode == "visible":
                vf = self._visible_frac()
                if vf:
                    self.z = max(0.05, avail / (self.dsize(self.cur)[0] * max(0.1, vf[1] - vf[0])))
        elif self.mode == "page":
            pages = self.rows[self.cur_row()]
            if self.two():
                sw = 2 * max(self.dsize(i)[0] for i in pages)
            else:
                sw = self.dsize(pages[0])[0]
            sh = max(self.dsize(i)[1] for i in pages)
            self.z = max(0.05, min((avail - g * (len(pages) - 1 if not self.two() else 1)) / sw,
                                   (vh - 2 * MARGIN) / sh))

    def anchor(self):
        if not self.doc or not self.geo:
            return None
        y = self.verticalScrollBar().value()
        if self.continuous():
            idx = max(0, bisect.bisect_right(self.row_tops, y) - 1)
            i = self.rows[self.disp_rows[idx]][0]
        else:
            i = self.rows[self.cur_row()][0]
        r = self.geo[i]
        return i, (y - r.top()) / r.height() if r.height() else 0

    def relayout(self, keep=True):
        anc = self.anchor() if keep else None
        vb, hb = self.verticalScrollBar(), self.horizontalScrollBar()
        vw, vh = self._vp()
        if not self.doc or not self.rows:
            self.geo, self.row_tops, self.disp_rows = {}, [], []
            vb.setRange(0, 0)
            hb.setRange(0, 0)
            self.viewport().update()
            return
        if anc and not self.continuous():
            self.cur = anc[0]
        self.compute_zoom()
        z, g = self.z, self.gap()
        self.disp_rows = list(range(len(self.rows))) if self.continuous() else [self.cur_row()]
        two = self.two()
        if two:
            half = max(max(self.dsize(i)[0] for i in r) for r in self.rows) * z
            content_w = 2 * half + g
        else:
            content_w = max(self.dsize(r[0])[0] for r in self.rows) * z
        self.total_w = content_w + 2 * MARGIN
        cw = max(self.total_w, vw)
        mid = cw / 2
        y = MARGIN
        tops, hs, geo = [], [], {}
        for r in self.disp_rows:
            pages = self.rows[r]
            sizes = [(self.dsize(i)[0] * z, self.dsize(i)[1] * z) for i in pages]
            rh = max(h for w, h in sizes)
            if two:
                if len(pages) == 2:
                    xs = [mid - g / 2 - sizes[0][0], mid + g / 2]
                elif self.cover and r == 0:
                    xs = [mid + g / 2]          # 封面放右半邊
                else:
                    xs = [mid - g / 2 - sizes[0][0]]
            else:
                xs = [mid - sizes[0][0] / 2]
            for i, x, (w, h) in zip(pages, xs, sizes):
                geo[i] = QRectF(round(x), round(y + (rh - h) / 2), round(w), round(h))
            tops.append(y)
            hs.append(rh)
            y += rh + g
        self.geo, self.row_tops, self.row_hs = geo, tops, hs
        self.total_h = y - g + MARGIN
        vb.setRange(0, max(0, int(self.total_h - vh)))
        vb.setPageStep(vh)
        hb.setRange(0, max(0, int(self.total_w - vw)))
        hb.setPageStep(vw)
        if self.mode in ("width", "page") and hb.maximum() > 0:
            hb.setValue(hb.maximum() // 2)  # 有特大頁（例如封面）撐寬版面時，橫向置中
        if anc and anc[0] in geo:
            i, frac = anc
            vb.setValue(int(geo[i].top() + frac * geo[i].height()))
        if self.mode == "visible":
            vf = self._visible_frac()
            if vf and self.cur in geo:
                hb.setValue(int(geo[self.cur].left() + vf[0] * geo[self.cur].width() - MARGIN))
        self.zoomChanged.emit()
        self.viewport().update()

    def page_rect(self, i):
        r = self.geo[i]
        return r.translated(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())

    def visible_pages(self):
        if not self.row_tops:
            return []
        if not self.continuous():
            return list(self.rows[self.disp_rows[0]])
        y0 = self.verticalScrollBar().value()
        y1 = y0 + self.viewport().height()
        a = max(0, bisect.bisect_right(self.row_tops, y0) - 1)
        out = []
        for idx in range(a, len(self.row_tops)):
            if self.row_tops[idx] > y1:
                break
            out += self.rows[self.disp_rows[idx]]
        return out

    # ---- 繪製
    def paintEvent(self, ev):
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), QColor(64, 64, 64))
        if not self.doc:
            p.setPen(QColor(200, 200, 200))
            f = p.font()
            f.setPointSize(13)
            p.setFont(f)
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message)
            return
        dpr = self.devicePixelRatioF()
        vis = self.visible_pages()
        # 先決定要哪些頁（含前後預先渲染），再送出請求，背景工作才不會誤判為過時
        order = list(vis)
        if vis:
            for i in (max(vis) + 1, max(vis) + 2, min(vis) - 1):
                if 0 <= i < self.doc.n and i not in order:
                    order.append(i)
        needs = {i: self._need_width(i, self.doc.sizes[i][0] * self.z * dpr) for i in order}
        self.wanted = frozenset(needs.items())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for i in vis:
            r = self.page_rect(i)
            p.fillRect(r, QColor(255, 255, 255))
            ent = self.cache.get(i)
            if ent:
                self.cache.move_to_end(i)
                self._draw_page(p, r, ent[0])
            else:
                p.setPen(QColor(150, 150, 150))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"第 {i + 1} 頁\n載入中…")
        for i in order:
            need = needs[i]
            ent = self.cache.get(i)
            if not ent or (ent[2] and abs(ent[1] - need) > need * 0.03):
                self._request(i, need)

    def _draw_page(self, p, r, pm):
        if self.rot == 0:
            p.drawPixmap(r, pm, QRectF(pm.rect()))
            return
        p.save()
        p.translate(r.center())
        p.rotate(self.rot)
        w, h = (r.height(), r.width()) if self.rot % 180 else (r.width(), r.height())
        p.drawPixmap(QRectF(-w / 2, -h / 2, w, h), pm, QRectF(pm.rect()))
        p.restore()

    def _need_width(self, i, w):
        return int(max(16, min(round(w), self.doc.max_px(i))))

    def _request(self, i, need):
        old = self.pending.get(i)
        if old is not None and abs(old - need) <= need * 0.03:
            return
        self.pending[i] = need
        doc, did = self.doc, self.doc_id
        key = (i, need)
        self.pool.start(Task(self.bridge, ("render", did, i, need), lambda: doc.render(i, need),
                             should_run=lambda: key in self.wanted and self.doc_id == did))

    def _on_render(self, token, img, err):
        _, did, i, need = token
        if did != self.doc_id or not self.doc:
            return
        if self.pending.get(i) == need:
            del self.pending[i]
        if err or img is None:
            if err and err != "skipped":
                old = self.cache.pop(i, None)
                if old:
                    self.cache_bytes -= old[2]
                self.cache[i] = (self._error_pixmap(err), need, 0)  # nbytes 0 表示錯誤頁，不再重試
                self.viewport().update()
            return
        pm = QPixmap.fromImage(img)
        old = self.cache.pop(i, None)
        if old:
            self.cache_bytes -= old[2]
        nbytes = max(1, pm.width() * pm.height() * 4)
        self.cache[i] = (pm, need, nbytes)
        self.cache_bytes += nbytes
        while self.cache_bytes > CACHE_LIMIT and len(self.cache) > 4:
            _, e = self.cache.popitem(last=False)
            self.cache_bytes -= e[2]
        # 實際頁面比例與版面不符（例如原檔旋轉頁）時修正
        w, h = self.doc.sizes[i]
        if img.width() > 0 and abs(img.height() / img.width() - h / w) > 0.02 * (h / w):
            self.doc.sizes[i] = (w, w * img.height() / img.width())
            self.relayout_timer.start()
        elif self.mode == "visible" and i == self.cur and i not in self.crop:
            self.relayout_timer.start()
        self.viewport().update()

    def _error_pixmap(self, err):
        pm = QPixmap(400, 200)
        pm.fill(QColor(255, 235, 235))
        q = QPainter(pm)
        q.setPen(QColor(160, 0, 0))
        q.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "無法顯示此頁\n" + err)
        q.end()
        return pm

    # ---- 捲動與目前頁
    def scrollContentsBy(self, dx, dy):
        self.viewport().update()
        self._update_current()

    def _update_current(self):
        if not self.row_tops:
            return
        vb = self.verticalScrollBar()
        if self.continuous():
            y = vb.value() + self.viewport().height() * 0.3
            idx = max(0, min(len(self.row_tops) - 1, bisect.bisect_right(self.row_tops, y) - 1))
            if vb.value() >= vb.maximum() > 0:
                idx = len(self.row_tops) - 1
            i = self.rows[self.disp_rows[idx]][0]
        else:
            i = self.rows[self.disp_rows[0]][0]
        if i != self.cur:
            self.cur = i
            self.pageChanged.emit(i)

    def goto(self, i, frac=0.0, bottom=False):
        if not self.doc:
            return
        i = max(0, min(self.doc.n - 1, i))
        r = self.row_of[i]
        old_row = self.cur_row()
        self.cur = self.rows[r][0]
        if (not self.continuous() and r not in self.disp_rows) or \
                (self.mode in ("page", "visible") and r != old_row):
            self.relayout(False)
        vb = self.verticalScrollBar()
        if bottom:
            vb.setValue(vb.maximum())
        elif not self.continuous() and frac == 0:
            vb.setValue(0)
        else:
            g = self.geo[i]
            y = g.top() + frac * g.height() - (self.gap() / 2 if frac == 0 else 0)
            if self.continuous() and frac == 0:
                y = self.row_tops[self.disp_rows.index(r)] - self.gap() / 2
            vb.setValue(int(y))
        self.pageChanged.emit(self.cur)
        self.viewport().update()

    def next_page(self):
        r = self.cur_row()
        if r + 1 < len(self.rows):
            self.goto(self.rows[r + 1][0])

    def prev_page(self):
        r = self.cur_row()
        if r > 0:
            self.goto(self.rows[r - 1][0])

    def position(self):
        anc = self.anchor()
        return list(anc) if anc else [0, 0.0]

    def _flip(self, forward):
        """單頁／雙頁檢視（不連續）捲到頂底時翻頁。"""
        import time
        now = time.monotonic()
        if now < self.flip_block:
            return True
        r = self.cur_row()
        if forward and r + 1 < len(self.rows):
            self.goto(self.rows[r + 1][0])
        elif not forward and r > 0:
            self.goto(self.rows[r - 1][0], bottom=True)
        else:
            return False
        self.flip_block = now + 0.25
        return True

    # ---- 縮放
    def percent(self):
        return round(self.z * 75)

    def set_mode(self, mode):
        self.mode = mode
        self.relayout(True)

    def set_percent(self, pct, anchor_pt=None):
        pct = max(10, min(800, pct))
        self.mode = "custom"
        if anchor_pt is None or not self.doc:
            self.z = pct / 75
            self.relayout(True)
            return
        vb, hb = self.verticalScrollBar(), self.horizontalScrollBar()
        old_z = self.z
        cy = vb.value() + anchor_pt.y()
        cx = hb.value() + anchor_pt.x()
        self.z = pct / 75
        self.relayout(False)
        ratio = self.z / old_z
        vb.setValue(int(cy * ratio - anchor_pt.y()))
        hb.setValue(int(cx * ratio - anchor_pt.x()))

    # ---- 自動捲動
    def toggle_auto(self):
        if self.auto_timer.isActive():
            self.stop_auto()
        elif self.doc:
            self.auto_acc = 0.0
            self.auto_timer.start()
            self.autoScrollChanged.emit(True)

    def stop_auto(self):
        if self.auto_timer.isActive():
            self.auto_timer.stop()
            self.autoScrollChanged.emit(False)

    def _auto_step(self):
        vb = self.verticalScrollBar()
        if vb.value() >= vb.maximum():
            if not self.continuous() and self.cur_row() + 1 < len(self.rows):
                self.goto(self.rows[self.cur_row() + 1][0])
            else:
                self.stop_auto()
            return
        self.auto_acc += self.auto_speed
        step = int(self.auto_acc)
        self.auto_acc -= step
        if step:
            vb.setValue(vb.value() + step)

    # ---- 事件
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.relayout(True)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.set_percent(round(self.percent() * step), ev.position())
            return
        if self.doc and not self.continuous() and not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            vb = self.verticalScrollBar()
            dy = ev.angleDelta().y()
            if (dy < 0 and vb.value() >= vb.maximum()) or (dy > 0 and vb.value() <= vb.minimum()):
                if self._flip(dy < 0):
                    return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        vb = self.verticalScrollBar()
        k = ev.key()
        vh = self.viewport().height()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.auto_timer.isActive():
            if k == Qt.Key.Key_Down:
                self.auto_speed = min(40.0, self.auto_speed * 1.4)
                return
            if k == Qt.Key.Key_Up:
                self.auto_speed = max(0.3, self.auto_speed / 1.4)
                return
            if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
                self.auto_speed = 0.5 + (k - Qt.Key.Key_0) * 1.5
                return
            if k == Qt.Key.Key_Minus:
                self.auto_speed = -abs(self.auto_speed)
                return
        down = k in (Qt.Key.Key_PageDown, Qt.Key.Key_Down) or (k == Qt.Key.Key_Space and not shift)
        up = k in (Qt.Key.Key_PageUp, Qt.Key.Key_Up) or (k == Qt.Key.Key_Space and shift)
        if self.doc and not self.continuous():
            if down and vb.value() >= vb.maximum() and self._flip(True):
                return
            if up and vb.value() <= vb.minimum() and self._flip(False):
                return
        if k in (Qt.Key.Key_PageDown, Qt.Key.Key_Space) and not shift:
            vb.setValue(vb.value() + int(vh * 0.9))
        elif k == Qt.Key.Key_PageUp or (k == Qt.Key.Key_Space and shift):
            vb.setValue(vb.value() - int(vh * 0.9))
        elif k == Qt.Key.Key_Down:
            vb.setValue(vb.value() + 60)
        elif k == Qt.Key.Key_Up:
            vb.setValue(vb.value() - 60)
        elif k == Qt.Key.Key_Right:
            self.next_page()
        elif k == Qt.Key.Key_Left:
            self.prev_page()
        elif k == Qt.Key.Key_Home:
            self.goto(0)
        elif k == Qt.Key.Key_End:
            self.goto(self.page_count() - 1)
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        self.stop_auto()
        if ev.button() == Qt.MouseButton.LeftButton:
            self.drag = (ev.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self.drag:
            p0, h0, v0 = self.drag
            d = ev.position() - p0
            self.horizontalScrollBar().setValue(int(h0 - d.x()))
            self.verticalScrollBar().setValue(int(v0 - d.y()))

    def mouseReleaseEvent(self, ev):
        self.drag = None
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)


# ---------------------------------------------------------------- 主視窗

def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.settings = load_settings()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 4) // 2)))
        self.bridge = Bridge()
        self.bridge.done.connect(self._on_task)
        self.doc = None
        self.path = None
        self.doc_id = 0
        self.reading_mode = False

        self.view = PageView(self.pool, self)
        s = self.settings
        self.view.mode = s.get("zoom") if s.get("zoom") in ZOOM_NAMES else "width"
        self.view.layout_mode = s.get("layout") if s.get("layout") in LAYOUTS else "single_cont"
        self.view.cover = bool(s.get("cover", True))
        self.view.gaps = bool(s.get("gaps", True))
        self.setCentralWidget(self.view)
        self.view.pageChanged.connect(self._on_page)
        self.view.zoomChanged.connect(self._on_zoom)

        self._build_actions()
        self._build_outline()
        self._build_menus()
        self._build_toolbar()
        self.setAcceptDrops(True)
        self.resize(1100, 1000)
        geo = s.get("geometry")
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except ValueError:
                pass
        self.setWindowTitle(APP_TITLE)
        if path:
            QTimer.singleShot(0, lambda: self.open_file(path))

    # ---- 動作
    def _act(self, text, fn=None, shortcut=None, checkable=False, tip=None):
        a = QAction(text, self, checkable=checkable)
        if shortcut:
            a.setShortcuts([QKeySequence(x) for x in (shortcut if isinstance(shortcut, (list, tuple)) else [shortcut])])
        if fn:
            a.triggered.connect(fn)
        if tip:
            a.setToolTip(tip)
        self.addAction(a)  # 隱藏工具列時快速鍵照樣有效
        return a

    def _build_actions(self):
        v = self.view
        self.a_open = self._act("開啟…", self.open_dialog, "Ctrl+O")
        self.a_quit = self._act("結束", self.close, ["Ctrl+Q", "Ctrl+W"])
        self.a_rot_cw = self._act("順時針", lambda: v.rotate(90), ["Ctrl+Shift++", "Ctrl+Shift+="])
        self.a_rot_ccw = self._act("逆時針", lambda: v.rotate(-90), "Ctrl+Shift+-")
        self.a_first = self._act("第一頁", lambda: v.goto(0))
        self.a_prev = self._act("上一頁", v.prev_page)
        self.a_next = self._act("下一頁", v.next_page)
        self.a_last = self._act("最後一頁", lambda: v.goto(v.page_count() - 1))
        self.a_gotopage = self._act("移至頁面…", self._focus_page, ["Ctrl+Shift+N", "Ctrl+G"])

        self.layout_group = QActionGroup(self)
        self.a_layout = {}
        for key, name in LAYOUTS.items():
            a = self._act(name, lambda _=False, k=key: self._set_layout(k), checkable=True)
            a.setChecked(v.layout_mode == key)
            self.layout_group.addAction(a)
            self.a_layout[key] = a
        self.a_gaps = self._act("顯示頁面之間的間隙", lambda c: self._set_flag("gaps", c), checkable=True)
        self.a_gaps.setChecked(v.gaps)
        self.a_cover = self._act("在雙頁檢視中顯示封面頁", lambda c: self._set_flag("cover", c), checkable=True)
        self.a_cover.setChecked(v.cover)
        self.a_auto = self._act("自動捲動", v.toggle_auto, "Ctrl+Shift+H", checkable=True)
        v.autoScrollChanged.connect(self.a_auto.setChecked)

        self.zoom_group = QActionGroup(self)
        self.a_zoom_to = self._act("縮放至…", self._focus_zoom, "Ctrl+Y")
        self.a_actual = self._act("實際大小", lambda: v.set_percent(100), "Ctrl+1")
        self.a_fitpage = self._act("縮放至頁面層級", lambda: v.set_mode("page"), "Ctrl+0")
        self.a_fitwidth = self._act("符合寬度", lambda: v.set_mode("width"), "Ctrl+2")
        self.a_fitvis = self._act("符合可見區域", lambda: v.set_mode("visible"), "Ctrl+3")
        self.a_zin = self._act("放大", lambda: v.set_percent(round(v.percent() * 1.2)), ["Ctrl+=", "Ctrl++"])
        self.a_zout = self._act("縮小", lambda: v.set_percent(round(v.percent() / 1.2)), "Ctrl+-")

        self.a_outline = self._act("目錄", None, "F4", checkable=True)
        self.a_toolbar = self._act("工具列", None, None, checkable=True)
        self.a_reading = self._act("閱讀模式", self._toggle_reading, "Ctrl+H", checkable=True)
        self.a_full = self._act("全螢幕模式", self._toggle_full, ["Ctrl+L", "F11"])
        self.a_esc = self._act("", self._escape, "Esc")

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("檔案(&F)")
        f.addAction(self.a_open)
        self.recent_menu = f.addMenu("最近開啟的檔案")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        f.addAction(self.a_quit)

        m = mb.addMenu("檢視(&V)")
        rot = m.addMenu("旋轉檢視")
        rot.addActions([self.a_rot_cw, self.a_rot_ccw])
        nav = m.addMenu("頁面導覽")
        nav.addActions([self.a_first, self.a_prev, self.a_next, self.a_last])
        nav.addSeparator()
        nav.addAction(self.a_gotopage)
        self.layout_menu = m.addMenu("頁面顯示")
        self._fill_layout_menu(self.layout_menu)
        zm = m.addMenu("縮放")
        zm.addActions([self.a_zoom_to, self.a_actual, self.a_fitpage, self.a_fitwidth, self.a_fitvis])
        zm.addSeparator()
        zm.addActions([self.a_zin, self.a_zout])
        m.addSeparator()
        sh = m.addMenu("顯示／隱藏")
        sh.addActions([self.a_outline, self.a_toolbar])
        m.addSeparator()
        m.addActions([self.a_reading, self.a_full])

    def _fill_layout_menu(self, menu):
        menu.addActions(list(self.a_layout.values()))
        menu.addSeparator()
        menu.addActions([self.a_gaps, self.a_cover])
        menu.addSeparator()
        menu.addAction(self.a_auto)

    def _build_toolbar(self):
        tb = QToolBar("工具列", self)
        self.toolbar = tb
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)

        self.open_btn = QToolButton(self)
        self.open_btn.setText("開啟")
        self.open_btn.setToolTip("開啟檔案（Ctrl+O）")
        self.open_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        tb_recent = QMenu(self)
        tb_recent.aboutToShow.connect(lambda: self._fill_recent(tb_recent))
        self.open_btn.setMenu(tb_recent)
        self.open_btn.clicked.connect(self.open_dialog)
        tb.addWidget(self.open_btn)
        tb.addAction(self.a_outline)

        tb.addSeparator()

        # 顯示方式放在右下角狀態列，跟 Adobe Reader 一樣
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.disp_btn = QToolButton(self)
        self.disp_btn.setStyleSheet("QToolButton { padding: 1px 8px; } "
                                    "QToolButton::menu-indicator { image: none; width: 0px; }")
        self.disp_btn.setToolTip("頁面顯示：單頁／雙頁、連續捲動、封面頁、間隙、旋轉、自動捲動")
        self.disp_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        dm = QMenu(self)
        self._fill_layout_menu(dm)
        dm.addSeparator()
        dm.addActions([self.a_rot_cw, self.a_rot_ccw])
        self.disp_btn.setMenu(dm)
        sb.addPermanentWidget(self.disp_btn)
        self._update_disp_btn()

        prev = QAction("◀", self)
        prev.setToolTip("上一頁")
        prev.triggered.connect(self.view.prev_page)
        tb.addAction(prev)
        self.page_edit = QLineEdit(self)
        self.page_edit.setFixedWidth(60)
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setValidator(QIntValidator(1, 999999, self))
        self.page_edit.returnPressed.connect(self._goto_edit)
        tb.addWidget(self.page_edit)
        self.total_label = QLabel(" / 0 ", self)
        tb.addWidget(self.total_label)
        nxt = QAction("▶", self)
        nxt.setToolTip("下一頁")
        nxt.triggered.connect(self.view.next_page)
        tb.addAction(nxt)
        tb.addSeparator()

        zout = QAction("－", self)
        zout.setToolTip("縮小（Ctrl+-）")
        zout.triggered.connect(self.a_zout.trigger)
        tb.addAction(zout)
        self.zoom_box = QComboBox(self)
        self.zoom_box.setEditable(True)
        self.zoom_box.setFixedWidth(130)
        self.zoom_box.addItems(list(ZOOM_NAMES.values()) + ["50%", "75%", "100%", "125%", "150%", "200%",
                                                             "300%", "400%"])
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_box.textActivated.connect(self._zoom_text)
        tb.addWidget(self.zoom_box)
        zin = QAction("＋", self)
        zin.setToolTip("放大（Ctrl++）")
        zin.triggered.connect(self.a_zin.trigger)
        tb.addAction(zin)

        self.a_toolbar.setChecked(True)
        self.a_toolbar.toggled.connect(tb.setVisible)

    def _build_outline(self):
        self.dock = QDockWidget("目錄", self)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.tree = QTreeWidget(self.dock)
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._outline_go)
        self.tree.itemActivated.connect(self._outline_go)
        self.dock.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)
        self.dock.setVisible(bool(self.settings.get("outline", False)))
        self.a_outline.setChecked(self.dock.isVisible())
        self.a_outline.toggled.connect(self.dock.setVisible)
        self.dock.visibilityChanged.connect(lambda vis: (self.a_outline.setChecked(vis)
                                                         if not self.isMinimized() and not self.reading_mode
                                                         else None))
        self.resizeDocks([self.dock], [280], Qt.Orientation.Horizontal)

    # ---- 檢視切換
    def _set_layout(self, key):
        self.view.set_layout(layout_mode=key)
        self.settings["layout"] = key
        self._update_disp_btn()

    def _update_disp_btn(self):
        if hasattr(self, "disp_btn"):
            self.disp_btn.setText(f"頁面顯示：{LAYOUTS[self.view.layout_mode]} ▴")

    def _set_flag(self, name, on):
        self.view.set_layout(**{name: on})
        self.settings[name] = on

    def _toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_reading(self, on):
        """閱讀模式：隱藏功能表、工具列與目錄，只留頁面。"""
        self.reading_mode = on
        if on:
            self._saved_ui = (self.toolbar.isVisible(), self.dock.isVisible())
            self.menuBar().hide()
            self.statusBar().hide()
            self.toolbar.hide()
            self.dock.hide()
        else:
            tbv, dv = getattr(self, "_saved_ui", (True, False))
            self.menuBar().show()
            self.statusBar().show()
            self.toolbar.setVisible(tbv)
            self.dock.setVisible(dv)
        self.a_reading.setChecked(on)

    def _escape(self):
        if self.view.auto_timer.isActive():
            self.view.stop_auto()
        elif self.reading_mode:
            self._toggle_reading(False)
        elif self.isFullScreen():
            self.showNormal()

    def _focus_page(self):
        if self.toolbar.isVisible():
            self.page_edit.setFocus()
            self.page_edit.selectAll()

    def _focus_zoom(self):
        if self.toolbar.isVisible():
            self.zoom_box.setFocus()
            self.zoom_box.lineEdit().selectAll()

    # ---- 開檔
    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else self.settings.get("last_dir", "")
        p, _ = QFileDialog.getOpenFileName(self, "開啟檔案", start, OPEN_FILTER)
        if p:
            self.open_file(p)

    def _fill_recent(self, menu=None):
        menu = menu or self.recent_menu
        menu.clear()
        rec = [p for p in self.settings.get("recent", []) if os.path.exists(p)][:15]
        if not rec:
            a = menu.addAction("（沒有最近開啟的檔案）")
            a.setEnabled(False)
        for p in rec:
            a = menu.addAction(os.path.basename(p))
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, p=p: self.open_file(p))

    def open_file(self, path):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.view.set_message("找不到檔案：\n" + path)
            return
        self._save_position()
        self._close_doc()
        self.doc_id += 1
        self.path = path
        self.setWindowTitle(f"{os.path.basename(path)} - {APP_TITLE}")
        self.view.set_message(f"正在開啟 {os.path.basename(path)} …")
        self.total_label.setText(" / … ")
        self.page_edit.clear()
        self.tree.clear()
        self.pool.start(Task(self.bridge, ("open", self.doc_id, path), lambda: open_doc(path)))

    def _close_doc(self):
        if self.doc:
            d = self.doc
            self.doc = None
            self.view.set_message("")
            # 等背景渲染結束後再關檔、刪暫時連結
            QTimer.singleShot(3000, d.close)

    def _on_task(self, token, value, err):
        kind, did = token[0], token[1]
        if did != self.doc_id:
            if kind == "open" and value is not None:
                value.close()
            return
        if kind == "open":
            if err:
                self.view.set_message(f"無法開啟 {os.path.basename(token[2])}\n\n{err}")
                self.total_label.setText(" / 0 ")
                return
            self.doc = value
            self.view.set_doc(value, did)
            self.total_label.setText(f" / {value.n} ")
            pos = self.settings.get("positions", {}).get(self.path)
            if pos:
                self.view.goto(int(pos[0]), float(pos[1]))
            else:
                self._on_page(0)
            self._add_recent(self.path)
            doc = value
            self.pool.start(Task(self.bridge, ("details", did), doc.load_details))
        elif kind == "details" and self.doc and not err:
            pos = self.view.position()
            self.doc.apply_details(value)
            self.view.sizes_changed()
            self.view.goto(pos[0], pos[1])
            self._fill_outline(self.doc.outline)

    def _fill_outline(self, outline):
        self.tree.clear()

        def add(parent, nodes):
            for title, page, kids in nodes:
                it = QTreeWidgetItem([title])
                it.setData(0, Qt.ItemDataRole.UserRole, page)
                if page is not None:
                    it.setToolTip(0, f"{title}（第 {page + 1} 頁）")
                parent.addChild(it) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(it)
                add(it, kids)
        add(self.tree, outline)
        if not outline:
            it = QTreeWidgetItem(["（此檔案沒有目錄）"])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree.addTopLevelItem(it)

    def _outline_go(self, item, _col=0):
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.view.goto(page)
            self.view.setFocus()

    # ---- 狀態
    def _on_page(self, i):
        if not self.page_edit.hasFocus():
            self.page_edit.setText(str(i + 1))

    def _on_zoom(self):
        self.zoom_box.setEditText(ZOOM_NAMES.get(self.view.mode, f"{self.view.percent()}%"))

    def _goto_edit(self):
        t = self.page_edit.text()
        if t.isdigit():
            self.view.goto(int(t) - 1)
        self.view.setFocus()

    def _zoom_text(self, t):
        rev = {v: k for k, v in ZOOM_NAMES.items()}
        if t in rev:
            self.view.set_mode(rev[t])
        else:
            m = re.search(r"\d+", t)
            if m:
                self.view.set_percent(int(m.group()))
        self.view.setFocus()

    def _add_recent(self, path):
        rec = [p for p in self.settings.get("recent", []) if p != path]
        self.settings["recent"] = [path] + rec[:29]
        self.settings["last_dir"] = os.path.dirname(path)
        self._write_settings()

    def _save_position(self):
        if self.doc and self.path:
            pos = self.settings.setdefault("positions", {})
            pos.pop(self.path, None)
            pos[self.path] = self.view.position()
            while len(pos) > 1000:
                pos.pop(next(iter(pos)))

    def _write_settings(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=1)
            os.replace(tmp, SETTINGS_PATH)
        except OSError:
            pass

    def closeEvent(self, ev):
        self.view.stop_auto()
        self._save_position()
        if self.reading_mode:
            self._toggle_reading(False)
        self.settings["geometry"] = bytes(self.saveGeometry()).hex()
        self.settings["outline"] = self.dock.isVisible()
        if self.view.mode in ZOOM_NAMES:
            self.settings["zoom"] = self.view.mode
        self._write_settings()
        self.doc_id += 1
        self.view.doc_id = -1
        self.pool.clear()
        self.pool.waitForDone(3000)
        if self.doc:
            self.doc.close()
        super().closeEvent(ev)

    # ---- 拖放
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_file(urls[0])
            for extra in urls[1:]:  # 多個檔案：其餘各開一個視窗
                w = MainWindow(extra)
                w.show()
                _windows.append(w)


_windows = []


def dark_palette(app):
    app.setStyle("Fusion")
    pal = QPalette()
    c = {
        QPalette.ColorRole.Window: QColor(45, 45, 45),
        QPalette.ColorRole.WindowText: QColor(225, 225, 225),
        QPalette.ColorRole.Base: QColor(32, 32, 32),
        QPalette.ColorRole.AlternateBase: QColor(45, 45, 45),
        QPalette.ColorRole.Text: QColor(225, 225, 225),
        QPalette.ColorRole.Button: QColor(55, 55, 55),
        QPalette.ColorRole.ButtonText: QColor(225, 225, 225),
        QPalette.ColorRole.Highlight: QColor(42, 110, 190),
        QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
        QPalette.ColorRole.ToolTipBase: QColor(60, 60, 60),
        QPalette.ColorRole.ToolTipText: QColor(230, 230, 230),
    }
    for role, col in c.items():
        pal.setColor(role, col)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 120))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    app.setPalette(pal)


def main():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("alexwudev.DjvuReader")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    f = QFont("Microsoft JhengHei UI", 10)
    app.setFont(f)
    dark_palette(app)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    ico = os.path.join(base, "DjvuReader.ico")
    if os.path.isfile(ico):
        app.setWindowIcon(QIcon(ico))
    threading.Thread(target=cleanup_links, daemon=True).start()
    w = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    w.show()
    _windows.append(w)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
