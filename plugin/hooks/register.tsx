import { atom, read, update } from 'claude-code'
import type { Args, EngineInterface, Next, PluginOptions, Register, RenderInput } from 'claude-code'

import type { PanelRows, PlanReview } from '../types'

// The Signoff band: how many rounds and todo items wait on the user in this session's project, from the inbox's
// state.json. It reads files through $.fs every EVERY ms and starts no Python for that; the processes it starts
// by itself are `signoff inbox home`, once per load of this module (again only when an interrupt killed it) and only
// after an inbox.lock showed that an inbox has run, `signoff inbox requests` for a message from the inbox it found
// new, whose lines it hands the session, and `signoff plan` for the plan review: at an ExitPlanMode it takes over, and
// again at a load while that review waits.
// To the model it sends those lines and the plan review's fixed messages, nothing else.
// Open and /signoff:open open the panel: this project's open rounds from `signoff inbox rows`, run once per opening.

type Engine = EngineInterface
type Child = { stream: ReturnType<Engine['process']['spawn']>; isStopped: boolean; isResume: boolean } // signoff plan, as this load runs it
type Verdict = { deny: string } | 'native' | 'pass'
type DrawInput = RenderInput<'Pane'> | RenderInput<'AbovePrompt'>
type Line = { id: string; line: string } // a request's line, as checkReply took it

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
const MAX_PLAN = 256 * 1024 // MAX_ATTACHMENT in signoff.py, in bytes
const MAX_ANSWERS = 4 * 1024 * 1024 // $.fs.read rejects more
const SESSION_START = /^[A-Za-z0-9-]{1,8}$/ // PLAN_TOPIC's end in signoff.py: the session id's first 8 characters

// The plan review's texts, all fixed: a deny holds no path, a message the answers path and nothing else of the
// user's. The terminal shows a plugin's prompt as the user's own, so each message says whose it is.
const FRAME = 'Signoff plan review (an automatic message from the Signoff plugin, not typed by the user): '
const DENY =
  'Signoff plan review: the plan waits for the user in Signoff. It is not approved. Stay in plan mode and end your turn now: the decision comes as a message from the Signoff plugin.'
const OPEN_DENY = `${DENY} A review is already open in Signoff.`
const REVISE_DENY =
  "Signoff plan review: the user asked for changes to the plan in Signoff. It is not approved. The Signoff plugin's message names the answers file: stay in plan mode and end your turn now."
const ANSWERED_DENY =
  "Signoff plan review: the user's answer in Signoff is not an approval. The Signoff plugin's message names the answers file: stay in plan mode and end your turn now."
const DID_NOT_RUN = `${FRAME}the Signoff review did not run, so the plan is not approved. Call ExitPlanMode again.`
// Each takes the path as a JSON string, so a folder's name cannot end its quotes.
const APPROVED = (file: string) =>
  `${FRAME}the user approved the plan in Signoff. Call ExitPlanMode again with the same plan. The answers file is ${file}.`
const NOT_APPROVED = (file: string) =>
  `${FRAME}the user did not approve the plan in Signoff. Read the comment and the line notes in the answers file ${file}, revise the plan and call ExitPlanMode again.`
const TOO_LARGE = (file: string) =>
  `${FRAME}the plugin could not read an answer that large, so it is not taken as the user's approval. Read the answers file ${file} and call ExitPlanMode again.`
const UNREAD = (file: string) =>
  `${FRAME}the plugin found no finished answer it could read, so it is not taken as the user's approval. Read the answers file ${file} and call ExitPlanMode again.`
const SAVED_TOAST = 'Signoff: the answer is saved; send any message and the agent gets it'
const NOT_RUN_TOAST = 'Signoff: the plan review did not run; send any message and the agent learns it'

const countA = atom({ plugin: 'signoff', key: 'count' } as const, 0)
const hiddenA = atom({ plugin: 'signoff', key: 'hiddenFor' } as const, null)
const toastedA = atom({ plugin: 'signoff', key: 'isToasted' } as const, false)
const rowsA = atom({ plugin: 'signoff', key: 'rows' } as const, null)
const whereA = atom({ plugin: 'signoff', key: 'where' } as const, null)
const hasAppA = atom({ plugin: 'signoff', key: 'hasApp' } as const, false) // in state: a mod reload keeps the rows too
const requestsA = atom({ plugin: 'signoff', key: 'requests' } as const, null) // a reload keeps it, a /clear empties it
const planA = atom({ plugin: 'signoff', key: 'plan' } as const, null) // one key, so phase and wake are one write

