  // ---- The inbox. Todo items come as a tree from the server; every part becomes an element with textContent,
  // so the agent's HTML is text, and only http(s) links are made. ----
  var ALERTS = ['note', 'tip', 'important', 'warning', 'caution'];  // class names, so the list stays closed
  function inlines(nodes, into, plain) {
    (nodes || []).forEach(function (n) {
      if (n.t === 'code') {
        into.append(el('code', 'ic', n.v));
        if (!plain) {  // plain: a title (inside a button) or a link's text: no button there
          into.append(copier(String(n.v), L.copyCode, 'link ic-copy'));
          if (relativeDelete(n.v)) into.append(el('span', 'ic-warn', '⚠ ' + L.relDelete));
        }
      } else if (n.t === 'b') into.append(inlines(n.c, el('strong'), plain));
      else if (n.t === 'link' && !plain && /^https?:\/\//i.test(String(n.href))) {
        var a = inlines(n.c, el('a'), 'link');
        a.href = n.href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        into.append(a);
      } else if (n.t === 'link') inlines(n.c, into, plain);
      else into.append(document.createTextNode(String(n.v || '')));
    });
    return into;
  }
  function blocks(list, into) {
    (list || []).forEach(function (b) {
      if (b.t === 'p') into.append(inlines(b.c, el('p')));
      else if (b.t === 'h') into.append(inlines(b.c, el('p', 'md-h')));  // not a page heading: it is the agent's
      else if (b.t === 'code') into.append(codeBlock(b));
      else if (b.t === 'hr') into.append(el('hr'));
      else if (b.t === 'quote') into.append(blocks(b.c, el('blockquote', 'md-quote')));
      else if (b.t === 'alert') {
        var kind = ALERTS.indexOf(b.kind) >= 0 ? b.kind : 'note', box = el('div', 'md-alert ' + kind);
        box.append(el('p', 'md-alert-title', L['alert_' + kind]));
        into.append(blocks(b.c, box));
      } else if (b.t === 'list') {
        var list = el(b.ordered ? 'ol' : 'ul', 'md-list');
        (b.items || []).forEach(function (it) {
          var li = el('li');
          if (it.check === true || it.check === false) {
            li.className = 'task' + (it.wait === true ? ' wait' : '');  // a todo step waiting on the user (server's)
            var box = el('span', 'md-check' + (it.check ? ' on' : ''), it.check ? '✓' : '');
            box.setAttribute('role', 'img');
            box.setAttribute('aria-label', it.check ? L.taskDone : L.taskOpen);
            li.append(box);
          }
          list.append(blocks(it.c, li));
        });
        into.append(list);
      } else if (b.t === 'table') {
        var wrap = el('div', 'md-table'), t = el('table'), head = el('tr'), body = el('tbody'), thead = el('thead');
        (b.head || []).forEach(function (c) { head.append(inlines(c, el('th'))); });
        (b.rows || []).forEach(function (r) {
          var tr = el('tr');
          r.forEach(function (c) { tr.append(inlines(c, el('td'))); });
          body.append(tr);
        });
        thead.append(head);
        t.append(thead, body);
        wrap.append(t);
        into.append(wrap);
      }
    });
    return into;
  }
  // The inbox list (Sally's inbox-main board): the page's header is the toolbar; a sidebar of views and projects
  // filters the list; a row shows in the pane beside it (a bottom sheet on a phone); a key bar at the foot.
  function inboxPage() {
    var body = document.body;
    body.classList.add('inbox');
    ['status', 'accept-all', 'done', 'intro-phone'].forEach(function (id) { byId(id).hidden = true; });
    byId('title').textContent = L.inbox;
    var waiting = data.waiting || [], sent = data.sent || [], projects = data.projects || [], idle = data.idle || [];
    var parked = data.parked || [];
    // The tab title keeps the count of Waiting on you, the same number as state.json. When that count, in all or per
    // project, differs from the rows on the page, the page reloads itself if that loses nothing (see below), else a bar at the top of the list says so.
    function waitingTitle(n) { document.title = (n ? '(' + n + ') ' : '') + L.inbox; }
    waitingTitle(waiting.length);
    var pane = byId('panels'), layout = byId('layout'), count = 0, rows = [], sections = [], current = null, filter = null;
    var terms = [];  // the search's words, folded; none: no search
    var narrow = window.matchMedia('(max-width: 760px)');

    // The toolbar: the sidebar switch first, Show all and Focus beside the brush.
    var sh = shell({label: L.side, keys: [[['J', 'K'], L.kMoveShort], [['⏎'], L.kOpenShort], [['F'], L.kFocusShort],
      [['/'], L.kSearchShort]],
      extra: [pane], setFocus: function (on) { setFocus(on); }, move: function (step) { return move(step); }});
    var has = sh.has, inert = sh.inert, closeSide = sh.closeSide, sideState = sh.sideState, drawer = sh.drawer;
    var nav = sh.nav, sideButton = sh.sideButton, focusButton = sh.focusButton, bar = sh.bar, keysButton = sh.keysButton;
    var fbar = sh.fbar, at = sh.at, prev = sh.prev, next = sh.next, scrim = sh.scrim;
    var allButton = el('button', 'tb-btn', L.showAll), navs = [];
    allButton.type = 'button';
    allButton.hidden = true;
    focusButton.before(allButton);
    var brand = el('p', 'brand'), logo = el('span', 'logo');
    logo.setAttribute('aria-hidden', 'true');
    brand.append(logo, 'Signoff');
    nav.append(brand);
    // The search: one field in the header, over every view. A query shows the rows that match (case and accents
    // ignored, every word somewhere in the row) and opens the To do groups and Answered that hold one; an empty
    // field gives the list back as the sidebar left it.
    var form = el('form', 'ib-search'), box = el('input'), boxLabel = el('label', 'visually-hidden', L.findLabel);
    var found = el('span', 'ib-search-count'), clear = el('button', '', L.clearSearch), none = el('p', 'hint ib-nomatch');
    var mac = shortcut[0] === '⌘';
    form.setAttribute('role', 'search');
    box.id = 'ib-search';
    boxLabel.htmlFor = box.id;
    box.type = 'search';
    box.placeholder = L.findPlaceholder;
    box.title = L.findLabel + ' (/, ' + (mac ? '⌘K' : 'Ctrl+K') + ')';
    box.autocomplete = 'off';
    box.spellcheck = false;
    box.maxLength = 200;
    found.id = 'ib-search-count';
    found.setAttribute('aria-live', 'polite');
    clear.id = 'ib-search-clear';
    clear.type = 'button';
    clear.hidden = none.hidden = true;
    form.append(boxLabel, box, found, clear);
    document.querySelector('body > header .actions').before(form);
    pane.append(none);

    // The open item: one row's view at a time, built on first use and kept, so typed text and statuses stay.
    var detail = el('section'), close = el('button', 'ib-close', L.close), view = el('div');
    var place = el('p', 'ib-empty', L.pickRow);
    detail.id = 'ib-detail';
    detail.setAttribute('aria-label', L.detail);
    close.type = 'button';
    detail.append(fbar, close, view);
    layout.append(detail);
    var state = el('p', 'bar-state'), dot = el('span', 'dot'), stateText = el('span', '', L.upToDate);
    dot.setAttribute('aria-hidden', 'true');
    state.setAttribute('role', 'status');
    state.append(dot, stateText);
    var working = {};
    sent.forEach(function (s) { if (s.state === 'working') working[s.project] = true; });
    var agents = Object.keys(working).length;
    if (agents) state.append(' · ' + (agents === 1 ? L.workingOne : fmt(L.workingN, {n: agents})));
    bar.append(state, el('span', 'grow'), sh.keys);
    function stale() {
      if (dot.classList.contains('new')) return;
      dot.classList.add('new');
      stateText.textContent = L.changed + ' · ';
      var again = el('button', 'link', L.reload);
      again.type = 'button';
      again.addEventListener('click', reloadNow);
      stateText.append(again);
    }

    // A reload keeps the open row, the filter, the search and the scroll in this tab's sessionStorage (the page URL reloads, never
    // the single-use start link). It happens by itself only when nothing is lost: no typed text in any row's view,
    // no sheet, drawer, picker, help or Focus, no selected text, and the focus not in a field. Not within 15 seconds
    // of this page's own load, so a count that never settles cannot loop; else the bar at the top of the list asks.
    var KEPT = 'signoff-inbox', AGAIN = 15000;
    // A row's key must survive the change that made the reload: a todo item's `since` is its file's time, so not that.
    function rowKey(r) {
      return [r.sec.key, r.w.project || '', r.title.textContent, r.w.kind === 'todo' ? '' : r.w.since || r.w.saved || ''].join('|');
    }
    function reloadNow(to) {  // to: a row whose link takes the focus after the reload (Park, Back), else none
      try {  // the load after a reload takes this once
        sessionStorage.setItem(KEPT, JSON.stringify({row: current ? rowKey(current) : null,
          focus: to && to.go ? rowKey(to) : null,
          filter: filter ? filter.key : null, query: box.value, scroll: pane.scrollTop, detail: detail.scrollTop, at: Date.now(),
          open: closable().filter(function (g) { return g.open; }).map(function (g) { return g.key; })}));
      } catch (e) { /* no storage: it reloads from the top */ }
      location.reload();
    }
    function typed() {  // text in a row's view that a reload would lose
      return rows.some(function (r) {
        return r.view && Array.prototype.some.call(r.view.querySelectorAll('input[type=text],textarea'),
          function (i) { return i.value.trim() !== ''; });
      });
    }
    function calm() {
      return !(has('sheet') || has('side-open') || has('focusing') || picker.open || byId('help').open
        || String(window.getSelection()) !== '' || typing(document.activeElement) || typed());
    }
    var fresh = el('p', 'ib-fresh'), freshText = el('span'), freshGo = el('button', 'primary', L.reload);
    freshGo.type = 'button';
    freshGo.addEventListener('click', reloadNow);
    fresh.hidden = true;
    fresh.setAttribute('role', 'status');
    fresh.append(freshText, freshGo);
    pane.prepend(fresh);
    // The rows per project, as state.json counts them: a row that went and another that came, or one that moved to
    // another project, keep the total and change this.
    function tally(pairs) {
      var c = {};
      pairs.forEach(function (p) { c[p[0]] = (c[p[0]] || 0) + p[1]; });
      return Object.keys(c).sort().filter(function (k) { return c[k]; }).map(function (k) { return k + '=' + c[k]; }).join(',');
    }
    // What the server counts: the rows shown, and a Park or a Back it took since (moved).
    var pairs = waiting.map(function (w) { return [w.project + '|' + w.mono, 1]; }), expect = waiting.length;
    var shownTally = tally(pairs);
    function counted(s) {
      var n = s.waiting;
      waitingTitle(n);
      if (n === expect && (!Array.isArray(s.projects) || tally(s.projects.map(function (p) {
        return [p.id + '|' + p.monogram, p.waiting];
      })) === shownTally)) {
        fresh.hidden = true;
        return;
      }
      if (calm() && performance.now() > AGAIN) return reloadNow();
      freshText.textContent = n > expect ? fmt(L.freshMore, {n: n - expect}) : L.freshChanged;
      fresh.hidden = false;
      stale();
    }
    // A Park or a Back the server took: the page expects the count without it (no bar), and reloads to show the
    // row in its new place unless that would lose typed text; then the status says so. A press from the list sends
    // the focus to the row that took its place (the next shown, else the one before).
    function moved(r, step, status, note, fromList) {
      expect += step;
      pairs.push([r.w.project + '|' + r.w.mono, step]);
      shownTally = tally(pairs);
      waitingTitle(expect);
      var all = shown(), i = all.indexOf(r), to = fromList ? all[i + 1] || all[i - 1] : null;
      if (!typed()) return reloadNow(to);
      status.textContent = note;
      if (to) to.go.focus();
    }
    var poll = setInterval(function () {
      fetch('state').then(function (r) {
        if (r.status === 503) return null;  // not counted yet: the next poll asks again
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).then(function (s) {
        if (!s || typeof s.waiting !== 'number') return;
        clocks.forEach(function (c) { c.t.textContent = ago(c.stamp); });
        counted(s);
      }).catch(function () { clearInterval(poll); });  // the page closed or the inbox stopped: the title stays
    }, 5000);

    // Moving the open item, and the narrow layouts: the drawer and the sheet keep the rest inert while open.
    // A closed group's rows (a project's To do, or Answered) are out of j and k and out of Focus too: both walk only
    // what the list shows.
    function shown() {
      return rows.filter(function (r) { return !r.li.hidden && !r.sec.el.hidden && !(r.grp && r.grp.ul.hidden); });
    }
    function select(r) {
      if (current) {
        current.go.removeAttribute('aria-current');
        current.li.classList.remove('on');
      }
      current = r || null;
      view.textContent = '';
      if (!current) view.append(place);
      else {
        if (current.grp && current.grp.ul.hidden) {  // back from a reload into a closed group: open its rows
          current.grp.open = true;
          paint(current.grp);
        }
        current.go.setAttribute('aria-current', 'true');
        current.li.classList.add('on');
        if (!current.view) current.view = detailOf(current);
        view.append(current.view);
        detail.scrollTop = 0;
      }
      bars();
    }
    function bars() {
      var all = shown(), i = all.indexOf(current);
      at.textContent = i < 0 ? '' : fmt(L.focusOf, {i: i + 1, n: all.length});
      prev.disabled = i <= 0;
      next.disabled = i < 0 || i >= all.length - 1;
    }
    function move(step) {
      if (has('side-open') || (pane.hidden && !has('focusing'))) return false;  // no list to move in
      var all = shown(), i = all.indexOf(current), to = i < 0 ? 0 : i + step;
      if (to < 0 || to >= all.length) return false;
      select(all[to]);
      if (has('focusing') || has('sheet')) current.heading.focus();
      else {
        current.go.focus();
        current.go.scrollIntoView({block: 'nearest'});
      }
      return true;
    }
    function openSheet() {
      body.classList.add('sheet');
      inert(true, detail);
      current.heading.focus();
    }
    function closeSheet() {
      if (!has('sheet')) return;
      body.classList.remove('sheet');
      inert(false);
      if (current) current.go.focus();
    }
    scrim.addEventListener('click', closeSheet);
    [narrow, drawer].forEach(function (q) { q.addEventListener('change', closeSheet); });
    close.addEventListener('click', closeSheet);
    function setFocus(on) {
      if (on && !shown().length) return;  // nothing shown to walk: every row waits behind a closed group, or none
      body.classList.toggle('focusing', on);
      focusButton.setAttribute('aria-pressed', String(on));
      if (on) {
        closeSide(false);
        closeSheet();
        layoutState();
        if (!current) select(shown()[0]);
        if (current) current.heading.focus();
      } else if (layoutState() || !current) focusButton.focus();  // back to the empty state: no row to land on
      else {
        current.go.focus();
        current.go.scrollIntoView({block: 'nearest'});
      }
      sideState();
      bars();
    }

    function section(title, n, note, cls, key) {
      var s = el('section', 'ib'), h = el('h2', 'ib-head' + (cls ? ' ' + cls : '')), ul = el('ul', 'ib-list');
      h.id = 'ib-head-' + sections.length;
      ul.setAttribute('aria-labelledby', h.id);
      h.append(document.createTextNode(title + ' '), el('span', 'n', String(n)));
      // The note sits apart by CSS only: the hidden comma reads "Parked 2, 1 with an agent waiting" aloud.
      if (note) h.append(el('span', 'visually-hidden', ', '), el('span', 'ib-note', note));
      s.append(h);
      pane.append(s);
      var sec ={el: s, head: h, ul: ul, key: key, rows: []};
      sections.push(sec);
      return sec;
    }
    function done(sec, empty) {  // a section's rows, or a line that says there are none
      sec.el.append(!sec.rows.length ? el('p', 'hint', empty) : sec.groups ? sec.box : sec.ul);
    }
    // A section's rows under one heading per project, by name (a run outside any project by its folder's name).
    // Waiting on you shows every group; To do shows each as one count row, closed until clicked, or filtered to.
    function grouped(sec, closed) {
      var by = {};
      sec.groups = [];
      sec.box = el('div', 'ib-groups');
      sec.rows.forEach(function (r) {
        var key = r.w.project || 'name:' + r.w.name, g = by[key];
        if (!g) {
          g = by[key] = {key: key, w: r.w, rows: [], open: !closed, box: el('div', 'ib-group'), ul: el('ul', 'ib-list')};
          sec.groups.push(g);
        }
        g.rows.push(r);
        g.ul.append(r.li);
        r.grp = g;
      });
      sec.groups.sort(function (a, b) { return a.w.name.toLowerCase() < b.w.name.toLowerCase() ? -1 : 1; });
      sec.groups.forEach(function (g) {
        var h = el('h3', 'ib-ghead' + (closed ? ' tog' : '')), m = mono(g.w);
        m.classList.add('sm');
        g.ul.id = 'ib-group-' + (count++);
        h.id = g.ul.id + '-h';
        g.ul.setAttribute('aria-labelledby', h.id);
        if (closed) {
          var n = g.rows.length;
          h.append(toggler(g, [m, el('span', 'nl', fmt(n === 1 ? L.moreOpenOne : L.moreOpen, {p: g.w.name, n: n}))]));
        } else h.append(m, el('span', 'nl', g.w.name), el('span', 'n', String(g.rows.length)));
        g.box.append(h, g.ul);
        sec.box.append(g.box);
        paint(g);
      });
    }
    function toggler(g, label) {  // a closed group's count row: a click shows or hides its rows
      var b = el('button', 'ib-gtoggle');
      b.type = 'button';
      b.setAttribute('aria-controls', g.ul.id);
      b.append.apply(b, label);
      b.addEventListener('click', function () {
        if (terms.length) return;  // a search holds every group with a match open
        g.open = g.ul.hidden;  // a project filter keeps its own project's group and Answered open (paint)
        paint(g);
        if (g.ul.hidden && current && current.grp === g) select(null);  // no open row out of sight; focus stays here
        bars();
      });
      g.toggle = b;
      return b;
    }
    // Every group behind a count row, the ones a reload keeps open or closed.
    function closable() {
      return [].concat.apply([], sections.map(function (x) { return x.groups || []; })).filter(function (g) { return g.toggle; });
    }
    // A project filter opens its own To do group, Answered and Parked, which then hold only that project's rows.
    function forced(g) {
      return terms.length > 0 || !!(filter && filter.project && (g.key === filter.project || g.key === 'answered'
        || g.key === 'parked'));
    }
    function paint(g) {  // a group: its rows open or closed, hidden when the filter leaves none of them
      var open = g.open || forced(g);
      g.ul.hidden = !open;
      if (g.toggle) g.toggle.setAttribute('aria-expanded', String(open));
      g.box.hidden = !g.rows.some(function (r) { return !r.li.hidden; });
    }
    function since(stamp) {  // short: the time today, the day and time before
      var d = new Date(stamp), today = d.toDateString() === new Date().toDateString();
      if (isNaN(d)) return '';
      try {
        return d.toLocaleString(root.lang, today ? {hour: '2-digit', minute: '2-digit'}
          : {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
      } catch (e) { return stamp; }
    }
    function mono(w) {
      var m = el('span', 'mono c' + (Number(w.color) % 6 || 0), w.mono);
      m.setAttribute('aria-hidden', 'true');
      return m;
    }
    function kindOf(o) {  // a glyph and a word, never colour alone
      var g = el('span', 'ib-glyph ' + o.cls, o.glyph);
      g.setAttribute('aria-hidden', 'true');
      return [g, el('span', 'ib-kind ' + o.cls, o.kind)];
    }
    // What the search reads of a row, folded once on the first query: the project, the title, the kind and the short
    // line, and what o.find() adds (a round's item titles, a todo item's body and messages).
    function fold(s) { return String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
    function hay(r) {
      if (r.hay === undefined) {
        r.hay = fold([r.w.name, r.title.textContent, r.o.kind, typeof r.o.sub === 'string' ? r.o.sub : '']
          .concat(r.o.find ? r.o.find() : []).filter(Boolean).join('\n'));
      }
      return r.hay;
    }
    // One row: a round's is a link to its page; any other row is a button that opens it in the pane beside the list.
    // o: kind, cls, glyph, title (text or inline nodes), sub (text or element), when, href, fill(box), find().
    function row(sec, w, o) {
      var li = el('li', 'ib-row'), go = el(o.href ? 'a' : 'button', 'ib-main'), t = el('span', 'ib-title');
      if (o.href) go.href = o.href; else go.type = 'button';
      if (Array.isArray(o.title)) inlines(o.title, t, true); else t.textContent = o.title;  // no link inside a button
      var sub = el('span', 'ib-sub');
      if (w.name) sub.append(el('span', 'ib-name', w.name));
      sub.append.apply(sub, kindOf(o));
      if (o.sub) sub.append(typeof o.sub === 'string' ? el('span', 'ib-text', o.sub) : o.sub);
      go.append(mono(w), t, whenOf(o), sub);
      li.append(go);
      var r = {li: li, go: go, w: w, o: o, title: t, sec: sec, projects: w.project ? [w.project] : []};
      go.addEventListener('click', function (e) {
        if (o.href) return;  // the round's own page, in this tab
        select(r);
        if (narrow.matches && !has('focusing')) openSheet();
        else if (e.detail === 0) r.heading.focus();  // Enter or Space: into the item; a mouse click stays on the list
      });
      sec.ul.append(li);
      sec.rows.push(r);
      rows.push(r);
      return r;
    }
    function detailOf(r) {
      var w = r.w, o = r.o, v = el('article', 'dv'), head = el('div', 'dh'), text = el('div', 'dhm'), meta = el('p', 'dhp');
      var h = el('h2', '', r.title.textContent), m = mono(w), box = el('div', 'ib-body md');
      h.id = 'ib-item-' + (count++);
      h.tabIndex = -1;
      v.setAttribute('aria-labelledby', h.id);
      m.classList.add('lg');
      if (w.name) meta.append(el('span', 'ib-name', w.name));
      meta.append.apply(meta, kindOf(o));
      if (o.when || o.stamp) meta.append(whenOf(o));
      text.append(meta, h);
      head.append(m, text);
      v.append(head, box);
      if (o.fill) o.fill(box);
      r.heading = h;
      return v;
    }
    // An open todo item's messages to the project's agent, and a box for a new one. The server checks that the
    // item (its ref, the first line as written) is still open, and writes a request file the session gets.
    function messages(project, name, t, box) {
      if (!t.ref) return;
      var wrap = el('div', 'ib-msg'), card = el('div', 'ib-card'), list = el('ul', 'ib-sent'), form = el('form', 'ib-form');
      var input = el('input'), send = el('button', 'primary', L.send), status = el('p', 'ib-msg-status');
      var label = el('label', 'ib-lab', fmt(L.msgTo, {p: name}));
      input.id = 'ib-msg-' + (count++);
      label.htmlFor = input.id;
      function show(r) {
        var li = el('li');
        li.append(el('span', 'ib-sent-text', r.text),
                  el('span', 'pill' + (r.done ? ' done' : ''), r.done ? L.msgDone : fmt(L.msgSent, {t: since(r.at)})));
        list.append(li);
        list.hidden = false;
      }
      list.hidden = true;
      (t.requests || []).forEach(show);
      input.type = 'text';
      input.maxLength = 1000;  // the server refuses longer
      input.placeholder = L.msgPlaceholder;
      send.type = 'submit';
      status.setAttribute('role', 'status');
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = input.value.trim();
        if (!text || send.disabled) return;
        send.disabled = true;
        status.textContent = '';
        fetch('request', {method: 'POST', headers: {'Content-Type': 'application/json'},
                          body: JSON.stringify({project: project, ref: t.ref, text: text})})
          .then(function (r) {
            if (r.ok) return r.json().then(function (j) {
              show({text: text, at: j.at, done: false});
              input.value = '';
              status.textContent = L.msgNote;
            });
            return r.text().then(function (m) { status.textContent = fmt(L.msgFailed, {m: m.trim()}); });
          }, function () { status.textContent = L.msgOffline; })
          .then(function () { send.disabled = false; });
      });
      form.append(input, send);
      card.append(label, form, el('p', 'ib-msg-hint', L.msgHint), status);
      wrap.append(card, list);
      box.append(wrap);
    }
    var Q = {kind: L.question, cls: 'k-q', glyph: '◆'}, W = {kind: L.waitingKind, cls: 'k-w', glyph: '●'};
    var T = {kind: L.todo, cls: 'k-t', glyph: '○'};
    function as(base, more) { return Object.assign({}, base, more); }
    // A row's time: how long ago it last changed, the exact time in the tooltip and datetime. Brought up to date by
    // each poll. A row with only text (`when`) keeps it.
    var clocks = [];
    function ago(stamp) {
      var m = Math.floor((Date.now() - new Date(stamp).getTime()) / 60000);
      return isNaN(m) ? '' : m < 1 ? L.justNow : fmt(L.ago, {t: lasted(stamp)});
    }
    function whenOf(o) {
      if (!o.stamp || isNaN(new Date(o.stamp))) return el('span', 'ib-when', o.when || '');
      var t = el('time', 'ib-when', ago(o.stamp));
      t.dateTime = o.stamp;
      try { t.title = new Date(o.stamp).toLocaleString(root.lang); } catch (e) { t.title = o.stamp; }
      clocks.push({t: t, stamp: o.stamp});
      return t;
    }
    // A live Claude Code session's row (hooks' sessions/ files): the user answers in the session itself. The server
    // sends `attach`, the session id's first 8 hex digits, for a background session only; the page never makes one.
    function live(w, note) {
      var cmd = w.attach ? 'claude attach ' + w.attach : '';
      return {title: L.session, sub: cmd || L.goTerminal, when: fmt(L.forTime, {t: lasted(w.since)}), fill: function (box) {
        note(box);
        if (cmd) box.append(el('p', '', L.attachNote), codeBlock({lang: 'bash', text: cmd}));
        else box.append(el('p', '', L.goTerminal));
      }};
    }
    // The whole item as plain text, what the page shows (not the Markdown source): code exactly as written.
    function inlineText(nodes) {
      return (nodes || []).map(function (n) {
        if (n.t === 'b') return inlineText(n.c);
        if (n.t === 'link') return inlineText(n.c) + (/^https?:\/\//i.test(String(n.href)) ? ' (' + n.href + ')' : '');
        return String(n.v || '');
      }).join('');
    }
    function blockText(list) {
      return (list || []).map(function (b) {
        if (b.t === 'code') return String(b.text);
        if (b.t === 'hr') return '---';
        if (b.t === 'quote' || b.t === 'alert') return blockText(b.c).replace(/^/gm, '> ');
        if (b.t === 'list') {
          return (b.items || []).map(function (it, i) {
            var mark = it.check === true ? '[x] ' : it.check === false ? '[ ] ' : '';
            return (b.ordered ? (i + 1) + '. ' : '- ') + mark + blockText(it.c).replace(/\n/g, '\n  ');
          }).join('\n');
        }
        if (b.t === 'table') {
          return [b.head || []].concat(b.rows || []).map(function (r) { return r.map(inlineText).join(' | '); }).join('\n');
        }
        return inlineText(b.c);
      }).filter(Boolean).join('\n\n');
    }
    function copyItem(t, box) {
      box.append(copier(function () {
        return [inlineText(t.title), blockText(t.body)].filter(Boolean).join('\n\n');
      }, L.copyText, 'ib-copytext', L.copyText));
    }
    // A todo chain's row: its first line, the hint when it waits, and how many of its steps are done. Opened: the
    // hint, the steps, Open round when it names a round open here, Later when it waits, its body (the waiting step
    // marked), Copy text and the message box. Its ref is where a message or Later goes: the waiting step's own.
    function stepsOf(t) { return t.steps && t.steps[1] ? fmt(L.stepsDone, {n: t.steps[0], m: t.steps[1]}) : ''; }
    function laterPill(r, done) {
      if (!r.parked) r.go.querySelector('.ib-sub').append(r.parked = el('span', 'pill'));
      r.parked.className = 'pill' + (done ? ' done' : '');
      r.parked.textContent = done ? L.laterDone : L.laterSent;
    }
    function todoRow(sec, who, project, t, base) {
      var r = row(sec, who, as(base, {title: t.title, sub: [t.waiting, stepsOf(t)].filter(Boolean).join(' · '),
        find: function () {
          return [blockText(t.body)].concat((t.requests || []).map(function (q) { return q.text; }));
        },
        fill: function (box) {
          if (t.waiting) {
            var hint = el('p', 'ib-hint');
            hint.append(el('strong', '', L.waitingOn), document.createTextNode(t.waiting));
            box.append(hint);
          }
          var acts = el('div', 'ib-acts'), status = el('p', 'ib-msg-status');
          if (stepsOf(t)) acts.append(el('span', 'ib-steps', stepsOf(t)));
          if (t.round) {
            var go = el('a', 'ib-go', L.openRound);
            go.href = t.round;
            acts.append(go);
          }
          if (t.park && t.ref) {  // the server takes Later only on a marked item's ref
            var park = el('button', '', L.later);
            park.type = 'button';
            status.setAttribute('role', 'status');
            park.addEventListener('click', function () {
              park.disabled = true;
              status.textContent = '';
              post('request', {kind: 'later', project: project, ref: t.ref}, status, L.laterFailed, function () {
                status.textContent = L.laterNote;
                laterPill(r, false);
              }).then(function () { park.disabled = false; });
            });
            acts.append(park);
          }
          if (acts.firstChild) box.append(acts, status);
          blocks(t.body, box);
          copyItem(t, box);
          messages(project, who.name, t, box);
        }}));
      var parked = (t.requests || []).filter(function (q) { return q.later; }).pop();
      if (parked) laterPill(r, parked.done);
      return r;
    }
    // A round's row: a link to its page, and Park (a parked one: Back) beside the link, never inside it. Opened, its
    // notes, Open round and the same button again, for Focus, which hides the list. A parked one says whether its
    // run still waits (Agent waiting) or has ended (Back then makes it a Run ended row).
    function roundRow(sec, w, isParked) {
      var buttons = [];
      function parkButton(fromList) {  // the server maps the numbers to the round; no path goes out
        var b = el('button', fromList ? 'ib-rowact' : '', isParked ? L.back : L.park);
        b.type = 'button';
        b.setAttribute('aria-label', fmt(isParked ? L.backOf : L.parkOf, {t: w.title}));
        b.addEventListener('click', function () {
          var status = fromList ? stateText : b.parentNode.nextSibling;  // the key bar's line, or the open view's
          buttons.forEach(function (x) { x.disabled = true; });
          status.textContent = '';
          post('park', {run: Number(w.group), round: Number(w.round), park: !isParked}, status, L.parkFailed,
            function () {
              moved(r, isParked ? 1 : -1, status, isParked ? L.backReload : L.parkReload, fromList);
              buttons.forEach(function (x) { x.hidden = true; });
            }).then(function () { buttons.forEach(function (x) { x.disabled = false; }); });
        });
        buttons.push(b);
        return b;
      }
      var run = 'run/' + Number(w.group) + '/', tab = w.tab && w.tab !== w.title ? w.tab : '';
      var aged = oldRound(w.opened);  // the round file's time; the row's clock stays on `since` (run start, or park)
      // The context rides on the short line, so the search and the link's name get it once.
      var r = row(sec, w, as(isParked ? (w.orphan ? {kind: L.parkedKind, cls: 'k-t', glyph: '◇'}
        : {kind: L.agentWaiting, cls: 'k-w', glyph: '●'}) : w.held ? {kind: L.heldKind, cls: 'k-t', glyph: '◇'}
        : w.orphan ? {kind: L.orphanKind, cls: 'k-q', glyph: '◆'} : Q, {title: w.title,
        sub: [tab, w.context].filter(Boolean).join(' · '),
        find: function () { return w.items || []; }, stamp: w.since, href: run, fill: function (box) {
          if (tab) box.append(el('p', '', tab));
          if (w.context) box.append(el('p', 'context', w.context));
          if (isParked) box.append(el('p', 'ib-vuln-note', w.orphan ? L.parkedNote : L.parkedLiveNote));
          else if (w.held) box.append(el('p', 'ib-vuln-note', L.held));
          else if (w.orphan) box.append(el('p', 'ib-vuln-note', L.orphan));
          if (aged) box.append(el('p', 'ib-vuln-note', fmt(L.oldOpenFor, {t: lasted(w.opened)}) + ' ' + L.oldNote));
          var acts = el('div', 'ib-acts'), go = el('a', 'ib-go', L.openRound), status = el('p', 'ib-msg-status');
          go.href = run;
          status.setAttribute('role', 'status');
          acts.append(go);
          if (w.project) acts.append(parkButton(false));
          box.append(acts, status);
        }}));
      if (aged) r.go.querySelector('.ib-sub').append(el('span', 'pill old', L.oldRound));
      if (w.project) {  // the server keeps a park only for a round inside a known project
        r.li.classList.add('has-act');
        r.li.append(parkButton(true));
      }
    }
    var s = section(L.waitingHead, waiting.length, '', 'wait', 'waiting'), waitSec = s;
    waiting.forEach(function (w) {
      if (w.kind === 'round') roundRow(s, w, false);
      else if (w.kind === 'session') {
        row(s, w, as(W, as(live(w, function (box) { box.append(el('p', '', L.sessionNote)); }), {stamp: w.since})));
      } else todoRow(s, w, w.project, w, as(W, {stamp: w.since}));
    });
    grouped(s, false);
    // The one summary line, under the header's title at every width.
    // A run outside any project is in no project: it counts in n, not in p.
    var real = s.groups.filter(function (g) { return g.w.project; }).length;
    var summary = !waiting.length ? '' : !real ? fmt(L.waitSummaryNone, {n: waiting.length})
      : fmt(L.waitSummary, {n: waiting.length, p: real === 1 ? L.projectsOne : fmt(L.projectsN, {n: real})});
    // Oldest or newest first within each project, kept in prefs.json like the density. The rows are grouped by
    // project now, so a stored "project" (By project, before) reads as oldest first.
    var sortBar = el('div', 'ib-sort'), sortButtons = [], sorts = SORTS.filter(function (k) { return k !== 'project'; });
    sortBar.setAttribute('role', 'group');
    sortBar.setAttribute('aria-label', L.sortBy);
    sortBar.hidden = waiting.length < 2;
    sorts.forEach(function (key) {
      var b = el('button', '', {oldest: L.oldestFirst, newest: L.newestFirst}[key]);
      b.type = 'button';
      b.addEventListener('click', function () {
        var was = sortKey;
        function revert(why) {  // not kept: back to the order that was, and say so
          applySort(was);
          sortNote.textContent = why;
          sortNote.hidden = false;
        }
        applySort(key);
        sortNote.hidden = true;
        // The sort alone goes to the inbox: the stored theme, palette and density stay as they are.
        fetch('prefs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sort: key})})
          .then(function (r) {
            if (r.ok) return;
            return r.text().then(function (m) { revert(fmt(L.apFailed, {m: m.split('\n')[0] || r.status})); });
          }, function () { revert(L.apOffline); });
      });
      sortButtons.push(b);
      sortBar.append(b);
    });
    var sortNote = el('p', 'hint');
    sortNote.hidden = true;
    sortNote.setAttribute('role', 'status');
    waitSec.el.append(sortBar, sortNote);
    function applySort(key) {
      sortKey = sorts.indexOf(key) >= 0 ? key : sorts[0];
      var stampOf = function (r) { return r.w.since || ''; };
      waitSec.rows.sort(function (a, b) {
        var c = stampOf(a) < stampOf(b) ? -1 : stampOf(a) > stampOf(b) ? 1 : 0;
        return sortKey === 'newest' ? -c : c;
      });
      // The groups follow their first row in this order, so the oldest (or newest) row is always at the top.
      waitSec.groups = [];
      waitSec.rows.forEach(function (r) {
        if (waitSec.groups.indexOf(r.grp) < 0) {
          waitSec.groups.push(r.grp);
          waitSec.box.append(r.grp.box);
        }
        r.grp.ul.append(r.li);
      });
      ordered();
      sortButtons.forEach(function (b, i) { b.setAttribute('aria-pressed', String(sorts[i] === sortKey)); });
      bars();
    }
    function ordered() {  // j and k follow what is on screen: a grouped section's rows group by group
      rows = [].concat.apply([], sections.map(function (x) {
        return !x.groups ? x.rows : [].concat.apply([], x.groups.map(function (g) {
          return x.rows.filter(function (r) { return r.grp === g; });
        }));
      }));
    }
    applySort(sortKey);
    done(s, L.nothingWaiting);
    if (sent.length) {
      s = section(L.sentHead, sent.length, '', 'tog', 'waiting');
      sent.forEach(function (w) {
        // "working" only when the project's session said so after the answer (its hooks); otherwise "sent".
        var busy = w.state === 'working', pill = el('span', 'state ' + (busy ? 'working' : 'sent'),
                                                    busy ? L.state_working : L.state_sent);
        row(s, w, {kind: L.answered, cls: 'k-ok', glyph: '✓', title: w.title, sub: pill, when: since(w.saved),
          fill: function (box) { box.append(pill.cloneNode(true)); }});
      });
      closedGroup(s, 'answered');
    }
    // Closed until clicked, as a To do group: the whole section is one group, its heading the count row.
    function closedGroup(sec, key) {
      var g = {key: key, rows: sec.rows, open: false, ul: sec.ul, box: el('div', 'ib-groups')};
      sec.ul.id = 'ib-group-' + (count++);
      sec.rows.forEach(function (r) { r.grp = g; });
      sec.groups = [g];
      sec.box = g.box;
      sec.box.append(sec.ul);
      sec.head.append(toggler(g, Array.prototype.slice.call(sec.head.childNodes)));
      paint(g);
      done(sec);
    }
    // Parked rounds: counted nowhere, kept until Back; the heading says how many an agent still waits on.
    var parkedSec = null;
    if (parked.length) {
      var agentsWait = parked.filter(function (w) { return !w.orphan; }).length;
      s = parkedSec = section(L.parkedHead, parked.length, agentsWait ? fmt(L.parkedLive, {n: agentsWait}) : '',
                              'tog', 'waiting');
      parked.forEach(function (w) { roundRow(s, w, true); });
      closedGroup(s, 'parked');
    }
    var idleSec = null;
    if (idle.length) {
      // Live sessions idle a while (a turn ended, or none began): shown dimmer, and never counted (round-12).
      s = idleSec = section(L.idleHead, idle.length, L.idleNote, '', 'waiting');
      s.el.classList.add('idle');
      idle.forEach(function (w) {
        row(s, w, as({kind: L.idleKind, cls: 'k-t', glyph: '◌'}, live(w, function (box) {
          box.append(el('p', '', fmt(L.idleText, {t: lasted(w.since)})));
        })));
      });
      done(s);
    }
    var todoSec = s = section(L.todoHead, projects.reduce(function (n, p) { return n + p.todos.length; }, 0),
                              L.byProject, '', 'todo');
    projects.forEach(function (p) {
      if (p.error) s.el.append(el('p', 'ib-error', p.name + ': ' + fmt(L.notRead, {f: p.file, r: p.error})));
      p.todos.forEach(function (t) { todoRow(s, p, p.id, t, T); });
    });
    grouped(s, true);
    done(s, L.nothingOpen);
    // Later on a todo item: a request the server words itself.
    function post(path, body, status, failed, done) {
      return fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
        .then(function (r) {
          if (r.ok) return r.json().then(done);
          return r.text().then(function (m) { status.textContent = fmt(failed, {m: m.trim()}); });
        }, function () { status.textContent = fmt(failed, {m: L.notAnswering}); });
    }
    ordered();
    // The sidebar: the views, then every project with a row here; each filters the list, a second click shows all.
    var todos = projects.reduce(function (n, p) { return n + p.todos.length; }, 0), named = {}, order = [];
    rows.forEach(function (r) {
      if (!r.w.project) return;
      var p = named[r.w.project] || (named[r.w.project] = {id: r.w.project, w: r.w, n: 0, hot: false});
      if (order.indexOf(p) < 0) order.push(p);
      var asks = r.sec === waitSec;  // Answered, Parked and Idle share its view, and count nowhere
      if (asks || r.sec.key === 'todo') p.n += 1;
      p.hot = p.hot || asks;
    });
    order.sort(function (a, b) { return a.w.name.toLowerCase() < b.w.name.toLowerCase() ? -1 : 1; });
    function group(title, items) {
      var h = el('h2', '', title), ul = el('ul', 'navs');
      h.id = 'ib-nav-' + navs.length;
      ul.setAttribute('aria-labelledby', h.id);
      items.forEach(function (it) {
        var b = el('button', 'nav'), li = el('li');
        b.type = 'button';
        b.key = it.key;
        b.filter = it.filter;
        b.setAttribute('aria-pressed', 'false');
        b.append(it.lead, el('span', 'nl', it.name));
        if (it.n) b.append(' ', el('span', 'ct' + (it.hot ? ' hot' : ''), String(it.n)));
        if (it.hot && it.filter.project) b.append(el('span', 'visually-hidden', ', ' + L.waitsHere));  // not the ring alone
        b.addEventListener('click', function () {
          setFilter(filter && filter.key === it.key && !terms.length ? null : it.filter);
          if (!has('side-open')) return;
          closeSide(false);
          var first = shown()[0];
          (first && !pane.hidden ? first.go : sideButton).focus();
        });
        li.append(b);
        if (it.filter.project) {  // the project's past decisions, read only, under the inbox (in this tab)
          var sub = el('ul', 'navs outline'), past = el('a', 'nav'), pli = el('li');
          past.href = 'history/' + encodeURIComponent(it.filter.project) + '/';
          past.append(el('span', 'nl', L.historyTitle));
          past.setAttribute('aria-label', fmt(L.pastOf, {p: it.name}));
          pli.append(past);
          sub.append(pli);
          li.append(sub);
        }
        ul.append(li);
        navs.push(b);
      });
      nav.append(h, ul);
    }
    var VIEWS = [
      {key: 'waiting', name: L.waitingHead, n: waiting.length, hot: waiting.length > 0,
       lead: icon(['M3 13h5l1.5 3h5L16 13h5', 'M5.5 5h13L21 13v6H3v-6z'])},
      {key: 'todo', name: L.todoHead, n: todos, lead: icon(['M9 6h11', 'M9 12h11', 'M9 18h11', 'M4.5 6h.01', 'M4.5 12h.01', 'M4.5 18h.01'])}];
    group(L.inbox, VIEWS.map(function (v) {
      return Object.assign(v, {filter: {key: v.key, view: v.key, name: v.name}});
    }));
    if (order.length) {
      group(L.projectsHead, order.map(function (p) {
        var m = mono(p.w);
        m.classList.add('sm');
        return {key: 'p:' + p.id, name: p.w.name, n: p.n, hot: p.hot, lead: m,
                filter: {key: 'p:' + p.id, project: p.id, name: p.w.name}};
      }));
    }

    // Nothing waiting and no filter: the calm empty state instead of the list (Sally's "inbox zero").
    var zero = el('div', 'ib-zero'), zbox = el('div', 'zbox'), mark = el('div', 'zmark', '✓'), zs = el('div', 'zs');
    mark.setAttribute('aria-hidden', 'true');
    zbox.append(mark, el('h2', '', L.zeroTitle), el('p', '', L.zeroText + (agents === 1 ? L.zeroWorkingOne
      : agents ? fmt(L.zeroWorking, {n: agents}) : '')), zs);
    // Idle and Parked have no view of their own: they sit under Waiting on you, as Answered does, so their buttons
    // land on their rows (Parked opened).
    [[todos, L.todoN, VIEWS[1]], [idle.length, L.idleN, VIEWS[0], idleSec],
      [parked.length, L.parkedN, VIEWS[0], parkedSec]].forEach(function (z) {
      if (!z[0]) return;
      var b = el('button', '', fmt(z[1], {n: z[0]}));
      b.type = 'button';
      b.addEventListener('click', function () {
        if (z[3] && z[3].groups) {
          z[3].groups[0].open = true;
          paint(z[3].groups[0]);
        }
        setFilter(z[2].filter);
        var first = z[3] ? z[3].rows[0].go : shown().length ? shown()[0].go
          : (todoSec.groups || []).map(function (g) { return g.toggle; })[0];  // To do: its first count row
        if (first) {
          first.focus();
          first.scrollIntoView({block: 'nearest'});
        }
      });
      zs.append(b);
    });
    if (sent.length) zbox.append(el('p', 'zfoot', fmt(L.lastAnswer, {t: since(sent[0].saved)})));
    zero.append(zbox);
    layout.append(zero);
    function layoutState() {
      var z = !filter && !terms.length && !waiting.length && !has('focusing');
      zero.hidden = !z;
      pane.hidden = detail.hidden = z;
      return z;
    }
    var projectCount = order.length === 1 ? L.projectsOne : fmt(L.projectsN, {n: order.length});
    function setFilter(f) {  // a view or project picked by hand: it ends a search
      filter = f || null;
      if (terms.length) {
        box.value = '';
        terms = [];
      }
      reflow();
    }
    function search() {
      terms = fold(box.value).split(/\s+/).filter(Boolean);
      reflow();
    }
    // The rows, groups and sections for the filter, or for the search, which looks past the filter at every view.
    function reflow() {
      rows.forEach(function (r) {
        r.li.hidden = terms.length ? !terms.every(function (t) { return hay(r).indexOf(t) >= 0; })
          : !!(filter && filter.project && r.projects.indexOf(filter.project) < 0);
      });
      sections.forEach(function (sec) { (sec.groups || []).forEach(paint); });
      sections.forEach(function (sec) {
        var any = sec.rows.some(function (r) { return !r.li.hidden; });
        sec.el.hidden = terms.length ? !any : !!filter && (filter.view ? sec.key !== filter.view : !any);
      });
      var n = rows.filter(function (r) { return !r.li.hidden; }).length;
      navs.forEach(function (b) { b.setAttribute('aria-pressed', String(!!filter && !terms.length && b.key === filter.key)); });
      allButton.hidden = !filter || terms.length > 0;
      found.textContent = !terms.length ? '' : !n ? L.matchNone : n === 1 ? L.matchOne : fmt(L.matchN, {n: n});
      clear.hidden = box.value === '';
      none.hidden = !terms.length || n > 0;
      none.textContent = none.hidden ? '' : fmt(L.noMatchText, {q: box.value.trim()});
      byId('intro').textContent = terms.length ? L.searchResults : filter ? fmt(L.showing, {f: filter.name})
        : summary || L.nothingWaitingShort + ' · ' + projectCount;
      var z = layoutState();
      if (current && shown().indexOf(current) < 0) select(null);
      if (!current && !z && !narrow.matches) select(shown()[0]);
      if (!current) select(null);
      bars();
    }
    allButton.addEventListener('click', function () {
      setFilter(null);
      sideButton.focus();
    });
    setFilter(null);
    sideState();
    // Back from our own reload: the filter, the open row and the scroll it had (once; a late visit starts fresh).
    var back = null;
    try {
      back = JSON.parse(sessionStorage.getItem(KEPT) || 'null');
      sessionStorage.removeItem(KEPT);
    } catch (e) { /* none kept */ }
    if (back && Date.now() - back.at < 60000) {
      closable().forEach(function (g) {  // the To do groups and Answered, as they were
        g.open = Array.isArray(back.open) && back.open.indexOf(g.key) >= 0;
        paint(g);
      });
      var navHit = navs.filter(function (b) { return b.key === back.filter; })[0];
      if (navHit) setFilter(navHit.filter);
      if (typeof back.query === 'string' && back.query.trim()) {
        box.value = back.query.slice(0, 200);
        search();
      }
      var same = rows.filter(function (r) { return !r.li.hidden && rowKey(r) === back.row; })[0];
      if (same) {
        select(same);
        detail.scrollTop = Number(back.detail) || 0;
      }
      pane.scrollTop = Number(back.scroll) || 0;
      var to = back.focus && shown().filter(function (r) { return rowKey(r) === back.focus; })[0];
      if (to) to.go.focus();  // after a Park or a Back from the list: the row that took its place
    }

    // Keys: j and k (or the arrows on a row) move between rows; Enter opens; f is Focus; Esc closes; ? lists them.
    var help = byId('help');
    [[['j', 'k'], L.kRows], [['↓', '↑'], L.kRows], [['Enter'], L.kOpen], [['f'], L.kFocus], [['/'], L.kSearch],
      [[mac ? '⌘' : 'Ctrl', 'K'], L.kSearch], [['Esc'], L.kBack], [shortcut, L.kTheme], [['?'], L.kHelp]].forEach(function (r) {
      var dt = el('dt');
      r[0].forEach(function (k, i) { if (i) dt.append(' '); dt.append(el('kbd', '', k)); });
      byId('help-keys').append(dt, el('dd', '', r[1]));
    });
    byId('help-title').textContent = L.keys;
    byId('help-close').textContent = L.close;
    keysButton.addEventListener('click', function () { help.showModal(); });
    byId('help-close').addEventListener('click', function () { help.close(); });
    // The search: / (outside a field) and Cmd+K (Ctrl+K elsewhere; from a field too, a chord types nothing) focus the
    // field. Esc empties it and goes back to the list; Enter or the down arrow go to the
    // first result, where j and k, Enter and the arrows walk the results.
    function toList() {
      var r = current && shown().indexOf(current) >= 0 ? current : shown()[0];
      if (!r) return false;
      if (r !== current) select(r);
      r.go.focus();
      r.go.scrollIntoView({block: 'nearest'});
      return true;
    }
    form.addEventListener('submit', function (e) { e.preventDefault(); });
    box.addEventListener('input', function () {
      if (has('focusing')) {  // a click into the field in Focus: the search shows a list, so Focus ends
        setFocus(false);
        box.focus();
      }
      search();
    });
    clear.addEventListener('click', function () {
      box.value = '';
      search();
      box.focus();
    });
    box.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        box.value = '';
        search();
        if (!toList()) sideButton.focus();
      } else if ((e.key === 'Enter' || e.key === 'ArrowDown') && toList()) e.preventDefault();
    });
    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || help.open || picker.open || has('sheet') || has('side-open')) return;
      // The slash may need Alt or Shift on some layouts (Hungarian among them), so only Ctrl and Cmd rule it out.
      var chord = (mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey) && !e.shiftKey && !e.altKey;
      var k = String(e.key).toLowerCase();
      if (!((chord && k === 'k') || (k === '/'&& !e.ctrlKey && !e.metaKey && !typing(e.target)))) return;
      e.preventDefault();
      if (has('focusing')) setFocus(false);
      box.focus();
      box.select();
    });
    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || help.open || picker.open) return;
      if (e.key === 'Escape') {  // also from the message box: closing keeps what was typed
        if (has('side-open')) closeSide(true);
        else if (has('sheet')) closeSheet();
        else if (has('focusing')) setFocus(false);
        else return;
        e.preventDefault();
        return;
      }
      if (typing(e.target)) return;
      if (e.key === '?') {
        e.preventDefault();
        help.showModal();
        return;
      }
      if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        setFocus(!has('focusing'));
        return;
      }
      var onRow = rows.some(function (r) { return r.go === document.activeElement; });
      var arrow = e.key === 'ArrowDown' || e.key === 'ArrowUp';
      if ((e.key !== 'j' && e.key !== 'k' && !arrow) || (arrow && !onRow)) return;
      if (move(e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1)) e.preventDefault();
    });
  }
  if (data.mode === 'inbox') {
    inboxPage();
    return;
  }

