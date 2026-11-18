"""Browser-side stem mixer (Web Audio): live mute / solo / volume, waveforms, seek, big video.

Plain HTML/CSS/JS so it works in any Gradio 5.x build. Handlers are attached by event delegation on
`document`, so it doesn't matter how Gradio injects the markup. The panel is visible from the start
with a placeholder (empty video area, greyed-out tracks) and fills in after a separation.
"""

MIXER_HTML = """
<div id="sm-root" class="empty">
<div id="sm-body"><div id="sm-main">
  <div id="sm-vwrap">
    <div id="sm-vph">Video preview appears here</div>
    <video id="sm-video" data-sm="play" muted playsinline preload="auto" style="display:none"></video>
    <div id="sm-note"></div>
  </div>
  <div class="sm-bar">
    <button id="sm-startbtn" class="sm-btn2" data-sm="start" title="Back to start (Home)" disabled>&#9198;</button>
    <button id="sm-playbtn" class="sm-btn" data-sm="play" disabled>&#9654; Play</button>
    <span id="sm-time" class="sm-time">0:00 / 0:00</span>
    <span class="sm-hint">Space = play / pause</span>
    <button id="sm-undobtn" class="sm-btn2" data-sm="undo" title="Undo (Ctrl+Z, or Cmd+Z on a Mac)" disabled>&#8630;</button>
    <button id="sm-redobtn" class="sm-btn2" data-sm="redo" title="Redo (Ctrl+Shift+Z or Ctrl+Y, or Cmd+Shift+Z on a Mac)" disabled>&#8631;</button>
    <span class="sm-sp"></span>
    <button id="sm-cmpbtn" class="sm-btn2" data-sm="cmp" style="display:none" title="Hear the original, untouched audio to compare with the separated tracks">Original</button>
    <span class="sm-lbl" id="sm-vlbl">Voices</span>
    <select id="sm-nv" class="sm-sel" title="How many voices are in this recording"><option>2</option><option>3</option><option>4</option></select>
    <button id="sm-splitbtn" class="sm-btn2" data-sm="split" title="Find and separate the different voices" disabled>Split voices</button>
    <button id="sm-backbtn" class="sm-btn2" data-sm="back" style="display:none" title="Try to take the backing vocals out of the music. Not always reliable: backing vocals are mixed into the music, so listen and check." disabled>Split backing vocals</button>
  </div>
  <div id="sm-rows"></div>
</div>
<div id="sm-meter"><div class="mt-t">dB</div><div class="mt-b"><canvas id="sm-mc"></canvas></div><div class="mt-l"><span>L</span><span>R</span></div></div>
</div></div>
"""