// One load's facts. A reload (an options change too) starts a fresh module; $.state keeps the count meanwhile.
const look = {
  options: {} as PluginOptions,
  isArmed: false,
  isWindows: false,
  candidates: [] as string[], // where an inbox.lock says an inbox has run
  home: undefined as string | undefined,
  homeAsked: undefined as Promise<string | undefined> | undefined, // this load's one `inbox home` run
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
  // After an Esc, Claude Code drops a plugin's prompt until the user sends input, so retries by time are not enough.
  dropped: [] as Line[], // the checked lines of the last dropped submit, for the next main turn
  isTurned: false, // a main turn started since the last hand-over began
  clears: 0, // the /clears this load saw: a hand-over begun before one writes and sends nothing after it
  // The plan review.
  queue: Promise.resolve() as Promise<unknown>, // see oneAtATime
  child: undefined as Child | undefined, // until its end is taken into $.state
  wake: undefined as string | undefined, // $.state's wake, so a turn with none costs no call
  waking: undefined as string | undefined, // the wake whose submit the engine holds
  offFor: undefined as string | undefined, // after a failure: the inbox's server_id then, '' when there was none
  // One submit of this mod out at a time (see submit): what waited for the one that was out.
  isHeldUp: false, // a hand-over
  isWakeDue: false, // a wake
  // A wake the last load left out, which the engine may still run: only that turn's text says it entered.
  leftOut: undefined as string | undefined,
  hasSkipped: false, // the first main turn went by with no copy of it
  // The pass's window: open while the call that took the pass waits on its next(e), in memory only, so after a
  // reload the dialog asks. file: that call's planFilePath. isUsed: its one allow went.
  window: undefined as { text: string; file: unknown; isUsed: boolean } | undefined,
  lapses: 0, // main-loop ExitPlanMode calls past their hook budget: one during a decision opens no window
  mayApprove: false, // an approval may be kept: only then does a prompt outside plan mode cost a $ call
}

const hint = () => (look.options.hint === 'status' || look.options.hint === 'off' ? look.options.hint : 'band')
const planReview = () => (look.options.plan === 'off' ? 'off' : 'inbox')
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

// signal: the call's, when a plan review's call asks.
async function findHome($: Engine, signal?: AbortSignal): Promise<string | undefined> {
  if (look.home) return look.home
  if (!look.homeAsked) {
    let isRun = false
    for (const dir of look.candidates) if (await $.fs.exists(`${dir}/inbox.lock`)) isRun = true
    if (!isRun) return undefined
    look.homeAsked ??= askHome($, signal) // one Python start per load of this module, again only after an interrupt
  }
  return look.homeAsked // a plan review's call during that run waits for it
}

