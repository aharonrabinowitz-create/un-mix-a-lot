"""Per-track effects, browser side: a Web Audio chain that mirrors stemsplit/fx.py.

`window.somaFx.build(ctx, params)` works on a live AudioContext (preview) and on an
OfflineAudioContext (used to check that preview and export sound the same).
"""
import json

from . import fx

_RAW = r"""
(function () {
  if (window.somaFx) return;
  const C = window.__FXCFG, K = C.constants, D = {};
  Object.keys(C.params).forEach((k) => { D[k] = C.params[k].def; });
  function mb(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function clean(p) {
    const o = Object.assign({}, D);
    for (const k in (p || {})) if (k in D) { const c = C.params[k], v = +p[k]; if (isFinite(v)) o[k] = Math.min(c.max, Math.max(c.min, v)); }
    return o;
  }
  const isDef = (p) => { const q = clean(p); return Object.keys(D).every((k) => q[k] === D[k]); };
  function irBuf(ctx, size) {
    const n = Math.max(1, Math.floor(size * ctx.sampleRate)), r = mb(K.irSeed), b = ctx.createBuffer(1, n, ctx.sampleRate), d = b.getChannelData(0);
    let e = 0; for (let i = 0; i < n; i++) { const v = (r() * 2 - 1) * Math.exp(-6.9078 * i / n); d[i] = v; e += v * v; }
    const s = 1 / (Math.sqrt(e) + 1e-12); for (let i = 0; i < n; i++) d[i] *= s;
    return b;
  }
  function noiseBuf(ctx) {
    const n = ctx.sampleRate, r = mb(K.noiseSeed), b = ctx.createBuffer(1, n, ctx.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = r() * 2 - 1;
    return b;
  }
  function curve(c) {
    const k = 1 + 9 * c, a = new Float32Array(4096), tk = Math.tanh(k);
    for (let i = 0; i < 4096; i++) a[i] = Math.tanh(k * (-1 + 2 * i / 4095)) / tk;
    return a;
  }
  function build(ctx, p0, opts) {
    const imm = !!(opts && opts.immediate) || (typeof OfflineAudioContext !== 'undefined' && ctx instanceof OfflineAudioContext);
    const sr = ctx.sampleRate;
    const set = (prm, v) => { if (imm) prm.value = v; else prm.setTargetAtTime(v, ctx.currentTime, 0.02); };
    const G = (v) => { const g = ctx.createGain(); g.gain.value = v; return g; };
    const input = G(1), output = G(1);
    // robot: ring modulator + short metallic comb
    const rDry = G(1), rWet = G(0), ring = G(0), osc = ctx.createOscillator(), rSum = G(1), s1 = G(1);
    osc.type = 'sine'; osc.frequency.value = 60;
    input.connect(rDry); input.connect(ring); osc.connect(ring.gain); osc.start();
    // comb = ring + fading copies, built from a chain of delays (no feedback loops, so every browser agrees)
    const rTaps = [];
    { let prev = ring; ring.connect(rSum);
      for (let k = 1; k < K.ringTaps; k++) { const d = ctx.createDelay(0.1), g = G(Math.pow(K.ringCombFb, k)); d.delayTime.value = K.ringCombS;
        prev.connect(d); d.connect(g); g.connect(rSum); prev = d; rTaps.push(g); } }
    rSum.connect(rWet); rDry.connect(s1); rWet.connect(s1);
    // chorus: copy delayed by a wobbling amount
    const cDry = G(1), cWet = G(0), cDl = ctx.createDelay(0.1), lfo = ctx.createOscillator(), lfoG = G(0.004), s2 = G(1);
    cDl.delayTime.value = K.chorusBaseS; lfo.frequency.value = 0.8; lfo.connect(lfoG); lfoG.connect(cDl.delayTime); lfo.start();
    s1.connect(cDry); s1.connect(cDl); cDl.connect(cWet); cDry.connect(s2); cWet.connect(s2);
    // tone
    const bass = ctx.createBiquadFilter(), tre = ctx.createBiquadFilter(), hp = ctx.createBiquadFilter(), lp = ctx.createBiquadFilter(), s3 = G(1);
    bass.type = 'lowshelf'; bass.frequency.value = K.bassHz; tre.type = 'highshelf'; tre.frequency.value = K.trebleHz;
    hp.type = 'highpass'; lp.type = 'lowpass'; hp.Q.value = lp.Q.value = 20 * Math.log10(K.q);
    s2.connect(bass); bass.connect(tre); tre.connect(hp); hp.connect(lp); lp.connect(s3);
    // crunch
    const kDry = G(1), kWet = G(0), shp = ctx.createWaveShaper(), s4 = G(1);
    shp.oversample = 'none'; s3.connect(kDry); s3.connect(shp); shp.connect(kWet); kDry.connect(s4); kWet.connect(s4);
    // echo
    const eDry = G(1), eWet = G(0), s5 = G(1), eDls = [], eGs = [];
    s4.connect(eDry); eDry.connect(s5); eWet.connect(s5);
    { let prev = s4;
      for (let k = 0; k < K.echoTaps; k++) { const d = ctx.createDelay(1.1), g = G(0); prev.connect(d); d.connect(g); g.connect(eWet); eDls.push(d); eGs.push(g); prev = d; } }
    // reverb
    const vDry = G(1), vWet = G(0), conv = ctx.createConvolver(), s6 = G(1);
    conv.normalize = false; s5.connect(vDry); s5.connect(conv); conv.connect(vWet); vDry.connect(s6); vWet.connect(s6);
    // static
    const ns = ctx.createBufferSource(), nG = G(0);
    ns.buffer = noiseBuf(ctx); ns.loop = true; ns.connect(nG); ns.start();
    s6.connect(output); nG.connect(output);
    let lastC = -1, lastSize = -1;
    function update(p0) {
      const p = clean(p0);
      set(rWet.gain, p.robot * K.ringMakeup); set(rDry.gain, 1 - p.robot); set(osc.frequency, p.robot_hz);
      set(cWet.gain, p.chorus); set(lfoG.gain, p.chorus_depth / 1000); set(lfo.frequency, p.chorus_rate);
      set(bass.gain, p.bass); set(tre.gain, p.treble); set(hp.frequency, p.lowcut); set(lp.frequency, Math.min(p.highcut, sr * 0.49));
      const m = Math.min(1, p.crunch * 3); set(kWet.gain, m); set(kDry.gain, 1 - m);
      if (p.crunch > 0 && p.crunch !== lastC) { shp.curve = curve(p.crunch); lastC = p.crunch; }
      set(eWet.gain, p.echo); eDls.forEach((d, k) => { set(d.delayTime, p.echo_time); set(eGs[k].gain, Math.pow(p.echo_fb, k)); });
      set(vWet.gain, p.reverb);
      if (p.reverb > 0 && p.reverb_size !== lastSize) { conv.buffer = irBuf(ctx, p.reverb_size); lastSize = p.reverb_size; }
      set(nG.gain, p.static * K.staticLevel);
    }
    update(p0);
    function dispose() { [osc, lfo, ns].forEach((n) => { try { n.stop(); } catch (e) {} }); try { output.disconnect(); } catch (e) {} }
    return { input: input, output: output, update: update, dispose: dispose };
  }
  window.somaFx = { ready: Promise.resolve(0), cfg: C, defaults: D, clean: clean, isDef: isDef, build: build };
})();
"""

FX_JS = "window.__FXCFG=" + json.dumps(fx.config()) + ";" + _RAW
