"""Round 3 (branch experiment, local testing only).
Step 1: karaoke model removes the LEAD singer -> music + backing singers.
Step 2: a vocals-vs-music model then runs on that result:
        its "vocals" = the backing singers alone, its "other/Instrumental" = music only.
Usage: python karaoke_chain.py "C:\\path\\song.mp3"
"""
import sys, os, time
import torch
from audio_separator.separator import Separator

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out3")
STEP1 = ("step1_karaoke_becruily", "mel_band_roformer_karaoke_becruily.ckpt")
STEP2 = [
    ("step2_vocals_kim", "vocals_mel_band_roformer.ckpt"),
    ("step2_vocals_becruily", "mel_band_roformer_vocals_becruily.ckpt"),
    ("step2_vocals_gabox_bs", "bs_roformer_vocals_gabox.ckpt"),
]

def run(tag, fname, path):
    d = os.path.join(OUT, tag)
    sep = Separator(output_dir=d, output_format="WAV")
    t0 = time.time(); sep.load_model(model_filename=fname)
    t1 = time.time(); files = sep.separate(path)
    tr = time.time() - t1
    out = [f if os.path.isabs(f) else os.path.join(d, f) for f in files]
    del sep
    if torch.cuda.is_available(): torch.cuda.empty_cache()
    return tr, out

def main(path):
    os.makedirs(OUT, exist_ok=True)
    rows = []
    tr, out = run(*STEP1, path)
    inst = [x for x in out if "(instrumental)" in os.path.basename(x).lower()]
    if not inst:
        sys.exit("Step 1 gave no Instrumental file: " + str(out))
    rows.append(f"  {STEP1[0]}: run {tr:.1f}s, OK")
    for tag, f in STEP2:
        try:
            tr, _ = run(tag, f, inst[0])
            rows.append(f"  {tag}: run {tr:.1f}s, OK")
        except BaseException as e:
            rows.append(f"  {tag}: FAILED ({type(e).__name__}: {str(e)[:120]})")
    print("\nSUMMARY (copy this back to Claude):")
    print("\n".join(rows))
    print("Output files are in:", OUT)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Drag a song onto Run karaoke chain.bat")
    main(sys.argv[1])
