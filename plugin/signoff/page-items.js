  var status = byId('status'), acceptButton = byId('accept-all'), doneButton = byId('done');
  status.textContent = L.notSaved;
  byId('accept-short').textContent = L.acceptShort;
  byId('accept-rest').textContent = L.acceptRest;
  acceptButton.querySelector('.undo').textContent = L.undo;
  byId('help-title').textContent = L.keys;
  byId('help-close').textContent = L.close;
  var editable = [];
  rounds.forEach(function (r, ri) { if (!r.readonly) editable.push(ri); });
  doneButton.textContent = editable.length > 1 ? fmt(L.doneRounds, {n: editable.length}) : L.done;
  // With more than one round to answer, each is accepted on its own and Done waits for all of them.
  var wizard = !historyMode && editable.length > 1;
  var accepted = rounds.map(function (r) { return !!r.accepted; });
  var rev = rounds.map(function (r) { return r.rev || 0; });  // saves of each round this page has seen
  var changed = rounds.map(function (r) { return !!r.changed; });

  // ---- The page in the shell: the rounds in the sidebar (the tabs, top to bottom) with the open round's items under
  // them, the round in main, and the key bar with the save state and the open round's Accept. The past decisions put
  // their list in the sidebar. ----
  var body = document.body, mainEl = document.querySelector('body > main');
  body.classList.add('run');
  if (historyMode) body.classList.add('hist');
  var sh = shell({label: historyMode ? L.historyTitle : L.rounds, focus: !historyMode,
    keys: historyMode ? [] : [[['J', 'K'], L.kItemShort], [['1', '–', '9'], L.kOptionShort], [['F'], L.kFocusShort]],
    setFocus: function (on) { setFocus(on); }, move: function (step) { return goItem(cur[active] + step); }});
  var accelKeys = shortcut[0] === '⌘' ? '⌘⏎' : 'Ctrl ⏎', acceptSlot = el('span', 'bar-acts');
  sh.bar.append(sh.keys, el('span', 'grow'), status, byId('done-reason'), acceptSlot);
  sh.focusButton.before(byId('progress'));
  mainEl.prepend(byId('notice'));
  byId('layout').before(sh.fbar);
  if (data.mono) {  // the project's monogram, as the inbox shows it
    var mono = el('span', 'mono lg c' + (Number(data.mono.color) % 6 || 0), data.mono.mono);
    mono.setAttribute('aria-hidden', 'true');
    document.querySelector('.head-text').before(mono);
  }
  var cur = rounds.map(function () { return 0; });  // each round's current item, for Focus and the sidebar

  // ---- Pictures, the agent's boxes, the user's marks, and boards ----
  var imageCount = 0;
  var markState = rounds.map(function () { return {}; });  // round -> item id -> [{image, box, comment}]
  var lineState = rounds.map(function () { return {}; });
  var selected = null;  // {ri, item, k}

  function boxRect(b, attrs) {
    return svg('rect', Object.assign({x: b[0] + '%', y: b[1] + '%', width: b[2] + '%', height: b[3] + '%'}, attrs));
  }
  function agentBoxes(boxes) {
    var id = 'cut' + (imageCount++);
    var o = svg('svg', {'class': 'overlay', 'aria-hidden': 'true', focusable: 'false'});
    var mask = svg('mask', {id: id});
    mask.append(svg('rect', {x: 0, y: 0, width: '100%', height: '100%', fill: 'white'}));
    var outlines = boxes.map(function (b) {
      mask.append(boxRect(b, {fill: 'black', rx: 6}));
      return boxRect(b, {'class': 'box', rx: 6});
    });
    var defs = svg('defs', {});
    defs.append(mask);
    o.append(defs, svg('rect', {'class': 'dim', width: '100%', height: '100%', mask: 'url(#' + id + ')'}));
    outlines.forEach(function (r) { o.append(r); });
    return o;
  }

  function board(img, item) {
    var fig = el('figure', 'board');
    var bar = el('div', 'board-bar');
    var state = {theme: pageTheme(), width: VIEWPORTS[0]};
    var box = el('div', 'board-box');
    var frame = el('iframe');
    // The board is the agent's own HTML with scripts: no same-origin, so it cannot reach this page or save.
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.title = img.caption || img.alt || item.title;
    frame.loading = 'lazy';
    box.append(frame);
    var HEIGHT = 820;
    function fit() {
      var avail = box.clientWidth || state.width;
      var scale = Math.min(1, avail / state.width);
      frame.style.width = state.width + 'px';
      frame.style.height = HEIGHT + 'px';
      frame.style.transform = 'scale(' + scale + ')';
      box.style.height = Math.round(HEIGHT * scale) + 'px';
    }
    function load() {
      frame.style.colorScheme = state.theme;
      frame.src = img.board + '?theme=' + state.theme;
    }
    function group(label, values, names, key) {
      var g = el('div', 'seg');
      g.setAttribute('role', 'group');
      g.setAttribute('aria-label', label);
      values.forEach(function (v, i) {
        var b = el('button', '', names[i]);
        b.type = 'button';
        b.setAttribute('aria-pressed', String(state[key] === v));
        b.addEventListener('click', function () {
          state[key] = v;
          Array.prototype.forEach.call(g.children, function (c, j) { c.setAttribute('aria-pressed', String(j === i)); });
          if (key === 'theme') load(); else fit();
        });
        g.append(b);
      });
      return g;
    }
    bar.append(el('span', 'board-label', L.board),
      group(L.theme, ['light', 'dark'], [L.light, L.dark], 'theme'),
      group(L.width, VIEWPORTS, VIEWPORTS.map(function (v) { return v + ' px'; }), 'width'));
    fig.append(bar, box);
    if (img.caption) fig.append(el('figcaption', '', img.caption));
    load();
    // Refit whenever the box changes width, including when its tab is first shown.
    if (window.ResizeObserver) new ResizeObserver(fit).observe(box); else window.addEventListener('resize', fit);
    fit();
    return fig;
  }

  function picture(ri, img, item, k, drawing) {
    var fig = el('figure');
    if (img.missing) {
      fig.append(el('div', 'missing', fmt(L.missing, {r: img.missing})));
      if (img.caption) fig.append(el('figcaption', '', img.caption));
      return fig;
    }
    // A picture that takes marks is not a link, so drawing on it does not open it.
    var frame = el(drawing ? 'div' : 'a', 'frame');
    if (!drawing) {
      frame.href = img.src;
      frame.target = '_blank';
      frame.rel = 'noopener noreferrer';
    }
    var pic = el('img');
    pic.src = img.src;
    pic.alt = img.alt || img.caption || item.title;
    pic.loading = 'lazy';
    pic.draggable = false;
    if (historyMode) {
      // This page sends nothing while read, so its server stops after the idle time: say so, not a silent gap. A
      // second request tells that from a picture the server could not read (an HTTP error resolves, a dead one not).
      pic.addEventListener('error', function () {
        fetch(pic.src).catch(function () {
          showHeader({title: L.historyTitle, intro: L.historyIntro, notice: data.inbox ? L.historyGoneInbox : L.historyGone});
        });
      });
    }
    frame.append(pic);
    if (img.boxes && img.boxes.length) frame.append(agentBoxes(img.boxes));
    if (item.marks) {
      var layer = svg('svg', {'class': 'marks' + (drawing ? ' drawing' : ''), 'aria-hidden': 'true', focusable: 'false'});
      layer.dataset.round = ri;
      layer.dataset.item = item.id;
      layer.dataset.image = k;
      frame.append(layer);
      if (drawing) drawOn(layer, ri, item, k);
    }
    fig.append(frame);
    if (img.caption) fig.append(el('figcaption', '', img.caption));
    if (drawing) {
      var tools = el('div', 'mark-tools');
      var full = el('a', 'link', L.full);
      full.href = img.src;
      full.target = '_blank';
      full.rel = 'noopener noreferrer';
      var add = el('button', 'link', fmt(L.addMark, {n: k + 1}));
      add.type = 'button';
      add.addEventListener('click', function () { addMark(ri, item, k, [40, 40, 20, 20]); });
      tools.append(add, full);
      // On a phone in Focus the picture starts small, so the options show; Larger gives it the width to draw on.
      // Not in the tools: Done disables their buttons, and this one must still work then.
      var size = el('button', 'link pic-size');
      size.type = 'button';
      var setSize = function (big) {  // the name says which picture: an item can have several
        size.textContent = big ? L.smaller : L.larger;
        size.setAttribute('aria-label', size.textContent + ': ' + fmt(L.picture, {n: k + 1}));
        size.setAttribute('aria-expanded', String(big));
      };
      setSize(false);
      size.addEventListener('click', function () { setSize(fig.classList.toggle('big')); });
      fig.classList.add('marking');
      fig.append(tools, size);
    }
    return fig;
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function round2(v) { return Math.round(v * 100) / 100; }
  function drawOn(layer, ri, item, k) {
    var start = null, temp = null;
    function at(e) {
      var r = layer.getBoundingClientRect();
      return [clamp((e.clientX - r.left) / r.width * 100, 0, 100), clamp((e.clientY - r.top) / r.height * 100, 0, 100)];
    }
    function box(a, b) {
      return [round2(Math.min(a[0], b[0])), round2(Math.min(a[1], b[1])), round2(Math.abs(a[0] - b[0])), round2(Math.abs(a[1] - b[1]))];
    }
    layer.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || finished || closed[ri]) return;
      e.preventDefault();
      layer.setPointerCapture(e.pointerId);
      start = at(e);
      temp = boxRect([start[0], start[1], 0, 0], {'class': 'mark new'});
      layer.append(temp);
    });
    layer.addEventListener('pointermove', function (e) {
      if (!start) return;
      var b = box(start, at(e));
      temp.setAttribute('x', b[0] + '%');
      temp.setAttribute('y', b[1] + '%');
      temp.setAttribute('width', b[2] + '%');
      temp.setAttribute('height', b[3] + '%');
    });
    function end(e) {
      if (!start) return;
      var b = box(start, at(e));
      start = null;
      temp.remove();
      if (b[2] >= 1 && b[3] >= 1) addMark(ri, item, k, b);  // a click is not a mark
    }
    layer.addEventListener('pointerup', end);
    layer.addEventListener('pointercancel', function () { if (start) { start = null; temp.remove(); } });
  }

  function marksOf(ri, item) { return markState[ri][item.id] || (markState[ri][item.id] = []); }
  function noteList(ri, item) {
    return (lineState[ri][item.id] || []).filter(function (x) { return x.comment; })
      .sort(function (a, b) { return a.att - b.att || a.line - b.line; });
  }
  function addMark(ri, item, k, b) {
    var list = marksOf(ri, item);
    if (list.length >= MAX_MARKS) return;
    list.push({image: k, box: b, comment: ''});
    selected = {ri: ri, id: item.id, n: list.length - 1};
    drawMarks(ri, item);
    listMarks(ri, item);
    var handle = byId(key(ri, item) + '-m' + (list.length - 1));
    if (handle) handle.focus();
    later(ri, 250);
  }
  function drawMarks(ri, item) {
    var layers = document.querySelectorAll('svg.marks[data-round="' + ri + '"][data-item="' + item.id + '"]');
    Array.prototype.forEach.call(layers, function (layer) {
      Array.prototype.slice.call(layer.querySelectorAll('.mark, .mark-num')).forEach(function (n) { n.remove(); });
      marksOf(ri, item).forEach(function (m, n) {
        if (String(m.image) !== layer.dataset.image) return;
        var on = selected && selected.ri === ri && selected.id === item.id && selected.n === n;
        layer.append(boxRect(m.box, {'class': 'mark' + (on ? ' selected' : ''), rx: 4}));
        var label = svg('text', {'class': 'mark-num', x: (m.box[0] + 0.8) + '%', y: (m.box[1] + 0.8) + '%', 'dominant-baseline': 'hanging'});
        label.textContent = String(n + 1);
        layer.append(label);
      });
    });
  }
  function listMarks(ri, item) {
    var holder = byId(key(ri, item) + '-marks');
    if (!holder) return;
    holder.replaceChildren();
    var list = marksOf(ri, item), ro = rounds[ri].readonly || closed[ri] || finished;
    if (!list.length) {
      holder.append(el('p', 'hint', ro ? L.noMarks : L.markHint));
      return;
    }
    list.forEach(function (m, n) {
      var row = el('div', 'mark-row');
      var handle = el('button', 'mark-handle', fmt(L.mark, {n: n + 1}) + ' ' + fmt(L.markOn, {p: m.image + 1}));
      handle.type = 'button';
      handle.id = key(ri, item) + '-m' + n;
      handle.setAttribute('aria-label', fmt(L.markHandle, {n: n + 1, p: m.image + 1}));
      handle.disabled = ro;
      handle.addEventListener('focus', function () { selected = {ri: ri, id: item.id, n: n}; drawMarks(ri, item); });
      handle.addEventListener('keydown', function (e) {
        var d = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
        if (!d || e.ctrlKey || e.metaKey || e.altKey) return;
        e.preventDefault();
        var b = m.box;
        if (e.shiftKey) {
          b[2] = clamp(round2(b[2] + d[0]), 1, 100 - b[0]);
          b[3] = clamp(round2(b[3] + d[1]), 1, 100 - b[1]);
        } else {
          b[0] = clamp(round2(b[0] + d[0]), 0, 100 - b[2]);
          b[1] = clamp(round2(b[1] + d[1]), 0, 100 - b[3]);
        }
        drawMarks(ri, item);
        later(ri, 400);
      });
      row.append(handle);
      if (ro) {
        row.append(el('p', 'mark-text', m.comment || '–'));
      } else {
        var ta = el('textarea', 'mark-comment');
        ta.value = m.comment;
        ta.maxLength = MAX_TEXT;
        ta.rows = 2;
        ta.dataset.round = ri;
        ta.setAttribute('aria-label', fmt(L.markComment, {n: n + 1}));
        ta.addEventListener('input', function () { m.comment = ta.value; });
        var rm = el('button', '', L.remove);
        rm.type = 'button';
        rm.setAttribute('aria-label', L.remove + ': ' + fmt(L.mark, {n: n + 1}));
        rm.addEventListener('click', function () {
          list.splice(n, 1);
          selected = null;
          drawMarks(ri, item);
          listMarks(ri, item);
          var next = byId(key(ri, item) + '-m' + Math.min(n, list.length - 1));
          if (next) next.focus();
          later(ri, 250);
        });
        row.append(ta, rm);
      }
      holder.append(row);
    });
  }

  function media(ri, img, item, k) {
    if (img.board) return board(img, item);
    return picture(ri, img, item, k, item.marks && !rounds[ri].readonly);
  }

  function labelOf(item, id) {
    if (typeof id === 'number') return numText(item, id);
    for (var i = 0; i < item.options.length; i++) if (item.options[i].id === id) return item.options[i].label;
    return id;
  }
  function numText(item, v) {  // no range: a past round's answer that its changed round no longer matches
    var unit = item.range && item.range.unit;
    return String(v) + (unit ? ' ' + unit : '');
  }
  function hasRec(item) { return item.recommended !== undefined; }
  function recList(item) { return !hasRec(item) ? [] : (Array.isArray(item.recommended) ? item.recommended : [item.recommended]); }
  // Element ids come from positions, never from agent ids, so no id can collide with another element's.
  function key(ri, item) { return 'r' + ri + '-i' + rounds[ri].decisions.items.indexOf(item); }
  function inputs(ri, item) { return Array.prototype.slice.call(document.getElementsByName(key(ri, item))); }
  // The item's choices as values: the ticked options, or a number item's value (none while it has no value).
  function picks(ri, item) {
    var p = inputs(ri, item).filter(function (i) { return i.checked; }).map(function (i) { return i.value; });
    var r = byId(key(ri, item) + '-n');
    return p.length || !r || r.dataset.unset ? p : [Number(r.value)];
  }

  // pos: the choice's place, 1 to 9 shown as its number key (the keys count Other and Skip too).
  function choiceRow(ri, item, suffix, value, text, detail, isRec, pos) {
    var row = el('div', 'opt');
    var input = el('input');
    input.type = item.select === 'one' ? 'radio' : 'checkbox';  // a number item's Skip must untick
    input.name = key(ri, item);
    input.value = value;
    input.id = key(ri, item) + suffix;
    input.dataset.round = ri;
    input.dataset.item = item.id;
    var label = el('label'), name = el('span', 'olab', text);
    label.htmlFor = input.id;
    if (pos <= 9) {
      var k = el('kbd', 'okey', String(pos));
      k.setAttribute('aria-hidden', 'true');  // the key list says it; a name stays the option's text
      label.append(k);
    }
    label.append(name);
    if (isRec) name.append(el('span', 'tag', L.recommended));
    if (detail) label.append(el('span', 'detail', detail));
    row.append(input, label);
    return row;
  }

  // A fact: the tone's sign, the text, and the tone's word for screen readers; colour is never the only signal.
  var GLYPHS = {good: '▲', mid: '●', bad: '▼'};
  function fact(f, holder) {
    var tone = Object.prototype.hasOwnProperty.call(GLYPHS, f.tone) ? f.tone : null;
    if (tone) {
      holder.classList.add(tone);
      var g = el('span', 'glyph', GLYPHS[tone]);
      g.setAttribute('aria-hidden', 'true');
      holder.append(g);
    }
    holder.append(document.createTextNode(f.text));
    if (tone) holder.append(el('span', 'visually-hidden', ' (' + L['tone_' + tone] + ')'));
    return holder;
  }
  function factOf(opt, c) {
    return opt.facts && Object.prototype.hasOwnProperty.call(opt.facts, c.id) ? opt.facts[c.id] : null;
  }
  function chips(item, opt) {
    if (!opt.facts || !Object.keys(opt.facts).length) return '';
    var box = el('span', 'facts');
    item.criteria.forEach(function (c) {
      var f = factOf(opt, c);
      if (!f) return;
      var chip = el('span', 'fact');
      chip.append(el('b', '', c.label));
      box.append(fact(f, chip));
    });
    return box;
  }
  // The table view: its inputs only stand in for the cards' inputs, which stay the one place a choice lives
  // (a different name, so collect() never sees them; the shortcuts pick on the cards and only move focus here).
  // mirrors[name] copies the cards' state in.
  var mirrors = {};
  function table(ri, item, rec) {
    var k = key(ri, item), wrap = el('div', 'cmp-wrap'), t = el('table', 'cmp');
    var head = el('tr'), body = el('tbody'), proxies = [];
    var corner = el('th');
    corner.scope = 'col';
    corner.append(el('span', 'visually-hidden', L.criterion));
    head.append(corner);
    item.options.forEach(function (opt, n) {
      var th = el('th'), label = el('label', 'pick'), input = el('input'), text = el('span', '', opt.label);
      th.scope = 'col';
      th.dataset.col = n;
      input.type = item.select === 'many' ? 'checkbox' : 'radio';
      input.name = k + '-x';
      input.id = k + '-x' + n;
      input.addEventListener('change', function () { byId(k + '-o' + n).click(); });
      if (rec.indexOf(opt.id) >= 0) text.append(el('span', 'tag', L.recommended));
      if (opt.detail) text.append(el('span', 'sub', opt.detail));
      label.append(input, text);
      th.append(label);
      head.append(th);
      proxies.push(input);
    });
    item.criteria.forEach(function (c) {
      var tr = el('tr'), th = el('th', '', c.label);
      th.scope = 'row';
      tr.append(th);
      item.options.forEach(function (opt, n) {
        var f = factOf(opt, c), td = el('td');
        td.dataset.col = n;
        td.append(f ? fact(f, el('span', 'tone')) : '–');
        tr.append(td);
      });
      body.append(tr);
    });
    var thead = el('thead');
    thead.append(head);
    t.append(thead, body);
    // A click anywhere in an option's column picks it, as a click anywhere on its card does.
    t.addEventListener('click', function (e) {
      var td = e.target.closest('td');
      if (td) proxies[Number(td.dataset.col)].click();
    });
    mirrors[k] = function () {
      proxies.forEach(function (p, n) {
        p.checked = byId(k + '-o' + n).checked;
        Array.prototype.forEach.call(t.querySelectorAll('[data-col="' + n + '"]'), function (c) { c.classList.toggle('picked', p.checked); });
      });
    };
    wrap.append(t);
    return wrap;
  }

  // An item the agent's next step waits on: a glyph for the eye and a word for everyone, never colour alone.
  function waitMark(cls, word, glyph) {
    var s = el('span', cls), g = el('span', 'wg', glyph || '■');
    g.setAttribute('aria-hidden', 'true');
    s.append(g, word);
    return s;
  }
  // High is the worst case, so it takes bad's colour with the up glyph (a fact's ▲ means better).
  var STAKES = {high: ['▲', 'bad'], medium: ['●', 'mid'], low: ['▼', 'good']};
  // A folded settled item shows the choice it holds now.
  function settledLine(ri, item) {
    var line = byId(key(ri, item) + '-sv');
    if (line) line.textContent = picks(ri, item).map(function (v) {
      return v === OTHER ? L.other : v === SKIP ? L.skip : labelOf(item, v);
    }).join(', ') || L.nothing;
  }
  // A number item: a slider and a field bound together, the unit beside them and the range's ends under the slider.
  // They have no name, so inputs() never takes them for options; picks() reads the slider, which the browser keeps
  // on the step and in range.
  function numberPick(ri, item) {
    var k = key(ri, item), g = item.range, box = el('div', 'num-pick'), row = el('div', 'num-row');
    var slide = el('div', 'num-slide'), range = el('input', 'num-range'), field = el('input', 'num-field');
    var ends = el('div', 'num-ends'), tag = el('span', 'tag', L.recommended);
    range.type = 'range';
    field.type = 'number';
    [range, field].forEach(function (i) {
      i.min = g.min;
      i.max = g.max;
      i.step = g.step;
      i.dataset.round = ri;
      i.dataset.num = item.id;
    });
    range.id = k + '-n';
    field.id = k + '-v';
    tag.id = k + '-rt';
    range.setAttribute('aria-label', item.title);
    field.setAttribute('aria-label', fmt(L.numValue, {t: item.title}));
    ends.setAttribute('aria-hidden', 'true');  // the slider's own min and max say it
    ends.append(el('span', '', numText(item, g.min)), el('span', '', numText(item, g.max)));
    slide.append(range, ends);
    row.append(slide, field);
    if (g.unit) row.append(el('span', 'num-unit', g.unit));
    row.append(tag);
    box.append(row);
    return box;
  }
  // v: the value to show on both inputs, or null for none (a done round with no answer).
  function setNumber(ri, item, v) {
    var k = key(ri, item), range = byId(k + '-n');
    if (v === null) range.dataset.unset = '1';
    else {
      delete range.dataset.unset;
      range.value = v;
    }
    byId(k + '-v').value = v === null ? '' : range.value;
    numShown(ri, item);
  }
  function numShown(ri, item) {
    var k = key(ri, item), range = byId(k + '-n'), unset = !!range.dataset.unset;
    range.setAttribute('aria-valuetext', unset ? L.nothing : numText(item, Number(range.value)));
    // Hidden, it keeps its place: the slider must not change width under a drag.
    byId(k + '-rt').classList.toggle('off', unset || Number(range.value) !== item.recommended);
  }
  function cut(s) { return Array.from(s).slice(0, MAX_QUOTE).join(''); }  // by code point, as the server
  // A lone CR, U+2028 and kin stay in their line by the rule: shown, so a row reads as one line.
  function vis(s) { return s.replace(/[\r\v\f\x85\u{2028}\u{2029}]/gu, '↵'); }
  // As the server's attachment_lines(): LF only.
  function attLines(t) {
    var a = t.split('\n');
    if (a[a.length - 1] === '') a.pop();
    return a.map(function (s) { return s.slice(-1) === '\r' ? s.slice(0, -1) : s; });
  }
  // A unified diff's rows [class, old, new, sign, text], or null; hunks after +++ only. Linear on any line.
  function diffRows(lines) {
    var out = [], head = 0, w = 0, hunks = 0, o = 0, n = 0, lo = 0, ln = 0;
    for (var i = 0; i < lines.length; i++) {
      var s = lines[i], c = s[0], t = s.slice(1), h = head && !lo && !ln && /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(s);
      if ((lo || ln) && c !== '\\') {
        w = 1;
        if (c === '-' && lo) out.push(['del', o++, '', c, t]), lo--;
        else if (c === '+' && ln) out.push(['add', '', n++, c, t]), ln--;
        else if ((c === ' ' || !s) && lo && ln) out.push(['ctx', o++, n++, ' ', t]), lo--, ln--;
        else return null;
      } else {
        if (w && /^[-+ ]/.test(s) && !/^(\+\+\+|---?) /.test(s)) return null;  // a hunk that counts short
        w = w && c === '\\';
        if (h) o = +h[1], lo = h[2] ? +h[2] : 1, n = +h[3], ln = h[4] ? +h[4] : 1, hunks++;
        else head |= s.startsWith('+++ ');
        out.push([h || c === '\\' ? 'hunk' : 'file', '', '', '', s]);
      }
    }
    return hunks && !lo && !ln ? out : null;
  }
  // Roles, not <table>: it lays out every row per chunk.
  function lineBox(lines, diff, pin) {
    var box = el('div', 'att-lines' + (diff ? ' diff' : '') + (pin ? ' pin' : '')), head = el('div'), i = 0;
    function role(e, r) { e.setAttribute('role', r); return e; }
    (pin && diff ? [L.lnNum] : []).concat(diff ? [L.lnOld, L.lnNew, L.lnSign, L.lnText] : [L.lnNum, L.lnText]).forEach(function (h) {
      head.append(role(el('span', 'visually-hidden', h), 'columnheader'));
    });
    role(box, 'table').append(role(head, 'row'));
    var rows = diff || lines.map(function (s, k) { return pin ? [0, s] : [0, k + 1, s]; });
    (function chunk() {
      var g = role(el('div'), 'rowgroup');
      for (var end = i + (i ? 2000 : 200); i < end && i < rows.length; i++) {
        var r = rows[i], tr = role(el('div', r[0]), 'row');
        if (pin) tr.append(role(el('span', 'n'), 'cell'));
        for (var k = 1; k < r.length; k++) {
          var td = role(el('span', k < 3 && k < r.length - 1 ? 'n' : '', k < r.length - 1 ? r[k] : vis(r[k])), 'cell');
          if (k === 3 && r[k].trim()) td.append(el('span', 'visually-hidden', r[k] === '+' ? L.lnAdd : L.lnDel));
          tr.append(td);
        }
        g.append(tr);
        if (pin) pin(tr, i);
      }
      g.style.containIntrinsicSize = 'auto ' + 1.5 * g.children.length + 'em';  // undrawn, as tall as its rows
      box.append(g);
      if (i < rows.length) setTimeout(function () { if (box.isConnected) chunk(); });
    })();
    return box;
  }
