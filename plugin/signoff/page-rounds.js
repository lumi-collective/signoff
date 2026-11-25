  // An item: the question, its text and pictures; the agent's recommendation and the cost in the margin (between the
  // question and the options when the window is narrow); then the options and the rest.
  function renderItem(ri, item, n) {
    var card = el('section', 'item');
    card.tabIndex = -1;  // j and k can land on a read-only item too
    card.dataset.item = item.id;
    var field = el('fieldset'), q = el('div', 'q'), fs = el('div', 'b');  // fs: everything under the margin
    var legend = el('legend');
    var h = el('h3');
    h.append(el('span', 'num', (n + 1) + '.'), document.createTextNode(item.title));
    if (item.blocking) h.append(' ', waitMark('badge warn', L.blocking));  // the space keeps the name two words apart
    legend.append(h);
    field.append(legend, q);
    if (item.settled) q.append(el('p', 'readonly-note settled', L.settledEarlier + ': ' + item.settled));
    if (item.settled && !rounds[ri].readonly) {  // folded until Change: a view, never saved
      var sw = el('span', 'sv', ' · '), sv = el('strong'), chg = el('button', '', L.change);
      sv.id = key(ri, item) + '-sv';
      fs.id = key(ri, item) + '-fold';
      chg.type = 'button';
      chg.setAttribute('aria-expanded', 'false');
      chg.setAttribute('aria-controls', fs.id);
      chg.addEventListener('click', function () {
        var open = !card.classList.toggle('folded');
        chg.setAttribute('aria-expanded', String(open));
        chg.textContent = open ? L.fold : L.change;
        settledLine(ri, item);
      });
      sw.append(sv);
      q.lastChild.append(sw, ' ', chg);
      card.classList.add('folded');
    }
    if (item.description) q.append(el('p', 'desc', item.description));
    if (item.commands && item.commands.length) {  // to run before deciding: the inbox's code block, Copy and all
      var cmds = el('div', 'cmds');
      item.commands.forEach(function (c) {
        if (c.caption) cmds.append(el('p', 'cmd-cap', c.caption));
        cmds.append(codeBlock(c, true));
      });
      q.append(cmds);
    }
    if (item.images && item.images.length) {
      var shots = el('div', 'shots');
      item.images.forEach(function (img, k) { shots.append(media(ri, img, item, k)); });
      q.append(shots);
    }
    var rec = recList(item);
    if (rec.length || item.cost || item.stakes || item.irreversible) {
      var side = el('div', 'margin');
      if (item.stakes) {  // a fact chip, so its colour pairs are the facts'
        var st = el('p', 'stakes');
        st.append(waitMark('fact ' + STAKES[item.stakes][1], L['stakes_' + item.stakes], STAKES[item.stakes][0]));
        side.append(st);
      }
      if (rec.length) {
        side.append(el('p', 'm-head', L.agentRecommends), el('p', 'rec', rec.map(function (r) { return labelOf(item, r); }).join(', ')));
        var why = rec.length === 1 && item.options && item.options.filter(function (o) { return o.id === rec[0]; })[0];
        if (why && why.detail) side.append(el('p', 'm-why', why.detail));
      }
      if (item.cost) {
        var cost = el('p', 'cost');
        cost.append(el('strong', '', L.cost), document.createTextNode(item.cost));
        side.append(cost);
      }
      if (item.irreversible) {
        var undo = el('p', 'cost');
        undo.append(el('strong', '', L.irreversible), document.createTextNode(item.irreversible));
        side.append(undo);
      }
      field.append(side);
      card.classList.add('has-margin');
    }
    field.append(fs);
    if (item.select === 'many') fs.append(el('p', 'hint-many', L.many));
    var compare = !!item.criteria, rows = [];
    if (compare) {
      // Cards or Table: a view, not an answer, so it is not saved. Other and Skip stay below in both.
      var bar = el('div', 'view-bar'), seg = el('div', 'seg'), view = el('div');
      seg.setAttribute('role', 'group');
      seg.setAttribute('aria-label', L.view);
      view.hidden = true;
      var wrap = table(ri, item, rec), hint = el('p', 'scroll-hint', L.scrollHint);
      // The hint shows only while the table is wider than its box: measured again on every resize and view switch.
      var fitHint = function () { hint.hidden = wrap.scrollWidth <= wrap.clientWidth; };
      if (window.ResizeObserver) new ResizeObserver(fitHint).observe(wrap); else window.addEventListener('resize', fitHint);
      [['cards', L.cards], ['table', L.table]].forEach(function (v) {
        var b = el('button', '', v[1]);
        b.type = 'button';
        b.dataset.view = v[0];
        b.setAttribute('aria-pressed', String(v[0] === 'cards'));
        b.addEventListener('click', function () {
          view.hidden = v[0] !== 'table';
          rows.forEach(function (r) { r.hidden = !view.hidden; });
          Array.prototype.forEach.call(seg.children, function (x) { x.setAttribute('aria-pressed', String(x === b)); });
          fitHint();
        });
        seg.append(b);
      });
      bar.append(el('span', 'board-label', L.view), seg);
      view.append(wrap, hint);
      fs.append(bar, view);
    }
    if (item.select === 'number') fs.append(numberPick(ri, item));
    var choices = el('div', 'choices');
    (item.options || []).forEach(function (opt, k) {
      var row = choiceRow(ri, item, '-o' + k, opt.id, opt.label, opt.detail, rec.indexOf(opt.id) >= 0, k + 1);
      if (compare) row.lastChild.append(chips(item, opt));
      rows.push(row);
      choices.append(row);
    });
    if (item.other) {
      var row = choiceRow(ri, item, '-other', OTHER, L.other, '', false, choices.children.length + 1);
      var text = el('input', 'other-text');
      text.type = 'text';
      text.id = key(ri, item) + '-t';
      text.maxLength = MAX_TEXT;
      text.setAttribute('aria-label', L.otherText);
      text.dataset.round = ri;
      text.dataset.item = item.id;
      text.dataset.pick = key(ri, item) + '-other';  // typing here picks Other
      row.append(text);
      choices.append(row);
    }
    // A number item's Skip gets no digit key: the digits pick numbered options, and it has none.
    if (item.skippable) choices.append(choiceRow(ri, item, '-skip', SKIP, L.skip, '', false,
      item.select === 'number' ? 10 : choices.children.length + 1));
    fs.append(choices);
    if (compare && item.options.some(function (o) { return o.facts && Object.keys(o.facts).some(function (c) { return o.facts[c].tone; }); })) {
      fs.append(el('p', 'legend-line', L.legend));
    }
    // Notes are data: rows draw late. A saved one off its line shows apart, read only, and goes back as it came.
    var atts = (item.attachments || []).map(function (att) { return attLines(att.text || ''); });
    var got = rounds[ri].answers && rounds[ri].answers.answers[item.id], ro = rounds[ri].readonly;
    var notes = lineState[ri][item.id] = ((got && got.lines) || []).map(function (x) {
      var ls = atts[x.att] || [];
      return Object.assign({stale: !(x.line <= ls.length && cut(ls[x.line - 1]) === x.quote)}, x);
    });
    var full = el('p', 'hint lfull');
    function find(ai, at) { return notes.filter(function (x) { return x.att === ai && x.line === at && !x.stale; })[0]; }
    function limit() {
      var cap = notes.length >= MAX_LINE_NOTES;
      full.textContent = cap ? L.lnFull : L.lnHint;
      card.querySelectorAll('[data-line]').forEach(function (b) {
        b.disabled = cap && !find(+b.dataset.att, +b.dataset.line);
      });
    }
    function shut() { return ro || closed[ri] || finished; }  // rows may be drawn after Done
    function noteRow(x, b) {
      var row = el('div', 'lnote'), cell = el('div', 'mark-row'), name = fmt(L.lnNote, {n: x.line, f: item.attachments[x.att].src}), off = shut();
      if (b) row.setAttribute('role', 'row'), cell.setAttribute('role', 'cell');
      if (x.stale) {  // the quote in its own element: a planted one cannot finish Signoff's sentence
        var was = el('p', 'readonly-note', fmt(L.lnStale, {n: x.line}));
        was.append(' ', el('code', '', vis(x.quote)));
        cell.append(was);
      }
      if (off || x.stale) {
        cell.append(el('p', 'mark-text', x.comment));
      } else {
        var ta = el('textarea');  // not kept on x: it would hold a dropped box
        ta.value = x.comment;
        ta.maxLength = MAX_TEXT;
        ta.dataset.round = ri;
        ta.setAttribute('aria-label', name);
        ta.addEventListener('input', function () { x.comment = ta.value; });
        ta.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.preventDefault(); b.focus(); } });
        cell.append(ta);
      }
      if (!off) {
        var rm = el('button', '', L.remove);
        rm.setAttribute('aria-label', L.remove + ': ' + name);
        rm.addEventListener('click', function () {
          notes.splice(notes.indexOf(x), 1);
          row.remove();
          (b || card).focus();
          limit();
          if (x.comment) later(ri, 250);  // an empty field was never saved
        });
        cell.append(rm);
      }
      row.append(cell);
      return row;
    }
    if (item.lines && !ro) fs.append(full), limit();
    // The agent's text files, never rendered: a diff or noted lines as rows while open and shown.
    (item.attachments || []).forEach(function (att, ai) {
      var box = el('details', 'attach'), sum = el('summary', '', att.caption || att.src);
      var lines = atts[ai], n = lines.length;
      var meta = [att.caption ? att.src : '', n ? (n === 1 ? L.line : fmt(L.lines, {n: n})) : ''];
      var diff = /\.(diff|patch)$/i.test(att.src);
      function view(e) {
        e.tabIndex = 0;  // it scrolls, so the keyboard must reach it
        e.setAttribute('aria-label', fmt(L.attachText, {r: att.caption || att.src}));
        box.append(e);
      }
      sum.append(el('span', 'attach-meta', meta.filter(Boolean).join(' · ')));
      box.append(sum);
      box.open = !!att.open || notes.some(function (x) { return x.att === ai && !x.stale; });
      function pin(tr, i) {  // the file's line, never the diff's
        var at = i + 1, off = shut(), b = el(off ? 'span' : 'button', '', String(at)), last = tr;
        if (!off) {
          b.dataset.att = ai;
          b.dataset.line = at;
          b.setAttribute('aria-label', fmt(L.lnNote, {n: at, f: att.src}));
          b.disabled = notes.length >= MAX_LINE_NOTES && !find(ai, at);
        }
        tr.firstChild.append(b);
        notes.forEach(function (x) {
          if (x.att !== ai || x.line !== at || x.stale) return;
          var r = noteRow(x, b);
          last.after(r);
          last = r;
        });
      }
      if (att.text === undefined) {
        box.append(el('p', 'attach-missing', fmt(L.attachMissing, {r: att.missing})));
      } else if (diff || item.lines) {
        // Setting open fires one toggle after render; a box in a hidden round waits for showRound's.
        box.addEventListener('toggle', function () {
          if (box.open && !box.children[1] && !box.closest('[hidden]')) view(lineBox(lines, diff && diffRows(lines), item.lines && pin));
        });
        box.addEventListener('click', function (e) {  // one listener: a box may hold 131k numbers
          var b = e.target.closest('[data-line]'), at = b && +b.dataset.line, x = b && find(ai, at), tr = b && b.closest('[role=row]');
          if (!b || shut()) return;
          if (!x) {
            notes.push(x = {att: ai, line: at, quote: cut(lines[at - 1]), comment: ''});
            tr.after(noteRow(x, b));
            limit();
          }
          tr.nextSibling.querySelector('textarea').focus();
        });
      } else {
        view(el('pre', '', att.text));
      }
      fs.append(box);
      notes.forEach(function (x) { if (x.att === ai && x.stale) fs.append(noteRow(x)); });
    });
    if (item.marks) {
      var marks = el('div', 'mark-list');
      marks.append(el('p', 'clabel', L.marks));
      var holder = el('div');
      holder.id = key(ri, item) + '-marks';
      marks.append(holder);
      fs.append(marks);
    }
    // Private covers the item's text: comment, Other text, mark and line notes. No data-item and no name, so it is
    // never taken for one of the choices.
    var k = key(ri, item), lockable = item.comment || item.other || item.marks || item.lines;
    // The agent marked the item decided elsewhere (signoff elsewhere): its note, filled in when the answers come in.
    var away = el('p', 'readonly-note');
    away.id = k + '-e';
    away.hidden = true;
    fs.prepend(away);
    if (lockable && item.sensitive && !rounds[ri].readonly) {  // the hint asks for an untick, so only where one can
      var sens = el('p', 'sens');
      sens.append(el('strong', '', L.sensTitle), document.createTextNode(L.sens));
      fs.append(sens);
    }
    if (lockable) {
      var crow = el('div', 'crow');
      if (item.comment) {
        var cl = el('label', 'clabel', L.comment);
        cl.htmlFor = k + '-c';
        crow.append(cl);
      }
      var lock = el('label', 'lock-label'), p = el('input');
      p.type = 'checkbox';
      p.id = k + '-p';
      p.dataset.round = ri;
      p.setAttribute('aria-describedby', k + '-pn');
      p.addEventListener('change', function () { showPrivate(ri, item); });
      lock.append(p, document.createTextNode(L.privateLabel));
      crow.append(lock);
      fs.append(crow);
    }
    if (item.comment) {
      var ta = el('textarea');
      ta.id = k + '-c';
      ta.maxLength = MAX_TEXT;
      ta.dataset.round = ri;
      fs.append(ta);
    }
    if (lockable) {
      var note = el('p', 'lock-note', L.lockNote);
      note.id = k + '-pn';
      note.hidden = true;
      fs.append(note);
    }
    card.append(field);
    return card;
  }
  function isPrivate(ri, item) {
    var p = byId(key(ri, item) + '-p');
    return !!p && p.checked;
  }
  function showPrivate(ri, item) {
    var on = isPrivate(ri, item), ta = byId(key(ri, item) + '-c'), note = byId(key(ri, item) + '-pn');
    if (ta) ta.classList.toggle('private', on);
    if (note) note.hidden = !on;
  }

  function when(stamp) {
    try { return new Date(stamp).toLocaleString(root.lang); } catch (e) { return stamp; }
  }

  var panels = byId('panels'), tabs = byId('tabs'), outlineHead = el('h2', '', L.outline), outline = el('ol', 'navs outline');
  panels.addEventListener('focusin', function (e) {  // the sidebar marks the item you are in
    var n = itemCards().indexOf(e.target.closest('.item[data-item]'));
    if (n >= 0 && n !== cur[active]) {
      cur[active] = n;
      place();
      markOutline();
    }
  });
  var sources = rounds.map(function () { return {}; });
  var kept = rounds.map(function () { return {}; });  // items marked decided elsewhere: their saved choice and note
  // The user's own answer wins over the mark: the item is theirs from then on, and the note goes.
  function unmark(ri, item) {
    if (!item || sources[ri][item.id] !== 'elsewhere') return;
    sources[ri][item.id] = 'user';
    byId(key(ri, item) + '-e').hidden = true;
  }
  function itemOf(ri, node) {
    var card = node && node.closest('.item[data-item]');
    return card && rounds[ri].decisions.items.filter(function (i) { return i.id === card.dataset.item; })[0];
  }
  var show = 'all';  // the summary's filter; kept here because the summary is rebuilt on most changes
  var blockSeen = null;  // the answers the single round's summary block was last built from
  function setChoice(ri, item, values) {
    inputs(ri, item).forEach(function (i) { i.checked = values.indexOf(i.value) >= 0; });
    if (mirrors[key(ri, item)]) mirrors[key(ri, item)]();
    // The restore and Accept all fire no change event.
    if (item.select !== 'number') return settledLine(ri, item);
    var n = values.filter(function (v) { return typeof v === 'number'; })[0];
    // Skipped: the slider still shows a value, the recommendation, for when Skip is unticked; read only, where
    // nothing can be unticked, no value at all.
    setNumber(ri, item, n !== undefined ? n : values.indexOf(SKIP) >= 0 && !rounds[ri].readonly ? item.recommended : null);
    settledLine(ri, item);
  }

  function renderPanel(ri) {
    if (byId('panel-' + ri)) return;
    var r = rounds[ri], d = r.decisions;
    var panel = el('section', 'rp');
    panel.id = 'panel-' + ri;
    panel.setAttribute('role', historyMode ? 'region' : 'tabpanel');
    panels.append(panel);
    if (historyMode) {
      panel.setAttribute('aria-label', r.title || r.file);
      if (r.outcome) {
        var out = el('div', 'outcome');
        out.append(el('strong', '', L.outcome + ': '), document.createTextNode(r.outcome.line));
        out.append(el('span', 'outcome-log', fmt(L.logged, {log: r.outcome.log, date: r.outcome.date})));
        panel.append(out);
      }
      if (r.warning) panel.append(el('p', 'readonly-note warn', L.warning + ': ' + r.warning));
      if (r.error) {
        panel.append(el('p', 'readonly-note warn', L.error), el('pre', 'error-text', r.error));
        return;
      }
    }
    if (d.lang) panel.lang = String(d.lang).replace(/_/g, '-');
    if (d.context) panel.append(el('p', 'context', d.context));  // which session asks, in the agent's words
    if (r.readonly && r.answers && r.answers.done) panel.append(el('p', 'readonly-note', L.readonly + (r.saved ? ' ' + when(r.saved) : '')));
    if (!r.readonly && oldRound(r.opened)) panel.append(el('p', 'readonly-note warn old-note', L.oldNote));
    var group = null;
    d.items.forEach(function (item, n) {
      if (item.group && item.group !== group) panel.append(el('h2', 'group', item.group));
      group = item.group || group;
      panel.append(renderItem(ri, item, n));
    });
    var free = el('section', 'item tail');  // tail: Focus shows it after the last item
    var box = el('div');
    var fl = el('label', 'ftitle', d.free_text.title || L.anything);
    fl.htmlFor = 'r' + ri + '-free';
    var fta = el('textarea');
    fta.id = fl.htmlFor;
    fta.placeholder = d.free_text.placeholder || '';
    fta.maxLength = MAX_TEXT;
    fta.dataset.round = ri;
    box.append(fl, fta);
    free.append(box);
    panel.append(free);
    if (wizard && !r.readonly) {
      var row = el('div', 'accept-row tail');
      var state = el('p', 'accept-state');
      state.id = 'accept-state-' + ri;
      state.setAttribute('aria-live', 'polite');
      // The round's Accept sits in the key bar, so it is always in reach; the bar shows the open round's only.
      var accept = el('button', 'primary accept-round', L.acceptRound), accel = el('kbd', '', accelKeys);
      accel.setAttribute('aria-hidden', 'true');  // the key list says it
      accept.append(accel);
      accept.type = 'button';
      accept.id = 'accept-' + ri;
      accept.addEventListener('click', function () { acceptRound(ri); });
      row.append(state);
      panel.append(row);
      acceptSlot.append(accept);
    }
    if (!historyMode && !wizard && !r.readonly) {
      // One round: the summary sits above a second Done, so the last look and the click are together.
      var block = el('section', 'summary-block ss tail');
      block.id = 'summary-block';
      var rows = el('div');
      rows.id = 'summary-rows';
      var end = el('div', 'summary-done');
      var done2 = doneCopy();
      done2.id = 'done-block';
      end.append(el('p', 'ss-note', L.signNote), done2);
      block.append(el('h2', 'ss-title', L.summary), rows, end);
      panel.append(block);
    }

    // Restore the saved answers; an item without one starts at the recommendation.
    var saved = r.answers;
    d.items.forEach(function (item) {
      var a = saved && saved.answers[item.id];
      sources[ri][item.id] = (a && a.source) || (hasRec(item) ? 'recommended' : 'user');
      var chosen = a ? a.choice : (r.readonly ? null : item.recommended);
      setChoice(ri, item, chosen === undefined || chosen === null ? [] : (Array.isArray(chosen) ? chosen : [chosen]));
      var ta = byId(key(ri, item) + '-c');
      if (a && a.source === 'elsewhere') {
        // Kept as saved, so a save of another item sends it back; the note is the agent's, not the comment box's.
        kept[ri][item.id] = {choice: a.choice, comment: a.comment || ''};
        var gone = byId(key(ri, item) + '-e');
        gone.textContent = L.elsewhere + (a.comment ? ': ' + a.comment : '');
        gone.hidden = false;
      } else if (ta && a) ta.value = a.comment;
      var other = byId(key(ri, item) + '-t');
      if (other && a && a.other !== undefined) other.value = a.other;
      var p = byId(key(ri, item) + '-p');
      if (p) {
        p.checked = a ? !!a.private : !!item.sensitive;  // a saved answer says what the user chose
        showPrivate(ri, item);
      }
      if (item.marks) {
        markState[ri][item.id] = ((a && a.marks) || []).map(function (m) { return {image: m.image, box: m.box.slice(), comment: m.comment}; });
        drawMarks(ri, item);
        listMarks(ri, item);
      }
    });
    if (saved) byId('r' + ri + '-free').value = saved.free_text;
    drawBlock();
    if (r.readonly) {
      Array.prototype.forEach.call(panel.querySelectorAll('input, textarea'), function (i) { i.disabled = true; });
    } else if (saved) {
      status.textContent = L.resumed;
    }
  }

  function collect(ri, done) {
    var answers = {};
    rounds[ri].decisions.items.forEach(function (item) {
      var picked = picks(ri, item);
      var ta = byId(key(ri, item) + '-c');
      var a = {choice: item.select === 'many' ? picked : (picked.length ? picked[0] : null), comment: ta ? ta.value : '',
        source: sources[ri][item.id]};
      if (a.source === 'elsewhere') {
        a.choice = kept[ri][item.id].choice;
        a.comment = kept[ri][item.id].comment;
      }
      if (picked.indexOf(OTHER) >= 0) a.other = byId(key(ri, item) + '-t').value;
      if (item.marks) a.marks = marksOf(ri, item);
      if (item.lines) a.lines = noteList(ri, item).map(function (x) { return {att: x.att, line: x.line, quote: x.quote, comment: x.comment}; });
      if (isPrivate(ri, item)) a.private = true;
      answers[item.id] = a;
    });
    return {answers: answers, free_text: byId('r' + ri + '-free').value, done: !!done, accepted: wizard && accepted[ri],
      rev: rev[ri]};
  }

  var chain = Promise.resolve(), timer = null, dirty = {}, finished = false, closed = {};
  function clock() {
    try { return new Date().toLocaleTimeString(root.lang); } catch (e) { return new Date().toLocaleTimeString(); }
  }
  function send(ri, done, keepalive) {
    var sent = collect(ri, done);
    var body = JSON.stringify(sent);
    function failed() {
      // An acceptance the server did not store is not one: show the round as open again.
      if (sent.accepted && accepted[ri]) {
        accepted[ri] = false;
        refresh();
      }
      return false;
    }
    // keepalive bodies are capped at 64 KiB, counted in bytes
    var small = new TextEncoder().encode(body).length < 60000;
    return fetch('save/' + ri, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: body,
      keepalive: !!keepalive && small, credentials: 'omit', cache: 'no-store'})
      .then(function (r) {
        if (r.ok) {
          return r.json().then(function (j) { if (typeof j.rev === 'number') rev[ri] = j.rev; }, function () {})
            .then(function () {
              status.textContent = fmt(L.saved, {t: clock()});
              if (done) {
                closed[ri] = true;  // final on the server: stop taking edits that could no longer be saved
                Array.prototype.forEach.call(byId('panel-' + ri).querySelectorAll('input, textarea, .mark-tools button, .mark-row button, .n button, .lfull'), function (i) {
                  i.disabled = true;
                  if (i.tagName === 'P') i.hidden = true;  // the line-notes hint
                });
              }
              return true;
            });
        }
        if (r.status === 400 || r.status === 409) {
          return r.text().then(function (m) {
            if (/another tab/.test(m)) {
              // Another tab saved this round since: say so where it cannot be missed.
              status.textContent = L.stale;
              var banner = byId('banner');
              banner.textContent = L.stale;
              banner.classList.add('on');
            } else {
              status.textContent = /already done/.test(m) ? L.already : fmt(L.bad, {m: m.trim()});
            }
            return failed();
          });
        }
        status.textContent = r.status === 413 ? L.tooLarge : fmt(L.couldNot, {s: r.status});
        return failed();
      }, function () {
        status.textContent = L.offline;
        return failed();
      });
  }
  function save(keepalive) {
    clearTimeout(timer);
    var todo = Object.keys(dirty).map(Number);
    dirty = {};
    chain = chain.then(function () {
      return todo.reduce(function (p, ri) {
        return p.then(function () { return finished || closed[ri] ? false : send(ri, false, keepalive); });
      }, Promise.resolve());
    });
    return chain;
  }
  function later(ri, ms) {
    if (finished || historyMode) return;
    drawBlock();
    if (accepted[ri] && !rounds[ri].readonly) {  // any change, a comment too, undoes the acceptance
      accepted[ri] = false;
      changed[ri] = true;
      refresh();
    }
    clearTimeout(timer);
    dirty[ri] = true;
    status.textContent = L.saving;
    timer = setTimeout(save, ms);
  }

  var active = editable.length ? editable[0] : 0;
  var undo = rounds.map(function () { return null; });
  function label(undoing) {
    acceptButton.querySelector('.accept').hidden = undoing;
    acceptButton.querySelector('.undo').hidden = !undoing;
  }
  var project = String((historyMode ? data.root : data.project) || '');  // a folder name from the server, never a path
  byId('project').textContent = project;
  byId('project-line').hidden = !project;
  byId('old-round').textContent = L.oldRound;
  if (data.inbox) {  // a run's rounds opened from the inbox: the way back, and why they may be read only
    var back = el('a', 'back', '← ' + L.inbox);
    back.href = '../../';
    byId('project-line').before(back);
    if (data.held) {
      byId('banner').textContent = L.held;
      byId('banner').classList.add('on');
      doneButton.hidden = true;
    } else if (data.orphan) {
      byId('banner').textContent = L.orphan;  // the run that asked is gone; the round still takes answers
      byId('banner').classList.add('on');
    }
  }
  // r: the round shown, whose opening time goes on the project line (the Summary and the past-decisions page: none).
  function showHeader(d, r) {
    var at = r && r.opened && new Date(r.opened), opened = byId('opened');
    opened.hidden = !at || isNaN(at);
    if (!opened.hidden) {
      var day;
      try {
        day = at.toLocaleString(root.lang, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
      } catch (e) { day = r.opened; }
      var t = lasted(r.opened);
      opened.textContent = t === L.underMinute ? fmt(L.openedNow, {d: day}) : fmt(L.opened, {d: day, t: t});
      opened.dateTime = r.opened;
      opened.title = when(r.opened);
    }
    byId('old-round').hidden = opened.hidden || r.readonly || !oldRound(r.opened);
    byId('project-line').hidden = !project && opened.hidden;
    byId('title').textContent = d.title;
    document.title = project ? project + ' · ' + d.title : d.title;
    byId('intro').textContent = d.intro || '';
    byId('intro-phone').textContent = d.intro || '';  // shown at the top of main; the header has no room for it
    var notice = byId('notice');
    notice.textContent = d.notice || '';
    notice.hidden = !d.notice;
  }

  if (historyMode) {
    // ---- The page of past decisions: a filterable list, and the chosen round read only ----
    [status, acceptButton, doneButton].forEach(function (b) { b.hidden = true; });
    var minutes = data.timeout;
    showHeader({title: L.historyTitle, intro: L.historyIntro,
      notice: minutes ? fmt(L.historyNotice, {m: minutes}) : L.historyNoticeNever});
    sh.nav.append(byId('history'));
    byId('history').hidden = false;
    byId('h-text-label').textContent = L.search;
    byId('h-from-label').textContent = L.from;
    byId('h-from').placeholder = L.fromHint;
    var list = byId('h-list'), items = [];
    // What the search may match in the answers: the chosen options, the Other text, comments and marks, the free
    // text. The text of a private answer is left out, or a hit would give it away.
    function answerWords(r) {
      if (!r.answers) return [];
      var words = [r.answers.free_text];
      (r.decisions ? r.decisions.items : []).forEach(function (it) {
        var a = r.answers.answers[it.id];
        if (!a) return;
        var picked = Array.isArray(a.choice) ? a.choice : [a.choice];
        (it.options || []).forEach(function (o) { if (picked.indexOf(o.id) >= 0) words.push(o.label); });
        if (typeof a.choice === 'number') words.push(numText(it, a.choice));
        if (a.private === true) return;
        words.push(a.other, a.comment);
        (a.marks || []).concat(a.lines || []).forEach(function (m) { words.push(m.comment); });
      });
      return words.filter(Boolean);
    }
    var choose = function (ri) {
      Array.prototype.forEach.call(panels.children, function (p) { if (p.id !== 'panel-' + ri) showPanel(p, false); });
      renderPanel(ri);
      showPanel(byId('panel-' + ri), true);
      items.forEach(function (b, i) { if (i === ri) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current'); });
      active = ri;
      mainEl.scrollTop = 0;
      if (sh.has('side-open')) {  // a narrow window: the drawer gives way to the round
        sh.closeSide(false);
        byId('panel-' + ri).tabIndex = -1;
        byId('panel-' + ri).focus();
      }
    };
    rounds.forEach(function (r, ri) {
      var li = el('li');
      var b = el('button', 'h-entry');
      b.type = 'button';
      var top = el('span', 'h-top');
      top.append(el('span', 'h-date', r.date), el('span', 'h-topic', r.topic + ' / ' + r.file));
      var name = el('span', 'h-title', r.title || r.file);
      if (r.tab && r.tab !== r.title) name.append(el('span', 'h-tab', r.tab));
      var st = el('span', 'h-status st-' + r.status, L['st_' + r.status]);
      b.append(top, name, st);
      if (r.warning) b.append(el('span', 'h-warn', '⚠ ' + L.warning));
      if (r.outcome) b.append(el('span', 'h-outcome', '→ ' + r.outcome.line));
      b.addEventListener('click', function () { choose(ri); });
      li.append(b);
      list.append(li);
      items.push(b);
      r.search = [r.title, r.tab, r.topic, r.file, r.outcome && r.outcome.line, r.error].concat(
        (r.decisions ? r.decisions.items : []).map(function (it) { return it.title; }), answerWords(r))
        .join(' ').toLowerCase();
    });
    var filter = function () {
      // From is ISO text like the dates in the list (2026, 2026-01 or 2026-01-31), not the viewer's regional format.
      var q = byId('h-text').value.trim().toLowerCase(), from = byId('h-from').value.trim(), shown = 0;
      rounds.forEach(function (r, ri) {
        var ok = (!q || r.search.indexOf(q) >= 0) && (!from || r.date >= from);
        items[ri].parentNode.hidden = !ok;
        if (ok) shown++;
      });
      byId('h-count').textContent = shown ? fmt(L.count, {n: shown, t: rounds.length}) : L.none;
    };
    byId('h-text').addEventListener('input', filter);
    byId('h-from').addEventListener('input', filter);
    filter();
    if (rounds.length) choose(0); else panels.append(el('p', 'hint', L.pick));
  } else {
    rounds.forEach(function (r, ri) { renderPanel(ri); });
    rounds.forEach(function (r, ri) {
      if (rounds.length < 2) return;
      var tab = el('button', 'nav');
      tab.type = 'button';
      tab.id = 'tab-' + ri;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', 'panel-' + ri);
      byId('panel-' + ri).setAttribute('aria-labelledby', tab.id);
      tabs.append(tab);
    });
    if (wizard) {
      // The Summary is the sign-off sheet: every answer of every round, and Done under it.
      var sum = el('button', 'nav sum'), all = rounds.reduce(function (n, r) { return n + r.decisions.items.length; }, 0);
      sum.type = 'button';
      sum.id = 'tab-' + rounds.length;
      sum.setAttribute('role', 'tab');
      sum.setAttribute('aria-controls', 'panel-' + rounds.length);
      sum.append(icon(['M7 3h7l4 4v14H7z', 'M14 3v4h4', 'M10 12h5', 'M10 16h5']), el('span', 'nl', L.summary),
        el('span', 'st', fmt(L.answersN, {n: all})));
      tabs.append(sum);
      var sp = el('section');
      sp.id = 'panel-' + rounds.length;
      sp.setAttribute('role', 'tabpanel');
      sp.setAttribute('aria-labelledby', sum.id);
      sp.hidden = true;
      panels.append(sp);
    }
    // The sidebar: the rounds (with more than one), then the open round's items.
    var roundsHead = el('h2', '', L.rounds);
    roundsHead.id = 'side-rounds';
    tabs.className = 'navs';
    tabs.setAttribute('aria-orientation', 'vertical');
    tabs.setAttribute('aria-labelledby', roundsHead.id);
    tabs.hidden = roundsHead.hidden = rounds.length < 2;
    outlineHead.id = 'side-outline';
    outline.setAttribute('aria-labelledby', outlineHead.id);
    sh.nav.append(roundsHead, tabs, outlineHead, outline);
  }
  // The open round's items as a list: a click goes to the item (in Focus, shows it).
  function drawOutline() {
    var cards = active === SUMMARY ? [] : itemCards();
    outline.replaceChildren();
    outlineHead.hidden = outline.hidden = !cards.length;
    cards.forEach(function (card, n) {
      var li = el('li'), b = el('button', 'nav');
      b.type = 'button';
      var item = rounds[active].decisions.items[n];
      b.append(el('span', 'n', String(n + 1)), el('span', 'nl', item.title));
      if (item.blocking) b.append(waitMark('st', L.blockingShort));
      if (item.stakes === 'high') b.append(waitMark('st', L.highShort, '▲'));
      if (item.irreversible) b.append(waitMark('st', L.undoShort, '!'));
      b.addEventListener('click', function () {
        sh.closeSide(false);
        goItem(n);
      });
      li.append(b);
      outline.append(li);
    });
    markOutline();
  }
  function markOutline() {
    Array.prototype.forEach.call(outline.querySelectorAll('button'), function (b, n) {
      if (n === cur[active]) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
  }
  function itemCards() {
    var panel = byId('panel-' + active);
    return panel ? Array.prototype.slice.call(panel.querySelectorAll('.item[data-item]')) : [];
  }
  // Focus: one item at a time; the round's free text and its acceptance (or the summary and Done) after the last.
  function place() {
    var panel = byId('panel-' + active), cards = active === SUMMARY ? [] : itemCards(), n = cur[active];
    cards.forEach(function (c, i) { c.classList.toggle('cur', i === n); });
    if (panel) panel.classList.toggle('at-end', n >= cards.length - 1);
    if (panel) panel.classList.toggle('at-start', n <= 0);
    sh.fbar.hidden = !cards.length;
    sh.at.textContent = !cards.length ? '' : fmt(L.itemOf, {i: n + 1, n: cards.length})
      + (rounds.length > 1 ? ' · ' + fmt(L.roundOf, {i: active + 1, n: rounds.length}) : '');
    sh.prev.disabled = n <= 0;
    sh.next.disabled = n >= cards.length - 1;
  }
  function goItem(n) {
    var cards = itemCards();
    if (!cards[n]) return false;
    cur[active] = n;
    place();
    markOutline();
    focusItem(cards[n]);
    if (body.classList.contains('focusing')) cards[n].scrollIntoView({block: 'start'});  // from its question down
    return true;
  }
  function setFocus(on, quiet) {
    body.classList.toggle('focusing', on);
    sh.focusButton.setAttribute('aria-pressed', String(on));
    if (on) sh.closeSide(false);
    place();
    sh.sideState();
    if (quiet) return;
    var card = itemCards()[cur[active]];
    if (card) focusItem(card); else sh.focusButton.focus();
    if (on && card) card.scrollIntoView({block: 'start'});
  }

  // Tabs: the WAI-ARIA tabs pattern with automatic activation.
  var tabButtons = Array.prototype.slice.call(tabs.querySelectorAll('[role=tab]'));
  var SUMMARY = rounds.length;
  // A panel hidden drops its rows; shown, its open boxes draw them again through their toggle.
  function showPanel(p, on) {
    p.hidden = !on;
    p.querySelectorAll(on ? '.attach[open]' : '.att-lines').forEach(function (e) { if (on) e.dispatchEvent(new Event('toggle')); else e.remove(); });
  }
  function showRound(ri) {
    active = ri;
    tabButtons.forEach(function (t, i) {
      showPanel(byId('panel-' + i), i === ri);
      t.setAttribute('aria-selected', i === ri ? 'true' : 'false');
      t.tabIndex = i === ri ? 0 : -1;
    });
    if (!tabButtons.length) showPanel(byId('panel-0'), true);
    place();
    drawOutline();
    acceptShown();
    mainEl.scrollTop = 0;
    if (ri === SUMMARY) {
      showHeader({title: L.summary});  // the sheet says what it is for
      acceptButton.hidden = true;
      renderSummary();
      return;
    }
    var d = rounds[ri].decisions;
    showHeader(d, rounds[ri]);
    acceptButton.hidden = rounds[ri].readonly || !d.items.some(hasRec);
    label(!!undo[ri]);
    fitNotes();  // the single round's summary block was skipped while its panel was hidden
  }
  function acceptShown() {  // the key bar shows the open round's Accept, until it is accepted
    editable.forEach(function (ri) {
      var b = byId('accept-' + ri);
      if (b) b.hidden = accepted[ri] || ri !== active;
    });
  }
  // Which round's state a glyph and a word show; never colour alone.
  function roundState(ri, summary) {
    if (rounds[ri].readonly) return ['✓', L.st_done, 'g-done'];
    if (wizard && accepted[ri]) return ['✓', L.accepted, 'g-acc'];
    if (wizard && changed[ri]) return ['↺', L.stChanged, 'g-chg'];
    return ['●', summary && wizard ? L.notAccepted : L.st_open, 'g-open'];
  }
  function tabName(ri) { return rounds[ri].decisions.tab || rounds[ri].decisions.title; }
  function missingRounds() { return editable.filter(function (ri) { return !accepted[ri] && !closed[ri]; }); }
  var doneButtons = byId('done-block') ? [doneButton, byId('done-block')] : [doneButton];
  // Done waits for every round to be accepted. It stays focusable and says why, instead of a silent grey button.
  function refresh() {
    tabButtons.forEach(function (tab, ri) {
      if (ri === SUMMARY) return;
      var st = roundState(ri), g = el('span', 'gl ' + st[2], st[0]);
      g.setAttribute('aria-hidden', 'true');
      tab.replaceChildren(g, el('span', 'nl', (ri + 1) + ' · ' + tabName(ri)), ' ', el('span', 'st', st[1]));
      var state = byId('accept-state-' + ri);
      if (state) {
        state.textContent = accepted[ri] ? L.acceptedNote : changed[ri] ? L.changedNote : '';
        state.className = 'accept-state' + (changed[ri] && !accepted[ri] ? ' changed' : '');
      }
    });
    acceptShown();
    if (!wizard) return;
    var count = editable.filter(function (ri) { return accepted[ri]; }).length;
    var progress = byId('progress');
    progress.hidden = false;
    progress.textContent = fmt(L.progress, {n: count, t: editable.length});
    var missing = missingRounds(), reason = byId('done-reason');
    var text = !missing.length ? '' : missing.length === 1 ? fmt(L.missingOne, {r: tabName(missing[0])})
      : fmt(L.missingMany, {n: missing.length, r: tabName(missing[0])});
    reason.hidden = !text;
    reason.textContent = text;
    doneButtons.forEach(function (b) { b.setAttribute('aria-disabled', String(!!missing.length)); });
    var summaryReason = byId('summary-reason');
    if (summaryReason) {
      summaryReason.hidden = !text;
      summaryReason.textContent = text;
    }
    if (active === SUMMARY) renderSummary();
  }
  function goToMissing() {
    var missing = missingRounds();
    if (!missing.length) return;
    showRound(missing[0]);
    focusRound(missing[0]);
  }
  byId('done-reason').addEventListener('click', goToMissing);
  function acceptRound(ri) {
    if (!wizard || rounds[ri].readonly || finished || closed[ri]) return;
    accepted[ri] = true;
    changed[ri] = false;
    clearTimeout(timer);
    dirty[ri] = true;
    save();
    refresh();
    // On to the next round not accepted yet, or the summary when there is none.
    var order = editable.filter(function (x) { return x > ri; }).concat(editable.filter(function (x) { return x < ri; }));
    var next = order.filter(function (x) { return !accepted[x]; })[0];
    var to = next === undefined ? SUMMARY : next;
    showRound(to);
    focusRound(to);
  }
  // Focus on the round's tab; with the sidebar hidden (a drawer, Focus) on the round itself, never on the page.
  function focusRound(ri) {
    var tab = tabButtons[ri], panel = byId('panel-' + ri);
    if (tab && tab.offsetParent !== null) return tab.focus();
    panel.tabIndex = -1;
    panel.focus();
  }
  function choiceText(ri, item, priv) {
    var picked = picks(ri, item);
    return {picked: picked, text: picked.map(function (v) {
      if (v === OTHER) return L.other + ': ' + (priv ? L.privateBadge : byId(key(ri, item) + '-t').value || '–');
      if (v === SKIP) return L.skipped;
      return labelOf(item, v);
    }).join(', ') || L.nothing};
  }
  // Every item of the given rounds with its choice, its flags and its comment, under a filter that only hides rows.
  function summaryRows(holder, list, heads) {
    var counts = {all: 0, comment: 0, differs: 0, blocking: 0}, boxes = [];
    var bar = el('div', 'summary-filter'), seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', L.show);
    bar.append(el('span', 'board-label', L.show), seg);
    holder.append(bar);
    list.forEach(function (ri) {
      var r = rounds[ri];
      var box = el('section', 'summary-round');
      if (heads) {
        var st = roundState(ri, true), head = el('h2', '', (ri + 1) + ' · ' + tabName(ri) + ' '), g = el('span', 'gl', st[0]);
        var chip = el('span', 'ss-st ' + st[2]);
        g.setAttribute('aria-hidden', 'true');
        chip.append(g, st[1]);
        head.append(chip);
        box.append(head);
      }
      var ul = el('ul', 'summary-list');
      r.decisions.items.forEach(function (item, n) {
        var priv = isPrivate(ri, item), c = choiceText(ri, item, priv), li = el('li');
        var go = el('button', 'link', (n + 1) + '. ' + item.title);
        go.type = 'button';
        go.addEventListener('click', function () {
          showRound(ri);
          var card = byId('panel-' + ri).querySelectorAll('.item[data-item]')[n];
          focusItem(card);
        });
        var away = sources[ri][item.id] === 'elsewhere';  // settled in chat: no choice, and no overruled warning
        li.append(go, el('span', 'summary-choice', away ? L.elsewhere : c.text));
        var rec = recList(item), flags = [];
        var same = away || (rec.length === c.picked.length && rec.every(function (x) { return c.picked.indexOf(x) >= 0; }));
        if (rec.length && !same) flags.push(L.differs);
        if (c.picked.indexOf(OTHER) >= 0) flags.push(L.otherBadge);
        if (c.picked.indexOf(SKIP) >= 0) flags.push(L.skipped);
        var count = item.marks ? marksOf(ri, item).length : 0;
        if (count) flags.push(count === 1 ? L.markBadge : fmt(L.marksBadge, {n: count}));
        var ta = byId(key(ri, item) + '-c'), note = away ? kept[ri][item.id].comment.trim() : ta ? ta.value.trim() : '';
        if (note) {
          flags.push(L.commentBadge);
          // The Summary is the view most likely to end up in a screenshot: a private comment is only named here.
          li.append(priv ? el('blockquote', 'summary-private', L.privateNote) : el('blockquote', 'summary-note', note));
        }
        noteList(ri, item).forEach(function (x) {
          li.append(el('blockquote', priv ? 'summary-private' : 'summary-note',
            fmt(L.lnNote, {n: x.line, f: item.attachments[x.att].src}) + ': ' + (priv ? L.privateBadge : x.comment)));
        });
        var marks = el('span', 'flags');  // one cell on the sheet
        flags.forEach(function (f) { marks.append(el('span', 'badge' + (f === L.differs ? ' warn' : ''), f)); });
        if (item.blocking) marks.append(waitMark('badge', L.blocking));  // the agent's mark, not a flag on the answer
        if (priv) marks.append(el('span', 'badge private', L.privateBadge));
        if (marks.firstChild) li.append(marks);
        if (flags.length) li.classList.add('flag');
        li.dataset.show = ['all'].concat(note ? ['comment'] : [], rec.length && !same ? ['differs'] : [],
          item.blocking ? ['blocking'] : []).join(' ');
        li.dataset.show.split(' ').forEach(function (k) { counts[k]++; });
        ul.append(li);
      });
      box.append(ul);
      holder.append(box);
      boxes.push(box);
    });
    var none = el('p', 'hint', L.noneShown);
    holder.append(none);
    Array.prototype.forEach.call(holder.querySelectorAll('.summary-note'), function (q) {
      var more = el('button', 'link note-more', L.more);
      more.type = 'button';
      more.setAttribute('aria-expanded', 'false');
      more.addEventListener('click', function () {
        var open = q.classList.toggle('open');
        more.textContent = open ? L.less : L.more;
        more.setAttribute('aria-expanded', String(open));
      });
      q.after(more);
    });
    function apply() {
      var any = false;
      Array.prototype.forEach.call(seg.children, function (b) { b.setAttribute('aria-pressed', String(b.dataset.show === show)); });
      boxes.forEach(function (box) {
        var shown = 0;
        Array.prototype.forEach.call(box.querySelectorAll('li'), function (li) {
          li.hidden = li.dataset.show.split(' ').indexOf(show) < 0;
          if (!li.hidden) shown++;
        });
        box.hidden = !shown;
        any = any || shown > 0;
      });
      none.hidden = any;
      fitNotes();  // rows shown again were skipped while hidden, maybe at another width
    }
    ['all', 'comment', 'differs', 'blocking'].forEach(function (k) {
      if (k === 'blocking' && !counts.blocking) return;  // a round without the mark looks as it did
      if (k === 'blocking') seg.classList.add('four');  // four labels outgrow a phone, so they wrap as buttons
      var b = el('button', '', L['show_' + k] + '\xa0' + counts[k]);  // a narrow screen wraps the label, never the count
      b.type = 'button';
      b.dataset.show = k;
      b.addEventListener('click', function () { show = k; apply(); });
      seg.append(b);
    });
    apply();
  }
  // CSS cuts a quote at 3 lines; the toggle after it shows only where it does, so measure again when the width changes.
  function fitNotes() {
    Array.prototype.forEach.call(document.querySelectorAll('.summary-note'), function (q) {
      if (q.offsetParent) q.nextSibling.hidden = !q.classList.contains('open') && q.scrollHeight <= q.clientHeight + 1;
    });
  }
  window.addEventListener('resize', fitNotes);
  function drawBlock() {
    var rows = byId('summary-rows');
    if (!rows) return;
    // Rebuild only when an answer changed: a text field's change event on blur would swap the buttons under a click.
    var seen = JSON.stringify(collect(editable[0], false).answers);
    if (seen === blockSeen) return;
    blockSeen = seen;
    rows.replaceChildren();
    summaryRows(rows, editable, false);
  }
  // A second Done below a summary, for the last look before the click.
  function doneCopy() {
    var b = el('button', 'primary', doneButton.textContent);
    b.type = 'button';
    b.addEventListener('click', finish);
    return b;
  }
  function renderSummary() {
    var panel = byId('panel-' + SUMMARY), sheet = el('div', 'ss');
    panel.replaceChildren(sheet);
    sheet.append(el('h2', 'ss-title', L.summary), el('p', 'ss-intro', L.summaryIntro));
    summaryRows(sheet, rounds.map(function (r, ri) { return ri; }), true);
    var end = el('div', 'summary-done'), count = editable.filter(function (ri) { return accepted[ri]; }).length;
    end.append(el('p', 'ss-note', fmt(L.progress, {n: count, t: editable.length}) + ' · ' + L.signNote));
    var why = el('button', 'link reason');
    why.type = 'button';
    why.id = 'summary-reason';
    why.addEventListener('click', goToMissing);
    var done2 = doneCopy();
    done2.setAttribute('aria-describedby', 'summary-reason');
    doneButtons = [doneButton, done2];
    end.append(why, done2);
    sheet.append(end);
    var missing = missingRounds();
    why.hidden = !missing.length;
    why.textContent = byId('done-reason').textContent;
    done2.setAttribute('aria-disabled', String(!!missing.length));
  }
  tabs.addEventListener('click', function (e) {
    var t = e.target.closest('[role=tab]');
    if (t) showRound(tabButtons.indexOf(t));
  });
  tabs.addEventListener('keydown', function (e) {
    var i = tabButtons.indexOf(e.target), n = tabButtons.length, next = null;
    if (i < 0) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % n;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + n) % n;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    if (next === null) return;
    e.preventDefault();
    showRound(next);
    tabButtons[next].focus();
  });

  // "Accept all" sets every item of the open round that has a recommendation to it, comments and marks untouched.
  // A second click restores the choices from before; changing any item's options afterwards ends that undo. An item
  // the agent marked decided elsewhere is neither set nor restored: once the user takes it back, it stays theirs.
  acceptButton.addEventListener('click', function () {
    var ri = active, items = rounds[ri].decisions.items;
    if (undo[ri]) {
      items.forEach(function (item) {
        if (!undo[ri][item.id]) return;
        setChoice(ri, item, undo[ri][item.id].choice);
        sources[ri][item.id] = undo[ri][item.id].source;
      });
      undo[ri] = null;
    } else {
      undo[ri] = {};
      items.forEach(function (item) {
        if (sources[ri][item.id] === 'elsewhere') return;
        undo[ri][item.id] = {source: sources[ri][item.id], choice: picks(ri, item)};
        if (hasRec(item)) {
          setChoice(ri, item, recList(item));
          sources[ri][item.id] = 'accept_all';
        }
      });
    }
    label(!!undo[ri]);
    later(ri, 150);
  });

  // A move of a number item's slider or field: the other follows, Skip goes, and the answer is the user's. A field
  // with no number in it yet ("-", "1e") leaves the slider where it was.
  function moveNumber(t) {
    var ri = Number(t.dataset.round), item = rounds[ri].decisions.items.filter(function (x) { return x.id === t.dataset.num; })[0];
    var k = key(ri, item), range = byId(k + '-n'), field = byId(k + '-v'), skip = byId(k + '-skip');
    if (t === range) field.value = range.value;
    else if (field.value === '') return;
    else range.value = field.value;
    delete range.dataset.unset;
    if (skip) skip.checked = false;
    numShown(ri, item);
    unmark(ri, item);
    sources[ri][item.id] = 'user';
    if (undo[ri]) {
      undo[ri] = null;
      label(false);
    }
    later(ri, t === range ? 250 : 800);
  }
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.dataset.round === undefined) return;
    var ri = Number(t.dataset.round);
    if (t.dataset.num !== undefined && t.type === 'number') t.value = byId(t.id.replace(/-v$/, '-n')).value;  // snapped
    if (t.dataset.item !== undefined && (t.type === 'radio' || t.type === 'checkbox')) {
      if (t.type === 'checkbox' && t.checked) {
        // Skip stands alone: ticking it clears the other boxes, ticking another box clears it.
        document.getElementsByName(t.name).forEach(function (i) {
          if (i !== t && (t.value === SKIP || i.value === SKIP)) i.checked = false;
        });
      }
      if (mirrors[t.name]) mirrors[t.name]();
      unmark(ri, itemOf(ri, t));
      sources[ri][t.dataset.item] = 'user';
      if (undo[ri]) {
        undo[ri] = null;
        label(false);
      }
    }
    later(ri, 250);
  });
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t.dataset.num !== undefined && t.dataset.round !== undefined) return moveNumber(t);
    if (t.dataset.round === undefined || (t.tagName !== 'TEXTAREA' && t.type !== 'text')) return;
    // Text typed on a marked item is the user's: collect would send the saved note instead and drop it.
    unmark(Number(t.dataset.round), itemOf(Number(t.dataset.round), t));
    if (t.classList.contains('other-text')) {
      var pick = byId(t.dataset.pick);
      if (!pick.checked) {
        pick.checked = true;
        pick.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    later(Number(t.dataset.round), 800);
  });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && Object.keys(dirty).length && !finished) save(true);
  });

  // One Done for every editable round: save them all first, so a bad one stops before any round is final.
  function each(list, done) {
    return list.reduce(function (p, ri) {
      return p.then(function (ok) { return !ok ? false : (closed[ri] ? true : send(ri, done)); });
    }, Promise.resolve(true));
  }
  function finish() {
    if (finished) return;
    if (wizard && missingRounds().length) {  // Done is not ready: say why and go there
      status.textContent = byId('done-reason').textContent;
      byId('done-reason').focus();
      return;
    }
    doneButtons.forEach(function (b) { b.disabled = true; });
    clearTimeout(timer);
    dirty = {};
    chain = chain.then(function () { return each(editable, false); })
      .then(function (ok) { return ok && each(editable, true); });
    chain.then(function (ok) {
      if (!ok) { doneButtons.forEach(function (b) { b.disabled = false; }); return; }
      finished = true;
      acceptButton.disabled = true;
      Array.prototype.forEach.call(document.querySelectorAll('main input, main textarea, .mark-tools button, .mark-row button, .n button'), function (i) { i.disabled = true; });
      status.textContent = L.done;
      var banner = byId('banner');
      banner.textContent = data.orphan ? L.doneOrphan : L.doneBanner;
      banner.classList.add('on');
      mainEl.scrollTop = 0;
    });
  }
  doneButton.addEventListener('click', finish);

  // Keyboard shortcuts. They never act while a text field has focus or a modifier key is held, so typing,
  // browser shortcuts and screen reader commands keep working.
  var help = byId('help');
  [[['1–9'], L.kPick], [['j', 'k'], L.kMove], [['↑', '↓'], L.kTabs], [['←', '↑', '→', '↓'], L.kMark],
    [['Ctrl', 'Enter'], L.kAccept], [['f'], L.kFocus], [shortcut, L.kTheme], [['?'], L.kHelp],
    [['Esc'], L.kLeave]].forEach(function (row) {
    if (historyMode && (row[1] === L.kAccept || row[1] === L.kFocus)) return;  // read only, and no Focus
    var dt = el('dt');
    row[0].forEach(function (k, i) {
      if (i) dt.append(' ');
      dt.append(el('kbd', '', k));
    });
    help.querySelector('#help-keys').append(dt, el('dd', '', row[1]));
  });
  function openHelp() { if (!help.open) help.showModal(); }
  sh.keysButton.addEventListener('click', openHelp);
  byId('help-close').addEventListener('click', function () { help.close(); });
  function typing(t) {
    return !!t && (t.isContentEditable || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT'
      || (t.tagName === 'INPUT' && t.type !== 'radio' && t.type !== 'checkbox' && t.type !== 'range'));
  }
  function shown(list) {  // the first enabled input on screen: in the table view the cards' inputs are hidden
    return Array.prototype.filter.call(list, function (i) { return !i.disabled && i.offsetParent !== null; })[0];
  }
  function focusItem(card) {
    var target = shown(card.querySelectorAll('.choices input:checked, .cmp input:checked')) || shown(card.querySelectorAll('input')) || card;
    target.focus();
    card.scrollIntoView({block: 'nearest'});
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.altKey && !historyMode && !typing(e.target) && !help.open
        && !picker.open && !sh.has('side-open')) {
      e.preventDefault();
      if (!wizard) finish();
      else if (active !== SUMMARY) acceptRound(active);
      else finish();
      return;
    }
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || help.open || picker.open) return;
    if (e.key === 'Escape') {  // also from a text box: leaving Focus keeps what was typed
      if (sh.has('side-open')) sh.closeSide(true);
      else if (sh.has('focusing')) setFocus(false);
      else return;
      e.preventDefault();
      return;
    }
    if (typing(e.target)) return;
    if (e.key === '?') {
      e.preventDefault();
      openHelp();
      return;
    }
    if ((e.key === 'f' || e.key === 'F') && !historyMode) {
      e.preventDefault();
      setFocus(!sh.has('focusing'));
      return;
    }
    var panel = byId('panel-' + active);
    if (!panel) return;
    var cards = Array.prototype.slice.call(panel.querySelectorAll('.item[data-item]'));
    var current = e.target.closest ? e.target.closest('.item[data-item]') : null;
    var at = sh.has('focusing') ? cur[active] : cards.indexOf(current);  // in Focus, the item shown
    if (e.key === 'j' || e.key === 'k') {
      var next = at < 0 ? 0 : at + (e.key === 'j' ? 1 : -1);
      if (next >= 0 && next < cards.length) {
        e.preventDefault();
        goItem(next);
      }
      return;
    }
    if (/^[1-9]$/.test(e.key) && current && !current.querySelector('.num-pick') && !current.classList.contains('folded')) {
      var input = current.querySelectorAll('.choices input[type=radio], .choices input[type=checkbox]')[Number(e.key) - 1];
      if (!input || input.disabled) return;
      e.preventDefault();
      input.checked = input.type === 'radio' ? true : !input.checked;
      (input.offsetParent === null && byId(input.id.replace(/-o(\d+)$/, '-x$1')) || input).focus();
      input.dispatchEvent(new Event('change', {bubbles: true}));
    }
  });

  if (!historyMode) {
    // #focus=<round>/<item> (from the inbox's start link for a panel row): that round's item in Focus. A hash
    // that names no round and item on this page is ignored.
    var asked = /^#focus=(\d{1,4})\/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$/.exec(location.hash);
    var askedRound = asked && rounds[Number(asked[1])];
    var askedItem = askedRound && askedRound.decisions ? askedRound.decisions.items.map(function (it) { return it.id; })
      .indexOf(asked[2]) : -1;
    if (askedItem >= 0) {
      active = Number(asked[1]);
      cur[active] = askedItem;  // before showRound, so the sidebar's outline marks it too
    }
    showRound(active);
    refresh();
    if (askedItem >= 0) {
      setFocus(true);
    } else if (window.matchMedia('(max-width: 760px)').matches) {
      // A phone opens in Focus, one item at a time; only at load, so turning the phone never switches it.
      setFocus(true, true);
    }
  }
})();
