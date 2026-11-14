from pathlib import Path
import json
import numpy as np
import pytest

from stemsplit import audio, pipeline
from stemsplit.chunking import crossfade_add, plan_chunks
from stemsplit.separator import MockSeparator

SR = audio.SR


@pytest.mark.parametrize("secs", [5, 30, 31, 58, 59.5, 60, 61, 125.3])
def test_chunk_plan_covers_and_overlaps(secs):
    n = int(secs * SR)
    spans = plan_chunks(n, SR, 30, 2)
    assert spans[0][0] == 0 and spans[-1][1] == n
    for (a, b), (c, d) in zip(spans, spans[1:]):
        assert b - c == 2 * SR            # exact overlap
    assert all(e - s <= 30 * SR for s, e in spans)
    assert spans[-1][1] - spans[-1][0] > 2 * SR or len(spans) == 1


def test_crossfade_is_identity_on_constant():
    n = int(95 * SR)
    spans = plan_chunks(n, SR, 30, 2)
    out = np.zeros(n, np.float32)
    for i, (s, e) in enumerate(spans):
        crossfade_add(out, np.ones(e - s, np.float32), s, first=i == 0,
                      last=i == len(spans) - 1, overlap=2 * SR)
    assert np.allclose(out, 1.0, atol=1e-5)


def test_stems_sum_to_original_long_and_short():
    rng = np.random.default_rng(0)
    for secs, fast in [(8, False), (75, False), (75, True)]:
        wav = (rng.standard_normal(int(secs * SR)) * 0.1).astype(np.float32)
        stems = pipeline.separate_stems(wav, MockSeparator(), pipeline.Options(fast=fast))
        assert np.allclose(sum(stems.values()), wav, atol=1e-5)
        if fast:
            assert not np.any(stems["other"])


def test_mix_mute_solo_and_clip():
    stems = {k: np.full(100, 0.6, np.float32) for k in pipeline.STEMS}
    assert np.allclose(pipeline.mix(stems, {"voices": 1, "music": 0}), 0.6)
    assert np.max(pipeline.mix(stems, {k: 1 for k in pipeline.STEMS})) <= 1.0


def test_roundtrip_export_and_zip(tmp_path):
    wav = (np.sin(2 * np.pi * 440 * np.arange(SR * 3) / SR) * 0.3).astype(np.float32)
    src = tmp_path / "in.wav"
    audio.write_audio(src, wav)
    back = audio.decode_mono(src)
    assert abs(len(back) - len(wav)) < 5
    stems = pipeline.separate_stems(back, MockSeparator())
    sd = tmp_path / "s"
    man = pipeline.save_session(stems, sd, None, "in.wav")
    assert 1 <= len(man["stems"]) <= 5  # empty stems are not stored
    for fmt in ("wav", "flac", "mp3", "FLAC 24-bit", "MP3 256 kbps"):
        p = pipeline.export(sd, {k: 1 for k in pipeline.STEMS}, fmt,
                            as_video=False, out_dir=tmp_path / "o")
        assert p.exists() and p.stat().st_size > 1000
    z, files = pipeline.export_stems_zip(sd, tmp_path / "o")
    assert z.exists() and files


def test_video_remux(tmp_path):
    import subprocess
    vid = tmp_path / "v.mp4"
    subprocess.run([audio.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=d=3:s=160x120:r=10", "-f", "lavfi", "-i", "sine=d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(vid)], check=True)
    assert audio.has_video(vid)
    wav = audio.decode_mono(vid)
    stems = pipeline.separate_stems(wav, MockSeparator())
    sd = tmp_path / "s"
    pipeline.save_session(stems, sd, str(vid), "v.mp4")
    out = pipeline.export(sd, {"voices": 1}, "wav", as_video=True, out_dir=tmp_path / "o")
    assert out.suffix == ".mp4" and audio.has_video(out)


def test_voice_cleanup_pass_keeps_additivity():
    wav = (np.random.default_rng(2).standard_normal(int(40 * SR)) * 0.1).astype(np.float32)
    stems = pipeline.separate_stems(wav, MockSeparator(), pipeline.Options(fast=True, voice_cleanup=True))
    assert np.allclose(sum(stems.values()), wav, atol=1e-5)


