(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var MAX_TEXT = 20000;  // the server refuses longer text
  var MAX_MARKS = 50;
  var MAX_LINE_NOTES = 50, MAX_QUOTE = 300;
  var OTHER = '_other', SKIP = '_skip';  // option ids never start with "_"
  var VIEWPORTS = [1280, 768, 390];
  var LABELS = {
    en: {done: 'Done', doneRounds: 'Done ({n} rounds)', acceptShort: 'Accept all', acceptRest: ' recommendations',
      undo: 'Undo accept all', recommended: 'Recommended', cost: 'Cost',
      comment: 'Comment (optional)', many: 'Select all that apply.', other: 'Other', otherText: 'Other: your answer',
      skip: 'Skip', anything: 'Anything else', notSaved: 'Not saved yet', saving: 'Saving…', saved: 'Saved {t}',
      resumed: 'Resumed from your saved answers', tooLarge: 'Could not save: the text is too long.',
      couldNot: 'Could not save ({s}).', bad: 'Could not save: {m}', offline: 'Could not save: Signoff is not answering. Check that it is still running.',
      already: 'This round is already done.', doneBanner: 'Done. Your answers are saved and the agent has them; you can close this tab.',
      readonly: 'This round is done; its answers are shown read only.', rounds: 'Rounds',
      keys: 'Keyboard shortcuts', close: 'Close', kPick: 'pick option 1 to 9 of the current item (Other and Skip come last)',
      kMove: 'next or previous item', kTabs: 'on the rounds: the previous or next round', kMark: 'on a mark: move it; with Shift, resize it',
      kHelp: 'show this list',
      stale: 'Changed in another tab or window, so this tab can no longer save. Copy any text you have not saved yet, then reload to see the latest answers.',
      acceptRound: 'Accept this round', accepted: 'accepted', acceptedNote: 'Accepted. If you change anything, accept it again.',
      changedNote: 'Changed since you accepted it: accept it again.', progress: '{n}/{t} accepted',
      missingOne: 'Round {r} not accepted yet',
      missingMany: '{n} rounds not accepted yet; next: {r}', summary: 'Summary',
      summaryIntro: 'Every answer in one list. Accept each round, then press Done.',
      differs: 'not the recommendation', otherBadge: 'Other', skipped: 'Skipped', marksBadge: '{n} marks', markBadge: '1 mark',
      commentBadge: 'comment', nothing: 'nothing chosen', notAccepted: 'not accepted yet',
      show: 'Show', show_all: 'All', show_comment: 'With comment', show_differs: 'Not the recommendation',
      noneShown: 'Nothing to show.', more: 'Full text', less: 'Show less',
      privateLabel: 'Private', sensTitle: 'May hold personal data.',
      sens: ' The agent marked this item Private. Untick it if your text can be shared.',
      lockNote: 'Private: your text still goes to the agent in the answers file, and the agent is asked not to repeat it in commits, memory, the cloud or a decision log. The Markdown copy and the Summary hide it. This is a request to the agent, not encryption.',
      privateBadge: 'private', privateNote: 'Private comment, hidden here.',
      view: 'View', cards: 'Cards', table: 'Table', criterion: 'Criterion',
      scrollHint: 'Scroll the table sideways. The criteria stay in place.',
      legend: '▲ better · ● middling · ▼ worse', tone_good: 'better', tone_mid: 'middling', tone_bad: 'worse',
      kAccept: 'accept the open round (Cmd+Enter on a Mac); with a single round, Done',
      light: 'Light', dark: 'Dark', theme: 'Theme', width: 'Width', board: 'Live mockup',
      appearance: 'Appearance', mode: 'Mode', palette: 'Palette', system: 'System', keep: 'Keep', cancel: 'Cancel',
      apPreview: 'preview', apKeep: 'keep', apCancel: 'cancel', kTheme: 'colours: palette, light or dark',
      density: 'Density', comfortable: 'Comfortable', compact: 'Compact',
      apOnly: 'This tab only: choose it in the inbox to open every Signoff page in it.',
      apFailed: 'Not kept: {m}', apOffline: 'Not kept: the inbox is not answering.', ownTheme: 'Signoff\'s own colours',
      attachMissing: 'Attachment not shown: {r}', lines: '{n} lines', line: '1 line', attachText: 'Attachment: {r}',
      missing: 'Picture not shown: {r}', full: 'Open full size', larger: 'Larger', smaller: 'Smaller',
      picture: 'picture {n}',
      addMark: 'Add a mark to picture {n}',
      marks: 'Your marks', markHint: 'Draw a rectangle on a picture to point at something, or use the Add a mark button.',
      mark: 'Mark {n}', markOn: 'on picture {p}', markHandle: 'Mark {n} on picture {p}. Arrow keys move it, Shift and arrow keys resize it.',
      markComment: 'Comment on mark {n}', remove: 'Remove', noMarks: 'No marks.',
      historyTitle: 'Past decisions', historyIntro: 'Every round under this project, newest first. Pick one to see it as it was answered.',
      historyNotice: 'Read only. Close it with Ctrl+C in the terminal; it also stops after {m} idle minutes.',
      historyNoticeNever: 'Read only. Close it with Ctrl+C in the terminal.',
      historyGone: 'Signoff has stopped, so pictures no longer load. Ask your agent to open past decisions again.',
      search: 'Search', from: 'From', fromHint: 'YYYY-MM-DD', count: '{n} of {t} rounds', none: 'No round matches.',
      st_done: 'done', st_open: 'open', st_new: 'not answered', st_error: 'cannot be read',
      outcome: 'Outcome', logged: 'logged in {log}, {date}', warning: 'Warning', error: 'This file cannot be read.',
      pick: 'Pick a round from the list.',
      inbox: 'Inbox', held: 'This round is open in another Signoff run, so it is shown read only here. Answer it there.',
      orphan: 'The Signoff run that asked this has ended; your answer goes to the project\'s sessions.',
      orphanKind: 'Run ended',
      doneOrphan: 'Done. Your answers are saved. The project\'s open sessions, or the next one to start, get a note that the answers are in, not the answers themselves; the agent reads them once you say so. You can close this tab.',
      waitingHead: 'Waiting on you', oldestFirst: 'Oldest first', newestFirst: 'Newest first',
      waitSummary: '{n} waiting on you in {p}', waitSummaryNone: '{n} waiting on you', moreOpen: '{p}: {n} more open', moreOpenOne: '{p}: 1 more open',
      stepsDone: '{n} of {m} steps done', later: 'Later', laterSent: 'Later: sent', laterDone: 'Later: done',
      laterNote: 'Sent. The agent is asked to take the waiting marker off and keep the item open.',
      laterFailed: 'Not sent: {m}',
      sortBy: 'Sort by', ago: '{t} ago', justNow: 'just now', freshMore: '{n} new waiting on you',
      freshChanged: 'The list changed', copyText: 'Copy text', copyCode: 'Copy this code',
      relDelete: 'Relative path: check the folder before running', question: 'Question', heldKind: 'Open elsewhere',
      waitingKind: 'Waiting on you', waitingOn: 'Waiting on you:', nothingWaiting: 'Nothing is waiting on you.',
      sentHead: 'Answered', answered: 'Answered', state_sent: 'sent', state_working: 'working',
      session: 'Claude Code session', forTime: 'for {t}', minutes: '{n} min', underMinute: 'under a minute', hours: '{n} h',
      days: '{n} days', goTerminal: 'Go to its terminal',
      sessionNote: 'This session waits on you: for a permission or an answer to its question. Answer it there; the inbox does not answer for you.',
      attachNote: 'It runs in the background. Attach to it from a terminal:',
      idleHead: 'Idle', idleKind: 'Idle', idleNote: 'not counted', idleN: 'Idle ({n})',
      idleText: 'Idle for {t}; it waits for your next prompt. An idle session does not count as waiting on you.',
      todoHead: 'To do', todo: 'To do', byProject: 'by project', nothingOpen: 'Nothing open.', notRead: '{f} not read: {r}',
      side: 'Views and projects', projectsHead: 'Projects', sidebar: 'Sidebar', showAll: 'Show all', focus: 'Focus',
      nothingWaitingShort: 'Nothing waiting', projectsN: '{n} projects', projectsOne: '1 project',
      showing: 'Showing {f}', detail: 'Open item', pickRow: 'Pick a row to see it here.', openRound: 'Open round',
      focusOf: '{i} of {n}', prev: 'Previous', next: 'Next', leaveFocus: 'Leave Focus',
      upToDate: 'Up to date', changed: 'Changed since this page loaded', reload: 'Reload', waitsHere: 'waiting on you',
      workingN: '{n} agents working', workingOne: '1 agent working', kMoveShort: 'move', kOpenShort: 'open',
      kFocusShort: 'focus', kKeysShort: 'keys', kFocus: 'Focus: one item at a time',
      kBack: 'close the open item, the sidebar or Focus; or this list',
      findLabel: 'Search the inbox', findPlaceholder: 'Search', clearSearch: 'Clear', matchNone: 'No matches',
      matchOne: '1 match', matchN: '{n} matches', searchResults: 'Search results', noMatchText: 'Nothing matches "{q}".',
      kSearch: 'search every row; Esc in the field clears it and goes back to the list', kSearchShort: 'search',
      zeroTitle: 'Nothing waits on you', zeroText: 'Every question is answered and no todo item needs you.',
      zeroWorking: ' The agents of {n} projects are working on what you sent.',
      zeroWorkingOne: ' One project\'s agent is working on what you sent.', todoN: 'To do ({n})',
      lastAnswer: 'Last answer sent {t}',
      msgTo: 'Message {p}\'s agent', msgHint: 'Its open session gets it now; with none open, the next one does.',
      copy: 'Copy', copied: 'Copied', copyFailed: 'Copy failed: select the text', copyLabel: 'Copy the {l} block',
      taskDone: 'done', taskOpen: 'open', alert_note: 'Note', alert_tip: 'Tip', alert_important: 'Important',
      alert_warning: 'Warning', alert_caution: 'Caution',
      kRows: 'next or previous row', kOpen: 'open the round, or show the todo item',
      msgPlaceholder: 'A message for this project\'s agent',
      send: 'Send', msgSent: 'sent {t}', msgDone: 'done', msgNote: 'Sent. The project\'s open session gets it now; with none open, the next one gets it when it starts.',
      msgFailed: 'Not sent: {m}', msgOffline: 'Not sent: the inbox is not answering.',
      notAnswering: 'the inbox is not answering',
      agentRecommends: 'Agent recommends', outline: 'In this round', itemOf: 'Item {i} of {n}', roundOf: 'Round {i} of {n}',
      answersN: '{n} answers', signNote: 'Done sends every answer to the agent.', stChanged: 'changed',
      kItemShort: 'item', kOptionShort: 'option', kLeave: 'leave Focus or close the sidebar; or this list',
      opened: 'Opened {d} · {t} ago', openedNow: 'Opened {d} · just now', oldRound: 'Open 2+ days',
      oldNote: 'Its questions may be out of date: if in doubt, ask the agent before you answer.',
      oldOpenFor: 'This round has been open for {t}.', codeLang: 'code',
      blocking: 'Agent waits on this', blockingShort: 'agent waits', show_blocking: 'Agent waits',
      numValue: '{t}, value',
      stakes_high: 'High stakes', stakes_medium: 'Medium stakes', stakes_low: 'Low stakes', highShort: 'high stakes',
      irreversible: 'Cannot be undone', undoShort: 'irreversible', settledEarlier: 'Settled earlier',
      change: 'Change', fold: 'Hide options',
      lnAdd: 'added', lnDel: 'removed', lnOld: 'Old line', lnNew: 'New line', lnNum: 'Line', lnSign: 'Change', lnText: 'Text',
      lnNote: 'Note on line {n} of {f}', lnFull: 'At most 50 line notes on an item.', lnStale: 'This file changed since. Line {n} said:',
      lnHint: 'Press a line number to add a note.'},
    hu: {done: 'Kész', doneRounds: 'Kész ({n} kör)', acceptShort: 'Javaslatok elfogadása', acceptRest: '',
      undo: 'Elfogadás visszavonása', recommended: 'Javasolt', cost: 'Költség',
      comment: 'Megjegyzés (nem kötelező)', many: 'Több is választható.', other: 'Egyéb', otherText: 'Egyéb: a te válaszod',
      skip: 'Kihagyom', anything: 'Bármi más', notSaved: 'Még nincs mentve', saving: 'Mentés…', saved: 'Mentve: {t}',
      resumed: 'Folytatás a mentett válaszokból', tooLarge: 'Nem sikerült menteni: túl hosszú a szöveg.',
      couldNot: 'Nem sikerült menteni ({s}).', bad: 'Nem sikerült menteni: {m}', offline: 'Nem sikerült menteni. Fut még a Signoff?',
      already: 'Ez a kör már lezárult.', doneBanner: 'Kész. A válaszaid mentve vannak, és az ágens megkapta őket; ezt a lapot bezárhatod.',
      readonly: 'Ez a kör lezárult; a válaszai csak olvashatók.', rounds: 'Körök',
      keys: 'Billentyűparancsok', close: 'Bezárás', kPick: 'az aktuális tétel 1–9. lehetősége (az Egyéb és a Kihagyom a végén)',
      kMove: 'következő vagy előző tétel', kTabs: 'a körökön: előző vagy következő kör', kMark: 'jelölésen: mozgatás; Shifttel átméretezés',
      kHelp: 'a lista megjelenítése',
      stale: 'Egy másik lapon vagy ablakban módosult. Töltsd újra az oldalt a legfrissebb válaszokért; ez a lap már nem tud menteni. Az újratöltés előtt másold ki a még nem mentett szövegedet.',
      acceptRound: 'Kör elfogadása', accepted: 'elfogadva', acceptedNote: 'Elfogadva. Ha bármit módosítasz, újra el kell fogadni.',
      changedNote: 'Az elfogadás óta módosult: fogadd el újra.', progress: '{n}/{t} elfogadva',
      missingOne: 'Még nincs elfogadva: {r}',
      missingMany: '{n} kör még nincs elfogadva; a következő: {r}', summary: 'Összegzés',
      summaryIntro: 'Minden válasz egy helyen. Fogadj el minden kört, aztán nyomd meg a Kész gombot.',
      differs: 'eltér a javaslattól', otherBadge: 'Egyéb', skipped: 'Kihagyva', marksBadge: '{n} jelölés', markBadge: '1 jelölés',
      commentBadge: 'megjegyzés', nothing: 'nincs választás', notAccepted: 'még nincs elfogadva',
      show: 'Mutasd', show_all: 'Mind', show_comment: 'Megjegyzéssel', show_differs: 'Eltér a javaslattól',
      noneShown: 'Nincs mit mutatni.', more: 'Teljes szöveg', less: 'Kevesebb',
      privateLabel: 'Privát', sensTitle: 'Személyes adat lehet.',
      sens: ' Az ágens Privátnak jelölte ezt a tételt. Vedd ki a pipát, ha a szöveged megosztható.',
      lockNote: 'Privát: a szöveged a válaszfájlban így is eljut az ágenshez, de az ágenst arra kérjük, hogy ne vigye tovább commitba, memóriába, felhőbe vagy döntésnaplóba. A Markdown-másolat és az Összegzés elrejti. Ez kérés az ágensnek, nem titkosítás.',
      privateBadge: 'privát', privateNote: 'Privát megjegyzés, itt rejtve.',
      view: 'Nézet', cards: 'Kártyák', table: 'Táblázat', criterion: 'Szempont',
      scrollHint: 'Húzd oldalra a táblázatot. A szempontok oszlopa helyben marad.',
      legend: '▲ jobb · ● közepes · ▼ rosszabb', tone_good: 'jobb', tone_mid: 'közepes', tone_bad: 'rosszabb',
      kAccept: 'a nyitott kör elfogadása (Macen Cmd+Enter); egyetlen körnél: Kész',
      light: 'Világos', dark: 'Sötét', theme: 'Téma', width: 'Szélesség', board: 'Élő makett',
      appearance: 'Megjelenés', mode: 'Mód', palette: 'Paletta', system: 'Rendszer', keep: 'Megtartás', cancel: 'Mégse',
      apPreview: 'előnézet', apKeep: 'megtartás', apCancel: 'mégse', kTheme: 'színek: paletta, világos vagy sötét',
      density: 'Sűrűség', comfortable: 'Tágas', compact: 'Tömör',
      apOnly: 'Csak ezen a lapon. Válaszd ki az inboxban, és minden Signoff-oldal így nyílik.',
      apFailed: 'Nincs megtartva: {m}', apOffline: 'Nincs megtartva: az inbox nem válaszol.', ownTheme: 'a Signoff saját színei',
      attachMissing: 'A melléklet nem látható: {r}', lines: '{n} sor', line: '1 sor', attachText: 'Melléklet: {r}',
      missing: 'A kép nem látható: {r}', full: 'Megnyitás teljes méretben', larger: 'Nagyobb', smaller: 'Kisebb',
      picture: '{n}. kép',
      addMark: 'Jelölés hozzáadása a(z) {n}. képhez',
      marks: 'A jelöléseid', markHint: 'Rajzolj téglalapot a képre, hogy megmutass valamit, vagy használd a Jelölés hozzáadása gombot.',
      mark: '{n}. jelölés', markOn: 'a(z) {p}. képen', markHandle: '{n}. jelölés a(z) {p}. képen. A nyilak mozgatják, a Shift és a nyilak átméretezik.',
      markComment: 'Megjegyzés a(z) {n}. jelöléshez', remove: 'Törlés', noMarks: 'Nincs jelölés.',
      historyTitle: 'Korábbi döntések', historyIntro: 'A projekt minden köre, a legújabb elöl. Válassz egyet: úgy jelenik meg, ahogy megválaszolták.',
      historyNotice: 'Csak olvasható. A terminálban Ctrl+C zárja be; {m} perc tétlenség után magától is leáll.',
      historyNoticeNever: 'Csak olvasható. A terminálban Ctrl+C zárja be.',
      historyGone: 'A Signoff leállt, ezért a képek már nem töltődnek be. Kérd meg az agentet, hogy nyissa meg újra a korábbi döntéseket.',
      search: 'Keresés', from: 'Ettől', fromHint: 'ÉÉÉÉ-HH-NN', count: '{n} / {t} kör', none: 'Egy kör sem felel meg.',
      st_done: 'kész', st_open: 'nyitott', st_new: 'megválaszolatlan', st_error: 'nem olvasható',
      outcome: 'Eredmény', logged: 'naplózva: {log}, {date}', warning: 'Figyelem', error: 'Ez a fájl nem olvasható.',
      pick: 'Válassz egy kört a listából.',
      inbox: 'Inbox', held: 'Ez a kör egy másik Signoff-futásban van nyitva, ezért itt csak olvasható. Ott válaszolj rá.',
      orphan: 'Az ezt kérdező Signoff-futás véget ért; a válaszod a projekt munkameneteihez kerül.',
      orphanKind: 'A futás véget ért',
      doneOrphan: 'Kész. A válaszaid mentve vannak. A projekt nyitott munkamenetei, vagy ha nincs ilyen, a következő, csak egy jelzést kapnak, hogy megjöttek a válaszok; az ágens akkor olvassa el őket, amikor szólsz neki. Ezt a lapot bezárhatod.',
      agentRecommends: 'Az ágens javaslata', outline: 'Ebben a körben', itemOf: '{i}. tétel / {n}', roundOf: '{i}. kör / {n}',
      answersN: '{n} válasz', signNote: 'A Kész minden választ elküld az ágensnek.', stChanged: 'módosult',
      kItemShort: 'tétel', kOptionShort: 'lehetőség', kLeave: 'kilépés a Fókuszból vagy az oldalsáv bezárása; vagy ez a lista',
      sidebar: 'Oldalsáv', focus: 'Fókusz', prev: 'Előző', next: 'Következő', leaveFocus: 'Kilépés a Fókuszból',
      kFocus: 'Fókusz: egyszerre egy tétel', kFocusShort: 'fókusz', kKeysShort: 'billentyűk',
      minutes: '{n} perce', underMinute: 'kevesebb mint egy perce', hours: '{n} órája', days: '{n} napja',
      opened: 'Megnyitva: {d} · {t}', openedNow: 'Megnyitva: {d} · épp most', oldRound: '2+ napja nyitva',
      oldNote: 'A kérdései elavulhattak: ha bizonytalan vagy, kérdezd meg az ágenst, mielőtt válaszolsz.',
      copy: 'Másolás', copied: 'Másolva', copyFailed: 'Nem sikerült másolni: jelöld ki a szöveget',
      copyLabel: 'A(z) {l} blokk másolása', relDelete: 'Relatív útvonal: futtatás előtt ellenőrizd a mappát',
      codeLang: 'kód', blocking: 'Erre vár az ágens', blockingShort: 'erre vár', show_blocking: 'Erre vár az ágens',
      numValue: '{t}, érték',
      stakes_high: 'Nagy tét', stakes_medium: 'Közepes tét', stakes_low: 'Kis tét', highShort: 'nagy tét',
      irreversible: 'Nem vonható vissza', undoShort: 'végleges', settledEarlier: 'Korábban eldőlt',
      change: 'Módosítás', fold: 'Opciók elrejtése',
      lnAdd: 'hozzáadva', lnDel: 'törölve', lnOld: 'Régi sor', lnNew: 'Új sor', lnNum: 'Sor', lnSign: 'Változás', lnText: 'Szöveg',
      lnNote: 'Megjegyzés: {f}, {n}. sor', lnFull: 'Egy tételen legfeljebb 50 sor-megjegyzés lehet.', lnStale: 'A fájl azóta változott. A {n}. sor ez volt:',
      lnHint: 'Egy sorszámot megnyomva megjegyzést írhatsz a sorhoz.'}
  };
  var data = JSON.parse(document.getElementById('signoff-data').textContent);
  var historyMode = data.mode === 'history';
  var rounds = historyMode ? data.entries : (data.rounds || []);  // the inbox page has no rounds of its own
  var root = document.documentElement;
  // The look every page opens in, as the inbox keeps it (prefs.json); set before the rest of this script builds the
  // page. The ids are a closed list, the server's PALETTES: one outside it falls back to the default.
  var PALETTES = [
    {id: 'catppuccin', name: 'Catppuccin', modes: 'Latte / Mocha', credit: 'MIT · Copyright (c) 2021 Catppuccin'},
    {id: 'signoff', name: 'Signoff', modes: 'Light / Dark', credit: ''},
    {id: 'rose-pine', name: 'Rosé Pine', modes: 'Dawn / Main', credit: 'MIT · Copyright (c) mvllow'},
    {id: 'gruvbox', name: 'Gruvbox', modes: 'Light / Dark', credit: 'MIT/X11 · Pavel Pertsev (morhetz)'},
    {id: 'tokyo-night', name: 'Tokyo Night', modes: 'Light / Night', credit: 'MIT · Copyright (c) 2018-present Enkia'},
    {id: 'dracula', name: 'Dracula', modes: 'Alucard / Dracula', credit: 'MIT · Copyright (c) 2023 Dracula Theme'}];
  var THEMES = ['system', 'light', 'dark'];
  var prefs = data.prefs || {};
  var look = {theme: THEMES.indexOf(prefs.theme) >= 0 ? prefs.theme : THEMES[0],
    palette: PALETTES.some(function (p) { return p.id === prefs.palette; }) ? prefs.palette : PALETTES[0].id};
  function setLook(l) {
    look = l;
    root.setAttribute('data-palette', l.palette);
    if (l.theme === 'system') root.removeAttribute('data-mode');
    else root.setAttribute('data-mode', l.theme);
    syncSwatches();
  }
  function syncSwatches() {  // the picker's swatches show their palette in the page's mode
    var mode = root.getAttribute('data-mode');
    Array.prototype.forEach.call(document.querySelectorAll('.sws'), function (s) {
      if (mode) s.setAttribute('data-mode', mode);
      else s.removeAttribute('data-mode');
    });
  }
  var theme = location.hash.slice(1);  // #dark or #light wins over the kept mode (screenshots); the picker starts there
  if (theme === 'dark' || theme === 'light') look = {theme: theme, palette: look.palette};
  setLook(look);
  // Comfortable or Compact rows and items, kept with the theme.
  var DENSITIES = ['comfortable', 'compact'];
  var density = DENSITIES.indexOf(prefs.density) >= 0 ? prefs.density : DENSITIES[0];
  function setDensity(d) {
    density = d;
    if (d === 'compact') root.setAttribute('data-density', d);
    else root.removeAttribute('data-density');
  }
  setDensity(density);
  // How the Waiting on you rows are ordered, kept like the density (the inbox list only).
  var SORTS = ['oldest', 'newest', 'project'];
  var sortKey = SORTS.indexOf(prefs.sort) >= 0 ? prefs.sort : SORTS[0];
  var first = rounds.filter(function (r) { return r.decisions; })[0];
  var lang = String((first && first.decisions.lang) || 'en').replace(/_/g, '-');
  root.lang = lang;
  var code = lang.toLowerCase().split('-')[0];
  var L = Object.prototype.hasOwnProperty.call(LABELS, code) ? LABELS[code] : LABELS.en;
  function fmt(s, vals) { return s.replace(/\{(\w+)\}/g, function (m, k) { return vals[k]; }); }
  function lasted(stamp) {  // how long since then, roughly: a session's wait, a round's age
    var m = Math.floor((Date.now() - new Date(stamp).getTime()) / 60000);
    if (isNaN(m)) return '';
    if (m < 1) return L.underMinute;
    if (m < 60) return fmt(L.minutes, {n: m});
    return m < 2880 ? fmt(L.hours, {n: Math.floor(m / 60)}) : fmt(L.days, {n: Math.floor(m / 1440)});
  }
  // A round asked this long ago may hold questions the agent has moved past: its page and its inbox row say so.
  // shortcut: the age text and the warning are judged at render only; a tab left open keeps them until its next reload.
  var OLD_ROUND = 2 * 86400000;
  function oldRound(stamp) { return !!stamp && Date.now() - new Date(stamp).getTime() >= OLD_ROUND; }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    return e;
  }
  function byId(id) { return document.getElementById(id); }
  function pageTheme() {
    var t = root.getAttribute('data-mode');
    if (t) return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  // ---- Appearance: a popover from the brush button or Cmd+, (Ctrl+,). Arrow keys show each palette or mode live on
  // the page behind it; Enter keeps, Esc goes back. Only the inbox can keep a choice for every page (POST prefs);
  // elsewhere it holds for this tab until a reload, and the picker says so. ----
  var picker = byId('appearance'), kept = look, keptDensity = density, opener = null;
  var keepable = !!prefs.keep, shortcut = /Mac|iPhone|iPad/.test(navigator.platform || '') ? ['⌘', ','] : ['Ctrl', ','];
  var openButton = byId('appearance-open');
  openButton.setAttribute('aria-label', L.appearance);
  openButton.title = L.appearance + ' (' + shortcut.join('') + ')';
  byId('ap-title').textContent = L.appearance;
  byId('ap-key').textContent = shortcut.join('');
  byId('ap-mode-label').textContent = L.mode;
  byId('ap-palette-label').textContent = L.palette;
  byId('ap-cancel').textContent = L.cancel;
  byId('ap-keep').textContent = L.keep;
  [[['↑', '↓'], L.apPreview], [['⏎'], L.apKeep], [['Esc'], L.apCancel]].forEach(function (k, i) {
    var keys = byId('ap-keys');
    if (i) keys.append(' · ');
    k[0].forEach(function (c) { keys.append(el('kbd', '', c)); });
    keys.append(' ' + k[1]);
  });
  var modeButtons = THEMES.map(function (t) {
    var b = el('button', '', L[t]);
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.addEventListener('click', function () { preview({theme: t, palette: look.palette}, b); });
    byId('ap-modes').append(b);
    return b;
  });
  var densityButtons = DENSITIES.map(function (d) {
    var b = el('button', '', L[d]);
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.addEventListener('click', function () { setDensity(d); mark(look); b.focus(); });
    byId('ap-density').append(b);
    return b;
  });
  byId('ap-density-label').textContent = L.density;
  var options = PALETTES.map(function (p) {
    var li = el('li', 'pi'), swatch = el('span', 'sws'), name = el('span', '', p.name);
    li.setAttribute('role', 'option');
    swatch.setAttribute('data-palette', p.id);  // its own palette's tokens: the row itself keeps the page's
    swatch.setAttribute('aria-hidden', 'true');
    for (var i = 0; i < 4; i++) swatch.append(el('i'));
    name.append(el('small', '', p.modes + ' · ' + (p.credit || L.ownTheme)));
    li.append(swatch, name, el('span', 'chk'));
    li.lastChild.setAttribute('aria-hidden', 'true');
    li.addEventListener('click', function () { preview({theme: look.theme, palette: p.id}, li); });
    byId('ap-palettes').append(li);
    return li;
  });
  function preview(l, focus) {
    setLook(l);
    mark(l);
    if (focus) focus.focus();
  }
  function mark(l) {
    modeButtons.forEach(function (b, i) {
      var on = THEMES[i] === l.theme;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    densityButtons.forEach(function (b, i) {
      var on = DENSITIES[i] === density;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    options.forEach(function (li, i) {
      var on = PALETTES[i].id === l.palette;
      li.setAttribute('aria-selected', String(on));
      li.tabIndex = on ? 0 : -1;
      li.lastChild.textContent = on ? '✓' : '';
    });
  }
  mark(look);
  function openPicker() {
    if (picker.open || byId('help').open) return;
    opener = document.activeElement;
    kept = look;
    keptDensity = density;
    byId('ap-note').textContent = keepable ? '' : L.apOnly;
    picker.showModal();
    syncSwatches();
    mark(look);
    options[PALETTES.map(function (p) { return p.id; }).indexOf(look.palette)].focus();
  }
  function closePicker(l) {
    preview(l);
    picker.close();
    if (opener && opener.focus) opener.focus();
    opener = null;  // it may sit in rows a round switch drops
  }
  function cancelPicker() {
    setDensity(keptDensity);
    closePicker(kept);
  }
  function keepPicker() {
    var l = look, note = byId('ap-note');
    if (!keepable) return closePicker(l);
    note.textContent = '';
    // The run pages the inbox shows sit under run/<n>/; the choice is the inbox's, at its page root.
    fetch(data.inbox ? '../../prefs' : 'prefs', {method: 'POST', headers: {'Content-Type': 'application/json'},
      // The density goes only when changed here: left out, the server keeps the stored one, which another tab may
      // have changed since this page loaded.
      body: JSON.stringify(density !== keptDensity ? {theme: l.theme, palette: l.palette, density: density}
        : {theme: l.theme, palette: l.palette})}).then(function (r) {
      if (r.ok) return closePicker(l);
      return r.text().then(function (t) { note.textContent = fmt(L.apFailed, {m: t.split('\n')[0] || r.status}); });
    }).catch(function () { note.textContent = L.apOffline; });
  }
  openButton.addEventListener('click', openPicker);
  byId('ap-cancel').addEventListener('click', cancelPicker);
  byId('ap-keep').addEventListener('click', keepPicker);
  picker.addEventListener('cancel', function (e) { e.preventDefault(); cancelPicker(); });
  picker.addEventListener('click', function (e) {  // a click outside the popover lands on its backdrop
    var r = picker.getBoundingClientRect();
    if (e.target === picker && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) {
      cancelPicker();
    }
  });
  picker.addEventListener('keydown', function (e) {
    e.stopPropagation();  // the page's own keys (j, k, 1–9, ?) wait until the picker is closed
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      return cancelPicker();
    }
    var o = options.indexOf(e.target), m = modeButtons.indexOf(e.target), d = densityButtons.indexOf(e.target);
    if (e.key === 'Enter' && (o >= 0 || m >= 0 || d >= 0)) {
      e.preventDefault();
      return keepPicker();
    }
    var step = {ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1}[e.key];
    if (!step || (o < 0 && m < 0 && d < 0)) return;
    e.preventDefault();
    if (o >= 0) {
      o = Math.max(0, Math.min(options.length - 1, o + step));
      preview({theme: look.theme, palette: PALETTES[o].id}, options[o]);
    } else if (d >= 0) {
      d = Math.max(0, Math.min(densityButtons.length - 1, d + step));
      setDensity(DENSITIES[d]);
      mark(look);
      densityButtons[d].focus();
    } else {
      m = Math.max(0, Math.min(modeButtons.length - 1, m + step));
      preview({theme: THEMES[m], palette: look.palette}, modeButtons[m]);
    }
  });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key === ',') {
      e.preventDefault();
      openPicker();
    }
  });

  // ---- The shell every page shares (Sally's boards): a sidebar beside the page's own header, a key bar under the
  // main area, and Focus. Under 1000 px the sidebar is a drawer, and the rest is inert while it is open.
  // o: label (the sidebar's name), keys ([[keys], word] for the bar), focus (false: no Focus), extra (more nodes to
  // make inert), setFocus(on), move(step) (Focus's Previous and Next; false when there is nowhere to go). ----
  function icon(d) {
    var s = svg('svg', {viewBox: '0 0 24 24', 'class': 'ic', 'aria-hidden': 'true'});
    d.forEach(function (p) { s.append(svg('path', {d: p})); });
    return s;
  }
  function shell(o) {
    var body = document.body, header = document.querySelector('body > header'), mainEl = document.querySelector('body > main');
    var drawer = window.matchMedia('(max-width: 1000px)');
    body.classList.add('app');
    function has(c) { return body.classList.contains(c); }
    var sideButton = el('button', 'icon'), focusButton = el('button', 'tb-btn', L.focus);
    sideButton.type = focusButton.type = 'button';
    sideButton.setAttribute('aria-label', L.sidebar);
    sideButton.setAttribute('aria-controls', 'ib-side');
    sideButton.append(icon(['M4 5h16v14H4z', 'M9 5v14']));
    header.insertBefore(sideButton, header.firstChild);
    focusButton.setAttribute('aria-pressed', 'false');
    focusButton.append(el('kbd', '', 'F'));
    focusButton.lastChild.setAttribute('aria-hidden', 'true');  // its name is "Focus"; the key is in the key list
    focusButton.hidden = o.focus === false;
    byId('accept-all').before(focusButton);
    var nav = el('nav', 'side');
    nav.id = 'ib-side';
    nav.setAttribute('aria-label', o.label);
    body.insertBefore(nav, header);
    var scrim = el('div', 'ib-scrim');
    body.append(scrim);
    var bar = el('footer', 'bar'), keys = el('p', 'bar-keys'), keysButton = el('button', '', '?');
    o.keys.forEach(function (k) {
      var s = el('span', 'k');
      k[0].forEach(function (c) { s.append(el('kbd', '', c), ' '); });
      s.append(k[1]);
      keys.append(s);
    });
    keysButton.type = 'button';
    keysButton.setAttribute('aria-label', L.keys);
    keys.append(keysButton, ' ' + L.kKeysShort);
    mainEl.after(bar);
    // Focus's own bar: where you are, Previous, Next, and the way out.
    var fbar = el('div', 'ib-fbar'), at = el('span'), prev = el('button', '', L.prev), next = el('button', '', L.next);
    var leave = el('button', '', L.leaveFocus);
    [prev, next, leave].forEach(function (b) { b.type = 'button'; });
    fbar.append(at, el('span', 'grow'), prev, next, leave);
    prev.addEventListener('click', function () { if (!o.move(-1)) prev.focus(); });
    next.addEventListener('click', function () { if (!o.move(1)) next.focus(); });
    leave.addEventListener('click', function () { o.setFocus(false); });
    focusButton.addEventListener('click', function () { o.setFocus(!has('focusing')); });
    function inert(on, except) {
      [nav, header, mainEl, bar].concat(o.extra || []).forEach(function (n) {
        if (n !== except && !n.contains(except)) n.inert = on;
      });
    }
    function sideShown() { return drawer.matches ? has('side-open') : !has('side-off') && !has('focusing'); }
    function sideState() { sideButton.setAttribute('aria-expanded', String(sideShown())); }
    function closeSide(back) {
      if (!has('side-open')) return;
      body.classList.remove('side-open');
      inert(false);
      sideState();
      if (back) sideButton.focus();
    }
    sideButton.addEventListener('click', function () {
      if (has('focusing')) {  // Focus hides the sidebar: leaving it brings the sidebar back
        body.classList.remove('side-off');
        o.setFocus(false);
        if (!drawer.matches) return sideState();
      }
      if (!drawer.matches) body.classList.toggle('side-off');
      else if (has('side-open')) closeSide(true);
      else {
        body.classList.add('side-open');
        inert(true, nav);
        // The entry shown now, else the first one a search left in view.
        var on = nav.querySelector('[aria-pressed=true],[aria-selected=true],[aria-current=true]');
        if (!on || on.offsetParent === null) {
          on = Array.prototype.filter.call(nav.querySelectorAll('button, input'), function (b) { return b.offsetParent !== null; })[0];
        }
        if (on) on.focus();
      }
      sideState();
    });
    scrim.addEventListener('click', function () { closeSide(true); });
    drawer.addEventListener('change', function () {
      closeSide(false);
      sideState();
    });
    sideState();
    return {nav: nav, bar: bar, keys: keys, keysButton: keysButton, sideButton: sideButton, focusButton: focusButton,
      fbar: fbar, at: at, prev: prev, next: next, scrim: scrim, drawer: drawer, has: has, inert: inert,
      closeSide: closeSide, sideState: sideState};
  }

  // A code block: a todo item's in the inbox, an item's command on a round's page. Its Copy is named by the caption
  // (a command's), else the language. It wraps, so a long line cannot run past the edge. command: a round's
  // command, which goes to a terminal whatever its lang, so the delete hint does not depend on it.
  function codeBlock(b, command) {
    var box = el('div', 'md-code'), bar = el('div', 'md-bar'), lang = String(b.lang || ''), text = String(b.text);
    bar.append(el('span', 'md-lang', lang || L.codeLang), copier(text, fmt(L.copyLabel, {l: String(b.caption || '') || lang || L.codeLang})));
    var pre = el('pre');
    pre.tabIndex = 0;  // the keyboard reaches it, to select from it
    pre.append(el('code', '', text));
    box.append(bar, pre);
    if ((command || /^(|text|bash|sh|zsh|shell|console|powershell|pwsh|cmd|bat)$/i.test(lang)) && relativeDelete(text)) box.append(el('p', 'md-warn', '⚠ ' + L.relDelete));
    return box;
  }
  // A Copy button: the text exactly, nothing added (a function gives it at the click). Signoff never runs a
  // command, it only hands it over.
  function copier(text, label, cls, caption) {
    var copy = el('button', cls || '', caption || L.copy);
    copy.type = 'button';
    copy.setAttribute('aria-label', label);
    copy.addEventListener('click', function () {
      function said(ok) {
        copy.textContent = ok ? L.copied : L.copyFailed;
        setTimeout(function () { copy.textContent = caption || L.copy; }, 1800);
      }
      if (!navigator.clipboard) return said(false);
      navigator.clipboard.writeText(typeof text === 'function' ? text() : text).then(function () { said(true); }, function () { said(false); });
    });
    return copy;
  }
  // A command that deletes (rm, rmdir, trash, del, Remove-Item) with a path that is not absolute depends on the folder
  // it runs in. Only a hint; nothing is blocked. shortcut: a quoted path with spaces is read as one word, an
  // unquoted one as several; a word starting with $ or % (a variable) counts as absolute.
  var DELETERS = /^(rm|rmdir|trash|del|remove-item)$/i;
  function relativeDelete(text) {
    return String(text).split(/&&|\|\||[;|\n]/).some(function (part) {
      var words = part.match(/"[^"]*"|'[^']*'|\S+/g) || [];
      while (words.length && /^(\$|sudo|doas|command|env|nohup|time)$/.test(words[0])) words.shift();
      if (!words.length || !DELETERS.test(words[0])) return false;
      return words.slice(1).some(function (w) {
        w = w.replace(/^["']|["']$/g, '');
        return w !== '' && !/^(-|\d*[<>&]|[\/~$%\\]|[A-Za-z]:[\\\/])/.test(w);
      });
    });
  }

