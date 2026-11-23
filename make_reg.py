"""產生 DjvuReader_register.reg（UTF-16），註冊 .djvu／.djv／.fb2／.epub 的「開啟檔案」選項。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
exe = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "DjvuReader", "DjvuReader.exe")
e = exe.replace("\\", "\\\\")
lines = ["Windows Registry Editor Version 5.00", "",
         r"[HKEY_CURRENT_USER\Software\Classes\DjvuReader.Document]",
         '@="DjVu 文件"', "",
         r"[HKEY_CURRENT_USER\Software\Classes\DjvuReader.Document\DefaultIcon]",
         f'@="\\"{e}\\",0"', "",
         r"[HKEY_CURRENT_USER\Software\Classes\DjvuReader.Document\shell\open\command]",
         f'@="\\"{e}\\" \\"%1\\""', "",
         r"[HKEY_CURRENT_USER\Software\Classes\Applications\DjvuReader.exe\shell\open\command]",
         f'@="\\"{e}\\" \\"%1\\""', "",
         r"[HKEY_CURRENT_USER\Software\Classes\Applications\DjvuReader.exe\SupportedTypes]"]
exts = [".djvu", ".djv", ".fb2", ".epub"]
lines += [f'"{x}"=""' for x in exts] + [""]
for x in exts:
    lines += [f"[HKEY_CURRENT_USER\\Software\\Classes\\{x}\\OpenWithProgids]", '"DjvuReader.Document"=hex(0):', ""]
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DjvuReader_register.reg")
with open(out, "w", encoding="utf-16") as f:
    f.write("\r\n".join(lines) + "\r\n")
print(out)