MIXER_CSS = """
#sm-root{border:1px solid var(--border-color-primary,#ddd);border-radius:12px;padding:10px 12px;
  background:var(--background-fill-secondary,#f7f7f8);color:var(--body-text-color,#222)}
#sm-note{position:absolute;left:10px;bottom:10px;max-width:80%;padding:5px 11px;border-radius:8px;background:rgba(0,0,0,.78);color:#fff;
  font-size:12px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:2}
#sm-note.show{opacity:1}
#sm-vwrap{position:relative;width:100%;aspect-ratio:16/9;max-height:46vh;background:#000;border-radius:10px;margin:0 auto 8px;
  display:flex;align-items:center;justify-content:center;overflow:hidden}
#sm-video{width:100%;height:100%;object-fit:contain;cursor:pointer}
#sm-vph{font-size:14px;opacity:.45;letter-spacing:.02em;color:#fff;border:1px dashed #555;border-radius:8px;padding:18px 28px}
.sm-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
#sm-body{display:grid;grid-template-columns:minmax(0,1fr) 62px;gap:12px}
#sm-main{min-width:0}
#sm-meter{display:flex;flex-direction:column;align-items:center;padding:2px 0;background:#0a0d14;border-radius:8px}
#sm-meter .mt-t,#sm-meter .mt-l{font-size:11px;opacity:.55;display:flex;justify-content:space-around;width:100%;padding:3px 0}
#sm-meter .mt-b{position:relative;flex:1;width:100%;min-height:200px}
#sm-mc{position:absolute;inset:0;width:100%;height:100%}
.sm-btn2{padding:6px 10px;border-radius:8px;border:1px solid var(--border-color-primary,#555);cursor:pointer;
  background:transparent;color:inherit;font-size:14px;line-height:1}
.sm-btn2:disabled{opacity:.35;cursor:default}
.sm-btn2.on{background:var(--button-primary-background-fill,#f97316);border-color:var(--button-primary-background-fill,#f97316);color:#fff}
.sm-sp{flex:1}
.sm-lbl,.sm-hint{font-size:12px;opacity:.6}
.sm-time{font-variant-numeric:tabular-nums;font-size:14px;opacity:.85;min-width:84px}
.sm-btn{padding:6px 18px;border-radius:8px;border:0;cursor:pointer;font-weight:600;
  background:var(--button-primary-background-fill,#f97316);color:var(--button-primary-text-color,#fff)}
.sm-btn:disabled{opacity:.35;cursor:default}
.sm-chip{padding:3px 10px;border-radius:999px;border:1px solid var(--border-color-primary,#ccc);
  background:transparent;color:inherit;cursor:pointer;font-size:12px}
.sm-chip:hover{background:var(--background-fill-primary,#fff)}
.sm-sel{padding:5px 6px;border-radius:8px;border:1px solid var(--border-color-primary,#555);background:var(--background-fill-primary,#111);color:inherit;font-size:13px}
.sm-who{position:relative;height:30px;border-radius:8px;background:var(--background-fill-primary,#fff);
  border:1px solid var(--border-color-primary,#e5e5e5);cursor:pointer;user-select:none}
.sm-seg{position:absolute;top:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:11px;
  font-weight:600;color:#fff;overflow:hidden;white-space:nowrap;opacity:.9;border-radius:6px}
.sm-seg:hover{opacity:1;filter:brightness(1.12)}
.sm-edge{position:absolute;top:-3px;bottom:-3px;width:12px;margin-left:-6px;cursor:ew-resize;z-index:3}
.sm-edge::after{content:"";position:absolute;left:5px;width:2px;top:0;bottom:0;background:#fff;border-radius:2px}
#sm-menu{position:fixed;z-index:2000;background:var(--background-fill-primary,#111);color:var(--body-text-color,#eee);
  border:1px solid var(--border-color-primary,#444);border-radius:10px;padding:6px;min-width:170px;box-shadow:0 8px 24px rgba(0,0,0,.45)}
#sm-menu .mh{font-size:11px;opacity:.6;padding:2px 8px 4px}
#sm-menu button{display:flex;align-items:center;gap:8px;width:100%;padding:6px 8px;border:0;background:transparent;
  color:inherit;cursor:pointer;border-radius:6px;font-size:13px;text-align:left}
#sm-menu button:hover{background:rgba(148,163,184,.2)}
.sm-row{display:grid;grid-template-columns:184px 1fr;gap:10px;align-items:center;padding:3px 0;
  border-top:1px solid var(--border-color-primary,#e5e5e5);transition:opacity .15s}
.sm-row.dim{opacity:.38}
.sm-row.ph{opacity:.35}
.sm-name{display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px}
.sm-dot{width:9px;height:9px;border-radius:50%;flex:none}
.sm-ctl{display:flex;align-items:center;gap:5px;margin-top:2px}
.sm-tog{width:24px;height:24px;flex:none;border-radius:6px;border:1px solid var(--border-color-primary,#ccc);
  background:var(--background-fill-primary,#fff);color:inherit;cursor:pointer;font-weight:700;font-size:12px}
.sm-tog:disabled{cursor:default}
.sm-tog.on.m{background:#ef4444;border-color:#ef4444;color:#fff}
.sm-tog.on.s{background:#eab308;border-color:#eab308;color:#111}
.sm-vol{flex:1;min-width:50px;accent-color:var(--button-primary-background-fill,#f97316)}
.sm-vi{width:15px;height:15px;flex:none;opacity:.7}
.sm-db{margin-left:auto;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums;opacity:.6;white-space:nowrap}
.sm-db.chg{opacity:1;color:var(--button-primary-background-fill,#f97316)}
.sm-wave{position:relative;height:38px;border-radius:8px;overflow:hidden;cursor:pointer;
  background:var(--background-fill-primary,#fff);border:1px solid var(--border-color-primary,#e5e5e5)}
.sm-wave.ph{cursor:default}
.sm-wave.ph::after{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:currentColor;opacity:.35}
.sm-wave canvas{width:100%;height:100%;display:block}
.sm-ruler{border-top:0 !important;padding:0 0 2px !important}
.sm-rl{position:relative;height:34px;cursor:ew-resize;user-select:none;touch-action:none;color:var(--body-text-color,#ddd)}
.sm-rl .tk{position:absolute;bottom:0;width:1px;background:currentColor;opacity:.4;height:6px;pointer-events:none}
.sm-rl .tk.M{height:13px;opacity:.7}
.sm-rl .tl{position:absolute;top:1px;font-size:10px;opacity:.8;transform:translateX(-50%);pointer-events:none;font-variant-numeric:tabular-nums;white-space:nowrap}
.sm-rl .tl.first{transform:none}
.sm-rl .tl.last{transform:translateX(-100%)}
.sm-rhead{background:var(--button-primary-background-fill,#f97316) !important;opacity:1 !important;width:2px}
.sm-rhead::before{content:"";position:absolute;top:0;left:-6px;width:14px;height:12px;border-radius:3px 3px 7px 7px;
  background:var(--button-primary-background-fill,#f97316)}
.sm-head{position:absolute;top:0;bottom:0;width:2px;background:var(--body-text-color,#111);left:0;
  pointer-events:none;opacity:.8}
.sm-empty{font-size:11px;opacity:.7;font-weight:400}
#hist-box{max-height:46vh;overflow:auto;border:1px solid rgba(128,128,128,.35);border-radius:8px}
.hrow{padding:7px 10px;cursor:pointer;user-select:none;-webkit-user-select:none;border-bottom:1px solid rgba(128,128,128,.18);font-size:13px}
.hrow:last-child{border-bottom:0}
.hrow .hm{display:block;font-size:11px;opacity:.65;margin-top:1px}
.hrow:hover{background:rgba(128,128,128,.12)}
.hrow.sel{background:rgba(249,115,22,.30)}
.sm-info{position:relative;display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;margin-left:5px;border-radius:50%;
  border:1px solid currentColor;font:italic 700 10px/1 serif;opacity:.6;cursor:help;vertical-align:1px}
.sm-info:hover,.sm-info:focus{opacity:1;outline:none}
.sm-tip{display:none;position:absolute;left:20px;top:-6px;width:210px;padding:8px 10px;border-radius:8px;background:#0b0f1a;color:#e5e7eb;border:1px solid #334155;
  font:400 12px/1.4 system-ui,sans-serif;font-style:normal;text-align:left;white-space:normal;box-shadow:0 6px 18px rgba(0,0,0,.45);z-index:50;pointer-events:none}
.sm-info:hover .sm-tip,.sm-info:focus .sm-tip{display:block}
.sm-zc{display:flex;align-items:center;gap:4px;justify-content:flex-end;padding-right:4px}
.sm-zb{padding:3px 8px !important;font-size:13px !important;min-width:26px}
.sm-rcol{min-width:0}
#sm-sb{position:relative;height:9px;margin-top:3px;border-radius:5px;background:rgba(148,163,184,.18);touch-action:none;cursor:pointer}
#sm-sbt{position:absolute;top:0;bottom:0;border-radius:5px;background:rgba(148,163,184,.7);cursor:grab;min-width:14px}
#sm-sb.flat{cursor:default}
#sm-sb.flat #sm-sbt{cursor:default;opacity:.4}
.sm-edge.sel::after{background:#facc15;width:4px;left:4px;box-shadow:0 0 0 2px rgba(250,204,21,.35)}
@media (max-width:640px){.sm-row{grid-template-columns:1fr}}
.sm-tog.fx{width:30px;font-size:11px}

.sm-tog.fx.open{background:var(--button-primary-background-fill,#f97316);border-color:transparent;color:#fff}
.sm-tog.fx.has::after{content:"";display:inline-block;width:5px;height:5px;border-radius:50%;background:#22c55e;margin-left:2px;vertical-align:middle}
.sm-pop{position:fixed;z-index:1500;width:400px;max-width:calc(100vw - 24px);overflow:auto;padding:12px 14px;border-radius:12px;
  background:var(--background-fill-primary,#111);color:var(--body-text-color,#eee);border:1px solid var(--border-color-primary,#444);
  box-shadow:0 12px 36px rgba(0,0,0,.55);display:none;font-size:13px}
.sm-pop.open{display:block}
.pp-h{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.pp-h b{font-size:14px}.pp-h .t{opacity:.7;flex:1}
.pp-x{background:transparent;border:0;color:inherit;cursor:pointer;font-size:16px;padding:0 4px;opacity:.7}
.fx-top{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;font-size:12px}
.fx-top select,.sm-pop input[type=text]{padding:4px 8px;border-radius:6px;border:1px solid var(--border-color-primary,#444);
  background:var(--background-fill-secondary,#181818);color:inherit;font-size:13px}
.fx-top select{flex:1;min-width:120px}
.fx-top button,.pp-go{padding:4px 10px;border-radius:6px;border:1px solid var(--border-color-primary,#444);
  background:transparent;color:inherit;cursor:pointer;font-size:12px}
.pp-go{background:var(--button-primary-background-fill,#f97316);border-color:transparent;color:#fff;font-weight:600;font-size:13px;padding:5px 14px}
.fx-note{opacity:.6;font-size:11px;line-height:1.35}
.fx-mod{border-top:1px solid var(--border-color-primary,#333);padding:6px 0}
.fx-mh{display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600;font-size:13px}
.fx-mod:not(.on) .fx-mh{opacity:.6;font-weight:500}
.fx-mh input{accent-color:var(--button-primary-background-fill,#f97316)}
.fx-grid{display:none;padding:4px 0 2px 24px}
.fx-mod.on .fx-grid{display:block}
.fx-p{display:grid;grid-template-columns:78px 1fr 64px;gap:8px;align-items:center;font-size:12px;padding:2px 0}
.fx-p input{width:100%;accent-color:var(--button-primary-background-fill,#f97316)}
.fx-p .v{text-align:right;opacity:.8;font-variant-numeric:tabular-nums}

/* settings pop-up */
#settings-modal,#history-modal{position:fixed !important;inset:0;z-index:1000;display:none !important;
  background:rgba(0,0,0,.55);align-items:center;justify-content:center}
#settings-modal.open,#history-modal.open{display:flex !important}
#settings-card,#history-card{max-width:560px;width:92vw;max-height:86vh;overflow:auto;background:var(--background-fill-primary,#111);
  border:1px solid var(--border-color-primary,#444);border-radius:14px;padding:16px !important;gap:10px}

/* progress / status */
.stat{font-size:13px}
.stat .pt{margin-top:6px;line-height:1.35;word-break:break-word}
.stat.ok .pt{color:#4ade80}
.stat.err .pt{color:#f87171}
.pg{height:8px;border-radius:6px;background:var(--border-color-primary,#333);overflow:hidden;position:relative}
.pgb{height:100%;background:var(--button-primary-background-fill,#f97316);border-radius:6px;transition:width .4s}
.pg.ind .pgb{width:35% !important;position:absolute;animation:sm-ind 1.2s ease-in-out infinite}
@keyframes sm-ind{0%{left:-35%}100%{left:100%}}
"""

