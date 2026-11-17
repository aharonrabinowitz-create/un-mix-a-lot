"""Karaoke model test (branch experiment, not part of the app).

Runs two models on one song and reports time and peak GPU memory:
  1. karaoke (lead vocal vs everything else): aufr33 + viperx Mel-Band RoFormer
  2. vocals vs music: Kimberley Jensen Mel-Band RoFormer
Weights are downloaded by audio-separator the first time (about 1 GB each).
Usage: python karaoke_test.py "C:\\path\\song.mp3"
"""
import sys, time, os
import torch
import subprocess

# audio-separator's install leaves some small dependencies out. Install any that are missing, one by one.
_PKG = {"yaml": "pyyaml", "sklearn": "scikit-learn", "cv2": "opencv-python"}
for _ in range(25):
    try:
        from audio_separator.separator import Separator
        break
    except ModuleNotFoundError as e:
        name = (e.name or "").split(".")[0]
        if not name:
            raise
        pkg = _PKG.get(name, name)
        print(f"Installing missing piece: {pkg}")
        uv = os.environ.get("UV", "uv")
        r = subprocess.run([uv, "pip", "install", "--python", sys.executable, pkg])
        if r.returncode != 0:
            raise
else:
    raise SystemExit("Too many missing pieces; send this screen to Claude.")

MODELS = [
    ("karaoke", "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"),
    ("vocals", "vocals_mel_band_roformer.ckpt"),
]

def main(path):
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out, exist_ok=True)
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU)")
    rows = []
    for tag, fname in MODELS:
        sep = Separator(output_dir=os.path.join(out, tag), output_format="WAV")
        t0 = time.time()
        sep.load_model(model_filename=fname)
        t_load = time.time() - t0
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        files = sep.separate(path)
        t_run = time.time() - t0
        peak = torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0
        rows.append((tag, fname, t_load, t_run, peak, files))
        print(f"\n[{tag}] {fname}\n  load {t_load:.1f}s  run {t_run:.1f}s  peak GPU {peak:.2f} GB\n  files: {files}")
        del sep
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    print("\nSUMMARY (copy this back to Claude):")
    for tag, fname, tl, tr, pk, _ in rows:
        print(f"  {tag}: load {tl:.1f}s, run {tr:.1f}s, peak GPU {pk:.2f} GB")
    print("Output files are in:", out)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Drag a song onto Run karaoke test.bat, or pass its path.")
    main(sys.argv[1])