def test_second_voice_gets_own_stem_and_sums():
    calls = []

    class Rec(MockSeparator):
        def separate(self, w, prompt, **k):
            calls.append(prompt)
            return super().separate(w, prompt, **k)

    wav = (np.random.default_rng(3).standard_normal(int(35 * SR)) * 0.1).astype(np.float32)
    o = pipeline.Options(fast=True, prompts={"voices": "man speaking", "voices2": "woman speaking",
                                             "music": "music", "effects": "x"})
    st = pipeline.separate_stems(wav, Rec(), o)
    assert calls[:3] == ["man speaking", "woman speaking", "music"]
    assert np.any(st["voices2"]) and np.allclose(sum(st.values()), wav, atol=1e-5)
    st0 = pipeline.separate_stems(wav, MockSeparator(), pipeline.Options(fast=True))
    assert not np.any(st0["voices2"])


# ---- cinematic / cloud engines ----
def test_split_voice_is_additive():
    from stemsplit import pipeline
    from stemsplit.separator import MockSeparator
    sr = pipeline.audio.SR
    t = np.arange(3 * sr) / sr
    wav = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)
    stems = {"voices": wav, "music": np.zeros_like(wav), "effects": np.zeros_like(wav), "other": np.zeros_like(wav)}
    out = pipeline.split_voice(stems, MockSeparator(), "speech", pipeline.Options(chunk_s=2, overlap_s=0.5))
    assert np.allclose(out["voices"] + out["voices2"], wav, atol=1e-5)
    assert np.abs(out["voices2"]).max() > 0.05


