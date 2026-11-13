"""Un-Mix-a-Lot as a native desktop window (no browser tab, no console).

Launched by `Un-Mix-a-Lot.vbs` / `Un-Mix-a-Lot.bat` (Windows, through pythonw.exe) or `Un-Mix-a-Lot.app` (Mac). Everything that would have
printed to a console goes to logs/stemsplit.log; errors that stop the app are shown in a message box.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "logs" / "stemsplit.log"

_FALLBACK_SPLASH = """<!doctype html><meta charset="utf-8"><title>Un-Mix-a-Lot</title>
<body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
background:#0b0f19;color:#e5e7eb;font-family:system-ui,-apple-system,"Segoe UI",sans-serif">
<div style="font-size:26px;font-weight:700">Un-Mix-a-Lot</div>
<div style="margin:18px 0 10px;width:34px;height:34px;border:4px solid #374151;border-top-color:#f97316;
border-radius:50%;animation:s 1s linear infinite"></div>
<div id=m style="opacity:.75;font-size:14px">Starting… the first launch can take up to a minute.</div>
<style>@keyframes s{to{transform:rotate(360deg)}}</style></body>"""


def _load_splash() -> str:
    """The animated logo screen (splash.html); falls back to a plain one if the file is missing."""
    try:
        return (ROOT / "splash.html").read_text(encoding="utf-8")
    except Exception:
        return _FALLBACK_SPLASH


SPLASH = _load_splash()


def _message(text: str, error: bool = True) -> None:
    """Native message box on Windows and macOS; otherwise just the log."""
    print(text)
    if sys.platform == "darwin":
        try:
            esc = text.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display dialog "{esc}" with title "Un-Mix-a-Lot" buttons {{"OK"}} '
                            f'default button "OK" with icon {"stop" if error else "note"}'], timeout=120)
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Un-Mix-a-Lot", 0x10 if error else 0x40)
        except Exception:
            pass


def _refresh_shortcut_icons() -> None:
    """Windows: point the desktop and Start menu shortcuts at our icon and make Explorer redraw them.
    Runs once per icon.ico change (stamp in logs/), in the background, never blocks or breaks startup."""
    if sys.platform != "win32":
        return
    try:
        icon = ROOT / "icon.ico"
        stamp = ROOT / "logs" / "icon_stamp.txt"
        mark = f"{icon.stat().st_mtime_ns}"
        if stamp.exists() and stamp.read_text().strip() == mark:
            return
        ps = (
            "$ico='%s,0'; $w=New-Object -ComObject WScript.Shell; "
            "$d=[Environment]::GetFolderPath('Desktop'); "
            "$sm=Join-Path ([Environment]::GetFolderPath('Programs')) 'Un-Mix-a-Lot'; "
            "foreach($p in @((Join-Path $d 'Un-Mix-a-Lot.lnk'),(Join-Path $d 'Stem Split.lnk'),(Join-Path $sm 'Un-Mix-a-Lot.lnk'))){ "
            "if(Test-Path $p){ $s=$w.CreateShortcut($p); $s.IconLocation=$ico; $s.Save() } }" % str(icon).replace("'", "''")
        )
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=60, creationflags=0x08000000)
        print("shortcut icons:", r.returncode, r.stderr[-300:])
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED: redraw icons
        if r.returncode == 0:
            stamp.write_text(mark)
    except Exception:
        traceback.print_exc()


def _setup_logging() -> None:
    LOG.parent.mkdir(exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 2_000_000:
        LOG.write_text("")
    f = open(LOG, "a", buffering=1, encoding="utf-8")
    # pythonw has no console: without this, any print() would crash the app.
    sys.stdout = sys.stderr = f
    print("\n===== Un-Mix-a-Lot starting =====")


def _ensure_webview():
    try:
        import webview  # noqa: F401
    except ImportError:
        print("Installing pywebview (one time)…")
        # pythonw.exe has no console streams, which breaks pip; use python.exe with a hidden window.
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
            exe = exe.parent / "python.exe"
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        r = subprocess.run([str(exe), "-m", "pip", "install", "pywebview"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, creationflags=flags)
        print(r.stdout[-3000:], r.stderr[-3000:])
        if r.returncode != 0:
            raise RuntimeError(f"pip install pywebview failed (exit {r.returncode}); see log above")
    import webview
    return webview


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


APP_ID = "UnMixALot.App"


def _set_window_identity(hwnd: int) -> None:
    """Windows: give the taskbar everything it needs to pin this window properly (name, icon and how to
    start it again), so "Pin to taskbar" keeps our icon instead of a generic one."""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    class PKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):  # 24 bytes on 64-bit Windows
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("p", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    def guid(text: str) -> GUID:
        u = uuid.UUID(text)
        return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    iid = guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")  # IPropertyStore
    store = ctypes.c_void_p()
    shell32 = ctypes.windll.shell32
    shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    if shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store)) != 0 or not store:
        return
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PKEY), ctypes.POINTER(PROPVARIANT))(vtbl[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    fmt = guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")  # System.AppUserModel.*
    exe = Path(sys.executable)
    if exe.name.lower() != "pythonw.exe" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"
    values = {5: APP_ID,                                                       # ID
              2: f'"{exe}" "{ROOT / "desktop.py"}"',                           # RelaunchCommand
              3: f"{ROOT / 'icon.ico'},0",                                     # RelaunchIconResource
              4: "Un-Mix-a-Lot"}                                               # RelaunchDisplayNameResource
    try:
        for pid, text in values.items():
            buf = ctypes.create_unicode_buffer(text)
            pv = PROPVARIANT()
            pv.vt = 31  # VT_LPWSTR
            pv.p = ctypes.cast(buf, ctypes.c_void_p).value
            hr = set_value(store, ctypes.byref(PKEY(fmt, pid)), ctypes.byref(pv))
            print("taskbar property", pid, "->", hr)
        commit(store)
    finally:
        release(store)


def _apply_window_identity() -> None:
    """Find our window (it may take a moment to appear) and set its taskbar identity."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        for _ in range(60):
            hwnd = ctypes.windll.user32.FindWindowW(None, "Un-Mix-a-Lot")
            if hwnd:
                _set_window_identity(hwnd)
                return
            time.sleep(0.5)
    except Exception:
        traceback.print_exc()