MIXER_JS = r"""
(function () {
  if (window.somaMixer) return;
  const COLORS = {voices:'#3b82f6', voices2:'#22c55e', voices3:'#06b6d4', voices4:'#ec4899', lead:'#ef4444', backing:'#14b8a6', music:'#a855f7', effects:'#f59e0b', other:'#94a3b8', original:'#9ca3af'};
  const DEFAULT = [['voices','Voices'],['music','Music'],['effects','Effects']];
  let ctx = null, stems = [], st = {}, srcs = [], playing = false, t0 = 0, off = 0, dur = 0;
  let raf = 0, token = 0, hasVideo = false, keepOff = null;
  let segs = [], nv = 0, drag = null;
  let lastSess = '';
  let vs = 0, vd = 0, vraf = 0, lastUV = 0, waveReg = [], sdrag = null;   // the visible part of the timeline: starts at vs, lasts vd seconds
  let undoS = [], redoS = [], lastPush = {g: '', t: 0}, restoring = false, noteT = 0, editT = 0;
  let selT = null, nudgeT = 0, lastEdgeT = 0, nudgeDirty = false, editBusy = false, resend = false, busyT = 0;   // the selected voice line (its time), for the arrow-key nudge
  let ghost = false, cmp = false;  // ghost: the untouched original, kept after a separation for A/B listening
  const FX = window.somaFx, FXC = FX.cfg;
  let fxs = {}, fxStash = {}, fxTimer = 0, popFor = null;
  const LOGP = {lowcut: 1, highcut: 1};
  const fxGet = (k) => FX.clean(fxs[k]);
  const fxIsDef = (k) => FX.isDef(fxs[k]);
  const modOn = (k, m) => { const c = fxGet(k); return Object.keys(m.off).some((n) => Math.abs(c[n] - m.off[n]) > 1e-9); };
  function fxPreset(k) {
    const cur = fxGet(k);
    for (const n in FXC.presets) { const p = FXC.presets[n]; if (Object.keys(p).every((q) => Math.abs(p[q] - cur[q]) < 1e-9)) return n; }
    return 'Custom';
  }
  function fxFmt(n, v) {
    const c = FXC.params[n], d = c.step >= 1 ? 0 : (c.step < 0.1 ? 2 : 1);
    return (+v).toFixed(d) + (c.unit ? ' ' + c.unit : '');
  }
  const toPos = (n, v) => { const c = FXC.params[n]; return LOGP[n] ? 1000 * Math.log(v / c.min) / Math.log(c.max / c.min) : v; };
  const fromPos = (n, x) => { const c = FXC.params[n]; return LOGP[n] ? c.min * Math.pow(c.max / c.min, x / 1000) : x; };
  const labelOf = (k) => { const s = stems.find((x) => x.key === k); return s ? s.label : k; };
  function popEl() {
    let p = $('sm-pop');
    if (!p) { p = document.createElement('div'); p.id = 'sm-pop'; p.className = 'sm-pop'; document.body.appendChild(p); }
    return p;
  }
  function closePop() {
    const p = $('sm-pop'); if (p) p.classList.remove('open');
    document.querySelectorAll('.sm-tog.open').forEach((b) => b.classList.remove('open'));
    popFor = null;
  }
  function placePop(anchor) {
    // Sits over the video area (top right), so the play bar and the tracks stay visible and usable.
    const p = popEl(), vw = $('sm-vph') && $('sm-vph').parentElement, r = vw ? vw.getBoundingClientRect() : null;
    const w = Math.min(400, window.innerWidth - 24);
    p.style.maxHeight = '';
    if (r && r.height > 200) {
      p.style.left = Math.max(12, r.right - w - 8) + 'px'; p.style.top = (r.top + 8) + 'px';
      p.style.maxHeight = (r.height - 16) + 'px';
      return;
    }
    const a = anchor.getBoundingClientRect();
    p.style.left = Math.max(12, Math.min(a.left, window.innerWidth - w - 12)) + 'px'; p.style.top = (a.bottom + 6) + 'px';
    p.style.maxHeight = (window.innerHeight - a.bottom - 18) + 'px';
  }
  function fxPanelHTML(k) {
    const cur = fxGet(k);
    let h = '<div class="pp-h"><b>Effects</b><span class="t">' + labelOf(k) + '</span><button class="pp-x" data-pp="close" title="Close">&#10005;</button></div>' +
      '<div class="fx-top"><select data-fx="preset" data-k="' + k + '">';
    Object.keys(FXC.presets).concat(['Custom']).forEach((n) => { h += '<option' + (n === 'Custom' ? ' hidden' : '') + '>' + n + '</option>'; });
    h += '</select><button data-fx="reset" data-k="' + k + '">Reset</button></div>' +
      '<div class="fx-note" style="margin-bottom:6px">Presets are starting points. Switch an effect on and adjust it. Your original audio is never changed; effects are added when you play and export.</div>';
    FXC.modules.forEach((m) => {
      h += '<div class="fx-mod' + (modOn(k, m) ? ' on' : '') + '" data-m="' + m.id + '"><label class="fx-mh"><input type="checkbox" data-fx="mod" data-k="' + k +
        '" data-m="' + m.id + '"' + (modOn(k, m) ? ' checked' : '') + '><span>' + m.name + '</span></label><div class="fx-grid">';
      m.params.forEach((n) => {
        const c = FXC.params[n], log = LOGP[n];
        h += '<label class="fx-p"><span>' + c.label + '</span><input type="range" data-fx="param" data-k="' + k + '" data-n="' + n + '" min="' +
          (log ? 0 : c.min) + '" max="' + (log ? 1000 : c.max) + '" step="' + (log ? 1 : c.step) + '" value="' + toPos(n, cur[n]) + '"><span class="v">' + fxFmt(n, cur[n]) + '</span></label>';
      });
      h += '</div></div>';
    });
    return h;
  }
  function openPop(mode, k, anchor) {
    const same = popFor && popFor.mode === mode && popFor.key === k;
    closePop(); if (same) return;
    const p = popEl(); p.innerHTML = fxPanelHTML(k);
    popFor = {mode: mode, key: k}; p.classList.add('open'); anchor.classList.add('open'); placePop(anchor);
  }
  function fxSync(k) {
    const b = $('sm-f-' + k); if (b) b.classList.toggle('has', !fxIsDef(k));
    if (!popFor || popFor.mode !== 'fx' || popFor.key !== k) return;
    const box = $('sm-pop'), cur = fxGet(k);
    box.querySelectorAll('input[data-n]').forEach((i) => { i.value = toPos(i.dataset.n, cur[i.dataset.n]); i.nextElementSibling.textContent = fxFmt(i.dataset.n, cur[i.dataset.n]); });
    box.querySelectorAll('.fx-mod').forEach((d) => {
      const m = FXC.modules.find((x) => x.id === d.dataset.m), on = modOn(k, m);
      d.classList.toggle('on', on); d.querySelector('input[data-fx=mod]').checked = on;
    });
    const sel = box.querySelector('select'); if (sel) sel.value = fxPreset(k);
  }
  function modToggle(k, mid, want) {
    const m = FXC.modules.find((x) => x.id === mid), cur = fxGet(k);
    fxStash[k] = fxStash[k] || {};
    const next = Object.assign({}, cur);
    if (want) {
      Object.assign(next, fxStash[k][mid] || m.on);
    } else {
      fxStash[k][mid] = Object.fromEntries(m.params.map((n) => [n, cur[n]]));
      Object.assign(next, m.off);
    }
    fxs[k] = next; fxChanged(k);
  }
  // route one track: straight to its volume when it has no effects, otherwise through the effects chain
  function fxRoute(s) {
    if (!s.pre) return;
    const c = ac();
    try { s.pre.disconnect(); } catch (e) {}
    if (s.fxn) { try { s.fxn.dispose(); } catch (e) {} s.fxn = null; }
    if (fxIsDef(s.key) || !playing) { s.pre.connect(s.gain); return; }
    s.fxn = FX.build(c, fxGet(s.key)); s.pre.connect(s.fxn.input); s.fxn.output.connect(s.gain);
  }
  function fxChanged(k) {
    const s = stems.find((x) => x.key === k);
    if (s) {
      const was = !!s.fxn, need = !fxIsDef(k);
      if (playing && was && need) s.fxn.update(fxGet(k)); else fxRoute(s);
    }
    fxSync(k);
    clearTimeout(fxTimer);
    fxTimer = setTimeout(() => { const b = $('fx-btn'); if (b) b.click(); }, 700);
  }
  function fxJson() {
    const o = {}; Object.keys(fxs).forEach((k) => { if (!FX.isDef(fxs[k])) o[k] = FX.clean(fxs[k]); });
    return JSON.stringify(o);
  }
  const $ = (id) => document.getElementById(id);
  const fmt = (t) => { t = Math.max(0, t|0); return Math.floor(t/60) + ':' + String(t%60).padStart(2,'0'); };
  const vid = () => $('sm-video');

  function vpause() { if (hasVideo) { try { vid().pause(); } catch (e) {} } }
  function vplay(t) {
    if (!hasVideo) return;
    const v = vid(); try { v.currentTime = t; } catch (e) {}
    const p = v.play(); if (p && p.catch) p.catch(() => {});
  }
  function vsync(t) {
    if (!hasVideo) return;
    const v = vid(); if (!v.seeking && Math.abs(v.currentTime - t) > 0.25) v.currentTime = t;
  }
  let master = null, an = [], lvl = [-90, -90], pk = [-90, -90], pkT = [0, 0];
  function ac() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain(); master.channelCount = 2; master.channelCountMode = 'explicit';
      master.channelInterpretation = 'speakers'; master.connect(ctx.destination);  // mono plays on both sides
      const sp = ctx.createChannelSplitter(2); master.connect(sp);
      an = [0, 1].map((i) => { const a = ctx.createAnalyser(); a.fftSize = 1024; sp.connect(a, i); return a; });
    }
    return ctx;
  }
  const mbuf = new Float32Array(1024);
  function drawMeter() {
    const cv = $('sm-mc'); if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.width = cv.clientWidth * dpr, H = cv.height = cv.clientHeight * dpr;
    const g = cv.getContext('2d'); g.clearRect(0, 0, W, H);
    const nowT = performance.now(), segs = 48, gap = 2 * dpr;
    const sh = (H - gap * (segs - 1)) / segs, bw = (W - 4 * dpr) / 2 - 2 * dpr, MIN = -60;
    let busy = false;
    for (let c = 0; c < 2; c++) {
      let db = -90;
      if (playing && an[c]) {
        an[c].getFloatTimeDomainData(mbuf); let p = 0;
        for (let i = 0; i < mbuf.length; i++) p = Math.max(p, Math.abs(mbuf[i]));
        db = p > 0 ? 20 * Math.log10(p) : -90;
      }
      lvl[c] = db > lvl[c] ? db : Math.max(db, lvl[c] - 1.2);
      if (lvl[c] > pk[c] || nowT - pkT[c] > 900) { if (lvl[c] >= pk[c] - 0.1) pkT[c] = nowT; pk[c] = Math.max(lvl[c], pk[c] - 0.6); }
      if (lvl[c] > MIN || pk[c] > MIN) busy = true;
      const x = 2 * dpr + c * (bw + 4 * dpr);
      for (let s = 0; s < segs; s++) {
        const segDb = MIN * (1 - (s + 1) / segs), on = lvl[c] >= segDb;
        const col = segDb > -6 ? '#ef4444' : segDb > -12 ? '#facc15' : '#4ade80';
        g.globalAlpha = on ? 1 : 0.14; g.fillStyle = col;
        g.fillRect(x, H - (s + 1) * sh - s * gap, bw, sh);
      }
      const ps = Math.max(0, Math.min(segs - 1, Math.floor((1 - Math.max(pk[c], MIN) / MIN) * segs) - 1));
      if (pk[c] > MIN) {
        const pdb = MIN * (1 - (ps + 1) / segs);
        g.globalAlpha = 1; g.fillStyle = pdb > -6 ? '#ef4444' : pdb > -12 ? '#facc15' : '#4ade80';
        g.fillRect(x, H - (ps + 1) * sh - ps * gap, bw, sh);
      }
    }
    g.globalAlpha = 1;
    return busy;
  }
  let mraf = 0;
  function meterLoop() { cancelAnimationFrame(mraf); const busy = drawMeter(); if (playing || busy) mraf = requestAnimationFrame(meterLoop); }
  function eff(k, forExport) {
    const s = st[k]; if (!s || s.mute) return 0;
    if (ghost && !forExport) {  // original is only heard while comparing, and then it is the only thing heard
      if (cmp) return k === 'original' ? s.vol : 0;
      if (k === 'original') return 0;
    }
    if (ghost && k === 'original') return 0;
    const anySolo = Object.values(st).some((x) => x.solo);
    return (anySolo && !s.solo) ? 0 : s.vol;
  }
  function refresh() {
    stems.forEach((s) => {
      const g = eff(s.key);
      if (s.gain) s.gain.gain.setTargetAtTime(g, ac().currentTime, 0.015);
      const row = $('sm-row-' + s.key); if (!row) return;
      row.classList.toggle('dim', g === 0);
      $('sm-m-' + s.key).classList.toggle('on', st[s.key].mute);
      $('sm-s-' + s.key).classList.toggle('on', st[s.key].solo);
      const sl = row.querySelector('.sm-vol'); if (sl && parseFloat(sl.value) !== st[s.key].vol) sl.value = st[s.key].vol;
      const db = $('sm-db-' + s.key);
      if (db) { const v = st[s.key].vol; db.textContent = 'Vol ' + volFmt(v); db.classList.toggle('chg', Math.abs(v - 1) > 0.004); }
    });
  }
  function volFmt(v) {
    if (v <= 0.001) return '-\u221E dB';
    const d = 20 * Math.log10(v);
    return (Math.abs(d) < 0.05 ? '0' : (d > 0 ? '+' : '') + d.toFixed(1)) + ' dB';
  }
  function now() { return playing ? ac().currentTime - t0 : off; }
  function X(t) { return 100 * (t - vs) / (vd || 1); }   // time -> percent across the visible part
  function Tx(e, el) {   // mouse position -> time, in the visible part
    const r = (el || $('sm-rl')).getBoundingClientRect();
    return vs + vd * Math.min(1, Math.max(0, (e.clientX - r.left) / (r.width || 1)));
  }
  function paintTime() {
    const t = Math.min(now(), dur);
    $('sm-time').textContent = fmt(t) + ' / ' + fmt(dur);
    const p = X(t), vis = !!dur && p >= -0.01 && p <= 100.01;
    document.querySelectorAll('.sm-head').forEach((h) => { h.style.left = (dur ? p : 0) + '%'; h.style.display = vis ? '' : 'none'; });
  }
  // ---- time ruler: ticks and seconds, with a playhead you can drag. It follows the zoom.
  const RSTEPS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  const MIN_VD = 0.2;   // the closest zoom: 0.2 seconds across the whole width
  function rfmt(t, step) {
    t = Math.round(t * 100) / 100;
    const m = Math.floor(t / 60), sec = t - m * 60;
    if (step < 0.1) return m + ':' + sec.toFixed(2).padStart(5, '0');
    if (step < 1) return m + ':' + sec.toFixed(1).padStart(4, '0');
    return m + ':' + String(Math.round(sec)).padStart(2, '0');
  }
  function drawRuler() {
    const rl = $('sm-rl'); if (!rl || !dur) return;
    const w = rl.clientWidth; if (w < 50) return;
    const step = RSTEPS.find((x) => (x / vd) * w >= 70) || RSTEPS[RSTEPS.length - 1];
    const minor = step >= 1 ? (step % 5 === 0 || step === 1 ? 5 : 4) : 5;
    const sub = step / minor, i0 = Math.max(0, Math.floor(vs / sub - 1e-9)), i1 = Math.ceil((vs + vd) / sub + 1e-9);
    let h = '';
    for (let i = i0; i <= i1; i++) {
      const t = i * sub; if (t > dur + 1e-9 || t < vs - 1e-9 || t > vs + vd + 1e-9) continue;
      const major = i % minor === 0, p = X(t);
      h += '<div class="tk' + (major ? ' M' : '') + '" style="left:' + p + '%"></div>';
      if (major) h += '<div class="tl' + (p < 3 ? ' first' : (p > 94 ? ' last' : '')) + '" style="left:' + p + '%">' + rfmt(t, step) + '</div>';
    }
    rl.querySelectorAll('.tk,.tl').forEach((e) => e.remove());
    rl.insertAdjacentHTML('afterbegin', h);
  }
  function addRuler(rows) {
    const r = document.createElement('div'); r.className = 'sm-row sm-ruler'; r.id = 'sm-row-ruler';
    r.innerHTML = '<div class="sm-zc"><button class="sm-btn2 sm-zb" id="sm-zout" data-sm="zout" title="Zoom out (Ctrl or Cmd + mouse wheel)">&minus;</button>' +
      '<button class="sm-btn2 sm-zb" id="sm-zin" data-sm="zin" title="Zoom in (Ctrl or Cmd + mouse wheel)">+</button>' +
      '<button class="sm-btn2 sm-zb" id="sm-zfit" data-sm="zfit" title="Show the whole file">Fit</button><span id="sm-zl" class="sm-hint">1&times;</span></div>' +
      '<div class="sm-rcol"><div class="sm-rl" id="sm-rl" title="Click or drag to move the playhead"><div class="sm-head sm-rhead"></div></div>' +
      '<div id="sm-sb" class="flat" title="Drag to scroll when zoomed in"><div id="sm-sbt"></div></div></div>';
    rows.prepend(r); drawRuler(); updateScroll();
    if (window.ResizeObserver) new ResizeObserver(() => { drawRuler(); redrawWaves(); }).observe($('sm-rl'));
  }
  // ---- zoom: one visible window (vs .. vs + vd) shared by the ruler, the waveforms, the voice strip and the playheads
  function clampView() {
    if (!dur) { vs = 0; vd = 0; return; }
    vd = Math.min(dur, Math.max(Math.min(MIN_VD, dur), vd || dur));
    vs = Math.min(Math.max(0, vs), dur - vd);
  }
  function viewChanged() {
    if (vraf) return;
    vraf = requestAnimationFrame(() => { vraf = 0; drawRuler(); redrawWaves(); renderWho(); updateScroll(); paintTime(); });
  }
  function setView(a, d) { vs = a; vd = d; clampView(); viewChanged(); }
  function zoomAt(f, t) {   // f > 1 zooms in, around time t
    if (!dur) return;
    const nd = Math.min(dur, Math.max(Math.min(MIN_VD, dur), vd / f)), fr = (t - vs) / vd;
    setView(t - fr * nd, nd);
  }
  function anchorT() { const p = now(); return (p >= vs && p <= vs + vd) ? p : vs + vd / 2; }
  function follow(t, auto) {   // keep a time in view. While playing, don't fight you if you just scrolled away.
    if (!dur || vd >= dur - 1e-6) return;
    if (auto && performance.now() - lastUV < 2500) return;
    if (t < vs || t > vs + vd * (auto ? 0.97 : 1)) setView(t - vd * (auto ? 0.05 : 0.5), vd);
  }
  function updateScroll() {
    const th = $('sm-sbt'); if (!th) return;
    const z = dur && vd ? dur / vd : 1;
    th.style.width = (100 / z) + '%'; th.style.left = (dur ? 100 * vs / dur : 0) + '%';
    $('sm-sb').classList.toggle('flat', z < 1.001);
    const zl = $('sm-zl'); if (zl) zl.textContent = (z < 9.95 ? String(+z.toFixed(1)) : String(Math.round(z))) + '\u00d7';
    const zi = $('sm-zin'), zo = $('sm-zout'), zf = $('sm-zfit');
    if (zi) zi.disabled = !dur || vd <= Math.min(MIN_VD, dur) + 1e-6;
    if (zo) zo.disabled = z < 1.001;
    if (zf) zf.disabled = z < 1.001;
  }
  document.addEventListener('wheel', (e) => {
    if (!dur || !(e.target.closest && e.target.closest('#sm-rows')) || !$('sm-rl')) return;
    if (!(e.target.closest('.sm-wave') || e.target.closest('#sm-who') || e.target.closest('.sm-rcol'))) return;   // only over the timeline itself
    const k = e.deltaMode === 1 ? 16 : 1;
    if (e.ctrlKey || e.metaKey) {   // Ctrl or Cmd + wheel, or a trackpad pinch: zoom where the pointer is
      e.preventDefault(); lastUV = performance.now();
      zoomAt(Math.exp(-e.deltaY * k * 0.0025), Tx(e, $('sm-rl')));
    } else if (vd < dur - 1e-6 && (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY))) {   // sideways scroll
      e.preventDefault(); lastUV = performance.now();
      const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * k;
      setView(vs + d / $('sm-rl').clientWidth * vd, vd);
    }
  }, {passive: false, capture: true});
  document.addEventListener('pointerdown', (e) => {   // the scroll bar under the ruler
    const sb = e.target.closest && e.target.closest('#sm-sb'); if (!sb || e.button !== 0 || !dur || vd >= dur - 1e-6) return;
    const th = e.target.closest('#sm-sbt'), r = sb.getBoundingClientRect();
    e.preventDefault(); lastUV = performance.now();
    if (th) { sdrag = {x: e.clientX, vs0: vs, w: r.width}; try { th.setPointerCapture(e.pointerId); } catch (x) {} }
    else setView(dur * Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) - vd / 2, vd);
  });
  document.addEventListener('pointermove', (e) => {
    if (!sdrag) return;
    if (!(e.buttons & 1)) { sdrag = null; return; }
    lastUV = performance.now(); setView(sdrag.vs0 + (e.clientX - sdrag.x) / sdrag.w * dur, vd);
  });
  document.addEventListener('pointerup', () => { sdrag = null; });
  let rdrag = null;
  function rulerT(e) { return Tx(e, $('sm-rl')); }
  function rulerTo(t) {
    off = t; if (hasVideo) { try { vid().currentTime = off; } catch (e) {} }
    paintTime();
  }
  document.addEventListener('pointerdown', (e) => {
    const rl = e.target.closest && e.target.closest('#sm-rl'); if (!rl || !stems.length) return;
    if (e.button !== 0) return;        // only the left button moves the playhead
    e.preventDefault(); rdrag = {was: playing};
    if (playing) halt();               // scrubbing is silent, like a DAW; playback resumes on release
    try { rl.setPointerCapture(e.pointerId); } catch (x) {}
    rulerTo(rulerT(e));
  });
  document.addEventListener('pointermove', (e) => {
    if (!rdrag) return;
    if (!(e.buttons & 1)) { rulerUp(e); return; }  // the button was let go somewhere we didn't see
    rulerTo(rulerT(e));
  });
  function rulerUp(e) {
    if (!rdrag) return;
    const was = rdrag.was; rdrag = null; rulerTo(rulerT(e));
    if (was) play(); else label();
  }
  document.addEventListener('pointerup', rulerUp);
  document.addEventListener('pointercancel', () => { if (rdrag) { const was = rdrag.was; rdrag = null; if (was) play(); } });
  function tick() {
    if (!playing) return;
    if (now() >= dur) { halt(); off = 0; if (hasVideo) vid().currentTime = 0; label(); paintTime(); return; }
    vsync(now()); paintTime(); follow(now(), true); raf = requestAnimationFrame(tick);
  }
  function label() { const b = $('sm-playbtn'); if (b) b.innerHTML = playing ? '&#10073;&#10073; Pause' : '&#9654; Play'; }
  function halt() {
    if (playing) off = ac().currentTime - t0;
    srcs.forEach((n) => { try { n.onended = null; n.stop(); } catch (e) {} });
    srcs = []; playing = false; cancelAnimationFrame(raf); vpause(); stems.forEach(fxRoute);
  }
  function play() {
    if (!stems.length) return;
    const c = ac(); c.resume();
    if (off >= dur - 0.05) off = 0;
    playing = true; stems.forEach(fxRoute);
    srcs = stems.map((s) => { const n = c.createBufferSource(); n.buffer = s.buf; n.connect(s.pre); n.start(0, off); return n; });
    t0 = c.currentTime - off; vplay(off); label(); tick(); meterLoop();
  }
  function toggle() { if (!stems.length) return; playing ? (halt(), label(), paintTime()) : play(); }
  function seek(t) {
    const was = playing; if (was) halt();
    off = Math.min(Math.max(0, t), dur); follow(off, false);
    if (was) play(); else { if (hasVideo) vid().currentTime = off; paintTime(); }
  }
  function reg(c, b, col) { waveReg = waveReg.filter((x) => x.c.isConnected && x.c !== c); waveReg.push({c: c, b: b, col: col}); }
  function redrawWaves() { waveReg.forEach((x) => { if (x.c.isConnected) paintWave(x.c, x.b, x.col); }); }
  function drawWave(canvas, buf, color) { reg(canvas, buf, color); paintWave(canvas, buf, color); }
  function paintWave(canvas, buf, color) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width = Math.max(300, canvas.clientWidth * dpr), h = canvas.height = canvas.clientHeight * dpr;
    const d = buf.getChannelData(0), sr = buf.sampleRate, v0 = vs, per = Math.max(1e-3, (vd || buf.duration) * sr / w);
    const g = canvas.getContext('2d'); g.clearRect(0, 0, w, h); g.fillStyle = color;
    for (let x = 0; x < w; x++) {
      const a = Math.floor(v0 * sr + x * per); if (a >= d.length) break;
      const b = Math.min(d.length, Math.max(a + 1, Math.floor(v0 * sr + (x + 1) * per)));
      let mx = 0; const step = Math.max(1, ((b - a) / 24) | 0);
      for (let i = a; i < b; i += step) { const v = Math.abs(d[i]); if (v > mx) mx = v; }
      const bar = Math.min(1, mx * 1.6) * h * 0.46;
      g.fillRect(x, h / 2 - bar, 1, Math.max(1, bar * 2));
    }
  }
  function rowHTML(key, label, silent, ph) {
    const dis = ph ? ' disabled' : '';
    return '<div><div class="sm-name"><span class="sm-dot" style="background:' + COLORS[key] + '"></span>' + label +
      (silent ? ' <span class="sm-empty">(empty)</span>' : '') +
      '<span class="sm-db" id="sm-db-' + key + '" title="Volume. Double-click the slider to reset it to 0 dB.">Vol 0 dB</span></div>' +
      '<div class="sm-ctl"><button class="sm-tog m" id="sm-m-' + key + '" data-sm="mute" data-k="' + key + '" title="Mute"' + dis + '>M</button>' +
      '<button class="sm-tog s" id="sm-s-' + key + '" data-sm="solo" data-k="' + key + '" title="Solo"' + dis + '>S</button>' +
      '<button class="sm-tog fx" id="sm-f-' + key + '" data-sm="fx" data-k="' + key + '" title="Effects (EQ, filter, reverb, presets...)"' + dis + '>FX</button>' +
      '<svg class="sm-vi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>Volume</title><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>' +
      '<input class="sm-vol" type="range" min="0" max="1.5" step="0.01" value="1" data-sm="vol" data-k="' + key + '" title="Volume. Double-click to reset to 0 dB."' + dis + '></div></div>' +
      '<div class="sm-wave' + (ph ? ' ph' : '') + '"' + (ph ? '' : ' data-sm="wave"') + '>' + (ph ? '' : '<canvas></canvas><div class="sm-head"></div>') + '</div>';
  }
  function placeholder() {
    if (!playing && !stems.length) drawMeter();
    const rows = $('sm-rows'); if (!rows || loading || stems.length || rows.children.length) return;
    DEFAULT.forEach(([k, l]) => {
      const row = document.createElement('div'); row.className = 'sm-row ph'; row.id = 'sm-row-' + k;
      row.innerHTML = rowHTML(k, l, false, true); rows.appendChild(row);
    });
  }
  // ---- "Who is talking" strip: blocks per voice, editable
  const keyOf = (v) => (v === 0 ? 'voices' : 'voices' + (v + 1));
  const vcol = (v) => COLORS[keyOf(v)] || '#94a3b8';
  function whoHint() {}   // no text here any more: it made the row jump around
  function renderWho() {
    const w = $('sm-who'); if (!w || !dur) return;
    if (selT !== null && !segs.some((s, i) => i > 0 && Math.abs(s[0] - selT) < 0.02)) selT = null;
    w.innerHTML = ''; const ve = vs + vd;
    segs.forEach((s, i) => {
      if (s[1] <= vs || s[0] >= ve) return;
      const a = Math.max(s[0], vs), b = Math.min(s[1], ve);
      const d = document.createElement('div'); d.className = 'sm-seg'; d.dataset.i = i;
      d.style.left = X(a) + '%'; d.style.width = (100 * (b - a) / vd) + '%'; d.style.background = vcol(s[2]);
      d.textContent = (b - a) / vd > 0.06 ? 'Voice ' + (s[2] + 1) : String(s[2] + 1);
      w.appendChild(d);
      if (i > 0 && s[0] >= vs && s[0] <= ve) {
        const e = document.createElement('div'); e.className = 'sm-edge' + (selT !== null && Math.abs(s[0] - selT) < 0.02 ? ' sel' : '');
        e.dataset.i = i; e.style.left = X(s[0]) + '%'; w.appendChild(e);
      }
    });
    const h = document.createElement('div'); h.className = 'sm-head'; w.appendChild(h); paintTime(); whoHint();
  }
  // A part's 4th value marks a cut you made by hand: its start line stays, even while both sides are the same voice,
  // so you can move it or give one side a new voice later. Other same-voice neighbours are joined as before.
  function mergeSegs() {
    const out = [];
    segs.forEach((s) => {
      const l = out[out.length - 1];
      if (l && l[2] === s[2] && !s[3]) l[1] = s[1]; else out.push([s[0], s[1], s[2], (l && l[2] === s[2]) ? s[3] : 0]);
    });
    segs = out;
  }
  let cuts = [], cutsFor = -1;  // hand-made cuts survive the reload that follows an edit
  function rememberCuts() { cuts = segs.filter((x) => x[3]).map((x) => x[0]); }
  function sendEdit() {   // one save at a time: a nudge made while the last save is still working is sent right after it
    if (editBusy) { resend = true; return; }
    editBusy = true; nudgeDirty = false; keepOff = now(); clearTimeout(busyT); busyT = setTimeout(() => { editBusy = false; }, 20000);
    const b = $('edit-btn'); if (b) b.click();
  }
  function editDone() { editBusy = false; clearTimeout(busyT); if (resend) { resend = false; sendEdit(); } }
  function closeMenu() { const m = $('sm-menu'); if (m) m.remove(); }
  function openMenu(i, t, x, y) {
    closeMenu();
    const s = segs[i], m = document.createElement('div'); m.id = 'sm-menu';
    let h = '<div class="mh">This part is…</div>';
    for (let v = 0; v < nv; v++) h += '<button data-act="voice" data-i="' + i + '" data-v="' + v + '"><span class="sm-dot" style="background:' + vcol(v) + '"></span>Voice ' + (v + 1) + (v === s[2] ? ' (now)' : '') + '</button>';
    if (nv < 4) h += '<button data-act="voice" data-i="' + i + '" data-v="' + nv + '"><span class="sm-dot" style="background:' + vcol(nv) + '"></span>A new voice (Voice ' + (nv + 1) + ')</button>';
    const ph = now(), okP = ph - s[0] > 0.05 && s[1] - ph > 0.05, okC = t - s[0] > 0.05 && s[1] - t > 0.05;
    if (okP) h += '<button data-act="cut" data-i="' + i + '" data-t="' + ph + '">Cut at the time marker (' + ph.toFixed(2) + ' s)</button>';
    if (okC && (!okP || Math.abs(t - ph) > 0.05)) h += '<button data-act="cut" data-i="' + i + '" data-t="' + t + '">Cut where I clicked (' + t.toFixed(2) + ' s)</button>';
    if (s[3]) h += '<button data-act="joinprev" data-i="' + i + '">Join with the part before</button>';
    if (segs[i + 1] && segs[i + 1][3]) h += '<button data-act="joinnext" data-i="' + i + '">Join with the part after</button>';
    m.innerHTML = h; document.body.appendChild(m);
    m.style.left = Math.min(x, window.innerWidth - 190) + 'px'; m.style.top = Math.min(y + 8, window.innerHeight - m.offsetHeight - 8) + 'px';
  }
  document.addEventListener('mousedown', (e) => {
    const ed = e.target.closest && e.target.closest('.sm-edge'); if (!ed || e.button !== 0) return;
    e.preventDefault(); e.stopPropagation(); closeMenu();
    const i = +ed.dataset.i; lastEdgeT = performance.now();
    drag = {i: i, moved: false, pre: snap()};
    selT = segs[i][0]; renderWho();   // clicking a line selects it, so the arrow keys can nudge it
  }, true);
  function endDrag() {
    if (!drag) return; const moved = drag.moved, pre = drag.pre; drag = null; lastEdgeT = performance.now();
    if (moved) { pushSnap(pre, 'moved a voice line'); mergeSegs(); rememberCuts(); renderWho(); sendEdit(); }
  }
  document.addEventListener('mousemove', (e) => {
    if (!drag) return;
    if (!(e.buttons & 1)) { endDrag(); return; }
    let t = Tx(e, $('sm-who'));
    t = Math.min(Math.max(t, segs[drag.i - 1][0] + 0.05), segs[drag.i][1] - 0.05);
    segs[drag.i - 1][1] = t; segs[drag.i][0] = t; selT = t; drag.moved = true; renderWho();
  });
  document.addEventListener('mouseup', endDrag);
  document.addEventListener('click', (e) => {
    const mb = e.target.closest && e.target.closest('#sm-menu button');
    if (mb) {
      const i = +mb.dataset.i;
      if (mb.dataset.act === 'voice') {
        pushUndo('changed who is talking'); const v = +mb.dataset.v; segs[i][2] = v; segs[i][3] = segs[i][3] && segs[i - 1] && segs[i - 1][2] === v ? segs[i][3] : 0;
        if (segs[i + 1] && segs[i + 1][2] !== v) segs[i + 1][3] = 0;
        if (v >= nv) nv = v + 1; mergeSegs(); rememberCuts(); renderWho(); closeMenu(); sendEdit();
      } else if (mb.dataset.act === 'cut') {
        pushUndo('added a cut'); const t = parseFloat(mb.dataset.t), s = segs[i]; segs.splice(i, 1, [s[0], t, s[2], s[3] || 0], [t, s[1], s[2], 1]);
        rememberCuts(); renderWho(); closeMenu();
      } else if (mb.dataset.act === 'joinprev' || mb.dataset.act === 'joinnext') {
        pushUndo('joined two parts'); const j = mb.dataset.act === 'joinprev' ? i : i + 1; if (segs[j]) segs[j][3] = 0;
        mergeSegs(); rememberCuts(); renderWho(); closeMenu();
      }
      return;
    }
    closeMenu();
    if (selT !== null && e.isTrusted && performance.now() - lastEdgeT > 400 && !(e.target.closest && e.target.closest('.sm-edge'))) { selT = null; renderWho(); }
    const sg = e.target.closest && e.target.closest('.sm-seg'); if (!sg) return;
    seek(Tx(e, $('sm-who')));  // left-click only moves the playhead
  });
  // Right-click a part to change who it is, or to cut it
  document.addEventListener('contextmenu', (e) => {
    const who = e.target.closest && e.target.closest('#sm-who'); if (!who || !segs.length) return;
    e.preventDefault();
    if (drag) drag = null;
    const t = Tx(e, who);
    let i = segs.findIndex((x) => t >= x[0] && t < x[1]); if (i < 0) i = t < segs[0][0] ? 0 : segs.length - 1;
    openMenu(i, t, e.clientX, e.clientY);
  });
  // ---- undo / redo: snapshots of the voice parts and the mixer (mute, solo, volume, effects)
  function snap() { return JSON.stringify({segs: segs.map((x) => x.slice()), nv: nv, st: st, fxs: fxs}); }
  function noteMsg(txt) {
    const n = $('sm-note'); if (!n) return; n.textContent = txt; n.classList.add('show');
    clearTimeout(noteT); noteT = setTimeout(() => { n.classList.remove('show'); }, 2200);
  }
  function undoBtns() {
    const u = $('sm-undobtn'), r = $('sm-redobtn');
    if (u) u.disabled = !undoS.length;
    if (r) r.disabled = !redoS.length;
  }
  function clearHistory() { undoS = []; redoS = []; lastPush = {g: '', t: 0}; undoBtns(); }
  // call pushUndo(...) BEFORE the change. Changes in a row with the same group name (a slider drag, a run of nudges) are one step.
  function pushSnap(js, label, group) {
    if (restoring || !stems.length) return;
    const t = performance.now();
    if (group && lastPush.g === group && t - lastPush.t < 900) { lastPush.t = t; return; }
    lastPush = {g: group || '', t: t};
    undoS.push({s: js, label: label}); if (undoS.length > 100) undoS.shift();
    redoS = []; undoBtns();
  }
  function pushUndo(label, group) { pushSnap(snap(), label, group); }
  function sendEditSoon() { clearTimeout(editT); editT = setTimeout(sendEdit, 450); }
  function applyState(js) {
    const o = JSON.parse(js); restoring = true;
    try {
      const key = (a) => JSON.stringify(a.map((x) => [x[0], x[1], x[2]]));
      const voicesChanged = key(o.segs) !== key(segs) || o.nv !== nv;
      Object.keys(o.st || {}).forEach((k) => { if (st[k]) st[k] = {mute: !!o.st[k].mute, solo: !!o.st[k].solo, vol: o.st[k].vol}; });
      refresh();
      const old = fxs; fxs = o.fxs || {};
      new Set(Object.keys(old).concat(Object.keys(fxs))).forEach((k) => { if (JSON.stringify(old[k] || null) !== JSON.stringify(fxs[k] || null)) fxChanged(k); });
      if (voicesChanged) { segs = o.segs.map((x) => x.slice()); nv = o.nv; rememberCuts(); renderWho(); sendEditSoon(); }
    } finally { restoring = false; }
  }
  function undo() {
    if (!undoS.length || !stems.length) return;
    clearTimeout(nudgeT); const e = undoS.pop(); redoS.push({s: snap(), label: e.label}); lastPush = {g: '', t: 0};
    applyState(e.s); undoBtns(); noteMsg('Undid: ' + e.label);
  }
  function redo() {
    if (!redoS.length || !stems.length) return;
    clearTimeout(nudgeT); const e = redoS.pop(); undoS.push({s: snap(), label: e.label}); lastPush = {g: '', t: 0};
    applyState(e.s); undoBtns(); noteMsg('Redid: ' + e.label);
  }
  // ---- nudge the selected voice line with the arrow keys
  function nudge(dir, big) {
    if (selT === null) return;
    const i = segs.findIndex((x, k) => k > 0 && Math.abs(x[0] - selT) < 0.02);
    if (i < 1) { selT = null; whoHint(); return; }
    let t = Math.round((segs[i][0] + dir * (big ? 0.1 : 0.01)) * 1000) / 1000;
    t = Math.min(Math.max(t, segs[i - 1][0] + 0.05), segs[i][1] - 0.05);
    if (Math.abs(t - segs[i][0]) < 1e-6) return;
    pushUndo('moved a voice line', 'nudge');
    segs[i - 1][1] = t; segs[i][0] = t; selT = t;
    nudgeDirty = true;
    follow(t, false); renderWho(); paintTime();
    clearTimeout(nudgeT); nudgeT = setTimeout(() => { mergeSegs(); rememberCuts(); renderWho(); sendEdit(); }, 600);
  }
  function applyPreset(p) {
    pushUndo('preset');
    Object.keys(st).forEach((k) => { st[k].solo = false; st[k].mute = false; });
    if (p === 'novoices') { ['voices', 'voices2', 'voices3', 'voices4'].forEach((k) => { if (st[k]) st[k].mute = true; }); }
    if (p === 'nomusic' && st.music) st.music.mute = true;
    if (p === 'noeffects' && st.effects) st.effects.mute = true;
    if (p === 'voices') { ['voices', 'voices2', 'voices3', 'voices4'].forEach((k) => { if (st[k]) st[k].solo = true; }); }
    refresh();
  }
  function reset() {
    if (!stems.length) return;
    pushUndo('reset the mixer');
    stems.forEach((s) => { st[s.key] = {mute: !!s.silent, solo: false, vol: 1}; });
    if (cmp) setCompare(false);
    document.querySelectorAll('.sm-vol').forEach((i) => { i.value = 1; });
    refresh();
  }

  function setCompare(on) {
    const g = stems.find((s) => s.ghost), rows = $('sm-rows'); if (!g || !rows) return;
    cmp = !!on; const b = $('sm-cmpbtn'); if (b) b.classList.toggle('on', cmp);
    const old = $('sm-row-original'); if (old) old.remove();
    if (cmp) {
      const row = document.createElement('div'); row.className = 'sm-row'; row.id = 'sm-row-original';
      row.innerHTML = rowHTML('original', 'Original', false, false);
      const rl = $('sm-row-ruler'); if (rl) rl.after(row); else rows.prepend(row);
      drawWave(row.querySelector('canvas'), g.buf, COLORS.original);
    }
    refresh();
  }
  function unload() {  // the file was removed: back to the empty starting screen
    cuts = []; cutsFor = -1; lastSess = ''; vs = 0; vd = 0; waveReg = []; selT = null; clearHistory(); halt(); token++; stems = []; st = {}; ghost = false; cmp = false; dur = 0; off = 0; hasVideo = false;
    const rows = $('sm-rows'); if (rows) rows.innerHTML = '';
    const root = $('sm-root'); if (root) root.classList.add('empty');
    vid().style.display = 'none'; vid().removeAttribute('src');
    const ph = $('sm-vph'); if (ph) { ph.textContent = 'Video preview appears here'; ph.style.display = ''; }
    ['sm-playbtn', 'sm-startbtn', 'sm-splitbtn', 'sm-backbtn'].forEach((i) => { const e = $(i); if (e) e.disabled = true; });
    ['sm-vlbl', 'sm-nv', 'sm-splitbtn'].forEach((i) => { const e = $(i); if (e) e.style.display = ''; });
    { const e = $('sm-backbtn'); if (e) e.style.display = 'none'; }
    const cb = $('sm-cmpbtn'); if (cb) { cb.style.display = 'none'; cb.classList.remove('on'); }
    paintTime(); drawMeter();
  }

  let loading = false;
  async function load(json) {
    halt(); off = (keepOff !== null ? keepOff : 0); keepOff = null; label(); loading = true;
    let m; try { m = JSON.parse(json); } catch (e) { editDone(); return; }
    if (!m || !m.stems) { editDone(); return; }
    const my = ++token, root = $('sm-root'), rows = $('sm-rows');
    hasVideo = !!m.video_url;
    root.classList.remove('empty'); root.classList.toggle('novideo', !hasVideo);
    if (hasVideo) {
      $('sm-vph').style.display = 'none'; vid().style.display = 'block';
      if (vid().getAttribute('src') !== m.video_url) {   // same video after an edit: keep it, so the picture stays where the playhead is
        vid().src = m.video_url; vid().load();
        vid().addEventListener('loadedmetadata', () => { try { vid().currentTime = off; } catch (x) {} }, {once: true});
      } else { try { vid().currentTime = off; } catch (x) {} }
    } else {
      vid().style.display = 'none'; vid().removeAttribute('src');
      const ph = $('sm-vph'); ph.textContent = 'No video'; ph.style.display = '';
    }
    stems = [];   // the old rows stay on screen until the new ones are ready, so an edit does not flash
    try {
      const c = ac();
      const bufs = await Promise.all(m.stems.map(async (s) => {
        const r = await fetch(s.url); if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + s.key);
        return c.decodeAudioData(await r.arrayBuffer());
      }));
      if (my !== token) return;
      rows.innerHTML = '';
      stems = m.stems.map((s, i) => {
        const gain = c.createGain(); gain.connect(master); const pre = c.createGain(); pre.connect(gain);
        return Object.assign({}, s, {buf: bufs[i], gain: gain, pre: pre});
      });
      // Same result reloaded (after a voice split or an edit): keep each track's mute, solo and volume.
      // New voice tracks start out like the Voices track they came from.
      const sk = ((m.stems[0] && m.stems[0].url) || '').split('?')[0].replace(/[^\/]*$/, '');
      const same = !!(sk && sk === lastSess), keep = same ? st : {}; lastSess = sk;
      if (!same) { clearHistory(); selT = null; }
      st = {}; stems.forEach((s) => {
        const o = keep[s.key] || (/^voices\d$/.test(s.key) ? keep.voices : null);
        st[s.key] = o ? {mute: !!o.mute, solo: !!o.solo, vol: o.vol} : {mute: !!s.silent, solo: false, vol: 1};
      });
      ghost = stems.some((s) => s.ghost); cmp = false;
      const kar = stems.some((s) => s.key === 'lead'), hasB = stems.some((s) => s.key === 'backing');   // a karaoke result: no voice tools, but a backing-singer split
      ['sm-vlbl', 'sm-nv', 'sm-splitbtn'].forEach((i) => { const e = $(i); if (e) e.style.display = kar ? 'none' : ''; });
      { const e = $('sm-backbtn'); if (e) e.style.display = (kar && !hasB) ? '' : 'none'; }
      const cb = $('sm-cmpbtn'); if (cb) { cb.style.display = ghost ? '' : 'none'; cb.classList.remove('on'); }
      fxs = m.fx || {}; fxStash = {}; closePop();
      dur = Math.max.apply(null, stems.map((s) => s.buf.duration));
      if (!same || !vd) { vs = 0; vd = dur; }   // a new file starts fully zoomed out; a reload after an edit keeps your zoom
      clampView();
      stems.forEach((s) => {
        if (s.ghost) return;
        const row = document.createElement('div'); row.className = 'sm-row'; row.id = 'sm-row-' + s.key;
        row.innerHTML = rowHTML(s.key, s.label, s.silent, false); rows.appendChild(row);
        drawWave(row.querySelector('canvas'), s.buf, COLORS[s.key]); fxSync(s.key);
      });
      if (cutsFor !== m.duration) { cuts = []; cutsFor = m.duration; }
      if ((m.nvoices || 0) !== nv) clearHistory();   // a new voice split ends the undo history: its steps would not fit any more
      const selIdx = selT === null ? -1 : segs.findIndex((x, k) => k > 0 && Math.abs(x[0] - selT) < 0.02);
      if (!(nudgeDirty && same && segs.length)) {   // if you nudged again while this save ran, your newer positions win
        segs = (m.segments || []).map((x) => [x[0], x[1], x[2], 0]); nv = m.nvoices || 0;
        if (selIdx > 0 && segs[selIdx] && !segs.some((x, k) => k > 0 && Math.abs(x[0] - selT) < 0.02)) selT = segs[selIdx][0];
      }
      segs.forEach((x, i) => { if (i > 0 && segs[i - 1][2] === x[2] && cuts.some((c) => Math.abs(c - x[0]) < 0.03)) x[3] = 1; });
      if (segs.length && nv > 1) {
        const w = document.createElement('div'); w.className = 'sm-row'; w.id = 'sm-row-who';
        w.innerHTML = '<div><div class="sm-name">Who is talking <span class="sm-info" tabindex="0">i<span class="sm-tip">Right-click a part to change it or cut it.<br>Drag a line to move it.<br>Click a line, then use the arrow keys to nudge it (Shift = bigger steps). Esc lets go.</span></span></div></div><div class="sm-who" id="sm-who"></div>';
        rows.prepend(w); renderWho();
      }
      addRuler(rows);
      $('sm-playbtn').disabled = false; $('sm-startbtn').disabled = false; $('sm-splitbtn').disabled = !!m.preview; { const e = $('sm-backbtn'); if (e) e.disabled = !!m.preview; }
      refresh(); paintTime(); meterLoop();
    } catch (e) {
      rows.innerHTML = '<div style="padding:8px;color:#f87171">Could not load stems: ' + e.message + '</div>';
    }
    loading = false; editDone();
  }

  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-sm]'); if (!t) return;
    const a = t.dataset.sm, k = t.dataset.k;
    if (a === 'play') toggle();
    else if (a === 'start') seek(0);
    else if (a === 'mute') { pushUndo('mute ' + labelOf(k)); st[k].mute = !st[k].mute; refresh(); }
    else if (a === 'solo') { pushUndo('solo ' + labelOf(k)); st[k].solo = !st[k].solo; refresh(); }
    else if (a === 'undo') undo();
    else if (a === 'redo') redo();
    else if (a === 'zin') { lastUV = performance.now(); zoomAt(2, anchorT()); }
    else if (a === 'zout') { lastUV = performance.now(); zoomAt(0.5, anchorT()); }
    else if (a === 'zfit') { lastUV = performance.now(); setView(0, dur); }
    else if (a === 'cmp') setCompare(!cmp);
    else if (a === 'split') { const b = $('split-btn'); if (b) b.click(); }
    else if (a === 'back') { const b = $('back-btn'); if (b) b.click(); }
    else if (a === 'fx') openPop('fx', k, t);
    else if (a === 'preset') applyPreset(t.dataset.p);
    else if (a === 'wave') seek(Tx(e, t));
  });
  document.addEventListener('dblclick', (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.sm === 'vol' && !t.disabled) { pushUndo('volume ' + labelOf(t.dataset.k)); t.value = 1; st[t.dataset.k].vol = 1; refresh(); e.preventDefault(); }
  });
  document.addEventListener('input', (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.sm === 'vol') {
      let v = parseFloat(t.value); if (Math.abs(v - 1) < 0.025) { v = 1; t.value = 1; }  // easy to land exactly on 0 dB
      pushUndo('volume ' + labelOf(t.dataset.k), 'vol:' + t.dataset.k); st[t.dataset.k].vol = v; refresh();
    }
    else if (t.dataset && t.dataset.fx === 'param') {
      const k = t.dataset.k, n = t.dataset.n; pushUndo('effects on ' + labelOf(k), 'fx:' + k);
      fxs[k] = Object.assign({}, fxGet(k), {[n]: fromPos(n, parseFloat(t.value))});
      t.nextElementSibling.textContent = fxFmt(n, fxs[k][n]);
      fxChanged(k);
    }
  });
  document.addEventListener('change', (e) => {
    const t = e.target; if (!t.dataset) return;
    if (t.dataset.fx === 'mod') { pushUndo('effects on ' + labelOf(t.dataset.k)); modToggle(t.dataset.k, t.dataset.m, t.checked); }
    else if (t.dataset.fx === 'preset') { const p = FXC.presets[t.value]; if (p) { pushUndo('effects on ' + labelOf(t.dataset.k)); fxs[t.dataset.k] = Object.assign({}, p); fxChanged(t.dataset.k); } }
  });
  document.addEventListener('click', (e) => {
    const r = e.target.closest('[data-fx="reset"]');
    if (r) { pushUndo('effects reset on ' + labelOf(r.dataset.k)); fxs[r.dataset.k] = {}; fxStash[r.dataset.k] = {}; fxChanged(r.dataset.k); return; }
    const t = e.target.closest('[data-pp]'); if (!t) return;
    if (t.dataset.pp === 'close') closePop();
  });
  // the panel closes when you click anywhere else, and on Escape
  document.addEventListener('mousedown', (e) => {
    if (!popFor) return;
    if (e.target.closest('#sm-pop') || e.target.closest('[data-sm="fx"]')) return;
    closePop();
  }, true);
  window.addEventListener('resize', closePop);

  // Space = play / pause, and nothing else (even when a button or checkbox has focus).
  function typing(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'textarea' || el.isContentEditable ||
      (tag === 'input' && !['checkbox', 'radio', 'button', 'range', 'file'].includes(el.type));
  }
  // ---- History list: click, Ctrl/Cmd+click and Shift+click to pick, Delete key to delete the picked ones
  let hAnchor = null;
  const hRows = () => Array.from(document.querySelectorAll('#hist-list .hrow'));
  function hSel() { return hRows().filter((r) => r.classList.contains('sel')).map((r) => r.dataset.sid); }
  document.addEventListener('click', (e) => {
    const r = e.target.closest && e.target.closest('#hist-list .hrow'); if (!r) return;
    const rows = hRows(), i = rows.indexOf(r), a = rows.findIndex((x) => x.dataset.sid === hAnchor);
    if (e.shiftKey && a >= 0) {
      if (!(e.ctrlKey || e.metaKey)) rows.forEach((x) => x.classList.remove('sel'));
      for (let k = Math.min(a, i); k <= Math.max(a, i); k++) rows[k].classList.add('sel');
    } else if (e.ctrlKey || e.metaKey) { r.classList.toggle('sel'); hAnchor = r.dataset.sid; }
    else { rows.forEach((x) => x.classList.remove('sel')); r.classList.add('sel'); hAnchor = r.dataset.sid; }
  });
  document.addEventListener('dblclick', (e) => {
    const r = e.target.closest && e.target.closest('#hist-list .hrow'); if (!r) return;
    hRows().forEach((x) => x.classList.toggle('sel', x === r)); const b = $('hist-open'); if (b) b.click();
  });
  document.addEventListener('keydown', (e) => {
    const m = $('history-modal'); if (!m || !m.classList.contains('open') || typing(document.activeElement)) return;
    if (e.code === 'Delete') { e.preventDefault(); const b = $('hist-del'); if (b) b.click(); }
    else if ((e.ctrlKey || e.metaKey) && e.code === 'KeyA') { e.preventDefault(); hRows().forEach((x) => x.classList.add('sel')); }
  });
  document.addEventListener('keydown', (e) => {
    if (e.code === 'Escape') {
      closePop(); closeMenu(); document.querySelectorAll('#settings-modal,#history-modal').forEach((m) => m.classList.remove('open'));
      if (selT !== null) { selT = null; renderWho(); }
      return;
    }
    const mod = e.ctrlKey || e.metaKey, ae = document.activeElement;
    if (mod && !e.altKey && !typing(ae)) {   // Undo / Redo (not while typing in a text box: that keeps its own undo)
      const kk = (e.key || '').toLowerCase();
      if (kk === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (kk === 'y') { e.preventDefault(); redo(); return; }
    }
    if ((e.code === 'ArrowLeft' || e.code === 'ArrowRight') && selT !== null && !mod && !e.altKey && !typing(ae) &&
        !(ae && ae.tagName && (ae.tagName.toLowerCase() === 'select' || (ae.tagName.toLowerCase() === 'input' && ae.type === 'range')))) {
      e.preventDefault(); nudge(e.code === 'ArrowLeft' ? -1 : 1, e.shiftKey); return;
    }
    if (e.code === 'Home' && !typing(document.activeElement)) { e.preventDefault(); seek(0); return; }
    if (e.code !== 'Space' || typing(document.activeElement)) return;
    e.preventDefault(); if (!e.repeat) toggle();
  }, true);
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && !typing(document.activeElement)) e.preventDefault();
  }, true);

  // Settings pop-up: open/close, and close when clicking the dim backdrop.
  document.addEventListener('click', (e) => {
    if (e.target && (e.target.id === 'settings-modal' || e.target.id === 'history-modal')) e.target.classList.remove('open');
  });

  setInterval(placeholder, 400);
  window.somaMixer = {
    load: load, reset: reset, toggle: toggle, unload: unload,
    gains: () => JSON.stringify(Object.fromEntries(Object.keys(st).filter((k) => k !== 'original').map((k) => [k, eff(k, true)]))),
    ready: () => stems.length > 0,
    segments: () => JSON.stringify({n: nv, segments: segs.map((x) => [x[0], x[1], x[2]])}),
    video: () => (hasVideo ? vid() : null),
    undo: undo, redo: redo, view: () => JSON.stringify({vs: vs, vd: vd, dur: dur}), selected: () => selT,
    histSel: hSel,
    history: () => JSON.stringify({undo: undoS.map((x) => x.label), redo: redoS.map((x) => x.label)}),
    fxJson: fxJson,
  };
})();
"""