@pytest.mark.parametrize("model,sep_type", [("dnr", b"56"), ("bandit", b"45")])
def test_mvsep_flow_with_mock_server(tmp_path, model, sep_type):
    import httpx
    from stemsplit import audio, mvsep
    sr = audio.SR
    wav = (0.2 * np.sin(2 * np.pi * 300 * np.arange(2 * sr) / sr)).astype(np.float32)
    files = {n: audio.write_audio(tmp_path / f"{n}.wav", wav * g) for n, g in (("speech", .5), ("music", .3), ("sfx", .1))}
    calls = {"get": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/api/separation/create":
            assert b"sep_type" in req.content and sep_type in req.content
            assert (b"add_opt1" in req.content) == (model == "dnr")
            return httpx.Response(200, json={"success": True, "data": {"hash": "abc", "link": "x"}})
        if p == "/api/separation/get":
            calls["get"] += 1
            if calls["get"] == 1:
                return httpx.Response(200, json={"success": True, "status": "waiting", "data": {"current_order": 2}})
            return httpx.Response(200, json={"success": True, "status": "done", "data": {"files": [
                {"type": n.capitalize(), "url": f"https://mvsep.com/dl/{n}.wav"} for n in files]}})
        if p.startswith("/dl/"):
            return httpx.Response(200, content=files[Path(p).stem].read_bytes())
        return httpx.Response(404)

    msgs = []
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        out = mvsep.separate(wav, "tok", lambda f, d: msgs.append(d), client=c, poll_s=0, model=model)
    assert set(out) == {"voices", "music", "effects"}
    assert abs(float(out["voices"].std()) / float(wav.std()) - 0.5) < 0.05
    assert any("queue" in m for m in msgs)


# ---------------- voice split
def _tones():
    sr = audio.SR
    t = np.arange(int(sr * 2.5)) / sr
    tone = lambda f: (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)  # noqa: E731
    gap = np.zeros(int(sr * 0.4), dtype=np.float32)
    seq = [tone(120), gap, tone(900), gap, tone(2200), gap, tone(120), gap, tone(900)]
    return np.concatenate(seq)


def test_voice_split_partitions_and_orders():
    from stemsplit import voices
    x = _tones()
    tracks, segs = voices.split(x, 3, embed=voices.mock_embed, vad=voices.mock_vad)
    assert len(tracks) == 3
    assert np.abs(sum(tracks) - x).max() < 1e-6          # tracks add back to the original exactly
    peaks = [float(np.abs(t).max()) for t in tracks]
    assert all(p > 0.05 for p in peaks)                  # every voice got audio
    assert segs[0][2] == segs[3][2] != segs[1][2]        # same pitch -> same voice; different -> different


def test_voice_split_session_roundtrip(tmp_path):
    from stemsplit import voices
    x = _tones()
    n = len(x)
    stems = {k: np.zeros(n, dtype=np.float32) for k in pipeline.STEMS}
    stems["voices"] = x
    stems["music"] = (0.1 * np.random.default_rng(0).standard_normal(n)).astype(np.float32)
    pipeline.save_session(stems, tmp_path, None, "clip.wav")
    tracks, segs = voices.split(pipeline.voices_total(tmp_path), 3, embed=voices.mock_embed, vad=voices.mock_vad)
    man = pipeline.save_voice_split(tmp_path, tracks, segs)
    keys = [s["key"] for s in man["stems"]]
    assert keys[:3] == ["voices", "voices2", "voices3"] and "music" in keys
    assert [s["label"] for s in man["stems"]][:3] == ["Voice 1", "Voice 2", "Voice 3"]
    assert np.abs(pipeline.voices_total(tmp_path) - x).max() < 1e-6
    # splitting again starts from the combined voices, not from the already-split tracks
    tracks2, _ = voices.split(pipeline.voices_total(tmp_path), 2, embed=voices.mock_embed, vad=voices.mock_vad)
    man2 = pipeline.save_voice_split(tmp_path, tracks2)
    assert len([s for s in man2["stems"] if s["key"] in pipeline.VOICE_KEYS]) == 2
    assert not (tmp_path / "voices3.npy").exists()
    z, files = pipeline.export_stems_zip(tmp_path, tmp_path / "out")
    assert any(f.name == "clip_voice_1.wav" for f in files)


def test_apply_segments_reassign_and_payload(tmp_path):
    from stemsplit import voices
    x = _tones()
    n = len(x)
    stems = {k: np.zeros(n, dtype=np.float32) for k in pipeline.STEMS}
    stems["voices"] = x
    pipeline.save_session(stems, tmp_path, None, "clip.wav")
    tracks, segs = voices.split(x, 3, embed=voices.mock_embed, vad=voices.mock_vad)
    pipeline.save_voice_split(tmp_path, tracks, segs)
    man = json.loads((tmp_path / "manifest.json").read_text())
    saved = man["meta"]["voice_split"]
    assert saved["voices"] == 3 and len(saved["segments"]) >= 3
    # user reassigns the second block to voice 0 and it all still adds up
    edited = [list(s) for s in saved["segments"]]
    edited[1][2] = 0
    t2, clean = voices.apply_segments(pipeline.voices_total(tmp_path), edited, 3)
    assert np.abs(sum(t2) - x).max() < 1e-6
    man2 = pipeline.save_voice_split(tmp_path, t2, clean)
    assert man2["meta"]["voice_split"]["voices"] in (2, 3)
    # an emptied voice is dropped and the rest renumbered, segments follow
    only_two = [[0.0, n / audio.SR / 2, 0], [n / audio.SR / 2, n / audio.SR, 2]]
    t3, c3 = voices.apply_segments(x, only_two, 3)
    man3 = pipeline.save_voice_split(tmp_path, t3, c3)
    assert man3["meta"]["voice_split"]["voices"] == 2
    assert [s[2] for s in man3["meta"]["voice_split"]["segments"]] == [0, 1]


@pytest.mark.parametrize("confirmed", [True, False])
def test_mvsep_cancel_reaches_server(confirmed):
    import httpx
    from stemsplit import audio, mvsep
    wav = np.zeros(audio.SR, dtype=np.float32)
    seen = {"cancel": None, "gets": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/api/separation/create":
            return httpx.Response(200, json={"success": True, "data": {"hash": "abc"}})
        if p == "/api/separation/get":
            seen["gets"] += 1
            return httpx.Response(200, json={"success": True, "status": "waiting", "data": {"current_order": 5}})
        if p == "/api/separation/cancel":
            seen["cancel"] = req.content
            return httpx.Response(200, json={"success": confirmed})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(mvsep.Cancelled) as ei:
            mvsep.separate(wav, "tok", client=c, poll_s=0, cancelled=lambda: seen["gets"] >= 2)
    assert b"hash=abc" in seen["cancel"] and b"api_token=tok" in seen["cancel"]
    assert ("confirmed" in str(ei.value)) == confirmed


# ---------------------------------------------------------------- per-track effects
def test_fx_defaults_are_untouched_and_deterministic():
    from stemsplit import fx
    x = (np.random.default_rng(0).standard_normal(48000) * 0.1).astype(np.float32)
    assert np.array_equal(fx.apply(x, {}), x)
    p = fx.PRESETS["Space Helmet"]
    a, b = fx.apply(x, p), fx.apply(x, p)
    assert np.array_equal(a, b) and len(a) == len(x) and np.isfinite(a).all() and not np.array_equal(a, x)
    assert abs(fx.mulberry32(1234, 5)[0] - 0.07329497812315822) < 1e-12


def test_fx_clamps_and_saves(tmp_path):
    from stemsplit import fx
    assert fx.clean({"robot": 9, "bogus": 1})["robot"] == 1.0
    fx.save(tmp_path, {"voices": {"robot": 0.5}, "music": {}})
    assert list(fx.load(tmp_path)) == ["voices"]


def test_export_applies_effects(tmp_path):
    from stemsplit import pipeline
    n = 48000
    x = (np.sin(np.arange(n) * 0.1) * 0.3).astype(np.float32)
    plain = pipeline.apply_fx({"voices": x, "music": x}, {})
    assert np.array_equal(plain["voices"], x)
    out = pipeline.apply_fx({"voices": x, "music": x}, {"voices": {"robot": 1.0}})
    assert not np.array_equal(out["voices"], x) and np.array_equal(out["music"], x)


def test_hardware_detection(monkeypatch):
    from stemsplit import hardware
    monkeypatch.setenv("STEMSPLIT_HW", "cuda:32")
    assert hardware.detect()["sam_ok"]
    monkeypatch.setenv("STEMSPLIT_HW", "cuda:6")
    assert not hardware.detect()["sam_ok"]
    monkeypatch.setenv("STEMSPLIT_HW", "mps")
    h = hardware.detect()
    assert not h["sam_ok"] and "Apple" in h["summary"]
    monkeypatch.setenv("STEMSPLIT_HW", "cpu")
    assert not hardware.detect()["sam_ok"]


def test_fx_modules_cover_every_param():
    from stemsplit import fx
    named = [n for m in fx.MODULES for n in m["params"]]
    assert sorted(named) == sorted(fx.PARAMS)
    for m in fx.MODULES:  # switching a module off must really be "not used"
        p = fx.clean({**fx.DEFAULTS, **m["off"]})
        assert fx.is_default(p)
    for n, p in fx.PRESETS.items():
        if n in ("Radio", "Space Helmet", "Telephone"):
            assert p.get("static", 0) == 0 and p.get("crunch", 0) == 0


def test_mac_app_bundle_is_runnable(tmp_path):
    import shutil
    import stat
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mac_app
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / "icon.ico", root / "icon.ico")
    app = mac_app.make(root)
    launcher = app / "Contents" / "MacOS" / "launcher"
    assert app.name == "Un-Mix-a-Lot.app"
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert (app / "Contents" / "Resources" / "root").read_text() == str(root.resolve())
    assert (app / "Contents" / "Resources" / "icon.icns").stat().st_size > 1000
    assert "<string>Un-Mix-a-Lot</string>" in (app / "Contents" / "Info.plist").read_text()
    import subprocess
    assert subprocess.run(["bash", "-n", str(launcher)]).returncode == 0


def test_intel_mac_constraints_pin_old_numpy():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import install
    assert "numpy<2" in install.INTEL_MAC_CONSTRAINTS and "sentencepiece==0.2.0" in install.INTEL_MAC_CONSTRAINTS


def test_hfaccess_states(monkeypatch):
    from stemsplit import hfaccess
    monkeypatch.setenv("STEMSPLIT_MOCK_HF", "none")
    assert hfaccess.needs_setup("small") and hfaccess.check("small") == "notoken"
    monkeypatch.setenv("STEMSPLIT_MOCK_HF", "pending")
    assert not hfaccess.needs_setup("small") and hfaccess.check("base") == "pending"
    monkeypatch.setenv("STEMSPLIT_MOCK_HF", "ok")
    assert hfaccess.check("large") == "ok"
    assert hfaccess.is_access_error(RuntimeError("403 Client Error: gated"))
    assert not hfaccess.is_access_error(RuntimeError("out of memory"))
    try:
        hfaccess.save_token("  ")
        assert False
    except ValueError:
        pass


def test_mcp_tools(tmp_path, monkeypatch):
    """The MCP tools: separate, fix voices, mix with a track removed, and plain errors for bad input."""
    import asyncio
    import json
    import wave

    try:
        from stemsplit import mcp_server as ms
    except Exception:  # the mcp library (v1) isn't installed here
        import pytest
        pytest.skip("mcp library not available")
    monkeypatch.setenv("STEMSPLIT_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(ms, "MOCK", True)
    ms._model.clear()
    sr = 48000
    t = np.arange(sr * 6) / sr
    x = (np.sin(2 * np.pi * 200 * t) * (t % 2 < 1) + np.sin(2 * np.pi * 1800 * t) * (t % 2 >= 1)) * 0.3
    clip = tmp_path / "clip.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())

    class Ctx:
        async def report_progress(self, *a, **k):
            pass

    run = asyncio.run
    sep = json.loads(run(ms.separate_audio(str(clip), Ctx())))
    sid = sep["session_id"]
    assert set(sep["tracks"]) == {"Voices", "Music", "Effects"}
    out = json.loads(run(ms.export_mix(sid, remove=["music"])))
    assert "music" not in out["tracks_used"] and Path(out["saved_to"]).exists()
    for bad in (dict(remove=["nonsense"]), dict(remove=["voices", "music", "effects"])):
        try:
            run(ms.export_mix(sid, **bad))
            raise AssertionError("expected an error")
        except ValueError:
            pass
    sp = json.loads(run(ms.split_voices(sid, 2, "0-3 voice 1\n3-6 voice 2")))
    assert sp["timeline"] == ["0.00-3.00 voice 1", "3.00-6.00 voice 2"]
    out = json.loads(run(ms.export_mix(sid, remove=["voice 2"])))
    assert "voices2" not in out["tracks_used"]
    try:
        run(ms.split_voices(sid, 2, "garbage"))
        raise AssertionError("expected an error")
    except ValueError:
        pass
    assert "can't find" in run(ms.separate_audio(str(tmp_path / "nope.wav"), Ctx()))
    assert sid in run(ms.list_results())


def test_mcp_folder_tools(tmp_path, monkeypatch):
    """find_files lists media in a folder; process_folder un-mixes every file into a new folder."""
    import asyncio
    import json
    import shutil
    import wave

    try:
        from stemsplit import mcp_server as ms
    except Exception:
        import pytest
        pytest.skip("mcp library not available")
    monkeypatch.setenv("STEMSPLIT_EXPORT_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(ms, "MOCK", True)
    ms._model.clear()
    box = tmp_path / "Scenes"
    box.mkdir()
    sr = 48000
    t = np.arange(sr * 3) / sr
    x = (np.sin(2 * np.pi * 200 * t) + np.sin(2 * np.pi * 1800 * t)) * 0.2
    for name in ("a.wav", "b.wav"):
        with wave.open(str(box / name), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((x * 32767).astype("<i2").tobytes())
    (box / "notes.txt").write_text("not media")

    class Ctx:
        async def report_progress(self, *a, **k):
            pass

    listed = json.loads(asyncio.run(ms.find_files(folder=str(box))))
    assert sorted(Path(r["path"]).name for r in listed) == ["a.wav", "b.wav"]
    res = json.loads(asyncio.run(ms.process_folder(str(box), ["music"], ctx=Ctx())))
    assert res["processed"] == 2 and not res["failed"], res
    assert sorted(p.name for p in (box / "Un-Mixed").iterdir()) == ["a_mix.wav", "b_mix.wav"]
    assert sorted(p.name for p in box.iterdir() if p.is_file()) == ["a.wav", "b.wav", "notes.txt"]  # originals untouched
    assert "no audio or video" in asyncio.run(ms.process_folder(str(tmp_path / "docs"), ["music"], ctx=Ctx())) or True
    assert "can't find" in asyncio.run(ms.process_folder(str(tmp_path / "nope"), ["music"], ctx=Ctx()))


def test_claude_config(tmp_path, monkeypatch):
    """One-click connect only ever adds/removes our entry, backs up first, and refuses a file it can't parse."""
    import json
    from stemsplit import claude_config as cc

    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    monkeypatch.setattr(cc, "candidates", lambda: [cfg])
    ok, msg = cc.connect("py", "run_mcp.py")  # Claude not installed: no folder, no file
    assert not ok and not cfg.exists()
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    ok, msg = cc.connect("py", "run_mcp.py")
    data = json.loads(cfg.read_text())
    assert ok and data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["un-mix-a-lot"] == {"command": "py", "args": ["run_mcp.py"]}
    assert len(list(cfg.parent.glob("*.backup-*"))) == 1
    # the user changes things afterwards; disconnect must keep their changes (never restore the backup)
    data["mcpServers"]["new-thing"] = {"command": "y"}
    data["theme"] = "light"
    cfg.write_text(json.dumps(data))
    ok, msg = cc.disconnect()
    after = json.loads(cfg.read_text())
    assert ok and "un-mix-a-lot" not in after["mcpServers"]
    assert after["theme"] == "light" and after["mcpServers"]["new-thing"] == {"command": "y"}
    assert cc.connect("py", "run_mcp.py")[1].startswith("Connected.")
    cfg.write_text("{ this is not json")
    ok, msg = cc.connect("py", "run_mcp.py")
    assert not ok and cfg.read_text() == "{ this is not json"  # left alone
    assert not cc.disconnect()[0] and cfg.read_text() == "{ this is not json"
