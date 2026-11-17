import { atom, read, update } from 'claude-code'
import type { Args, EngineInterface, Next, PluginOptions, Register, RenderInput } from 'claude-code'

import type { PanelRows } from '../types'

// The Signoff band: how many rounds and todo items wait on the user in this session's project, from the inbox's
// state.json. It reads files through $.fs every EVERY ms and starts no Python for that; the processes it starts
// by itself are `signoff inbox home`, once per load of this module, and only after an inbox.lock showed that an inbox
// has run, and `signoff inbox requests` for a message from the inbox it found new, whose lines it hands the session.
// Open and /signoff:open open the panel: this project's open rounds from `signoff inbox rows`, run once per opening.

type Engine = EngineInterface
type DrawInput = RenderInput<'Pane'> | RenderInput<'AbovePrompt'>

const COMMAND = 'signoff:open' // commands/open.md: a mod cannot register a name with a colon
const PANE = 'signoff'
const TITLE = 'Signoff'
const MAX_ROWS = 24 // MAX_ROWS in signoff.py: rounds and items in a rows reply
const MAX_TEXT = 200 // MAX_OFFER_TEXT in signoff.py
const EVERY = 5_000
// The inbox touches inbox.lock every STATE_EVERY (2 s) while it lives; a dead one changes no file.
const LIVE_WITHIN = 15_000
const MAX_FILE = 64 * 1024 // state.json and projects/<id>.json hold a few hundred bytes
const ID = /^[0-9a-f]{16}$/
const REQUEST_FILE = /^[0-9a-f]{16}\.json$/
// A request written near the session's start may also come in the SessionStart hand-over: twice beats never.
// shortcut: the margin is a guess, as the gap between that hook and the first look is not measured.
const SINCE_MARGIN = 10_000
const MAX_IDS = 10 // MAX_HANDOVER in signoff.py
const MAX_SENT = 1000
const MAX_LINE = 4096 // a request_line() is at most about 2,800 characters
const RETRY_AFTER = 60_000
const MAX_RETRIES = 3 // in a row per load; then only at the folder's next change
const NOT_ONE_LINE = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u

const countA = atom({ plugin: 'signoff', key: 'count' } as const, 0)
const hiddenA = atom({ plugin: 'signoff', key: 'hiddenFor' } as const, null)
const toastedA = atom({ plugin: 'signoff', key: 'isToasted' } as const, false)
const rowsA = atom({ plugin: 'signoff', key: 'rows' } as const, null)
const whereA = atom({ plugin: 'signoff', key: 'where' } as const, null)
const hasAppA = atom({ plugin: 'signoff', key: 'hasApp' } as const, false) // in state: a mod reload keeps the rows too
const requestsA = atom({ plugin: 'signoff', key: 'requests' } as const, null) // a reload keeps it, a /clear empties it

// One load's facts. A reload (an options change too) starts a fresh module; $.state keeps the count meanwhile.
const look = {
  options: {} as PluginOptions,
  isArmed: false,
  isWindows: false,
  candidates: [] as string[], // where an inbox.lock says an inbox has run
  home: undefined as string | undefined,
  isHomeAsked: false,
  project: undefined as string | undefined,
  root: '', // the session root it was found for: $.session.root(), which a `/cd` moves and a Bash `cd` does not
  projectsAt: -1, // the projects/ folder's time at the last listing
  stateKey: '',
  stateCount: 0,
  // The toast: a live 0 was seen, and then one live count above it (a second one in a row toasts, so a rise that
  // ends within a look, such as a permission prompt answered at once, does not). The first live count is no rise.
  wasZero: false,
  isRising: false,
  isShown: false, // whether show() has drawn this load's first count (and set the status line)
  isBusy: false,
  isOpening: false, // a panel row's `signoff inbox open` runs
  // The requests: their own flag, so a submit the engine holds does not hold up the count.
  isDelivering: false,
  listedAt: -1, // the requests folder's time at the last listing
  listedNow: 0, // and the clock's then
  isCut: false, // a batch left new requests for the next look
  failures: 0, // failed batches in a row
  retryAt: 0, // when a failed batch is tried again; 0: not by time
}