SETTINGS = ROOT / "settings.json"
MIN_W, MIN_H = 980, 640
DEFAULT_W, DEFAULT_H = 1400, 1000  # snug around the whole interface


def _screen_work_area(webview) -> tuple[int, int]:
    """Usable screen size (taskbar excluded) in the units the window size uses; (0, 0) if unknown."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            try:
                u.SetProcessDPIAware()  # same mode pywebview uses, so sizes below are real pixels
            except Exception:
                pass
            r = wintypes.RECT()
            u.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
            try:
                scale = u.GetDpiForSystem() / 96
            except Exception:
                scale = 1.0
            return int((r.right - r.left) / scale), int((r.bottom - r.top) / scale)
        scr = webview.screens[0]
        return int(scr.width), int(scr.height) - 80  # leave room for the menu bar / Dock
    except Exception:
        return 0, 0


def _initial_window_size(webview) -> tuple[int, int]:
    """Open large enough to show the whole interface: nearly the full screen, or the size you last used."""
    ww, wh = _screen_work_area(webview)
    w, h = 1280, 820
    if ww and wh:  # the interface needs about this much; on a smaller screen use what fits
        w, h = min(DEFAULT_W, ww - 60), min(DEFAULT_H, wh - 40)
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if saved.get("win_w") and saved.get("win_h"):
            w, h = int(saved["win_w"]), int(saved["win_h"])
    except Exception:
        pass
    if ww and wh:
        w, h = min(w, ww), min(h, wh)  # never bigger than the screen
    return max(w, MIN_W), max(h, MIN_H)


def _remember_window_size(win) -> None:
    """Called as the window closes: keep its size for next time. Must not return False (that would cancel closing)."""
    try:
        w, h = int(win.width), int(win.height)
        if w >= MIN_W and h >= MIN_H:
            data = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
            data["win_w"], data["win_h"] = w, h
            SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _mac_identity() -> None:
    """On a Mac, show our name and icon in the Dock and menu bar instead of Python's."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSBundle, NSImage

        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "Un-Mix-a-Lot"
        icon = ROOT / "icon.ico"
        if icon.exists():
            img = NSImage.alloc().initByReferencingFile_(str(icon))
            if img is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def main() -> None:
    os.chdir(ROOT)
    _setup_logging()
    threading.Thread(target=_refresh_shortcut_icons, daemon=True).start()
    os.environ["STEMSPLIT_DESKTOP"] = "1"
    try:
        webview = _ensure_webview()
    except Exception:
        traceback.print_exc()
        _message("Couldn't set up the app window.\n\nSee logs/stemsplit.log, or "
                 "start it in a browser with run.py instead.")
        return

    _mac_identity()
    width, height = _initial_window_size(webview)
    window = webview.create_window("Un-Mix-a-Lot", html=SPLASH, width=width, height=height,
                                   min_size=(MIN_W, MIN_H), background_color="#0b0f19")
    try:
        window.events.closing += lambda: _remember_window_size(window)
    except Exception:
        pass

    def boot(win):
        threading.Thread(target=_apply_window_identity, daemon=True).start()
        try:
            import app  # heavy imports (torch, gradio) happen here, behind the splash screen

            app.preload()
            port = _free_port()
            app.serve(prevent_thread_lock=True, port=port)
            try:  # let the logo animation finish (splash.html returns how long that takes, in ms)
                wait_ms = win.evaluate_js("window.resolveLogo ? window.resolveLogo() : 0") or 0
                time.sleep(min(float(wait_ms), 5000) / 1000)
            except Exception:
                pass
            win.load_url(f"http://127.0.0.1:{port}/?__theme=dark")
        except Exception:
            traceback.print_exc()
            _message("Un-Mix-a-Lot couldn't start.\n\nDetails are in logs/stemsplit.log.")
            win.destroy()

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass
    if sys.platform == "win32":
        try:  # own taskbar identity, so Windows shows Un-Mix-a-Lot (not "Python") and our icon
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    start_args = dict(private_mode=False, storage_path=str(ROOT / "logs" / "webview"))
    icon = ROOT / "icon.ico"
    if icon.exists():
        start_args["icon"] = str(icon)  # window + taskbar icon (otherwise pythonw.exe's Python icon is used)
    try:
        try:
            webview.start(boot, (window,), **start_args)
        except TypeError:  # an older pywebview without the icon option
            start_args.pop("icon", None)
            webview.start(boot, (window,), **start_args)
    except Exception:
        traceback.print_exc()
        _message("The app window failed to open." + (" (The Microsoft Edge WebView2 runtime may be missing.)" if sys.platform == "win32" else "") +
                 "\n\nStart it in a browser instead: python run.py. Details: logs/stemsplit.log.")
    os._exit(0)  # window closed: stop the server and any model threads


if __name__ == "__main__":
    main()
