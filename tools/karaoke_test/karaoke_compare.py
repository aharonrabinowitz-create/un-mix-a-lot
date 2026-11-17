"""Round 2: compare more models on the same song (branch experiment, local testing only).
Each model is tried on its own, so one failure does not stop the rest.
Usage: python karaoke_compare.py "C:\\path\\song.mp3"
"""
import sys, os, time, subprocess
import torch
from audio_separator.separator import Separator

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out2")

KARAOKE = [
    ("karaoke_gabox", "mel_band_roformer_karaoke_gabox.ckpt"),
    ("karaoke_becruily", "mel_band_roformer_karaoke_becruily.ckpt"),
]
VOCALS = [
    ("vocals_becruily", "mel_band_roformer_vocals_becruily.ckpt"),
    ("vocals_gabox_bs", "bs_roformer_vocals_gabox.ckpt"),
]

def run(tag, fname, path):
    sep = Separator(output_dir=os.path.join(OUT, tag), output_format="WAV")
    t0 = time.time(); sep.load_model(model_filename=fname); tl = time.time() - t0
    if torch.cuda.is_available(): torch.cuda.reset_peak_memory_stats()
    t0 = time.time(); files = sep.separate(path); tr = time.time() - t0
    pk = torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0
    out = [os.path.join(OUT, tag, f) if not os.path.isabs(f) else f for f in files]
    del sep
    if torch.cuda.is_available(): torch.cuda.empty_cache()
    return tl, tr, pk, out

def attempt(tag, fname, path, rows):
    try:
        tl, tr, pk, out = run(tag, fname, path)
        rows.append(f"  {tag}: run {tr:.1f}s, peak GPU {pk:.2f} GB, OK")
        return out
    except BaseException as e:
        rows.append(f"  {tag}: FAILED ({type(e).__name__}: {str(e)[:120]})")
        return []

def main(path):
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for tag, f in KARAOKE + VOCALS:
        attempt(tag, f, path, rows)
    # two-step: all vocals first (Kimberley Jensen), then karaoke on the vocal track only
    first = attempt("step1_vocals_kim", "vocals_mel_band_roformer.ckpt", path, rows)
    voc = [x for x in first if "(vocals)" in os.path.basename(x).lower()]
    if voc:
        for tag, f in [("step2_karaoke_aufr33", "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt")] + KARAOKE:
            attempt("2step_" + tag, f, voc[0], rows)
    print("\nSUMMARY (copy this back to Claude):")
    print("\n".join(rows))
    print("Output files are in:", OUT)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Drag a song onto Run karaoke compare.bat")
    main(sys.argv[1])