const hint = () => (look.options.hint === 'status' || look.options.hint === 'off' ? look.options.hint : 'band')
const isAbsolute = (path: string | undefined): path is string => typeof path === 'string' && path.startsWith('/')
const line = (n: number) => `Signoff: ${n} waiting`

// The defaults of inbox_home() on macOS and Linux, as hook.sh mirrors them: only to know whether to ask at all.
async function candidates($: Engine): Promise<string[]> {
  const own = await $.env.get('SIGNOFF_INBOX_HOME')
  if (own) return isAbsolute(own) ? [own] : []
  const home = await $.env.get('HOME')
  if (!isAbsolute(home)) return []
  const xdg = await $.env.get('XDG_STATE_HOME')
  return [`${home}/Library/Application Support/signoff`, isAbsolute(xdg) ? `${xdg}/signoff` : `${home}/.local/state/signoff`]
}

async function readJSON($: Engine, path: string): Promise<unknown> {
  const stat = await $.fs.stat(path)
  if (stat.kind !== 'file' || stat.size > MAX_FILE) return undefined
  return JSON.parse(await $.fs.read(path))
}

async function findHome($: Engine): Promise<string | undefined> {
  if (look.home || look.isHomeAsked) return look.home
  let isRun = false
  for (const dir of look.candidates) if (await $.fs.exists(`${dir}/inbox.lock`)) isRun = true
  if (!isRun) return undefined
  look.isHomeAsked = true // one Python start per load of this module; a failed one is not tried again
  const ran = await $.process.run([`${$.plugin.root}/bin/signoff`, 'inbox', 'home'], { timeoutMs: 10_000 })
  const home = ran.stdout.replace(/\r?\n$/, '')
  if (ran.exitCode === 0 && isAbsolute(home) && !home.includes('\n')) look.home = home
  return look.home
}

// The hooks note each project's root in projects/<id>.json; the session's is the longest root holding the session's
// root, as the monitor fixed its project at the session's start: a Bash `cd` does not move it.
async function findProject($: Engine, home: string): Promise<string | undefined> {
  const folder = `${home}/projects`
  const at = (await $.fs.stat(folder)).mtimeMs
  const session = await $.session.root()
  // A new note (a nested project's, say) changes the folder's time.
  if (at === look.projectsAt && session === look.root) return look.project
  const real = (await $.fs.stat(session, { resolve: true })).realPath
  if (!real) return undefined
  let best = ''
  let project: string | undefined
  for (const entry of await $.fs.list(folder)) {
    if (entry.kind !== 'file' || !entry.name.endsWith('.json') || entry.name.startsWith('.tmp-')) continue
    const noted = (await readJSON($, `${folder}/${entry.name}`).catch(() => undefined)) as { id?: unknown; root?: unknown } | undefined
    const root = typeof noted?.root === 'string' ? noted.root.replace(/\/$/, '') : ''
    if (typeof noted?.id !== 'string' || !ID.test(noted.id) || !isAbsolute(root) || root.length <= best.length) continue
    if (real === root || real.startsWith(`${root}/`)) {
      best = root
      project = noted.id
    }
  }
  // The notes on the requests folder were the other project's, as at a /clear.
  if (project !== look.project) Object.assign(look, { listedAt: -1, isCut: false, failures: 0, retryAt: 0 })
  look.project = project
  look.root = session
  look.projectsAt = at // only after a whole listing: a failed one is tried again at the next look
  return project
}

async function countOf($: Engine, home: string, project: string): Promise<number> {
  const path = `${home}/state.json`
  const stat = await $.fs.stat(path)
  const key = `${project}:${stat.mtimeMs}:${stat.size}`
  if (key === look.stateKey) return look.stateCount
  const state = (await readJSON($, path)) as { schema?: unknown; projects?: unknown } | undefined
  const entry = state?.schema === 1 && Array.isArray(state.projects) ? state.projects.find(p => p?.id === project) : undefined
  const n = entry?.waiting
  look.stateKey = key
  look.stateCount = Number.isInteger(n) && n > 0 ? n : 0
  return look.stateCount
}

