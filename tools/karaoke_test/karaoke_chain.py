"""Round 3 (branch experiment, local testing only).
Step 1: karaoke model removes the LEAD singer -> music + backing singers.
Step 2: a vocals-vs-music model then runs on that result:
        its "vocals" = the backing singers alone, its "other/Instrumental" = music only.
Usage: python karaoke_chain.py "C:\\path\\song.mp3"
"""
import sys, os, time, shutil
import torch
from audio_separator.separator import Separator

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "_work")        # temporary, removed at the end
OUT = WORK
STEP1 = ("step1_karaoke_becruily", "mel_band_roformer_karaoke_becruily.ckpt")
STEP2 = [
    ("step2_vocals_kim", "vocals_mel_band_roformer.ckpt", "Kim"),
    ("step2_vocals_becruily", "mel_band_roformer_vocals_becruily.ckpt", "Becruily"),
    ("step2_vocals_gabox_bs", "bs_roformer_vocals_gabox.ckpt", "Gabox"),
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

def pick(files, word):
    for x in files:
        if word in os.path.basename(x).lower():
            return x
    return None

def put(src, dest_dir, name):
    if src and os.path.exists(src):
        shutil.copyfile(src, os.path.join(dest_dir, name))

def main(path):
    song = os.path.splitext(os.path.basename(path))[0]
    res = os.path.join(HERE, "Results", song)
    os.makedirs(res, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    rows = []
    tr, out = run(*STEP1, path)
    inst = pick(out, "(instrumental)")
    if not inst:
        sys.exit("Step 1 gave no Instrumental file: " + str(out))
    put(inst, res, "1 - Music plus backing singers (lead singer removed).wav")
    put(pick(out, "(vocals)"), res, "2 - Lead singer only.wav")
    rows.append(f"  step 1 karaoke becruily: run {tr:.1f}s, OK")
    for tag, f, label in STEP2:
        try:
            tr, o2 = run(tag, f, inst)
            voc = pick(o2, "(vocals)")
            music = [x for x in o2 if x != voc]
            put(music[0] if music else None, res, f"3 - Music only ({label} model).wav")
            put(voc, res, f"4 - Backing singers only ({label} model).wav")
            rows.append(f"  {label}: run {tr:.1f}s, OK")
        except BaseException as e:
            rows.append(f"  {label}: FAILED ({type(e).__name__}: {str(e)[:120]})")
    shutil.rmtree(WORK, ignore_errors=True)
    print("\nSUMMARY (copy this back to Claude):")
    print("\n".join(rows))
    print("All results are in ONE folder:", res)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Drag a song onto Run karaoke chain.bat")
    main(sys.argv[1])
