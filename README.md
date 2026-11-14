# Un-Mix-a-Lot

A free, simple app that splits an audio or video file into **voices**, **music** and **effects**, then lets you mix, tweak and export the pieces. Made for filmmakers, including people making AI video, who want clean dialogue, music and sound effects as separate tracks.

Windows is the main supported platform. Mac works (tested on an Intel iMac); Linux has not been tested.

## Use at your own risk

Un-Mix-a-Lot is free software provided **"as is", with no warranty of any kind**. It may have bugs, crash, or give poor results. The author(s) and contributors are not responsible for any damage, lost or corrupted files, or other harm from using it. **Always work on copies of your files.** You are responsible for having the right to use any audio or video you process. Cloud engines upload your audio to a third party, and every model and service has its own license. Support and updates are offered when time allows, if at all. Read the full text in [DISCLAIMER.txt](DISCLAIMER.txt).

## What it does

- **Split** a file into voices, music and effects. Pick an engine: BandIt v2 (local, runs on a regular computer), cloud engines on mvsep.com, or Meta's SAM Audio (local, needs a strong NVIDIA GPU).
- **Mixer:** mute, solo and volume for every track, waveforms, click to seek, a level meter, and a synced video preview. Space plays and pauses.
- **Split voices:** turn the Voices track into one track per speaker (2 to 4). A "Who is talking" strip shows who speaks when; click a block to reassign it, drag a line to move it. The pieces always add back up to the original voices track.
- **Effects on every track:** EQ, Filter, Ring modulator, Chorus, Distortion, Delay, Reverb and Noise, each with its own on/off switch, plus friendly presets (Robot Voice, Radio, Space Helmet, Telephone, Cave, Underwater, and more). You hear changes live; your original audio is never changed. Effects are applied when you export.
- **Export** the current mix (WAV, FLAC or MP3), the individual stems, or the original video with the new mix put back in (the video stream is copied, not re-encoded).
- **History** keeps past results so you can reopen them, and **Settings** holds your options and keys.
- **Automatic hardware check:** the app only offers engines your computer can run.
- Long files are handled in chunks with crossfades. Everything is mono 48 kHz.

## Engines

| Engine | What it does | Notes |
|---|---|---|
| BandIt v2 (Local) | Splits into dialogue, music and effects with no prompts | Default. Runs on a CPU (about 30 s for 20 s of audio). Downloads about 450 MB on first use. Code Apache-2.0; weights CC BY-SA 4.0 (Zenodo record 12701995). |
| BandIt v2 (Cloud, MVSEP) | Same model, run on mvsep.com | Uploads your audio. Needs your own MVSEP API token (Settings, Model tab). |
| DnR v3 (Cloud, MVSEP) | Highest quality in our tests | Uploads your audio. Needs your MVSEP token. The free MVSEP tier allows one job at a time. |
| SAM Audio (Local) | Pulls out whatever you describe | Needs gated Hugging Face weights and an NVIDIA GPU with about 13 GB of video memory. Hidden automatically on computers that can't run it. |

The MVSEP token is stored in `settings.json` next to the app. That file is ignored by git and is never uploaded.

## How the SAM Audio engine works

For each chunk the app runs SAM Audio as a cascade over residuals: `"speech"` gives Voices, `"music"` on the leftovers gives Music, and by default everything left is called Effects (untick "Effects = leftovers" to run an explicit effects pass; then the remainder becomes Other). Chunks are overlap-added with crossfades, and the last stem is computed as the original minus the others, so **voices + music + effects + other equals the original exactly**.

