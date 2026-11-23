# DjvuReader

**English** | [繁體中文](README.zh-TW.md)

A lightweight Windows document reader for quickly opening and reading files, modeled on Adobe Reader.
Supports **DjVu**, **FB2** (including `.fb2.zip`), **EPUB** and **PDF**.

![icon](icon_preview.png)

## Features

- **Large files never freeze the window**: opening files, reading page sizes and rendering all run on background threads, and only the visible pages (plus their neighbors) are rendered.
- **DjVu files with non-ASCII names just work**: the DjVuLibre command-line tools only accept ANSI paths, so files named in Chinese, Russian and other scripts fail to open. DjvuReader creates a hard link on the same drive (no extra disk space, the original file is untouched) for DjVuLibre to read, and removes it when the file is closed.
- **Remembers where you stopped**: each file reopens at your last reading position.
- **Page display** (bottom-right corner, or View → Page Display): Single Page, Enable Scrolling, Two Page, Two Page Scrolling, with options for gaps between pages and showing the cover page in two-page view.
- **Zoom**: Actual Size, Zoom to Page Level, Fit Width, Fit Visible (trims page margins automatically); Ctrl + mouse wheel zooms around the cursor.
- **Rotate view, automatic scrolling, reading mode and full screen.**
- Outline (bookmarks) sidebar, recent files, drag-and-drop opening and a dark interface.

## Keyboard shortcuts (same as Adobe Reader)

| Action | Shortcut |
| --- | --- |
| Open | Ctrl+O |
| Go to page | Ctrl+Shift+N / Ctrl+G |
| Zoom to page level / Actual size / Fit width / Fit visible | Ctrl+0 / Ctrl+1 / Ctrl+2 / Ctrl+3 |
| Zoom in / Zoom out | Ctrl+= / Ctrl+- |
| Rotate clockwise / counterclockwise | Ctrl+Shift++ / Ctrl+Shift+- |
| Automatic scrolling (↑↓ change speed, 0–9 set speed) | Ctrl+Shift+H |
| Reading mode | Ctrl+H |
| Full screen | Ctrl+L / F11 |
| Outline | F4 |
| Previous / next page | ← / → |

## Running and packaging

Requirements: Python 3.12, PyQt6, PyMuPDF and [DjVuLibre](https://djvu.sourceforge.net/) (`ddjvu`, `djvused`, `djvudump`; install with scoop: `scoop install djvulibre`).

```powershell
pip install PyQt6 pymupdf pyinstaller
python djvu_reader.py file.djvu
```

Build a standalone program (DjVuLibre is bundled into `_internal\djvulibre`):

```powershell
python -m PyInstaller --noconfirm DjvuReader.spec
```

The output is in `dist\DjvuReader\`; copy it to `%LOCALAPPDATA%\Programs\DjvuReader\`.
`python make_reg.py` generates `DjvuReader_register.reg`; import it to make DjvuReader available under "Open with" for .djvu / .djv / .fb2 / .epub files.
`python make_icon.py` regenerates the icon.

Settings and reading positions are stored in `%LOCALAPPDATA%\DjvuReader\settings.json`.

## Licenses

DjVu decoding uses [DjVuLibre](https://djvu.sourceforge.net/) (GPL); FB2 / EPUB / PDF use [PyMuPDF](https://pymupdf.readthedocs.io/) (AGPL).