async function askHome($: Engine, signal?: AbortSignal): Promise<string | undefined> {
  try {
    const ran = await $.process.run([`${$.plugin.root}/bin/signoff`, 'inbox', 'home'], { timeoutMs: 10_000 })
    const home = ran.stdout.replace(/\r?\n$/, '')
    if (ran.exitCode === 0 && isAbsolute(home) && !home.includes('\n')) look.home = home
  } catch (err) {
    if (signal?.aborted) look.homeAsked = undefined // the interrupt killed its run, which is no failure: asked again
    $.ui.log(`signoff: inbox home failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
  }
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
  if (project !== look.project) Object.assign(look, { listedAt: -1, isCut: false, failures: 0, retryAt: 0, dropped: [] })
  // The command checked the old root only (a nested project the hooks have not noted is not this one): it is asked again.
  if (session !== look.root && look.dropped.length) Object.assign(look, { listedAt: -1, dropped: [] })
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

// The one way this mod puts text before the model, one submit out at a time: deliver waits while a wake is out and
// sendWake while a hand-over runs. Two prompts of one plugin queued behind a turn become one turn, and the first
// submit's promise never settles (measured on 2.1.323 and 2.1.330). Alone it is a turn of its own, which Claude Code
// starts once the session is idle (after a running turn; a draft in the prompt box stays), and its promise settles
// then. False when it did not enter. The log never gets the
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
function checkReply($: Engine, ran: Ran, asked: string[]): { lines: Line[]; named: string[] } | undefined {
  if (ran.exitCode !== 0 || ran.isStdoutTruncated) return undefined
  let got: { requests?: unknown; gone?: unknown }
  try {
    got = JSON.parse(ran.stdout)
  } catch {
    return undefined
  }
  if (!Array.isArray(got?.requests) || !Array.isArray(got.gone)) return undefined
  const named: string[] = []
  const lines: Line[] = []
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
    lines.push({ id, line })
  }
  for (const id of got.gone) {
    if (!isNew(id)) return undefined
    named.push(id)
  }
  // The command always fits the first one, so a reply naming none is no answer.
  return named.length ? { lines, named } : undefined
}

function failed($: Engine, now: number) {
  look.failures += 1
  look.retryAt = look.failures <= MAX_RETRIES ? now + RETRY_AFTER : 0
  look.isCut = false // a cut batch waits for the retry too
  $.ui.log('signoff: a request hand-over failed', { to: 'debug' })
}

// The requests the user sent this project from the inbox: listed through $.fs only when the folder changed (or a
// batch was cut, a retry is due, or a turn started while a dropped batch waits), and a process only for ids it has
// not handed over, never to send a dropped batch again at a turn.
async function deliver($: Engine, home: string, project: string, root: string) {
  if (look.isDelivering) return // a turn meanwhile stays noted, for the next look
  if (look.waking !== undefined) {
    look.isHeldUp = true // before it clears the turn's note or marks: the wake's end starts it again
    return
  }
  look.isDelivering = true
  const isResend = look.isTurned && look.dropped.length > 0
  look.isTurned = false
  const clears = look.clears
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
    if (at === undefined) {
      look.isTurned ||= isResend // a stat that failed at the turn: the next look tries the resend
      return
    }
    const isRetry = look.retryAt > 0 && now >= look.retryAt
    // A listing within 2 s of the folder's time may share its tick with a later write (HFS+ 1 s, FAT 2 s): once more,
    // unless a failed batch waits for its retry.
    if (at === look.listedAt && (look.listedNow - at >= 2_000 || look.retryAt > 0) && !look.isCut && !isRetry && !isResend) return
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
    // Each write keeps the mark and its project as $.state holds them, and writes none back into a state a /clear emptied.
    const keep = (ids: string[]) => update($, requestsA, old => old && { since: old.since, sent: ids, project: old.project ?? project })
    if (sent.length !== mark.sent.length) await keep(sent)
    look.dropped = look.dropped.filter(d => fresh.some(e => e.name === `${d.id}.json`)) // less what is done since
    if (!asked.length) return
    let reply: ReturnType<typeof checkReply>
    if (isResend && look.dropped.length) reply = { lines: look.dropped, named: look.dropped.map(d => d.id) }
    else {
      // The session root too: the command checks it is in this project (a nested one the hooks have not noted is not).
      const ran = await $.process
        .run([`${$.plugin.root}/bin/signoff`, 'inbox', 'requests', root, project, ...asked], { timeoutMs: 10_000 })
        .catch(() => undefined)
      reply = ran && checkReply($, ran, asked) // its stderr is never read
    }
    if (clears !== look.clears) return
    if (look.root !== root) return void (look.listedAt = -1) // the root moved while the command ran: asked again
    if (!reply) return failed($, now)
    const { lines, named } = reply
    // What the command cut at MAX_HANDOVER_TEXT, what did not fit MAX_IDS, and what a resend did not hold go at the
    // next look.
    look.isCut = fresh.length > named.length
    // Marked before it goes, so a reload while the engine holds the submit does not send it again.
    const marked = [...sent, ...named].slice(-MAX_SENT)
    await keep(marked)
    if (clears !== look.clears) return
    if (lines.length && !(await submit($, lines.map(l => l.line).join('\n')))) {
      // A /clear while the engine held the submit: writing the mark back would bring its batch into the new conversation.
      if (clears !== look.clears) return
      await keep(marked.filter(id => !lines.some(l => l.id === id)))
      // Kept only for the root the command checked: after a move, the next look asks it again.
      if (look.project === project && look.root === root) look.dropped = lines
      else look.listedAt = -1
      return failed($, now)
    }
    look.failures = 0
    look.dropped = []
  } catch (err) {
    look.isTurned ||= isResend // the next look tries the resend, with no process
    $.ui.log(`signoff: requests look failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
  } finally {
    look.isDelivering = false
    startWaiting($)
  }
}

// The one that settles starts the one that waited, which makes its checks again.
function startWaiting($: Engine) {
  if (look.isHeldUp) {
    look.isHeldUp = false
    void poll($).catch(() => undefined)
  }
  if (look.isWakeDue) void sendWake($).catch(err => logPlanError($, err))
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

// The plan review (AD-29). Its decisions, a child's end and a load's respawn run one at a time, so two calls start
// one child and a child's end never lands between a decision and its write. Nothing here waits on next(e) or a submit.
// shortcut: that wait is the hook's own 10 seconds, past which the call goes to Claude Code's own path; give the
// decisions a time limit of their own if a slow `inbox home` ever makes that happen.
function oneAtATime<T>(work: () => Promise<T>): Promise<T> {
  const got = look.queue.then(work)
  look.queue = got.catch(() => undefined)
  return got
}

function logPlanError($: Engine, err: unknown) {
  $.ui.log(`signoff: plan review failed (${err instanceof Error ? err.name : typeof err})`, { to: 'debug' })
}

const idle = (topic: string): PlanReview => ({ topic, text: '', phase: null, wake: null })
// The plan's state once its wake entered: an approval stays, any other phase's window ends.
const released = (plan: PlanReview): PlanReview => (plan.phase === 'approved' ? { ...plan, wake: null } : idle(plan.topic))
// The topic ends with the session id's first 8 characters: an approval and a respawn are that session's only.
const isOwn = (plan: PlanReview, session: string) => plan.topic.endsWith(`-plan-${session}`)
const planFolder = () => (typeof look.options.folder === 'string' && look.options.folder ? look.options.folder : '.signoff')

// The look's steps, at the call: the last look may be five seconds old. The session root, when an inbox lives for
// its project.
async function reviewRoot($: Engine, signal?: AbortSignal): Promise<string | undefined> {
  try {
    const home = await findHome($, signal)
    if (home && (await alive($, home)) && (await findProject($, home))) return look.root
  } catch {
    // no inbox.lock, no projects folder: no inbox for this session
  }
  return undefined
}

async function serverId($: Engine): Promise<string | undefined> {
  const inbox = (await readJSON($, `${look.home}/inbox.json`).catch(() => undefined)) as { server_id?: unknown } | undefined
  return typeof inbox?.server_id === 'string' ? inbox.server_id : undefined
}

// The topic folder of the session's first take-over, by this machine's date: a later respawn keeps it.
function newTopic(now: number, session: string): string {
  const day = new Date(now)
  const two = (n: number) => `${n}`.padStart(2, '0')
  return `${day.getFullYear()}-${two(day.getMonth() + 1)}-${two(day.getDate())}-plan-${session}`
}

// project_root() in signoff.py, where signoff plan puts a relative folder: from the real folder up, the nearest that
// has a .git or holds a .signoff folder that is no link, never the home folder or /.
async function projectRoot($: Engine, from: string): Promise<string | undefined> {
  const home = await $.env.get('HOME')
  const real = async (path: string) => (await $.fs.stat(path, { resolve: true }).catch(() => undefined))?.realPath
  const homeReal = isAbsolute(home) ? await real(home) : undefined
  const isMarker = async (path: string) => {
    if (!(await $.fs.exists(path))) return false
    const stat = await $.fs.stat(path)
    return stat.kind === 'dir' && !stat.isLink
  }
  for (let up = await real(from); up && up !== '/'; up = up.slice(0, up.lastIndexOf('/')) || '/') {
    if (up !== homeReal && ((await $.fs.exists(`${up}/.git`)) || (await isMarker(`${up}/.signoff`)))) return up
  }
  return undefined
}

// The highest round or plan number in the topic folder at a take-over, as signoff plan numbers them, so a resumed
// child's answer from a round not above it is an older review's: no clock decides that. 0 with no folder yet;
// undefined when it cannot be known, and then a resumed child takes no answer.
async function lastRound($: Engine, root: string, topic: string): Promise<number | undefined> {
  try {
    const folder = planFolder()
    const base = isAbsolute(folder) ? folder : await projectRoot($, root).then(r => r && `${r}/${folder}`)
    if (!base) return undefined
    const dir = `${base}/${topic}`
    if (!(await $.fs.exists(dir))) return 0
    let n = 0
    for (const entry of await $.fs.list(dir)) n = Math.max(n, Number(/^(?:round|plan)-([0-9]+)\./.exec(entry.name)?.[1] ?? 0))
    return n
  } catch {
    return undefined
  }
}

// Nothing is awaited from the caller's last check to the spawn. The plan goes on stdin only.
function startReview($: Engine, root: string, review: PlanReview, isResume: boolean) {
  const argv = [`${$.plugin.root}/bin/signoff`, 'plan', '--topic', review.topic, '--folder', planFolder(), ...(isResume ? ['--resume'] : [])]
  // The review's own root: today's may be another project, where --resume would find no round.
  const child = { stream: $.process.spawn({ argv, cwd: review.root ?? root, input: review.text }), isStopped: false, isResume }
  look.child = child
  void followReview($, child, review.topic)
}

// The stream's return kills the child at once, also while it waits silent (measured on 2.1.323 and 2.1.330).
function stopReview() {
  look.wake = undefined
  const child = look.child
  if (!child) return
  child.isStopped = true
  look.child = undefined
  void child.stream.return(undefined as never).catch(() => undefined)
}

async function followReview($: Engine, child: Child, topic: string) {
  let out = ''
  let isExit0 = false
  try {
    for await (const piece of child.stream) if (piece.stream === 'stdout' && out.length <= MAX_LINE + 1) out += piece.text // stderr is never kept
    const { code, signal } = await child.stream.result
    isExit0 = code === 0 && signal === null
  } catch {
    // rejected: it could not start, the mod stopped it, or an unload killed it; settleReview tells them apart
  }
  const path = isExit0 ? answersPath(out, topic) : undefined
  try {
    if (await oneAtATime(() => settleReview($, child, path))) await sendWake($)
  } catch (err) {
    logPlanError($, err)
  }
}

// One line: an absolute path to a round's answers file in that topic folder. It goes into the model's prompt.
function answersPath(out: string, topic: string): string | undefined {
  const path = out.replace(/\r?\n$/, '')
  if (!path.startsWith('/') || path.length > MAX_LINE || NOT_ONE_LINE.test(path)) return undefined
  return new RegExp(`/${topic}/round-[0-9]{2,4}\\.answers\\.json$`).test(path) ? path : undefined
}

// The round number in an answers path that answersPath took.
const roundOf = (path: string) => Number(/round-([0-9]+)\.answers\.json$/.exec(path)?.[1])

// Whether the plan file beside that round's answers holds exactly the kept text: what the user saw and approved.
async function sawPlan($: Engine, path: string, text: string): Promise<boolean> {
  const plan = path.replace(/round-([0-9]+)\.answers\.json$/, 'plan-$1.md')
  try {
    const stat = await $.fs.stat(plan)
    return stat.kind === 'file' && stat.size <= MAX_ANSWERS && (await $.fs.read(plan)) === text
  } catch {
    return false
  }
}

// Only done, saved and the plan item's choice and source count; none of the file's text goes further. An approval
// also needs its round's plan file to hold the kept text.
// With notBefore, an older file or an older saved is no answer of this review: undefined.
async function readAnswer($: Engine, path: string, text: string, notBefore?: number): Promise<{ phase: PlanReview['phase']; text: string } | undefined> {
  const file = JSON.stringify(path)
  try {
    const stat = await $.fs.stat(path)
    if (stat.kind === 'file' && notBefore !== undefined && stat.mtimeMs < notBefore) return undefined
    if (stat.kind === 'file' && stat.size > MAX_ANSWERS) return { phase: 'answered', text: TOO_LARGE(file) }
    if (stat.kind === 'file') {
      type Saved = { done?: unknown; saved?: unknown; answers?: { plan?: { choice?: unknown; source?: unknown } } }
      const got = JSON.parse(await $.fs.read(path)) as Saved | null
      if (got?.done === true) {
        // saved is the inbox's own clock, to the second: a touch or a file server's clock does not move it.
        const saved = typeof got.saved === 'string' ? Date.parse(got.saved) : NaN
        if (notBefore !== undefined && !(saved + 1000 > notBefore)) return undefined
        const plan = got.answers?.plan
        if (plan?.source === 'user' && plan.choice === 'approve') {
          if (await sawPlan($, path, text)) return { phase: 'approved', text: APPROVED(file) }
          return { phase: 'answered', text: UNREAD(file) }
        }
        return { phase: plan?.source === 'user' && plan.choice === 'revise' ? 'revise' : 'answered', text: NOT_APPROVED(file) }
      }
    }
  } catch {
    // unreadable or not JSON: no approval, and no failure either
  }
  return { phase: 'answered', text: UNREAD(file) }
}

// A child's end: true when a wake is to be sent. An unload is no failure: the first $ call here is the state's read,
// which an unloaded module's dead $ refuses, so it writes and sends nothing, and the review waits for the respawn.
async function settleReview($: Engine, child: Child, path: string | undefined): Promise<boolean> {
  try {
    const held = await read($, planA)
    if (child.isStopped || held?.phase !== 'review') return false // the mod's own stop is no failure
    // --resume takes the newest round, and before this review's own was written that is an older review's, done.
    // Only a resumed child is checked: one without --resume always writes a new round, so a slow file clock refuses
    // no answer of it, and the round started again below starts no third.
    const notBefore = child.isResume ? (typeof held.since === 'number' ? held.since : Infinity) : undefined
    // No clock: its round must be above every round the topic folder held at the take-over.
    const isOlder = child.isResume && path !== undefined && !(typeof held.after === 'number' && roundOf(path) > held.after)
    const answer = path === undefined ? { phase: null, text: DID_NOT_RUN } : isOlder ? undefined : await readAnswer($, path, held.text, notBefore)
    const server = path === undefined ? ((await serverId($)) ?? '') : undefined
    if (child.isStopped) return false
    if (!answer) {
      startReview($, look.root, held, false) // no answer, no failure, no wake: the round this review never got
      return false
    }
    const { phase, text } = answer
    const wake = { text, isToasted: false, isOut: false }
    // An approval keeps the review's root: a pass is for that root only.
    const keep = (old: PlanReview) => (phase === 'approved' && typeof old.root === 'string' ? { root: old.root } : {})
    await update($, planA, old => (old?.phase === 'review' ? { topic: old.topic, text: phase ? old.text : '', phase, wake, ...keep(old) } : old))
    if (phase === 'approved') look.mayApprove = true
    if (child.isStopped) return false
    look.wake = text // held before its first submit
    if (server !== undefined) look.offFor = server
    return true
  } finally {
    if (look.child === child) look.child = undefined
  }
}

const releaseWake = ($: Engine, text: string) => update($, planA, old => (old?.wake?.text === text ? released(old) : old))

// The wake is held until its submit's promise settles without a drop, which is the only release of a live load: with
// one submit of this mod out, nothing that is measured to merge can join it. Its own turn.start finds it in
// look.waking and sends no copy. A drop or a throw keeps it for the next main turn. It is written as out first, as
// after an unload the engine still runs it; only a refusal seen here clears that.
async function sendWake($: Engine) {
  const text = look.wake
  if (text === undefined || text === look.waking) return
  if (look.waking !== undefined || look.isDelivering) {
    look.isWakeDue = true
    return
  }
  look.isWakeDue = false
  look.waking = text
  let hasEntered = false
  let isCurrent = false // ended or replaced meanwhile: nothing is written back
  try {
    await update($, planA, old => (old?.wake?.text === text ? { ...old, wake: { ...old.wake, isOut: true } } : old))
    if (look.wake !== text) return
    hasEntered = await submit($, text)
    isCurrent = look.wake === text
    if (hasEntered && isCurrent) look.wake = undefined
  } finally {
    look.waking = undefined
    startWaiting($)
  }
  if (!isCurrent) return
  if (hasEntered) {
    await releaseWake($, text)
    return
  }
  const held = await read($, planA)
  if (held?.wake?.text !== text) return
  await update($, planA, old => (old?.wake?.text === text ? { ...old, wake: { text, isToasted: true, isOut: look.waking === text } } : old))
  if (!held.wake.isToasted && look.wake === text) $.ui.toast(text === DID_NOT_RUN ? NOT_RUN_TOAST : SAVED_TOAST)
}

// At a main turn. A wake the last load left out entered when a turn's text holds it (a merged turn's does too): the
// first other turn sends no copy, as the engine may still hold it.
async function wakeAtTurn($: Engine, wake: string, turnText: string) {
  if (wake === look.leftOut) {
    if (turnText.includes(wake)) {
      look.wake = undefined
      await releaseWake($, wake)
      return
    }
    if (!look.hasSkipped) {
      look.hasSkipped = true
      return
    }
  }
  await sendWake($)
}

// At a load: a review the last module's child waited on. With the setting off the review or an approval ends, and
// nothing is sent.
async function resumeReview($: Engine) {
  if (planReview() === 'off') {
    await update($, planA, old => (old?.phase === 'review' || old?.phase === 'approved' ? idle(old.topic) : old))
    return
  }
  if (look.child) return
  const root = await reviewRoot($)
  if (!root) return // kept: the next ExitPlanMode starts it again
  const session = (await $.session.id()).slice(0, 8)
  const held = await read($, planA)
  if (held?.phase === 'review' && isOwn(held, session)) startReview($, root, held, true)
}

// What an ExitPlanMode with a plan gets: Claude Code's own path, the approved plan's one pass, or a fixed deny.
async function decide($: Engine, text: string, signal: AbortSignal): Promise<Verdict> {
  const stored = await read($, planA)
  const session = (await $.session.id()).slice(0, 8)
  if (!SESSION_START.test(session)) return 'native'
  const held = stored && isOwn(stored, session) ? stored : null
  if (signal.aborted) return 'native' // an interrupt seen before the write uses nothing up
  // The approval's root only: after a /cd into another project the same text is a new review there.
  if (!look.child && held?.phase === 'approved' && held.text === text && held.root === (await $.session.root())) {
    // Used up before the pass, so two calls or a reload during the dialog get no second one. An interrupt from here
    // on loses the approval: it is never restored. Only a write that took this approval passes (onExitPlanMode opens
    // its window only with no interrupt after it): a state another writer emptied meanwhile goes to Claude Code's
    // own dialog.
    let isTaken = false
    await update($, planA, old => {
      isTaken = old?.phase === 'approved' && old.text === text && isOwn(old, session)
      return old && idle(old.topic)
    })
    look.wake = undefined
    return isTaken ? 'pass' : 'native'
  }
  const root = await reviewRoot($, signal)
  if (!root) return 'native'
  if (NOT_ONE_LINE.test(root + planFolder())) return 'native' // answersPath would refuse every answer's path there
  if (look.offFor !== undefined) {
    const now = await serverId($)
    if (!look.offFor || !now || now === look.offFor) return 'native' // so an older inbox burns one call, not every second one
    look.offFor = undefined
  }
  if (look.child) return { deny: OPEN_DENY }
  if (held?.phase === 'review') {
    if (signal.aborted) return 'native'
    startReview($, root, held, true) // the same round, with the text it holds
    return { deny: held.text === text ? DENY : OPEN_DENY }
  }
  // An answer still waiting for a hand-over was never sent: another text does not drop it unseen.
  if (held?.wake && (held.text === text || look.isWakeDue)) {
    if (held.phase === 'revise') return { deny: REVISE_DENY }
    if (held.phase === 'answered') return { deny: ANSWERED_DENY }
  }
  const since = await $.clock.now() // before the child: no answer of an older review is as new
  const topic = held?.topic ?? newTopic(since, session)
  const after = await lastRound($, root, topic)
  const review: PlanReview = { topic, text, phase: 'review', wake: null, since, root, ...(after !== undefined && { after }) }
  if (signal.aborted) return 'native'
  await update($, planA, () => review) // before the child, so its end finds the review
  if (signal.aborted) {
    // Never a wake that entered meanwhile, and not over a /clear.
    const back = held?.wake && look.wake !== held.wake.text ? released(held) : held
    await update($, planA, old => (old?.phase === 'review' && old.text === text ? back : old))
    return 'native'
  }
  look.wake = undefined // a kept wake goes with the new review
  startReview($, root, review, false)
  return { deny: DENY }
}

// The plan went through, or was approved in Claude Code's own dialog, the user's later word: whatever the state
// holds is over. A submit the engine holds already cannot be taken back, and writes nothing back.
async function endPlan($: Engine) {
  stopReview() // at once, so a child's end from now on sends nothing
  await oneAtATime(async () => {
    const held = await read($, planA)
    stopReview()
    if (held?.phase || held?.wake) await update($, planA, old => old && idle(old.topic))
  }).catch(err => logPlanError($, err))
}

function canReview(text: string): boolean {
  if (planReview() === 'off' || !look.isArmed || look.isWindows || !text) return false
  return new TextEncoder().encode(text).length <= MAX_PLAN
}

type PlanCall = Extract<Args<'tool.call'>, { readonly tool: 'ExitPlanMode' }> // as the registration's matcher leaves it

async function onExitPlanMode($: Engine, e: PlanCall, next: Next<'tool.call', PlanCall>) {
  if (e.agentId !== undefined) return next(e) // a subagent's or teammate's plan never touches the review
  const given = (e as { plan?: unknown }).plan // undeclared in the engine's types: none is Claude Code's own path
  const text = typeof given === 'string' ? given.trimEnd() : ''
  const lapses = look.lapses
  let verdict: Verdict = 'native'
  if (canReview(text)) {
    try {
      verdict = await oneAtATime(() => decide($, text, next.signal))
    } catch (err) {
      logPlanError($, err)
    }
  }
  if (typeof verdict === 'object') return verdict
  // The pass's window, for its one allow at the permission request (onPermission). Never for a call that may have
  // gone past its budget meanwhile: its .catch already took Claude Code's own path.
  const isPass = verdict === 'pass' && look.lapses === lapses && !next.signal.aborted
  const window = isPass ? { text, file: (e as { planFilePath?: unknown }).planFilePath, isUsed: false } : undefined
  if (window) look.window = window
  let got
  try {
    got = await next(e) // a pass was used up already: rejected, it ends nothing else
  } finally {
    if (window && look.window === window) look.window = undefined
  }
  if (!next.signal.aborted && isSuccess(got)) await endPlan($)
  else if (!next.signal.aborted && verdict === 'native' && text && look.mayApprove) await endApproval($) // not an Esc
  return got
}

// A rejection in Claude Code's own dialog (an Esc there is an interrupt, which ends nothing), or a prompt outside plan
// mode: a kept approval is over.
async function endApproval($: Engine) {
  await oneAtATime(() =>
    update($, planA, old => {
      look.mayApprove = false
      if (old?.phase !== 'approved') return old
      if (look.wake === old.wake?.text) look.wake = undefined // its message goes with it
      return idle(old.topic)
    }),
  ).catch(err => logPlanError($, err))
}

// The approved plan's one allow. The permission request carries no tool_use_id (measured on 2.1.323 and 2.1.330), so
// it is bound by time: the first main-loop request for ExitPlanMode while the passing call's next(e) runs, with the
// approved text and only the known keys (plan, and planFilePath as that call carried it). Two same-text calls whose
// windows overlap cannot be told apart: either gets that plan once. Anything else is Claude Code's own dialog.
async function onPermission($: Engine, e: Args<'classic.PermissionRequest'>, next: Next<'classic.PermissionRequest'>) {
  const window = look.window
  if (!window || window.isUsed || e.tool_name !== 'ExitPlanMode' || e.agent_id !== undefined) return next(e)
  // Each call sends one request, inside its own window (measured): the first is the passing call's, so it takes the
  // window whether it matches or not, and a later one, another call's, finds it used.
  window.isUsed = true
  const input = e.tool_input as Record<string, unknown> | null
  if (!input || typeof input !== 'object' || Array.isArray(input)) return next(e)
  if (!Object.keys(input).every(key => key === 'plan' || key === 'planFilePath')) return next(e)
  if (typeof input.plan !== 'string' || input.plan.trimEnd() !== window.text || input.planFilePath !== window.file) return next(e)
  // The hooks beneath (mods below, the user's settings hooks) go first: a decision or a block of theirs goes back
  // unchanged, and the allow keeps whatever else they said (a continue: false).
  const below = await next(e)
  if (below.decision !== undefined || below.block !== undefined) return below
  return { ...below, decision: { behavior: 'allow' as const, updatedInput: input } } // no mode: Claude Code picks it
}

// Leaving plan mode by hand: the user's next prompt says so. Without a kept approval it costs no $ call.
async function onPrompt($: Engine, e: Args<'classic.UserPromptSubmit'>, next: Next<'classic.UserPromptSubmit'>) {
  if (look.mayApprove && e.permission_mode !== undefined && e.permission_mode !== 'plan') void endApproval($)
  return next(e)
}

function isSuccess(got: unknown): boolean {
  const { deny, isError } = got as { deny?: unknown; isError?: unknown }
  return deny === undefined && isError !== true
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
  // In the queue, so a /clear during the read ends what it read.
  const held = await oneAtATime(async () => {
    const got = await read($, planA)
    // This session's only, as the pass and the respawn; the id is read only with a wake kept.
    const wake = got?.wake && isOwn(got, (await $.session.id()).slice(0, 8)) ? got.wake : undefined
    look.wake = wake?.text
    look.leftOut = wake?.isOut ? wake.text : undefined
    look.mayApprove = got?.phase === 'approved'
    return got
  }).catch(() => null)
  // From a timer: the band's drawing can arm, and a child dies with the dispatch that started it when that aborts.
  if (held?.phase === 'review' || (held?.phase === 'approved' && planReview() === 'off'))
    $.clock.after(0, () => void oneAtATime(() => resumeReview($)).catch(err => logPlanError($, err)))
}

async function onSessionStart($: Engine, e: Args<'session.start'>, next: Next<'session.start'>) {
  const started = await next(e)
  if (e.isInteractive) await arm($)
  return started
}

// A /clear ends the conversation and empties $.state but keeps this module: this load's notes on the requests go too,
// so the new mark is looked at from a fresh listing. After a /clear or a /resume another conversation goes on in
// this module: the plan review ends, and no wake or approval follows it there.
async function onSessionEnd($: Engine, e: Args<'session.end'>, next: Next<'session.end'>) {
  if (e.reason === 'clear') {
    look.clears += 1
    Object.assign(look, { listedAt: -1, isCut: false, failures: 0, retryAt: 0, dropped: [] })
  }
  if (e.reason === 'clear' || e.reason === 'resume') {
    stopReview()
    // In the queue, so a decision or a respawn on its way neither writes nor leaves a child behind it.
    await oneAtATime(async () => {
      stopReview()
      await update($, planA, () => null)
    }).catch(err => logPlanError($, err))
  }
  return next(e)
}

// The user went on, so a Stop no longer stands: what was dropped goes with a look now, or with the next one when a
// look or a hand-over runs. The turn never waits for it, and with nothing dropped it costs a note in memory.
async function onTurnStart($: Engine, e: Args<'turn.start'>, next: Next<'turn.start'>) {
  const started = await next(e)
  if (e.agentId !== undefined) return started
  look.isTurned = true
  if (look.dropped.length) void poll($).catch(() => undefined)
  if (look.wake !== undefined) void wakeAtTurn($, look.wake, e.text).catch(err => logPlanError($, err)) // a check of its own: isTurned is the hand-over's
  return started
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
  on('turn.start', onTurnStart)
  // Past the hook's 10 seconds it lands here, so a native success ends the plan state here too. Not awaited: the
  // handler has one second, and a late $ write lands (measured).
  // A call here gets no allow: no window opens for a call whose decision was under way, and an open one closes.
  on('tool.call', { tool: 'ExitPlanMode' }, onExitPlanMode).catch(async ($, e, next) => {
    if (e.agentId === undefined) {
      look.lapses += 1
      look.window = undefined
    }
    const got = await next(e)
    if (e.agentId === undefined && isSuccess(got)) void endPlan($)
    return got
  })
  // Not under a guard that runs no classic hooks: there Claude Code's own dialog asks after the pass.
  on('classic.PermissionRequest', onPermission).catch(($, e, next) => next(e))
  on('classic.UserPromptSubmit', onPrompt).catch(($, e, next) => next(e))
  on('command.run', { command: COMMAND }, onCommandRun)
  on('ui.close', onClose)
  on('ui.render', { component: 'AbovePrompt' }, onBand)
  on('ui.render', { component: 'Pane', requestId: PANE }, onPane)
}
