"""Preview (Web Audio in the browser) and export (Python) must sound the same."""
import json

import numpy as np
import pytest

from stemsplit import fx
from stemsplit.fxjs import FX_JS

pw = pytest.importorskip("playwright.sync_api")

CASES = {
    "robot": {"robot": 0.85, "robot_hz": 55},
    "chorus": {"chorus": 0.6, "chorus_depth": 5, "chorus_rate": 0.7},
    "tone": {"bass": 6, "treble": -6, "lowcut": 300, "highcut": 4000},
    "crunch": {"crunch": 0.5},
    "echo": {"echo": 0.5, "echo_time": 0.2, "echo_fb": 0.5},
    "reverb": {"reverb": 0.5, "reverb_size": 0.8},
    "static": {"static": 0.5},
    **{"preset " + n: p for n, p in fx.PRESETS.items() if p},
}


def signal(n=48000 * 2):
    t = np.arange(n) / 48000
    rng = np.random.default_rng(3)
    x = 0.4 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 1.5 * t) > 0) + 0.1 * rng.standard_normal(n) * (t > 1)
    return np.clip(x, -0.9, 0.9).astype(np.float32)


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page()
        pg.set_content("<html><body></body></html>")
        pg.evaluate(FX_JS)
        pg.evaluate('somaFx.ready')
        yield pg
        b.close()


def render(pg, x, params):
    return np.array(pg.evaluate("""async ([x, params]) => {
      const n = x.length, c = new OfflineAudioContext(1, n, 48000);
      const b = c.createBuffer(1, n, 48000); b.getChannelData(0).set(Float32Array.from(x));
      const s = c.createBufferSource(); s.buffer = b;
      const f = somaFx.build(c, params); s.connect(f.input); f.output.connect(c.destination); s.start();
      const r = await c.startRendering(); return Array.from(r.getChannelData(0));
    }""", [x.tolist(), params]), dtype=np.float32)


@pytest.mark.parametrize("name", list(CASES))
def test_preview_matches_export(page, name):
    x = signal()
    a = fx.apply(x, CASES[name])
    b = render(page, x, CASES[name])
    ref = float(np.sqrt(np.mean(a ** 2)))
    err = float(np.sqrt(np.mean((a - b) ** 2))) / (ref + 1e-9)
    corr = float(np.corrcoef(a, b)[0, 1])
    print(name, "rel err", round(err, 4), "corr", round(corr, 5))
    assert corr > 0.99 and err < 0.15, (name, err, corr)
