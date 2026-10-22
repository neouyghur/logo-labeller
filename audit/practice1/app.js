/* Labelling sheets for the 2026-10 gallery quality audit (quality_audit/PLAN.md).
 *
 * One static page per labeller and task, opened from disk (file://). window.CFG says
 * which task and labeller; window.DATA (data_<labeller>.js) lists the items in that
 * labeller's own shuffled order. Labels autosave to localStorage when the browser
 * allows it, and always export to / import from TSV.
 *
 * Modes (CFG.task):
 *   task1    non-logo: each tile is logo (default) / non-logo (+reason) / unsure, tags flag, generic
 *   task2    duplicates: an image and its candidates; click candidates that show the same logo
 *   company  company correctness: correct (default) / wrong / can't tell
 *   adj1, adj2   adjudication of task1 / task2: load both labellers' TSVs, settle disagreements
 */
(function () {
  'use strict';
  const CFG = window.CFG, DATA = window.DATA;
  const MODE = CFG.task, ADJ = MODE === 'adj1' || MODE === 'adj2';
  const BASE = ADJ ? (MODE === 'adj1' ? 'task1' : 'task2') : MODE;
  const PER = BASE === 'task2' ? 10 : 100;
  const SKEY = `qa202610:${MODE}:${CFG.labeller}:${DATA.version}`;
  const REASONS = { context: 'C', multi: 'M', blank: 'B', other: 'O' };
  const REASON_TIP = { context: 'logo in context: mockup, sign, vehicle, photo', multi: 'several logos',
                       blank: 'blank or corrupt', other: 'other non-logo' };
  const $ = (s, el = document) => el.querySelector(s);
  const tileSrc = id => `${CFG.tiles}${id.slice(0, 2)}/${id}.png`;

  let st = { labels: {}, confirmed: {} }, storageOK = true, page = 0, hoverId = null;
  let adjA = null, adjB = null, items = DATA.items;

  // ---------- storage ----------
  function load() {
    try {
      const s = window.localStorage.getItem(SKEY);
      if (s) st = JSON.parse(s);
    } catch (e) { storageOK = false; }
  }
  function save() {
    if (!storageOK) return;
    try { window.localStorage.setItem(SKEY, JSON.stringify(st)); }
    catch (e) { storageOK = false; showWarn(); }
  }
  function showWarn() {
    if (!storageOK) {
      const w = $('#warn');
      w.style.display = 'block';
      w.textContent = 'Browser storage is not available for this page, so labels are NOT saved automatically. ' +
        'Export TSV before closing the page, and import it to continue later.';
    }
  }

  // ---------- label model ----------
  function rec(id) { return st.labels[id] || null; }
  function t1(id) { const r = rec(id) || {}; return { l: r.l || 'logo', r: r.r || '', f: r.f || 0, g: r.g || 0 }; }
  function touch(id) { if (ADJ) { st.touched = st.touched || {}; st.touched[id] = 1; } }
  function setT1(id, v) {
    touch(id);
    const r = Object.assign(t1(id), v);
    if (r.l !== 'nonlogo') r.r = '';
    if (r.l === 'logo' && !r.f && !r.g) delete st.labels[id]; else st.labels[id] = r;
    save(); refresh(id);
  }
  function t2(id) { const r = rec(id) || {}; return { sel: r.sel || [], u: r.u || 0 }; }
  function t2label(r) { return r.u ? 'unsure' : (r.sel.length ? 'yes' : 'no'); }
  function setT2(id, v) {
    touch(id);
    const r = Object.assign(t2(id), v);
    if (!r.sel.length && !r.u) delete st.labels[id]; else st.labels[id] = r;
    save(); refresh(id);
  }
  function co(id) { const r = rec(id) || {}; return { c: r.c || 'correct' }; }
  function setCo(id, c) { if (c === 'correct') delete st.labels[id]; else st.labels[id] = { c }; save(); refresh(id); }

  // ---------- rendering ----------
  function pages() { return Math.max(1, Math.ceil(items.length / PER)); }
  function render() {
    const g = $('#grid');
    g.innerHTML = '';
    g.className = BASE === 'task2' ? 'items' : '';
    const slice = items.slice(page * PER, (page + 1) * PER);
    slice.forEach((it, i) => g.appendChild(BASE === 'task2' ? itemEl(it, page * PER + i) : tileEl(it, page * PER + i)));
    if (!slice.length) g.textContent = ADJ ? 'No disagreements to settle on this page.' : 'Nothing to label.';
    $('#pageinfo').textContent = `Page ${page + 1} / ${pages()}`;
    $('#prev').disabled = page === 0; $('#next').disabled = page >= pages() - 1;
    const done = $('#done');
    done.textContent = st.confirmed[page] ? 'Page done ✓' : 'Mark page done';
    done.className = st.confirmed[page] ? '' : 'primary';
    progress();
    window.scrollTo(0, 0);
  }
  function progress() {
    const n = Object.keys(st.confirmed).filter(k => st.confirmed[k]).length;
    $('#bar > div').style.width = `${100 * n / pages()}%`;
    $('#progress').textContent = `${n} of ${pages()} pages done`;
  }
  function chip(txt, on, cls, tip, fn) {
    const c = document.createElement('span');
    c.className = 'chip' + (on ? ' on ' + cls : '');
    c.textContent = txt; c.title = tip;
    c.addEventListener('click', e => { e.stopPropagation(); fn(); });
    return c;
  }
  function whoLine(id) {
    const d = document.createElement('div');
    d.className = 'who';
    const a = adjA && adjA[id], b = adjB && adjB[id];
    const fmt = r => !r ? '—' : BASE === 'task1' ? (r.label + (r.reason ? '/' + r.reason : '') +
      (r.flag === '1' ? ' +flag' : '') + (r.generic === '1' ? ' +generic' : '')) : r.dup;
    d.innerHTML = 'A: <b></b> · B: <b></b>';
    d.querySelectorAll('b')[0].textContent = fmt(a);
    d.querySelectorAll('b')[1].textContent = fmt(b);
    return d;
  }
  function tileEl(it, n) {
    const d = document.createElement('div');
    d.className = 'tile'; d.dataset.id = it.id;
    const img = document.createElement('img');
    img.src = tileSrc(it.t); img.alt = ''; img.loading = 'lazy';
    img.addEventListener('mouseenter', () => { hoverId = it.id; zoom([it.t]); });
    img.addEventListener('click', () => {
      if (BASE === 'company') { const c = co(it.id).c; setCo(it.id, c === 'correct' ? 'wrong' : c === 'wrong' ? 'cant' : 'correct'); return; }
      const r = t1(it.id);
      setT1(it.id, { l: r.l === 'logo' ? 'nonlogo' : r.l === 'nonlogo' ? 'unsure' : 'logo', r: r.l === 'logo' ? 'other' : '' });
    });
    const num = document.createElement('span'); num.className = 'num'; num.textContent = n + 1;
    d.appendChild(img);
    const row = document.createElement('div'); row.className = 'row';
    d.appendChild(row);
    if (BASE === 'company') {
      const c = document.createElement('div'); c.className = 'company'; c.textContent = it.c; d.appendChild(c);
    }
    row.appendChild(num);
    if (ADJ) d.appendChild(whoLine(it.id));
    paintTile(d, it.id);
    return d;
  }
  function paintTile(d, id) {
    const row = $('.row', d), num = $('.num', d);
    row.innerHTML = ''; row.appendChild(num);
    if (BASE === 'company') {
      const c = co(id).c;
      d.className = 'tile' + (c === 'wrong' ? ' wrong' : c === 'cant' ? ' cant' : '');
      row.appendChild(chip('wrong', c === 'wrong', '', 'not this company\'s logo', () => setCo(id, c === 'wrong' ? 'correct' : 'wrong')));
      row.appendChild(chip("can't tell", c === 'cant', 'u', 'cannot tell', () => setCo(id, c === 'cant' ? 'correct' : 'cant')));
      return;
    }
    const r = t1(id);
    d.className = 'tile' + (r.l === 'nonlogo' ? ' nonlogo' : r.l === 'unsure' ? ' unsure' : '');
    for (const [k, ch] of Object.entries(REASONS)) {
      row.appendChild(chip(ch, r.l === 'nonlogo' && r.r === k, '', 'non-logo: ' + REASON_TIP[k],
        () => setT1(id, r.l === 'nonlogo' && r.r === k ? { l: 'logo' } : { l: 'nonlogo', r: k })));
    }
    row.appendChild(chip('?', r.l === 'unsure', 'u', 'unsure', () => setT1(id, { l: r.l === 'unsure' ? 'logo' : 'unsure' })));
    row.appendChild(chip('F', !!r.f, 't', 'flag', () => setT1(id, { f: r.f ? 0 : 1 })));
    row.appendChild(chip('G', !!r.g, 't', 'generic site/UI icon', () => setT1(id, { g: r.g ? 0 : 1 })));
  }
  function itemEl(it, n) {
    const d = document.createElement('div');
    d.className = 'item'; d.dataset.id = it.id;
    const q = document.createElement('div'); q.className = 'q';
    const qi = document.createElement('img'); qi.src = tileSrc(it.q); qi.alt = '';
    qi.addEventListener('mouseenter', () => zoom([it.q]));
    q.appendChild(qi);
    const row = document.createElement('div'); row.className = 'row'; q.appendChild(row);
    const stt = document.createElement('div'); stt.className = 'state'; q.appendChild(stt);
    if (ADJ) q.appendChild(whoLine(it.id));
    d.appendChild(q);
    const nb = document.createElement('div'); nb.className = 'nb';
    it.n.forEach(t => {
      const im = document.createElement('img'); im.src = tileSrc(t); im.alt = ''; im.loading = 'lazy'; im.dataset.t = t;
      im.addEventListener('mouseenter', () => zoom([it.q, t]));
      im.addEventListener('click', () => {
        const r = t2(it.id), s = new Set(r.sel);
        s.has(t) ? s.delete(t) : s.add(t);
        setT2(it.id, { sel: it.n.filter(x => s.has(x)) });
      });
      nb.appendChild(im);
    });
    d.appendChild(nb);
    paintItem(d, it, n);
    return d;
  }
  function paintItem(d, it, n) {
    const r = t2(it.id), lab = t2label(r);
    d.className = 'item' + (lab === 'yes' ? ' yes' : lab === 'unsure' ? ' unsure' : '');
    d.querySelectorAll('.nb img').forEach(im => im.classList.toggle('sel', r.sel.includes(im.dataset.t)));
    const row = $('.q .row', d); row.innerHTML = '';
    row.appendChild(chip('unsure', !!r.u, 'u', 'cannot decide', () => setT2(it.id, { u: r.u ? 0 : 1 })));
    row.appendChild(chip('clear', false, '', 'no duplicate', () => setT2(it.id, { sel: [], u: 0 })));
    $('.q .state', d).textContent = `#${n + 1} · ` + (lab === 'yes' ? `duplicate (${r.sel.length} marked)` :
      lab === 'unsure' ? 'unsure' : 'no duplicate');
  }
  function refresh(id) {
    const el = document.querySelector(`[data-id="${id}"]`);
    if (!el) return;
    if (BASE === 'task2') { const i = items.findIndex(x => x.id === id); paintItem(el, items[i], i); }
    else paintTile(el, id);
  }
  function zoom(ts) {
    const z = $('#zoom'), box = $('.imgs', z);
    box.innerHTML = '';
    ts.forEach(t => { const im = document.createElement('img'); im.src = tileSrc(t); im.alt = ''; box.appendChild(im); });
    z.className = ts.length > 1 ? 'pair' : '';
    $('.cap', z).textContent = ts.length > 1 ? 'left: the image · right: the candidate' : '';
  }

  // ---------- keyboard (task 1 / company, on the hovered tile) ----------
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'ArrowRight') { nav(1); return; }
    if (e.key === 'ArrowLeft') { nav(-1); return; }
    if (!hoverId || BASE === 'task2') return;
    const k = e.key.toLowerCase();
    if (BASE === 'company') {
      const m = { w: 'wrong', t: 'cant', k: 'correct', l: 'correct' };
      if (m[k]) setCo(hoverId, m[k]);
      return;
    }
    const m = { l: { l: 'logo' }, c: { l: 'nonlogo', r: 'context' }, m: { l: 'nonlogo', r: 'multi' },
                b: { l: 'nonlogo', r: 'blank' }, o: { l: 'nonlogo', r: 'other' }, n: { l: 'nonlogo', r: 'other' },
                u: { l: 'unsure' } };
    if (m[k]) setT1(hoverId, m[k]);
    else if (k === 'f') setT1(hoverId, { f: t1(hoverId).f ? 0 : 1 });
    else if (k === 'g') setT1(hoverId, { g: t1(hoverId).g ? 0 : 1 });
  });
  document.addEventListener('mouseover', e => { if (!e.target.closest('.tile')) hoverId = null; });
  function nav(dp) { const p = page + dp; if (p >= 0 && p < pages()) { page = p; render(); } }

  // ---------- TSV export / import ----------
  function rows() {
    const out = [];
    const conf = i => (st.confirmed[Math.floor(i / PER)] ? 1 : 0);
    DATA.items.forEach((it, i) => {
      const idx = items.indexOf(it), c = ADJ ? (idx >= 0 ? conf(idx) : 1) : conf(i);
      if (BASE === 'task1') {
        const r = t1(it.id);
        out.push([it.id, r.l, r.r, r.f, r.g, c]);
      } else if (BASE === 'task2') {
        const r = t2(it.id);
        out.push([it.id, t2label(r), r.sel.join(','), r.u, c]);
      } else {
        out.push([it.id, co(it.id).c, c]);
      }
    });
    return out;
  }
  const HEAD = { task1: ['item_id', 'label', 'reason', 'flag', 'generic', 'page_confirmed'],
                 task2: ['item_id', 'dup', 'dup_ids', 'unsure', 'page_confirmed'],
                 company: ['item_id', 'company_label', 'page_confirmed'] };
  function exportTSV() {
    const who = ADJ ? 'final' : CFG.labeller;
    const lines = [[...HEAD[BASE], 'labeller', 'adjudicated'].join('\t')];
    rows().forEach(r => lines.push([...r, who, ADJ ? (items.some(x => x.id === r[0]) ? 1 : 0) : 0].join('\t')));
    const txt = lines.join('\n') + '\n';
    window.__lastExport = txt;
    const ts = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([txt], { type: 'text/tab-separated-values' }));
    a.download = `labels_${MODE}_${who}_${ts}.tsv`;
    document.body.appendChild(a); a.click(); a.remove();
  }
  function parseTSV(txt) {
    const lines = txt.split(/\r?\n/).filter(l => l.length);
    const h = lines.shift().split('\t');
    return lines.map(l => { const v = l.split('\t'), o = {}; h.forEach((k, i) => o[k] = v[i] === undefined ? '' : v[i]); return o; });
  }
  function importRows(rs, into) {
    const known = new Set(DATA.items.map(x => x.id));
    let n = 0;
    rs.forEach(r => {
      if (!known.has(r.item_id)) return;
      n++;
      if (BASE === 'task1') {
        const v = { l: r.label || 'logo', r: r.reason || '', f: +r.flag || 0, g: +r.generic || 0 };
        if (v.l === 'logo' && !v.f && !v.g) delete into[r.item_id]; else into[r.item_id] = v;
      } else if (BASE === 'task2') {
        const v = { sel: r.dup_ids ? r.dup_ids.split(',') : [], u: +r.unsure || 0 };
        if (!v.sel.length && !v.u) delete into[r.item_id]; else into[r.item_id] = v;
      } else {
        if (r.company_label && r.company_label !== 'correct') into[r.item_id] = { c: r.company_label }; else delete into[r.item_id];
      }
    });
    return n;
  }
  function importTSV(file) {
    file.text().then(txt => {
      const rs = parseTSV(txt);
      const n = importRows(rs, st.labels);
      if (!ADJ) {
        rs.forEach(r => {
          const i = DATA.items.findIndex(x => x.id === r.item_id);
          if (i >= 0 && r.page_confirmed === '1') st.confirmed[Math.floor(i / PER)] = true;
        });
      }
      save(); render();
      alert(`Imported ${n} rows.`);
    });
  }

  // ---------- adjudication ----------
  function disagree(id) {
    const a = adjA[id], b = adjB[id];
    if (!a || !b) return true;
    if (BASE === 'task1') return a.label !== b.label || a.label === 'unsure' || (a.label === 'nonlogo' && a.reason !== b.reason);
    return a.dup !== b.dup || a.dup === 'unsure';
  }
  function sigOf(m) {
    let h = 0;
    const s = Object.keys(m).sort().map(k => k + JSON.stringify(m[k])).join('|');
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return String(h);
  }
  function startAdj() {
    const ids = DATA.items.map(x => x.id);
    // decisions already made on this page survive a reload, as long as the same two TSVs are loaded
    const sig = sigOf(adjA) + ':' + sigOf(adjB);
    if (st.sig !== sig) { st.sig = sig; st.touched = {}; st.confirmed = {}; }
    const keep = {};
    Object.keys(st.touched || {}).forEach(id => { keep[id] = st.labels[id]; });
    // every other item starts from the agreed labels; tags and marked candidates are the union of both
    st.labels = {};
    ids.forEach(id => {
      const a = adjA[id], b = adjB[id];
      if (!a || !b) return;
      if (BASE === 'task1') {
        const l = a.label === b.label ? a.label : 'unsure';
        const v = { l, r: l === 'nonlogo' ? (a.reason === b.reason ? a.reason : 'other') : '',
                    f: (+a.flag || +b.flag) ? 1 : 0, g: (+a.generic || +b.generic) ? 1 : 0 };
        if (!(v.l === 'logo' && !v.f && !v.g)) st.labels[id] = v;
      } else {
        const s = new Set([...(a.dup_ids ? a.dup_ids.split(',') : []), ...(b.dup_ids ? b.dup_ids.split(',') : [])]);
        const v = { sel: [...s], u: a.dup === b.dup ? 0 : 1 };
        if (a.dup === b.dup && a.dup === 'no') v.sel = [];
        if (v.sel.length || v.u) st.labels[id] = v;
      }
    });
    Object.keys(keep).forEach(id => { if (keep[id] === undefined) delete st.labels[id]; else st.labels[id] = keep[id]; });
    items = DATA.items.filter(x => disagree(x.id));
    page = 0;
    $('#adjinfo').textContent = `${items.length} of ${ids.length} items need a decision ` +
      '(different labels, or unsure). Agreed items are exported as agreed.';
    save(); render();
  }
  function loadAdj(which, file) {
    file.text().then(txt => {
      const m = {};
      parseTSV(txt).forEach(r => { m[r.item_id] = r; });
      if (which === 'A') adjA = m; else adjB = m;
      $('#adj' + which).textContent = `${which}: ${Object.keys(m).length} rows`;
      if (adjA && adjB) startAdj();
    });
  }

  // ---------- page shell ----------
  function init() {
    document.title = CFG.title;
    $('h1').textContent = CFG.title + (ADJ ? '' : ` — labeller ${CFG.labeller}`);
    load(); showWarn();
    $('#prev').addEventListener('click', () => nav(-1));
    $('#next').addEventListener('click', () => nav(1));
    $('#prev2').addEventListener('click', () => nav(-1));
    $('#next2').addEventListener('click', () => nav(1));
    $('#done').addEventListener('click', () => { st.confirmed[page] = !st.confirmed[page]; save(); render(); });
    $('#export').addEventListener('click', exportTSV);
    $('#importfile').addEventListener('change', e => { if (e.target.files[0]) importTSV(e.target.files[0]); e.target.value = ''; });
    $('#helpbtn').addEventListener('click', () => { $('#help').style.display = 'block'; });
    $('#help').addEventListener('click', () => { $('#help').style.display = 'none'; });
    $('#helptext').innerHTML = CFG.help;
    if (ADJ) {
      $('#adjbar').style.display = 'flex';
      $('#fileA').addEventListener('change', e => loadAdj('A', e.target.files[0]));
      $('#fileB').addEventListener('change', e => loadAdj('B', e.target.files[0]));
      items = [];
    }
    render();
  }
  window.__app = { st: () => st, items: () => items, setT1, setT2, setCo, exportRows: rows, exportTSV,
                   importRows, parseTSV, loadAdjText: (w, txt) => loadAdj(w, { text: () => Promise.resolve(txt) }),
                   nav, page: () => page };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