> **Status:** the chunking, stem maths, mixer, effects, voice splitting, export and UI are tested end to end. SAM Audio (local) has been run on one NVIDIA RTX GPU on Windows; other setups are untested. See [First run checklist](#first-run-checklist).

## Install

You don't need to install Python or anything else first. The installer fetches its own Python 3.12.

**Mac (Apple chip or Intel):**

1. Download **`Un-Mix-a-Lot-Mac.zip`** from the project's [Releases page](https://github.com/aharonrabinowitz-create/un-mix-a-lot/releases/latest) and unzip it into a permanent folder, for example `Documents/Un-Mix-a-Lot`.
2. Double-click **`Install Un-Mix-a-Lot`**. The first time, macOS may block it: right-click the file, choose **Open**, then **Open** again. If it says you don't have the right privileges, open Terminal and run `cd ~/Documents/Un-Mix-a-Lot && chmod +x "Install Un-Mix-a-Lot.command"` (adjust the folder to where you put the project), then try again.
3. Wait 5 to 15 minutes. When it finishes, there is an **`Un-Mix-a-Lot`** app in the folder. Double-click it, or drag it to the Dock or Applications.

What works on a Mac: BandIt v2 (Local, on the CPU), the cloud engines (MVSEP), voice splitting, the mixer, effects and export. SAM Audio is hidden because it needs an NVIDIA GPU. Intel Macs work but are slower and use older libraries (the installer handles this). If you move the project folder, run `.venv/bin/python mac_app.py` once to refresh the app.

If the window doesn't open, run `.venv/bin/python run.py` and open http://127.0.0.1:7860 in a browser. Logs are in `logs/stemsplit.log`. Exports are saved to `Documents/Un-Mix-a-Lot`. To try the Mac's GPU on an Apple chip, start the app with `STEMSPLIT_DEVICE=mps` (experimental).

**Windows:** run **`Un-Mix-a-Lot-Setup.exe`** (from the project's Releases page) and click through the setup wizard. It installs for your account only (no administrator needed), downloads Python and the libraries, adds Start Menu and Desktop shortcuts, and can start the app when it finishes. Windows may show "Windows protected your PC" because the installer isn't code-signed: click **More info**, then **Run anyway**. To remove it, use Windows' **Installed apps** list. Without the Setup.exe you can double-click **`Install Un-Mix-a-Lot.bat`** in the project folder, then **`Un-Mix-a-Lot.vbs`**. An NVIDIA GPU and a recent driver are needed only for SAM Audio (the installer picks the smaller CPU build when no NVIDIA GPU is found). SAM Audio also needs Git installed.

To rebuild the Setup.exe yourself: `installer/build.sh` (needs NSIS, `sudo apt install nsis`).

**Linux, or if you prefer the command line:** Python 3.11+, then

```bash
git clone https://github.com/aharonrabinowitz-create/un-mix-a-lot.git && cd un-mix-a-lot
python install.py          # creates .venv, installs PyTorch + dependencies (add --cpu if you have no NVIDIA GPU)
python run.py              # then open http://127.0.0.1:7860
```

`torchcodec` (required by `sam_audio`) needs FFmpeg shared libraries on the system: Windows `winget install Gyan.FFmpeg.Shared`, Linux `sudo apt install ffmpeg`. The installer warns if it can't load.

### Get access to the weights (Hugging Face)

The weights are gated and are **not** bundled with this repo; each user downloads them from Hugging Face.

1. Create a free account at huggingface.co and open the model page, e.g. <https://huggingface.co/facebook/sam-audio-small> (also `-base`, `-large`). Click **Agree / Request access** and wait for approval. Access is per repo, so request each size you want.
2. Create an access token at <https://huggingface.co/settings/tokens> (type *Read* is enough).
3. Log in once: `.venv/bin/hf auth login` (Windows: `.venv\Scripts\hf.exe auth login`) and paste the token. Alternatively set `HF_TOKEN`.

### Run

**Windows:** double-click **`Un-Mix-a-Lot.vbs`**. It opens Un-Mix-a-Lot in its own window with no console, and puts a shortcut with an icon on your desktop the first time. Exports are saved to `Documents\Un-Mix-a-Lot`. Logs are in `logs\stemsplit.log`. If the window doesn't open (the Edge WebView2 runtime is missing, say), use `Un-Mix-a-Lot (browser).bat`. Install the project somewhere permanent, such as `C:\Users\<you>\Apps\stemsplit`, not Downloads.

**Any OS:**

```bash
python run.py              # then open http://127.0.0.1:7860
```

The first separation downloads the weights (several GB) and is slow; after that they're cached.

If you hit an out-of-memory error, try a smaller model, Fast mode, or turn off *Predict time spans* / reranking under *Advanced*. Community reports put the small model around 21 GB VRAM, which is tight on some cards; I haven't measured it on yours. `STEMSPLIT_DTYPE=bfloat16` is an experimental option to cut memory (not verified to preserve quality).

## Use it from Claude (MCP, optional)

MCP lets Claude Desktop run Un-Mix-a-Lot for you, on your own PC. You ask in plain words, for example
"remove the music from my video and save it to my Desktop", or "take every video in my Scenes folder and
remove the music".

Setup: open **Settings, MCP tab** in the app and press **Connect to Claude Desktop**, then quit Claude Desktop
completely and open it again. If the button can't do it, press **Show the text to paste** and paste that text
into Claude Desktop (Settings, Developer, Edit Config). **Disconnect** in the same tab removes it again.

What Claude can do: find audio and video files by name, split a file into voices, music and effects, split
voices into separate speakers (with corrections), mute or turn down any track, and save the result as audio or
as the original video with new audio. Your original files are never changed or deleted, and an existing file is
never replaced (a number is added to the name). Everything Claude makes also appears in the app's History.
Claude can't hear audio, so listen to the result yourself.

Everything runs locally on your computer. Claude Desktop asks before it uses a tool, unless you tell it to
always allow.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `STEMSPLIT_MODELS` | `small,base,large` | SAM Audio sizes offered in the dropdown |
| `STEMSPLIT_MAX_SECONDS` | `0` (unlimited) | Max input length |
| `STEMSPLIT_WORKDIR` | system temp / `stemsplit` | Where sessions live (cleaned after 6 h) |
| `HOST`, `PORT` | `127.0.0.1`, `7860` | Server binding (set `HOST=0.0.0.0` for LAN) |
| `STEMSPLIT_MOCK=1` | off | Fake band-split "model" for UI work without a GPU |

## Develop

```bash
pip install gradio==5.50.0 numpy imageio-ffmpeg pytest soundfile
python -m pytest tests          # chunking, additivity, mixing, export, video remux
STEMSPLIT_MOCK=1 python app.py  # UI with the stand-in model, no GPU needed
```

## License and credits

This project's own code is MIT (see `LICENSE`). It does not include any third-party models or model code. Each person downloads those themselves.

- **SAM Audio (Meta).** Code and weights are under Meta's [SAM License](https://github.com/facebookresearch/sam-audio/blob/main/LICENSE). Users install `sam_audio` from Meta's GitHub and download the weights from Hugging Face after accepting Meta's terms. **Do not bundle SAM Audio code or weights in this repo or in an installer**; if SAM Materials are ever redistributed, the license text must go with them. Banned uses in Meta's license include military, weapons and espionage uses and breaking privacy or export law. Credit: SAM Audio, arXiv 2512.18099. This is a summary, not legal advice.
- **BandIt v2.** Code Apache-2.0 (see `stemsplit/bandit_v2/LICENSE`). Weights are CC BY-SA 4.0 (Zenodo record 12701995): credit the authors and share adaptations alike. They are downloaded on first use, not stored in this repo. The older BandIt weights (Zenodo record 10160698) are CC BY-NC and are not used.
- **MVSEP.** The cloud engines (BandIt v2 and DnR v3) run on [mvsep.com](https://mvsep.com) using each person's own account and API token. Your audio is uploaded to MVSEP. Read their terms and limits (the free tier allows one job at a time). DnR v3 weights are not public.
- **Voice splitting** uses Silero VAD and SpeechBrain's ECAPA speaker model, installed automatically on first use under their own licenses.

This app is meant to be run locally on your own computer. It is not set up as a hosted web service.

## First run checklist

1. `python install.py`, then `hf auth login`, then `python run.py`.
2. Separate a 20–30 s clip with `small`. Confirm: it loads (a 401/403 means HF access isn't approved yet), stems sound right, no OOM.
3. Try a file longer than 60 s to exercise chunking; listen at the 28 s boundaries for seams.
4. Note the VRAM use and seconds per pass for your card.
5. If a prompt like "sound effects" under-captures, edit it in *Advanced* (README of SAM Audio suggests short lowercase noun phrases).
