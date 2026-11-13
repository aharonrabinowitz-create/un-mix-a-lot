#!/usr/bin/env python3
"""One-shot local installer: creates .venv, installs PyTorch + dependencies, and sets up the Mac app.

Most people never run this by hand: double-click "Install Un-Mix-a-Lot" (Mac or Windows), which fetches
Python 3.12 for you and then runs this file. Running it yourself still works:

    python install.py            # Windows/Linux with an NVIDIA GPU: CUDA 12.8 wheels (needed for RTX 50-series)
    python install.py --cuda cu126
    python install.py --cpu      # no NVIDIA GPU: BandIt v2 and the cloud engines still work
    python3 install.py           # Mac: standard PyTorch build, creates Un-Mix-a-Lot.app; SAM Audio is skipped

SAM Audio (the NVIDIA-only engine) is installed only when it can run; force it with --with-sam, skip it with --no-sam.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
APP = "Un-Mix-a-Lot"

# Intel Macs stop at PyTorch 2.2.2, which needs numpy 1.x, and sentencepiece has no newer Intel build.
INTEL_MAC_CONSTRAINTS = "numpy<2\nsentencepiece==0.2.0\n"


def vpy() -> str:
    return str(VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


def run(*cmd):
    print("\n$", " ".join(map(str, cmd)))
    subprocess.check_call([str(c) for c in cmd])


def is_intel_mac() -> bool:
    return sys.platform == "darwin" and platform.machine() == "x86_64"


def _fix_xformers(cuda: str):
    """SAM Audio pulls in xformers. The newest xformers needs a helper package (mslk) that has no Windows build,
    so SAM would fail to load. On Windows use the last version that doesn't need it; on Linux add the helper."""
    index = "https://download.pytorch.org/whl/" + cuda
    try:
        if sys.platform == "win32":
            run(vpy(), "-m", "pip", "install", "xformers==0.0.35", "--no-deps", "--index-url", index)
        elif sys.platform.startswith("linux"):
            run(vpy(), "-m", "pip", "install", "-U", "mslk", "--index-url", index)
    except subprocess.CalledProcessError:
        print("\nCouldn't apply the xformers fix; SAM Audio may not start.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuda", default="cu128", help="PyTorch CUDA wheel tag (cu128, cu126, ...)")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--with-sam", action="store_true", help="also install SAM Audio")
    ap.add_argument("--no-sam", action="store_true", help="skip SAM Audio")
    a = ap.parse_args()

    if sys.version_info < (3, 11):
        sys.exit(f"{APP} needs Python 3.11 or newer (you have {sys.version.split()[0]}). "
                 f"Double-click \"Install {APP}\" instead: it gets the right Python for you.")
    if not VENV.exists():
        print("Creating virtual environment in .venv …")
        venv.EnvBuilder(with_pip=True).create(VENV)
    mac = sys.platform == "darwin"
    if not mac and not a.cpu and not shutil.which("nvidia-smi"):
        a.cpu = True  # no NVIDIA graphics card: skip the multi-GB GPU build; BandIt v2 and the cloud engines still work
        print("No NVIDIA GPU found: installing the smaller CPU build.")
    if is_intel_mac():
        cfile = VENV / "constraints.txt"
        cfile.write_text(INTEL_MAC_CONSTRAINTS)
        os.environ["PIP_CONSTRAINT"] = str(cfile)
        print("Intel Mac detected: using the older libraries it can run (numpy 1.x).")
    run(vpy(), "-m", "pip", "install", "--upgrade", "pip")
    if mac:  # the standard build already supports Apple chips; there is no CUDA on a Mac
        run(vpy(), "-m", "pip", "install", "torch", "torchvision", "torchaudio")
    else:
        index = "https://download.pytorch.org/whl/" + ("cpu" if a.cpu else a.cuda)
        # exact versions known to work together (newer ones can break SAM Audio's dependencies)
        run(vpy(), "-m", "pip", "install", "torch==2.11.0", "torchvision==0.26.0", "torchaudio==2.11.0",
            "--index-url", index)
    run(vpy(), "-m", "pip", "install", "-r", ROOT / "requirements.txt")
    sam = a.with_sam or not (a.no_sam or a.cpu or mac)
    sam_ok = False
    if sam:
        try:
            run(vpy(), "-m", "pip", "install", "-r", ROOT / "requirements-sam.txt")
            _fix_xformers(a.cuda)
            chk = subprocess.run([vpy(), "-c", "import sam_audio.model"], capture_output=True, text=True)
            sam_ok = chk.returncode == 0
            if not sam_ok:
                print("\nSAM Audio installed but could not start:")
                print(chk.stderr.strip().splitlines()[-1] if chk.stderr.strip() else "unknown error")
                print("Everything else works. SAM Audio will stay hidden-or-failing until this is fixed.")
        except subprocess.CalledProcessError:
            print("\nSAM Audio could not be installed (it needs Git: https://git-scm.com). "
                  "Everything else works; run this installer again after installing Git if you want SAM.")
    if mac:
        try:
            run(vpy(), ROOT / "mac_app.py")
        except subprocess.CalledProcessError:
            print("\nCouldn't create the Mac app. You can still start it with: .venv/bin/python desktop.py")

    print("\n" + "=" * 60)
    if sam_ok:
        tc = subprocess.run([vpy(), "-c", "import torchcodec"], capture_output=True, text=True)
        if tc.returncode != 0:
            print("WARNING: torchcodec could not load. It needs FFmpeg shared libraries:")
            print("  Windows: winget install Gyan.FFmpeg.Shared   (then reopen the terminal)")
            print("  Linux: sudo apt install ffmpeg")
            print(tc.stderr.strip().splitlines()[-1] if tc.stderr.strip() else "")
    print("Installed. Next steps:")
    n = 1
    if sam_ok:
        hf = VENV / ("Scripts/hf.exe" if sys.platform == "win32" else "bin/hf")
        print(f"  {n}. Request access to the SAM Audio models on Hugging Face (see README, 'Get access').")
        print(f"  {n + 1}. {hf} auth login        (paste a Hugging Face access token)")
        n += 2
    if mac:
        print(f"  {n}. Double-click '{APP}' (the app) in this folder.")
    elif sys.platform == "win32":
        print(f"  {n}. Double-click '{APP}.vbs'.")
    else:
        print(f"  {n}. python run.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