async function alive($: Engine, home: string): Promise<boolean> {
  const lock = await $.fs.stat(`${home}/inbox.lock`)
  return lock.kind === 'file' && Math.abs((await $.clock.now()) - lock.mtimeMs) <= LIVE_WITHIN
}

// live: the project's count while an inbox lives, undefined when there is no live reading (no inbox, a failed look).
// Only a live count moves Hide and the toast, so a slow look or an inbox restart neither undoes Hide nor toasts.
async function show($: Engine, live: number | undefined) {
  if (live !== undefined) {
    const hiddenFor = await read($, hiddenA)
    if (hiddenFor !== null && hiddenFor !== live) await update($, hiddenA, () => null) // Hide holds until it changes
    if (live === 0) {
      look.wasZero = true
      look.isRising = false
    } else if (look.wasZero && !look.isRising) {
      look.isRising = true
    } else if (look.isRising) {
      look.wasZero = look.isRising = false
      if (!(await read($, toastedA))) {
        await update($, toastedA, () => true)
        $.ui.toast(`${line(live)} · /${COMMAND}`)
      }
    }
  }
  const n = live ?? 0
  if (look.isShown && n === (await read($, countA))) return
  look.isShown = true
  await update($, countA, () => n)
  if (hint() === 'status') $.ui.status(n ? `${line(n)} · /${COMMAND}` : undefined)
}

