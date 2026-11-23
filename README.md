# DjvuReader

精簡的 Windows 文件閱讀器，專門用來「快速打開、看內容」，操作方式參考 Adobe Reader。
支援 **DjVu**、**FB2**（含 `.fb2.zip`）、**EPUB**、**PDF**。

![icon](icon_preview.png)

## 特色

- **大檔不卡死**：開檔、讀頁面尺寸、渲染都在背景執行緒，介面隨時可操作；只渲染看得到的頁面與前後頁。
- **非英數檔名的 DjVu 也開得了**：DjVuLibre 命令列工具只吃 ANSI 路徑，檔名含中文、俄文等字元時會直接失敗。本程式改在同一磁碟建硬連結（不佔空間、不動原檔）給 DjVuLibre 讀，關檔後自動刪除。
- **記住閱讀位置**：每個檔案重開時回到上次讀到的地方。
- **頁面顯示**（右下角或「檢視 → 頁面顯示」）：單頁檢視、啟用捲動、雙頁檢視、雙頁捲動，可切換頁面間隙、雙頁檢視的封面頁。
- **縮放**：實際大小、縮放至頁面層級、符合寬度、符合可見區域（自動裁掉頁邊空白），Ctrl＋滾輪以游標為中心縮放。
- **旋轉檢視、自動捲動、閱讀模式、全螢幕**。
- 目錄（書籤）側欄、最近開啟的檔案、拖放開檔、暗色介面。

## 快速鍵（與 Adobe Reader 相同）

| 功能 | 快速鍵 |
| --- | --- |
| 開啟 | Ctrl+O |
| 移至頁面 | Ctrl+Shift+N／Ctrl+G |
| 縮放至頁面層級／實際大小／符合寬度／符合可見區域 | Ctrl+0／Ctrl+1／Ctrl+2／Ctrl+3 |
| 放大／縮小 | Ctrl+=／Ctrl+- |
| 順時針／逆時針旋轉 | Ctrl+Shift++／Ctrl+Shift+- |
| 自動捲動（↑↓ 調速、0–9 設速度） | Ctrl+Shift+H |
| 閱讀模式 | Ctrl+H |
| 全螢幕 | Ctrl+L／F11 |
| 目錄 | F4 |
| 上一頁／下一頁 | ←／→ |

## 執行與打包

需求：Python 3.12、PyQt6、PyMuPDF，以及 [DjVuLibre](https://djvu.sourceforge.net/)（`ddjvu`、`djvused`、`djvudump`；用 scoop 安裝：`scoop install djvulibre`）。

```powershell
pip install PyQt6 pymupdf pyinstaller
python djvu_reader.py 檔案.djvu
```

打包成獨立程式（會把 DjVuLibre 一併打包進 `_internal\djvulibre`）：

```powershell
python -m PyInstaller --noconfirm DjvuReader.spec
```

產物在 `dist\DjvuReader\`，可複製到 `%LOCALAPPDATA%\Programs\DjvuReader\`。
`python make_reg.py` 會產生 `DjvuReader_register.reg`，匯入後可在「開啟檔案」中選用本程式開 .djvu／.djv／.fb2／.epub。
`python make_icon.py` 重新產生圖示。

設定與閱讀位置存在 `%LOCALAPPDATA%\DjvuReader\settings.json`。

## 授權說明

DjVu 解碼使用 [DjVuLibre](https://djvu.sourceforge.net/)（GPL），FB2／EPUB／PDF 使用 [PyMuPDF](https://pymupdf.readthedocs.io/)（AGPL）。