// The one way this mod puts text before the model: a turn of its own, which Claude Code starts once the session is
// idle (after a running turn; a draft in the prompt box stays). False when it did not enter. The log never gets the
// text, nor a drop's reason, which may quote it.
async function submit($: Engine, text: string): Promise<boolean> {
  try {
    if ((await $.prompt.submit({ text })).drop === undefined) return true
    $.ui.log('signoff: a prompt was dropped', { to: 'debug' })
  } catch (err) {
    $.ui.log(`signoff: a prompt did not enter (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
  }
  return false
}

type Ran = { exitCode: number; stdout: string; isStdoutTruncated: boolean }

// `signoff inbox requests` builds each line with request_line(), the one escaper; this only checks the reply is
// exactly that for the ids asked, and passes the lines on unchanged. Undefined for anything else, except a line in
// its frame with its own id that is not one line by this engine's Unicode table (an older Python's misses a few
// format characters): marked, not sent, so it holds up no later request; a later session start with no other live
// session of the project hands it over.
function checkReply($: Engine, ran: Ran, asked: string[]): { lines: string[]; sentIds: string[]; named: string[] } | undefined {
  if (ran.exitCode !== 0 || ran.isStdoutTruncated) return undefined
  let got: { requests?: unknown; gone?: unknown }
  try {
    got = JSON.parse(ran.stdout)
  } catch {
    return undefined
  }
  if (!Array.isArray(got?.requests) || !Array.isArray(got.gone)) return undefined
  const named: string[] = []
  const lines: string[] = []
  const sentIds: string[] = []
  const isNew = (id: unknown): id is string => typeof id === 'string' && asked.includes(id) && !named.includes(id)
  for (const r of got.requests as { id?: unknown; line?: unknown }[]) {
    const { id, line } = r ?? {}
    if (!isNew(id) || typeof line !== 'string') return undefined
    if (!line.startsWith('Signoff inbox: ') || !line.endsWith(`inbox done ${id}`)) return undefined
    named.push(id)
    if (line.length > MAX_LINE || NOT_ONE_LINE.test(line)) {
      $.ui.log('signoff: a request line was not one line; a later session start with no other live session of the project hands it over', { to: 'debug' })
      continue
    }
    sentIds.push(id)
    lines.push(line)
  }
  for (const id of got.gone) {
    if (!isNew(id)) return undefined
    named.push(id)
  }
  // The command always fits the first one, so a reply naming none is no answer.
  return named.length ? { lines, sentIds, named } : undefined
}

function failed($: Engine, now: number) {
  look.failures += 1
  look.retryAt = look.failures <= MAX_RETRIES ? now + RETRY_AFTER : 0
  look.isCut = false // a cut batch waits for the retry too
  $.ui.log('signoff: a request hand-over failed', { to: 'debug' })
}

// The requests the user sent this project from the inbox: listed through $.fs only when the folder changed (or a
// batch was cut, or a retry is due), and a process only for ids it has not handed over.
async function deliver($: Engine, home: string, project: string, root: string) {
  if (look.isDelivering) return
  look.isDelivering = true
  try {
    const now = await $.clock.now()
    const held = await read($, requestsA)
    // The mark is for the project beside it, set at the first live look; a reload keeps both. A root moved into
    // another project gets a new mark, as a /clear does: the ids handed over stay, and that project's older requests
    // come at a later session start with no other live session of the project. A held mark with no project (a build
    // before this one) takes this one.
    const isOther = held?.project !== undefined && held.project !== project
    const mark = {
      since: held && !isOther ? held.since : now - SINCE_MARGIN,
      sent: held?.sent ?? [],
      project,
    }
    if (held?.project !== project) await update($, requestsA, () => mark)
    const folder = `${home}/requests/${project}`
    const at = await $.fs.stat(folder).then(
      s => (s.kind === 'dir' ? s.mtimeMs : undefined),
      () => undefined, // no folder: no request yet
    )
    if (at === undefined) return
    const isRetry = look.retryAt > 0 && now >= look.retryAt
    // A listing within 2 s of the folder's time may share its tick with a later write (HFS+ 1 s, FAT 2 s): once more,
    // unless a failed batch waits for its retry.
    if (at === look.listedAt && (look.listedNow - at >= 2_000 || look.retryAt > 0) && !look.isCut && !isRetry) return
    look.listedAt = at // before the listing: a write during it changes the time again, for the next look
    look.listedNow = now
    look.isCut = false
    look.retryAt = 0
    let entries
    try {
      entries = await $.fs.list(folder)
    } catch (err) {
      look.listedAt = -1 // tried again at the next look
      throw err
    }
    const names = new Set(entries.map(e => e.name))
    const pending = entries.filter(e => e.kind === 'file' && !e.isLink && REQUEST_FILE.test(e.name) && !names.has(`${e.name.slice(0, 16)}.done`))
    // Only a done one is dropped: an id not in this folder may be another project's, handed over before a /cd.
    const sent = mark.sent.filter(id => !names.has(`${id}.done`)).slice(-MAX_SENT)
    const fresh = pending
      .filter(e => e.mtimeMs >= mark.since && !sent.includes(e.name.slice(0, 16)))
      .sort((a, b) => a.mtimeMs - b.mtimeMs || (a.name < b.name ? -1 : 1))
    const asked = fresh.slice(0, MAX_IDS).map(e => e.name.slice(0, 16))
    // Each write keeps the mark and its project as $.state holds them.
    const keep = (ids: string[]) => update($, requestsA, old => ({ since: old?.since ?? mark.since, sent: ids, project: old?.project ?? project }))
    if (sent.length !== mark.sent.length) await keep(sent)
    if (!asked.length) return
    // The session root too: the command checks it is in this project (a nested one the hooks have not noted is not).
    const ran = await $.process
      .run([`${$.plugin.root}/bin/signoff`, 'inbox', 'requests', root, project, ...asked], { timeoutMs: 10_000 })
      .catch(() => undefined)
    const reply = ran && checkReply($, ran, asked) // its stderr is never read
    if (!reply) return failed($, now)
    // What the command cut at MAX_HANDOVER_TEXT, and what did not fit MAX_IDS, goes at the next look.
    look.isCut = fresh.length > asked.length || reply.named.length < asked.length
    // Marked before it goes, so a reload while the engine holds the submit does not send it again.
    const marked = [...sent, ...reply.named].slice(-MAX_SENT)
    await keep(marked)
    if (reply.lines.length && !(await submit($, reply.lines.join('\n')))) {
      await keep(marked.filter(id => !reply.sentIds.includes(id)))
      return failed($, now)
    }
    look.failures = 0
  } catch (err) {
    $.ui.log(`signoff: requests look failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
  } finally {
    look.isDelivering = false
  }
}

async function poll($: Engine) {
  if (look.isBusy) return
  look.isBusy = true
  const isShown = hint() !== 'off' // off shows nothing, yet the user's messages still reach the session
  let n: number | undefined
  try {
    const home = await findHome($)
    if (home && (await alive($, home))) {
      const project = await findProject($, home)
      if (project) {
        void deliver($, home, project, look.root) // the root it was found for, kept with it
        if (isShown) n = await countOf($, home, project)
      }
    }
  } catch (err) {
    // Any failure shows nothing. Only the error's kind: a message may hold a path.
    $.ui.log(`signoff: look failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
  } finally {
    look.isBusy = false
  }
  if (isShown) await show($, n)
}

// A panel row: the numbers its rows reply gave. The inbox maps them to the item and opens it in Focus.
type Target = { run: number; round: number; item: number; inbox: number }

// `signoff inbox open` starts the inbox when needed and opens a fresh start link in the browser (with a target:
// that item, or the list when it is gone). Its stderr may hold that link (no browser opened), so the mod reads
// neither stream: only the exit code. With isApp, `--app` keeps the target (none: drops a waiting one) for the macOS
// app and opens the app.
async function openInbox($: Engine, target?: Target, isApp = false): Promise<string> {
  if (look.isWindows || (await $.env.get('OS')) === 'Windows_NT') return 'Signoff: the inbox does not run on Windows yet'
  const to = target ? ['--run', `${target.run}`, '--focus', `${target.round}/${target.item}`, '--inbox', `${target.inbox}`] : []
  try {
    const ran = await $.process.run([`${$.plugin.root}/bin/signoff`, 'inbox', 'open', ...(isApp ? ['--app'] : []), ...to], { timeoutMs: 30_000 })
    // Not "the question is open": an app from before targets shows what it showed.
    if (ran.exitCode === 0 && isApp) return 'Signoff: opening the Signoff app'
    // Exit 0 also when no browser opened (SSH, no desktop): then the link is on stderr, which a terminal shows.
    if (ran.exitCode === 0) return 'Signoff: the inbox is open; no browser window? Run signoff inbox open in a terminal'
  } catch {
    // could not start, or ran past its time: the same line
  }
  if (isApp) return 'Signoff: the app did not open; run signoff inbox open in a terminal to see the inbox'
  return 'Signoff: the inbox did not open; run signoff inbox open in a terminal to see why'
}

const APP_ID = 'io.github.lumi-collective.signoff.inbox' // app/Info.plist; `inbox open --app` checks the same folders
// shortcut: only the two Applications folders, read once per panel opening with no process started; Launch Services
// may know a copy elsewhere (app/bin of a checkout), which gets no App button. Ask it (mdfind) if that turns out to matter.
async function findApp($: Engine): Promise<boolean> {
  if (look.isWindows) return false
  const home = await $.env.get('HOME')
  for (const folder of ['/Applications', ...(isAbsolute(home) ? [`${home}/Applications`] : [])]) {
    try {
      const path = `${folder}/Signoff.app/Contents/Info.plist`
      const stat = await $.fs.stat(path)
      if (stat.kind === 'file' && stat.size <= MAX_FILE && (await $.fs.read(path)).includes(`<string>${APP_ID}</string>`)) return true
    } catch {
      // not there: the next folder
    }
  }
  return false
}

// The inbox already makes each title one line; this holds for whatever the command printed: no control, format or
// line-separator character (a terminal escape starts with one), at most MAX_TEXT characters.
const oneLine = (text: unknown) =>
  typeof text === 'string' ? Array.from(text).slice(0, MAX_TEXT).join('').replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/gu, ' ') : ''
const isIndex = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 0
const fit = (text: string, width: number) => {
  const chars = Array.from(text)
  return chars.length <= width ? text : `${chars.slice(0, Math.max(1, width - 1)).join('')}…`
}
const FAILED: PanelRows = { rounds: [], more: 0, inbox: 0, isFailed: true }

// `signoff inbox rows <root>`: numbers and titles only, or FAILED. Its stderr is never read. The session root's
// project, as the band counts it: the root the project was found for, else the session's root now.
async function fetchRows($: Engine): Promise<PanelRows> {
  try {
    const root = look.root || (await $.session.root())
    const ran = await $.process.run([`${$.plugin.root}/bin/signoff`, 'inbox', 'rows', root], { timeoutMs: 10_000 })
    if (ran.exitCode !== 0 || ran.isStdoutTruncated) return FAILED
    const got = JSON.parse(ran.stdout) as { rounds?: unknown; more?: unknown; inbox?: unknown }
    if (!Array.isArray(got?.rounds) || !isIndex(got.more)) return FAILED
    // Every inbox with rows names itself; only a folder outside any project gets an empty list without it.
    const inbox = isIndex(got.inbox) && got.inbox > 0 ? got.inbox : 0
    if (got.rounds.length && !inbox) return FAILED
    const rows: PanelRows = { rounds: [], more: got.more, inbox, isFailed: false }
    let left = MAX_ROWS
    // A longer reply (a later inbox's cap) shows its first MAX_ROWS; what it cuts counts in more.
    for (const r of got.rounds as { run?: unknown; round?: unknown; title?: unknown; item?: unknown; items?: unknown }[]) {
      if (!isIndex(r?.run) || !isIndex(r.round) || !isIndex(r.item) || !Array.isArray(r.items)) return FAILED
      if (left < 1) {
        rows.more += 1 + r.items.length
        continue
      }
      const items = (r.items as { item?: unknown; title?: unknown }[]).slice(0, left - 1)
      if (!items.every(i => isIndex(i?.item))) return FAILED
      rows.more += r.items.length - items.length
      // A round's row opens the item the inbox names for it.
      rows.rounds.push({ run: r.run, round: r.round, title: oneLine(r.title), item: r.item, items: items.map(i => ({ item: i.item as number, title: oneLine(i.title) })) })
      left -= 1 + items.length
    }
    return rows
  } catch (err) {
    $.ui.log(`signoff: rows failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
    return FAILED
  }
}

// $.ui.open before anything else is awaited, so it is the person's own press or command and is placed at any width.
// Focus only on a press: after /signoff:open the person may type on, and a typed digit would press a row.
async function openPanel($: Engine, isPress: boolean) {
  const opened = await $.ui.open({ id: PANE, title: TITLE, rows: 16, columns: 64, ...(isPress ? { focus: true as const } : {}) })
  // Not placed (no surface that places panes): the panel's tree draws in the band until the pane is placed.
  await update($, whereA, () => (opened.isPlaced ? 'pane' : 'band'))
  await update($, rowsA, () => null)
  const hasApp = await findApp($)
  await update($, hasAppA, () => hasApp)
  const rows = await fetchRows($)
  await update($, rowsA, () => rows)
}

async function closePanel($: Engine) {
  await $.ui.close({ id: PANE })
  await update($, whereA, () => null) // our own close does not come back through onClose
}

// One `signoff inbox open` at a time: a second press while one runs does nothing.
async function goInbox($: Engine, target?: Target, isApp = false) {
  if (look.isOpening) return
  look.isOpening = true
  try {
    $.ui.toast(await openInbox($, target, isApp))
  } finally {
    look.isOpening = false
  }
}

const isPlacedNow = async ($: Engine) => (await $.ui.panes()).some(p => p.id === PANE && p.isPlaced)

// The panel, in its pane or, when the open was not placed, in the band. There no Button has a hotkey (a key typed
// into the prompt could press one), and Close sits on the heading row.
async function drawPanel($: Engine, e: DrawInput, isBand: boolean) {
  const { Box, Text, Button } = $.ui.resolve(e)
  const rows = await read($, rowsA)
  const w = e.props.bodyColumns
  let n = 0
  const digit = () => (isBand || n >= 9 ? {} : { hotkey: String(++n) })
  const go = () => goInbox($)
  // Rows are drawn only from a reply that named the inbox.
  const goTo = (run: number, round: number, item: number, isApp = false) => () =>
    goInbox($, { run, round, item, inbox: rows?.inbox ?? 0 }, isApp)
  // With the app, each row ends in an App button with no hotkey (a hotkey is one digit or letter, Shift folded,
  // and 1-9 are the rows'): Tab and Enter, or a click.
  const hasApp = await read($, hasAppA)
  const row = (key: string, text: string, run: number, round: number, item: number) => {
    const browser = (
      <Button key={key} plain {...digit()} onPress={goTo(run, round, item)}>
        {fit(text, w - (hasApp ? 11 : 6))}
      </Button>
    )
    return hasApp ? (
      <Box key={`row:${key}`} flexDirection="row" width={w}>
        {browser}
        <Box flexGrow={1} />
        <Button key={`app:${key}`} plain dimColor label="App" onPress={goTo(run, round, item, true)} />
      </Box>
    ) : (
      browser
    )
  }
  const listed = rows?.rounds.length ?? 0
  const waiting = await read($, countA) // the band's count: todo items and waiting sessions too
  const status = !rows
    ? 'looking…'
    : rows.isFailed
      ? 'no inbox answered; Open the inbox starts one'
      : listed
        ? `${listed} open round${listed === 1 ? '' : 's'} here${rows.more ? `, ${rows.more} more in the inbox` : ''}`
        : waiting
          ? `no open rounds here; ${waiting} waiting in the inbox (todo items, sessions)`
          : 'no open rounds here'
  const heading = (
    <Text wrap="truncate-end">
      <Text weight="bold">{TITLE}</Text>
      <Text dimColor> · {status}</Text>
    </Text>
  )
  const list = (rows?.rounds ?? []).flatMap(r => [
    row(`round:${r.run}/${r.round}`, r.title, r.run, r.round, r.item),
    ...r.items.map(i => row(`item:${r.run}/${r.round}/${i.item}`, `  · ${i.title}`, r.run, r.round, i.item)),
  ])
  return (
    <Box flexDirection="column" width={w}>
      {isBand ? (
        <Box flexDirection="row" gap={2} width={w}>
          {heading}
          <Box flexGrow={1} />
          <Button key="band-close" plain label="Close" onPress={() => closePanel($)} />
        </Box>
      ) : (
        heading
      )}
      {list}
      <Box marginTop={1} gap={3} flexWrap="wrap">
        <Button key="all" plain {...(isBand ? {} : { hotkey: 'a' })} label={rows?.isFailed ? 'Open the inbox' : 'All projects in the inbox'} onPress={go} />
        {hasApp ? <Button key="app:all" plain dimColor label="App" onPress={() => goInbox($, undefined, true)} /> : null}
        {isBand ? null : <Button key="close" plain hotkey="q" role="dismiss" label="Close" onPress={() => closePanel($)} />}
      </Box>
    </Box>
  )
}

// Starts the looking, once per load, in an interactive session: from session.start, or from the band's first
// drawing when a reload fires no session.start (it fires "for changed modules only"), and in the desktop app and
// the VS Code extension, whose session.start says isInteractive false (tried 2026-11-15).
async function arm($: Engine) {
  if (look.isArmed) return
  look.isArmed = true
  if (hint() !== 'status') $.ui.status(undefined) // a status line stays until replaced, also past a switch in /config
  look.isWindows = (await $.env.get('OS')) === 'Windows_NT'
  if (look.isWindows) return // no inbox there yet: nothing to look at
  look.candidates = await candidates($)
  $.clock.every(EVERY, () => void poll($)) // a reload drops it with the module
}

async function onSessionStart($: Engine, e: Args<'session.start'>, next: Next<'session.start'>) {
  const started = await next(e)
  if (e.isInteractive) await arm($)
  return started
}

// A /clear ends the conversation and empties $.state but keeps this module: this load's notes on the requests go too,
// so the new mark is looked at from a fresh listing.
async function onSessionEnd(_$: Engine, e: Args<'session.end'>, next: Next<'session.end'>) {
  if (e.reason === 'clear') Object.assign(look, { listedAt: -1, isCut: false, failures: 0, retryAt: 0 })
  return next(e)
}

// Typed at the prompt it opens the panel and says nothing: the command's output row would go into the conversation.
// A `claude -p` run (origin sdk) has no screen: it opens the inbox, and its output is the line. Windows: a toast.
async function onCommandRun($: Engine, e: Args<'command.run'>) {
  if (e.origin.kind === 'sdk') return { text: (await openInbox($)).replace(/^Signoff: /, '') } // Claude Code prefixes the plugin's name
  if (look.isWindows || (await $.env.get('OS')) === 'Windows_NT') $.ui.toast(await openInbox($))
  else await openPanel($, false)
  return {}
}

// The person's close mark closes the pane without our Close: the band must learn of it too.
async function onClose($: Engine, e: Args<'ui.close'>, next: Next<'ui.close'>) {
  if (e.id === PANE) await update($, whereA, () => null)
  return next(e)
}

async function onPane($: Engine, e: RenderInput<'Pane'>, next: Next<'ui.render', RenderInput<'Pane'>>) {
  if (e.requestId !== PANE) return next(e)
  return drawPanel($, e, false)
}

// The count row, or nothing while the panel's pane is up.
async function bandRow($: Engine, e: RenderInput<'AbovePrompt'>) {
  if (hint() !== 'band') return null
  const n = await read($, countA)
  const hiddenFor = await read($, hiddenA)
  if (!n || hiddenFor === n) return null
  // whereA subscribes the band to opens and closes; the pane list says whether it shows now (placed after a widening too).
  if ((await read($, whereA)) !== null && (await $.ui.panes()).some(p => p.id === PANE && p.isShown)) return null
  const { Box, Text, Button } = $.ui.resolve(e)
  return (
    <Box flexDirection="row" gap={2} width={e.props.bodyColumns}>
      <Text wrap="truncate-end">{line(n)}</Text>
      <Box flexGrow={1} />
      <Button key="open" plain label="Open" onPress={() => openPanel($, true)} />
      <Button key="hide" plain dimColor label="Hide" onPress={async () => { const shown = await read($, countA); await update($, hiddenA, () => shown) }} />
    </Box>
  )
}

// The band is shared with other mods' rows: keep what next(e) drew, ours on top. A panel that was not placed draws
// here, whatever the hint, until the engine places its pane (a wider terminal): one surface draws it at a time.
async function onBand($: Engine, e: RenderInput<'AbovePrompt'>, next: Next<'ui.render', RenderInput<'AbovePrompt'>>) {
  await arm($)
  if (e.props.hasSurvey) return next(e)
  const isBand = (await read($, whereA)) === 'band' && !(await isPlacedNow($))
  const mine = isBand ? await drawPanel($, e, true) : await bandRow($, e)
  if (!mine) return next(e)
  const { Box } = $.ui.resolve(e)
  // next(e) rejects only when nothing answers beneath (a test without an engine hook): ours still draws.
  const below = await next(e).catch(() => undefined)
  return below ? (
    <Box flexDirection="column">
      {mine}
      {below}
    </Box>
  ) : (
    mine
  )
}

export const register: Register = (on, options) => {
  look.options = options
  on('session.start', onSessionStart)
  on('session.end', onSessionEnd)
  on('command.run', { command: COMMAND }, onCommandRun)
  on('ui.close', onClose)
  on('ui.render', { component: 'AbovePrompt' }, onBand)
  on('ui.render', { component: 'Pane', requestId: PANE }, onPane)
}
