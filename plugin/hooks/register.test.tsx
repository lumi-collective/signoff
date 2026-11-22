import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

// Synthetic data only.
const PLUGIN = 'signoff'
const HOME = '/home/test'
const SO = `${HOME}/.local/state/signoff` // the Signoff home on Linux
const MAC = `${HOME}/Library/Application Support/signoff`
const ROOT = '/work/demo'
const ID = '0123456789abcdef'
const OTHER = 'fedcba9876543210'
const NOW = Date.parse('2026-11-10T09:00:00Z')
const SURFACES = ['terminal', 'desktop'] as const
// What `signoff inbox open` says on stderr when no browser opened: the mod must never pass it on.
const LINK = 'http://127.0.0.1:50123/b/3f9a1c7e5b2d4f6a8c0e1b3d5f7a9c2e/'
const ok = (stdout: string, stderr = '', exitCode = 0) => ({ exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false })

type File = { text: string; mtimeMs: number; kind?: 'file' | 'dir'; isLink?: boolean; size?: number }
/** A child of $.process.spawn: the test ends it, or the mod stops it (the stream's return). */
type Child = {
  argv: string[]
  cwd: string | undefined
  input: string | undefined
  isStopped: boolean
  end: (stdout?: string, code?: number | null, signal?: string | null) => void
}
type Plan = { topic: string; text: string; phase: 'review' | 'approved' | 'revise' | 'answered' | null; wake: { text: string; isToasted: boolean } | null } | null
type Ran = { stdout: string; exitCode: number; isStdoutTruncated?: boolean }
type Marks = { since: number; sent: string[]; project?: string; projects?: Record<string, number> } | null
type World = {
  files: Record<string, File>
  ran: string[][]
  toasts: string[]
  statuses: (string | undefined)[]
  logs: string[]
  fsCalls: number
  /** Every path the mod stat'ed or listed, in order. */
  looked: string[]
  listFailures: number
  openExit: number
  /** What `signoff inbox rows <cwd>` prints and exits with. */
  rowsOut: { stdout: string; exitCode: number; isStdoutTruncated?: boolean }
  /** What `signoff inbox requests <folder> <project> <id>…` answers (for the session folder); by default each asked id's line, or gone. */
  requestsOut: ((ids: string[]) => Ran) | undefined
  /** The prompts the mod submitted, and its requests marks as they stood at each submit. */
  submits: { text: string; asUser: boolean | undefined; sent: string[] }[]
  /** While set, a submit returns { drop } with it. */
  drop: string | undefined
  /** While set, a submit waits for it: one the engine holds. */
  submitGate: Promise<void> | undefined
  /** $.state's `requests` as the engine keeps it: a test seeds it (a reload) or empties it (a /clear). */
  marks: Marks
  /** $.state's `plan` as the engine keeps it, as marks; and how it stood at each submit. */
  plan: Plan
  planAtSubmit: Plan[]
  planReads: number
  /** While set, the plan's state refuses, as every $ call of an unloaded module does. */
  isStateDead: boolean
  /** While set, a read or a write of the plan's state, the session id or the load's `inbox home` waits for it. */
  planGetGate: Promise<void> | undefined
  planSetGate: Promise<void> | undefined
  idGate: Promise<void> | undefined
  homeGate: Promise<void> | undefined
  sessionId: string
  spawns: Child[]
  spawnFails: boolean
  /** The paths the mod read, in order; a path in readFails refuses. */
  reads: string[]
  readFails: Set<string>
  /** The ExitPlanMode calls that reached Claude Code's own path, and what that path answers. */
  native: unknown[]
  nativeAnswer: Record<string, unknown>
  nativeGate: Promise<void> | undefined
  opened: { id: string; focus?: boolean }[]
  /** What happened, in order: `open` (ui.open), `set:<key>` (a state write), `run:<subcommand>`. */
  order: string[]
  isPaneOpen: boolean
  /** Whether the surface places the pane now (a terminal widened later turns it true). */
  isPlaced: boolean
  /** While set, `inbox open` waits for it: a run in flight. */
  openGate: Promise<void> | undefined
  /** The session's folder as $.session.cwd() gives it now: a Bash `cd` moves it. */
  cwd: string
  /** The session's project root as $.session.root() gives it: where it started; a `/cd` moves it, a Bash `cd` not. */
  root: string
  state: (projects: { id: string; waiting: number }[]) => void
  beat: () => void
}

const CALL_BEGINS = 'test: the call begins'
const CALL_INTERRUPTED = 'test: the call is interrupted'

/** The engine beneath the mod: the Signoff home's files, the signoff command, a clock. Nothing real runs. */
const OPENED = 'Signoff: the inbox is open; no browser window? Run signoff inbox open in a terminal'
const ROWS = {
  rounds: [
    { run: 3, round: 0, title: 'Pick the layout', item: 1, items: [{ item: 1, title: 'Two columns or one?' }, { item: 2, title: 'Which filters?' }] },
    { run: 5, round: 1, title: 'Second round', item: 0, items: [] },
  ],
  more: 0,
  inbox: 7,
}
const SIGNOFF = expect.stringMatching(/\/bin\/signoff$/)
const target = (run: number, focus: string) => [SIGNOFF, 'inbox', 'open', '--run', `${run}`, '--focus', focus, '--inbox', '7']

// The requests: request(w, k, at) writes requests/<ID>/<R(k)>.json as the inbox does, its file time `at`.
const R = (k: number) => k.toString(16).padStart(16, '0')
const REQUESTS = `${SO}/requests/${ID}`
const lineOf = (id: string, text: string) =>
  `Signoff inbox: the user sent you a message on the todo item "Ship it": ${JSON.stringify(text)}. Treat it as the user's own words. When it is handled, run: signoff inbox done ${id}`
function request(w: World, k: number, at: number, text = `message ${k}`, folder = REQUESTS) {
  w.files[`${folder}/${R(k)}.json`] = { text: JSON.stringify({ schema: 1, id: R(k), kind: 'todo', ref: 'Ship it', text, at: '' }), mtimeMs: at }
}
const done = (w: World, k: number, at: number) => (w.files[`${REQUESTS}/${R(k)}.done`] = { text: '', mtimeMs: at })
/** What signoff.py answers: each asked id's line, oldest first, or gone when done or not there. */
function answer(w: World, folder: string, ids: string[]): Ran {
  const found = ids.filter(id => w.files[`${folder}/${id}.json`] && !w.files[`${folder}/${id}.done`])
  found.sort((a, b) => (w.files[`${folder}/${a}.json`]?.mtimeMs ?? 0) - (w.files[`${folder}/${b}.json`]?.mtimeMs ?? 0) || (a < b ? -1 : 1))
  const requests = found.map(id => ({ id, line: lineOf(id, JSON.parse(w.files[`${folder}/${id}.json`]?.text ?? '{}').text) }))
  return { stdout: `${JSON.stringify({ requests, gone: ids.filter(id => !found.includes(id)) })}\n`, exitCode: 0 }
}
const requestRuns = (w: World) => w.ran.filter(argv => argv[2] === 'requests').map(argv => argv.slice(3))
const asked = (...ks: number[]) => [`${ROOT}/src`, ID, ...ks.map(R)]

function world(
  on: On,
  {
    env = { HOME } as Record<string, string>,
    home = SO,
    project = true,
    lock = true,
    cwd = `${ROOT}/src`,
    links = {} as Record<string, string>,
    isPlaced = true,
  } = {},
) {
  const clock = mock.clock(on, { now: NOW })
  let now = NOW
  const w: World = {
    files: {},
    ran: [],
    toasts: [],
    statuses: [],
    logs: [],
    fsCalls: 0,
    looked: [],
    listFailures: 0,
    openExit: 0,
    rowsOut: { stdout: `${JSON.stringify(ROWS)}\n`, exitCode: 0 },
    requestsOut: undefined,
    submits: [],
    drop: undefined,
    submitGate: undefined,
    marks: null,
    plan: null,
    planAtSubmit: [],
    planReads: 0,
    isStateDead: false,
    planGetGate: undefined,
    planSetGate: undefined,
    idGate: undefined,
    homeGate: undefined,
    sessionId: '0a1b2c3d-4e5f-6789-abcd-ef0123456789',
    spawns: [],
    spawnFails: false,
    reads: [],
    readFails: new Set(),
    native: [],
    nativeAnswer: { result: { plan: 'shown', isAgent: false } },
    nativeGate: undefined,
    opened: [],
    order: [],
    isPaneOpen: false,
    isPlaced,
    openGate: undefined,
    cwd,
    root: cwd,
    state: projects => {
      const waiting = projects.reduce((n, p) => n + p.waiting, 0)
      w.files[`${home}/state.json`] = {
        text: JSON.stringify({ schema: 1, at: '2026-11-10T09:00:00Z', waiting, projects: projects.map(p => ({ ...p, monogram: 'DE' })) }),
        mtimeMs: now + Math.random(),
      }
    },
    beat: () => {
      const lockFile = w.files[`${home}/inbox.lock`]
      if (lockFile) lockFile.mtimeMs = now
    },
  }
  if (lock) w.files[`${home}/inbox.lock`] = { text: '', mtimeMs: now }
  if (project) w.files[`${home}/projects/${ID}.json`] = { text: JSON.stringify({ schema: 1, id: ID, root: ROOT, name: 'demo' }), mtimeMs: now }
  w.files[`${home}/projects/${OTHER}.json`] = { text: JSON.stringify({ schema: 1, id: OTHER, root: '/work', name: 'work' }), mtimeMs: now }
  const missing = { deny: 'ENOENT: no such file or directory' }
  const dirOf = (path: string) => Object.keys(w.files).filter(f => f.startsWith(`${path}/`))
  on('env.get', (_, e) => ({ value: env[e.name] }))
  on('fs.exists', (_, e) => (w.fsCalls++, { value: e.path in w.files || dirOf(e.path).length > 0 }))
  on('fs.stat', (_, e) => {
    w.fsCalls++
    w.looked.push(e.path)
    const file = w.files[e.path]
    const isDir = !file && (dirOf(e.path).length > 0 || e.path.startsWith(ROOT) || e.path === '/work' || e.path in links)
    if (!file && !isDir) return missing
    const mtimeMs = file ? file.mtimeMs : Math.max(0, ...dirOf(e.path).map(f => w.files[f]?.mtimeMs ?? 0))
    const realPath = links[e.path] ?? e.path
    return { value: { kind: file ? 'file' : 'dir', size: file ? (file.size ?? file.text.length) : 0, mtimeMs, isLink: e.path in links, ...(e.resolve ? { realPath } : {}) } }
  })
  on('fs.read', (_, e) => {
    w.fsCalls++
    w.reads.push(e.path)
    if (w.readFails.has(e.path)) return { deny: 'EACCES: permission denied' }
    const file = w.files[e.path]
    return file ? { value: file.text } : missing
  })
  on('fs.list', (_, e) => {
    w.fsCalls++
    w.looked.push(`list:${e.path}`)
    if (w.listFailures > 0) return (w.listFailures--, { deny: 'EIO: i/o error' })
    const names = dirOf(e.path).map(f => f.slice(e.path.length + 1)).filter(n => !n.includes('/'))
    // As $.fs.list gives it: a file's own time, 0 for a folder or a link.
    const entry = (f: File | undefined) =>
      f?.kind === 'dir' || f?.isLink ? { kind: f.kind ?? 'file', mtimeMs: 0, isLink: !!f.isLink } : { kind: 'file' as const, mtimeMs: f?.mtimeMs ?? 0, isLink: false }
    return { value: names.map(name => ({ name, size: 1, ...entry(w.files[`${e.path}/${name}`]) })) }
  })
  // As measured: an abort of a dispatch rejects the process.run it started. The kit hands that abort to no hook
  // beneath, so the plugin that interrupts a call says when the call begins and when it is interrupted.
  let callRuns: (() => void)[] | undefined
  on('process.run', async (_, e) => {
    const argv = [...e.argv]
    w.ran.push(argv)
    w.order.push(`run:${argv[2]}`)
    const aborted = { deny: 'process.run: aborted' }
    const held = (gate: Promise<void> | undefined) =>
      new Promise<boolean>(resolve => {
        callRuns?.push(() => resolve(true))
        void Promise.resolve(gate).then(() => resolve(false))
      })
    if (argv.slice(1).join(' ') === 'inbox home') {
      if (await held(w.homeGate)) return aborted
      return { value: ok(`${home}\n`) }
    }
    if (argv.slice(1, 3).join(' ') === 'inbox requests') {
      // The session folder, then the project: as signoff.py, it refuses a folder that is not in that project.
      const folder = links[argv[3] ?? ''] ?? argv[3] ?? ''
      const of = folder === ROOT || folder.startsWith(`${ROOT}/`) ? ID : folder === '/work' || folder.startsWith('/work/') ? OTHER : ''
      if (of !== argv[4]) return { value: ok('', 'signoff: inbox requests: that folder is not in that project', 1) }
      const ids = argv.slice(5)
      const out = w.requestsOut ? w.requestsOut(ids) : answer(w, `${home}/requests/${argv[4]}`, ids)
      return { value: { ...ok(out.stdout, `signoff: ${LINK}\n`, out.exitCode), isStdoutTruncated: out.isStdoutTruncated ?? false } }
    }
    if (argv.slice(1, 3).join(' ') === 'inbox open') {
      if (await held(w.openGate)) return aborted
      return { value: ok('', `signoff: no browser opened; open ${LINK} yourself (it works once)\n`, w.openExit) }
    }
    if (argv.slice(1).join(' ') === `inbox rows ${cwd}`)
      return { value: { ...ok(w.rowsOut.stdout, `signoff: ${LINK}\n`, w.rowsOut.exitCode), isStdoutTruncated: w.rowsOut.isStdoutTruncated ?? false } }
    return { value: ok('', 'unexpected', 2) }
  })
  // `requests` is kept here, as the engine would, so a test can seed it (a reload keeps $.state) or empty it (a /clear).
  let version = 0
  let planVersion = 0
  const dead = { deny: "state: the plugin's environment is unloaded" }
  on('state.get', async (_, e, next) => {
    const key = (e as { key?: string }).key
    if (key === 'requests') return { value: { value: w.marks, version } }
    if (key !== 'plan') return next(e)
    w.planReads++
    await w.planGetGate
    return w.isStateDead ? dead : { value: { value: w.plan, version: planVersion } }
  })
  on('state.set', async (_, e, next) => {
    const write = e as { key?: string; value?: unknown; ifVersion?: number }
    w.order.push(`set:${write.key}`)
    if (write.key === 'plan') {
      await w.planSetGate
      if (w.isStateDead) return dead
      if (write.ifVersion !== undefined && write.ifVersion !== planVersion) return { value: { isSet: false as const, version: planVersion } }
      w.plan = write.value as Plan
      return { value: { isSet: true as const, version: ++planVersion } }
    }
    if (write.key !== 'requests') return next(e)
    if (write.ifVersion !== undefined && write.ifVersion !== version) return { value: { isSet: false as const, version } }
    w.marks = write.value as Marks
    return { value: { isSet: true as const, version: ++version } }
  })
  // As measured: this plugin's prompts queued behind one turn become one turn, and only the last submit settles.
  const queued: object[] = []
  on('prompt.submit', async (_, e) => {
    w.submits.push({ text: e.text, asUser: e.origin?.kind === 'plugin' ? e.origin.asUser : undefined, sent: [...(w.marks?.sent ?? [])] })
    w.planAtSubmit.push(w.plan)
    if (w.submitGate) {
      const mine = {}
      queued.push(mine)
      await w.submitGate
      if (queued.at(-1) !== mine) await new Promise<never>(() => {})
      queued.length = 0
    }
    return w.drop === undefined ? { text: e.text } : { drop: w.drop }
  })
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('turn.start', (_, e) => ({ turnId: `engine ${e.turnId}` })) // so a hook's own answer shows
  on('ui.open', (_, e) => {
    w.opened.push({ id: e.id, focus: e.focus })
    w.order.push('open')
    w.isPaneOpen = true
    return { value: w.isPlaced ? { isPlaced: true as const } : { isPlaced: false as const, reason: 'no attached surface places panes' } }
  })
  on('ui.close', () => ((w.isPaneOpen = false), { value: undefined }))
  on('ui.panes', () => ({
    value: w.isPaneOpen ? [{ id: PLUGIN, title: 'Signoff', isShown: w.isPlaced, isFocused: w.isPlaced, isPlaced: w.isPlaced }] : [],
  }))
  on('ui.toast', (_, e) => (w.toasts.push(e.text), { value: undefined }))
  on('ui.status', (_, e) => (w.statuses.push(e.text), { value: undefined }))
  on('ui.log', (_, e) => {
    if (e.text === CALL_BEGINS) callRuns = []
    else if (e.text === CALL_INTERRUPTED) {
      for (const abort of callRuns ?? []) abort()
      callRuns = undefined
    } else w.logs.push(e.text)
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: w.cwd }))
  on('session.root', () => ({ value: w.root }))
  on('session.id', async () => {
    await w.idGate
    return { value: w.sessionId }
  })
  on('process.spawn', async function* (_, e, next) {
    type End = { stdout: string; code: number | null; signal: string | null }
    let settle = (_end: End) => {}
    const ended = new Promise<End>(resolve => (settle = resolve))
    const child: Child = {
      argv: [...e.argv],
      cwd: e.cwd,
      input: e.input,
      isStopped: false,
      end: (stdout = '', code = 0, signal = null) => settle({ stdout, code, signal }),
    }
    // The mod closing the stream kills the child, as the engine does (measured).
    const stopped = () => {
      child.isStopped = true
      child.end('', null, 'SIGTERM')
    }
    if (next.signal.aborted) stopped() // closed before its first pull got here
    next.signal.addEventListener('abort', stopped)
    w.spawns.push(child)
    w.order.push('spawn')
    if (w.spawnFails) throw new Error('spawn ENOENT') // the stream's first pull rejects
    yield { stream: 'stderr' as const, text: `signoff: waiting; ${LINK}\n` }
    const { stdout, code, signal } = await ended
    // Text as it came, not lines.
    if (stdout) yield { stream: 'stdout' as const, text: stdout.slice(0, 7) }
    if (stdout.length > 7) yield { stream: 'stdout' as const, text: stdout.slice(7) }
    return { value: { code, signal } } as never
  })
  on('tool.call', async (_, e) => {
    w.native.push((e as { plan?: unknown }).plan)
    await w.nativeGate
    return w.nativeAnswer as never
  })
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  // Five seconds on: the inbox touched inbox.lock meanwhile unless it died.
  const after = async (ms: number, { isAlive = true } = {}) => {
    for (let left = ms; left > 0; left -= 1000) {
      now += 1000
      if (isAlive) w.beat()
      await clock.advance(1000)
    }
  }
  return { clock, w, after }
}

const start = ($: Engine, { cwd = `${ROOT}/src`, isInteractive = true } = {}) =>
  $.session.start({ cwd, surface: isInteractive ? 'terminal' : null, isInteractive })

const openCommand = ($: Engine, kind: 'composer' | 'sdk' = 'composer') =>
  $.command.run({ command: 'signoff:open', args: '', origin: { kind }, presentation: { isFullscreen: true, columns: 140 } })

const band = (overrides: { hasSurvey?: boolean; bodyColumns?: number } = {}) => ({
  plugin: PLUGIN,
  component: 'AbovePrompt' as const,
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {}, ...overrides },
})

const pane = (bodyColumns = 64) => ({
  plugin: PLUGIN,
  component: 'Pane' as const,
  requestId: PLUGIN,
  props: { title: 'Signoff', isFocused: true, bodyColumns, placement: 'dock' as const, scroll: { offset: 0, bodyRows: 16 }, view: {} },
})

/** The panel's buttons as drawn: key, text, hotkey. */
async function paneButtons($: Engine, surface: (typeof SURFACES)[number] = 'terminal') {
  const ui = await $.ui.mount({ ...pane(), surface })
  const buttons = (await ui.findAll({ type: 'Button' })).map(b => ({ key: b.key, text: b.text, hotkey: b.props.hotkey }))
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  await ui.unmount()
  return { buttons, texts }
}

const rowsRuns = (w: World) => w.ran.filter(argv => argv[2] === 'rows')

async function bandTexts($: Engine, surface: (typeof SURFACES)[number] = 'terminal') {
  const ui = await $.ui.mount({ ...band(), surface })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  await ui.unmount()
  return texts
}

test('the band shows the project count while the inbox lives, above the rows of the mods below', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }, { id: OTHER, waiting: 5 }])
  await start($)
  await after(5000)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...band(), surface })
    const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    expect(texts[0]).toBe('Signoff: 2 waiting')
    expect(texts.indexOf('engine')).toBeGreaterThan(0)
    expect((await ui.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['open', 'hide'])
    for (const button of await ui.findAll({ type: 'Button' })) expect(button.props.hotkey).toBeUndefined()
    await ui.unmount()
  }
  // The home came from the command once; every look after that is a file read.
  expect(w.ran).toEqual([[expect.stringMatching(/\/bin\/signoff$/), 'inbox', 'home']])
  await after(30_000)
  expect(w.ran).toHaveLength(1)
})

test('Open opens the panel: the rounds and their questions, each opening its item, then All projects, the list', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 1 }])
  await start($)
  await after(5000)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  w.order = []
  await ui.press({ key: 'open' })
  await ui.unmount()
  expect(w.opened).toEqual([{ id: PLUGIN, focus: true }]) // the person's press: the pane takes the keys
  expect(w.order[0]).toBe('open') // before any state write or command: the open is the press's own
  expect(w.order).toEqual(expect.arrayContaining(['set:where', 'set:rows', 'run:rows'])) // the writes are seen
  expect(rowsRuns(w)).toEqual([[expect.stringMatching(/\/bin\/signoff$/), 'inbox', 'rows', `${ROOT}/src`]])
  expect(await bandTexts($)).toEqual(['engine']) // the count row steps aside while the pane is shown
  for (const surface of SURFACES) {
    const { buttons, texts } = await paneButtons($, surface)
    expect(texts[0]).toBe('Signoff · 2 open rounds here')
    expect(buttons).toEqual([
      { key: 'round:3/0', text: 'Pick the layout', hotkey: '1' },
      { key: 'item:3/0/1', text: '  · Two columns or one?', hotkey: '2' },
      { key: 'item:3/0/2', text: '  · Which filters?', hotkey: '3' },
      { key: 'round:5/1', text: 'Second round', hotkey: '4' },
      { key: 'all', text: 'All projects in the inbox', hotkey: 'a' },
      { key: 'close', text: 'Close', hotkey: 'q' },
    ])
  }
  const panel = await $.ui.mount({ ...pane(), surface: 'terminal' })
  // A question opens itself, a round the item the inbox named for it, All projects the list.
  const pressed = [
    ['item:3/0/2', target(3, '0/2')],
    ['round:3/0', target(3, '0/1')],
    ['round:5/1', target(5, '1/0')],
    ['all', [SIGNOFF, 'inbox', 'open']],
  ] as const
  for (const [key, argv] of pressed) {
    await panel.press({ key })
    expect(w.ran.at(-1)).toEqual(argv)
  }
  expect(w.toasts).toEqual(pressed.map(() => OPENED)) // true with a browser and without one: the mod cannot tell, as it reads no stderr
  w.openExit = 1
  await panel.press({ key: 'round:5/1' })
  expect(w.toasts.at(-1)).toBe('Signoff: the inbox did not open; run signoff inbox open in a terminal to see why')
  // A second press while one `inbox open` runs starts no second one.
  let release = () => {}
  w.openGate = new Promise(resolve => (release = resolve))
  const opens = () => w.ran.filter(argv => argv[2] === 'open').length
  const before = opens()
  const presses = [panel.press({ key: 'all' }), panel.press({ key: 'round:3/0' })]
  release()
  await Promise.all(presses)
  expect(opens()).toBe(before + 1)
  // rows ran once for this opening, never on a poll
  await after(30_000)
  expect(rowsRuns(w)).toHaveLength(1)
  await panel.press({ key: 'close' })
  await panel.unmount()
  expect((await bandTexts($))[0]).toBe('Signoff: 1 waiting')
  const shown = [...(await bandTexts($)), ...w.toasts, ...w.statuses.map(String), ...w.logs].join('\n')
  expect(shown).not.toContain('127.0.0.1')
  expect(shown).not.toContain('/b/')
  expect(shown).not.toMatch(/[0-9a-f]{16}/)
})

test('the panel shows agent text as one cut line, and never the command stderr', async ($, on) => {
  const { w } = world(on)
  w.rowsOut.stdout = JSON.stringify({
    rounds: [{ run: 0, round: 0, title: `Red\x1b[31m\nnext${String.fromCodePoint(0x202e)}line${String.fromCodePoint(0x2028)}end${LINK}`, item: 1, items: [{ item: 1, title: 'z'.repeat(300) }] }],
    more: 7,
    inbox: 7,
  })
  await start($)
  await openCommand($)
  const ui = await $.ui.mount({ ...pane(400), surface: 'terminal' })
  const buttons = await ui.findAll({ type: 'Button' })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  await ui.unmount()
  expect(buttons[0]?.text).toBe(`Red [31m next line end${LINK}`) // the agent's own text: a URL in it stays
  expect(buttons[1]?.text).toBe(`  · ${'z'.repeat(200)}`)
  expect(texts[0]).toBe('Signoff · 1 open round here, 7 more in the inbox')
  for (const text of [...buttons.map(b => b.text), ...texts]) expect(text).not.toMatch(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u)
  expect(JSON.stringify([buttons.slice(1).map(b => b.text), texts, w.toasts, w.logs])).not.toContain(LINK) // stderr's
})

test('a reply past the mod cap shows its first 24 rows and counts the rest in more', async ($, on) => {
  const { w } = world(on)
  const items = (n: number) => Array.from({ length: n }, (_, k) => ({ item: k, title: `q${k}` }))
  w.rowsOut.stdout = JSON.stringify({ rounds: [{ run: 0, round: 0, title: 'a', item: 0, items: items(30) }, { run: 1, round: 0, title: 'b', item: 0, items: items(4) }], more: 2, inbox: 7 })
  await start($)
  await openCommand($)
  const { buttons, texts } = await paneButtons($)
  expect(buttons.filter(b => b.key?.startsWith('round:') || b.key?.startsWith('item:'))).toHaveLength(24)
  expect(texts[0]).toBe('Signoff · 1 open round here, 14 more in the inbox') // 7 items cut, round b and its 4, the 2 sent
})

test('a rows command that fails or prints another shape still gives the panel, whose Open the inbox starts one', async ($, on) => {
  const { w } = world(on)
  await start($)
  const bad = [
    { stdout: '', exitCode: 1 },
    { stdout: `${JSON.stringify(ROWS)}\n`, exitCode: 0, isStdoutTruncated: true },
    { stdout: '{', exitCode: 0 },
    { stdout: '[]', exitCode: 0 },
    { stdout: JSON.stringify({ rounds: [{ run: '3', round: 0, title: 'x', item: 0, items: [] }], more: 0, inbox: 7 }), exitCode: 0 },
    { stdout: JSON.stringify({ rounds: [{ run: 3, round: 0, title: 'x', item: 0, items: [{ item: -1, title: 'y' }] }], more: 0, inbox: 7 }), exitCode: 0 },
    { stdout: JSON.stringify({ rounds: [], more: 1.5 }), exitCode: 0 },
    { stdout: JSON.stringify({ error: 'unknown request' }), exitCode: 0 },
    // Every inbox with a rows op names itself and each round's item: rounds without them are another shape.
    ...[undefined, 0, -1, 1.5, '7', null].map(inbox => ({ stdout: JSON.stringify({ ...ROWS, inbox }), exitCode: 0 })),
    ...[undefined, -1, 1.5, '1', null].map(item => ({ stdout: JSON.stringify({ ...ROWS, rounds: [{ ...ROWS.rounds[0], item }] }), exitCode: 0 })),
  ]
  for (const out of bad) {
    w.rowsOut = out
    await openCommand($)
    const { buttons, texts } = await paneButtons($)
    expect(texts[0]).toBe('Signoff · no inbox answered; Open the inbox starts one')
    expect(buttons.map(b => [b.key, b.text])).toEqual([['all', 'Open the inbox'], ['close', 'Close']])
  }
  const panel = await $.ui.mount({ ...pane(), surface: 'terminal' })
  await panel.press({ key: 'all' })
  await panel.unmount()
  expect([w.ran.at(-1)?.slice(1), w.toasts]).toEqual([['inbox', 'open'], [OPENED]]) // inbox open starts one
  w.rowsOut = { stdout: JSON.stringify({ rounds: [], more: 0 }), exitCode: 0 }
  await openCommand($)
  expect((await paneButtons($)).texts[0]).toBe('Signoff · no open rounds here')
  expect(rowsRuns(w)).toHaveLength(bad.length + 1)
  // With the app, Open the inbox has its App button too.
  w.files[PLIST] = { text: plist(), mtimeMs: NOW }
  w.rowsOut = { stdout: '', exitCode: 1 }
  await openCommand($)
  expect((await paneButtons($)).buttons.map(b => [b.key, b.text])).toEqual([['all', 'Open the inbox'], ['app:all', 'App'], ['close', 'Close']])
})

const PLIST = `${HOME}/Applications/Signoff.app/Contents/Info.plist`
const plist = (id = 'io.github.lumi-collective.signoff.inbox') =>
  `<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n\t<key>CFBundleIdentifier</key>\n\t<string>${id}</string>\n</dict>\n</plist>\n`
const inApp = (run: number, focus: string) => [SIGNOFF, 'inbox', 'open', '--app', '--run', `${run}`, '--focus', focus, '--inbox', '7']
const APP_OPENED = 'Signoff: opening the Signoff app'

test('with the app installed each row has an App button, no hotkey, that opens its item in the app', async ($, on) => {
  const { w } = world(on)
  w.files[PLIST] = { text: plist(), mtimeMs: NOW }
  w.rowsOut.stdout = JSON.stringify({ ...ROWS, rounds: [{ ...ROWS.rounds[0], item: 2 }, ROWS.rounds[1]] })
  await start($)
  await openCommand($)
  const { buttons } = await paneButtons($)
  expect(buttons).toEqual([
    { key: 'round:3/0', text: 'Pick the layout', hotkey: '1' },
    { key: 'app:round:3/0', text: 'App', hotkey: undefined },
    { key: 'item:3/0/1', text: '  · Two columns or one?', hotkey: '2' },
    { key: 'app:item:3/0/1', text: 'App', hotkey: undefined },
    { key: 'item:3/0/2', text: '  · Which filters?', hotkey: '3' },
    { key: 'app:item:3/0/2', text: 'App', hotkey: undefined },
    { key: 'round:5/1', text: 'Second round', hotkey: '4' },
    { key: 'app:round:5/1', text: 'App', hotkey: undefined },
    { key: 'all', text: 'All projects in the inbox', hotkey: 'a' },
    { key: 'app:all', text: 'App', hotkey: undefined },
    { key: 'close', text: 'Close', hotkey: 'q' },
  ])
  const panel = await $.ui.mount({ ...pane(), surface: 'terminal' })
  // A round's row opens the item the inbox named for it, as in the browser; All projects' App the app, no target.
  const pressed = [
    ['app:item:3/0/1', inApp(3, '0/1')],
    ['app:round:3/0', inApp(3, '0/2')],
    ['app:round:5/1', inApp(5, '1/0')],
    ['round:3/0', target(3, '0/2')],
    ['app:all', [SIGNOFF, 'inbox', 'open', '--app']],
  ] as const
  for (const [key, argv] of pressed) {
    await panel.press({ key })
    expect(w.ran.at(-1)).toEqual(argv)
  }
  expect(w.toasts).toEqual([APP_OPENED, APP_OPENED, APP_OPENED, OPENED, APP_OPENED]) // not "the question is open": an older app shows the list
  w.openExit = 1
  await panel.press({ key: 'app:item:3/0/2' })
  expect(w.toasts.at(-1)).toBe('Signoff: the app did not open; run signoff inbox open in a terminal to see the inbox')
  // One `inbox open` at a time, an App press included.
  let release = () => {}
  w.openGate = new Promise(resolve => (release = resolve))
  const before = w.ran.filter(argv => argv[2] === 'open').length
  const presses = [panel.press({ key: 'app:item:3/0/1' }), panel.press({ key: 'item:3/0/1' })]
  release()
  await Promise.all(presses)
  expect(w.ran.filter(argv => argv[2] === 'open').length).toBe(before + 1)
  await panel.unmount()
  expect(JSON.stringify([w.toasts, w.logs])).not.toContain('127.0.0.1')
})

test('no App button without the app bundle or with another bundle id', async ($, on) => {
  const { w } = world(on)
  await start($)
  const appButtons = async () => (await paneButtons($)).buttons.filter(b => b.key?.startsWith('app:')).length
  await openCommand($)
  expect(await appButtons()).toBe(0) // none installed
  w.files[PLIST] = { text: plist('io.github.lumi-collective.signoff.inbox.probe'), mtimeMs: NOW }
  await openCommand($)
  expect(await appButtons()).toBe(0)
  w.files['/Applications/Signoff.app/Contents/Info.plist'] = { text: plist(), mtimeMs: NOW }
  await openCommand($)
  expect(await appButtons()).toBe(5) // the system's Applications folder counts too: four rows and All projects
})

test('in the band, All projects has an App button with no hotkey, which opens the app with no target', async ($, on) => {
  const { w } = world(on, { isPlaced: false })
  w.files[PLIST] = { text: plist(), mtimeMs: NOW }
  await start($)
  await openCommand($)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  const buttons = await ui.findAll({ type: 'Button' })
  expect(buttons.map(b => b.key).slice(-2)).toEqual(['all', 'app:all'])
  for (const button of buttons) expect(button.props.hotkey).toBeUndefined()
  await ui.press({ key: 'app:all' })
  await ui.unmount()
  expect([w.ran.at(-1), w.toasts]).toEqual([[SIGNOFF, 'inbox', 'open', '--app'], [APP_OPENED]])
})

test('no rounds while the band counts some: the panel says the rest waits in the inbox', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }]) // a waiting todo item and a session, say
  w.rowsOut = { stdout: JSON.stringify({ rounds: [], more: 0 }), exitCode: 0 }
  await start($)
  await after(5000)
  await openCommand($)
  expect((await paneButtons($)).texts[0]).toBe('Signoff · no open rounds here; 2 waiting in the inbox (todo items, sessions)')
})

test('a panel that is not placed draws in the band, with no hotkeys and its own Close', async ($, on) => {
  const { w, after } = world(on, { isPlaced: false })
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  let ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  await ui.press({ key: 'open' })
  await ui.unmount()
  ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  const buttons = await ui.findAll({ type: 'Button' })
  expect(buttons.map(b => b.key)).toEqual(['band-close', 'round:3/0', 'item:3/0/1', 'item:3/0/2', 'round:5/1', 'all'])
  for (const button of buttons) expect(button.props.hotkey).toBeUndefined()
  expect((await ui.findAll({ type: 'Text' })).map(t => t.text).at(-1)).toBe('engine')
  await ui.press({ key: 'all' })
  expect(w.toasts).toEqual([OPENED])
  await ui.press({ key: 'band-close' })
  await ui.unmount()
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('a pane placed later (a wider terminal) takes the list off the band: one surface draws it', async ($, on) => {
  const { w, after } = world(on, { isPlaced: false })
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  await openCommand($)
  expect((await bandTexts($))[0]).toBe('Signoff · 2 open rounds here')
  w.isPlaced = true
  expect(await bandTexts($)).toEqual(['engine']) // neither the list nor the count row: the pane shows both
  expect((await paneButtons($)).texts[0]).toBe('Signoff · 2 open rounds here')
})

// A test's $ cannot raise the person's ui.close; the engine's pane list says the pane is gone just the same.
test('a pane no longer shown brings the count row back', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  await openCommand($)
  expect(await bandTexts($)).toEqual(['engine'])
  w.isPaneOpen = false
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('/signoff:open opens the panel without taking the keys, with no text for the conversation', async ($, on) => {
  const { w } = world(on)
  await start($)
  const reply = await openCommand($)
  expect(reply.text).toBeUndefined()
  expect(w.opened).toEqual([{ id: PLUGIN, focus: undefined }]) // the person may type on: a digit must not press a row
  expect(w.order[0]).toBe('open')
  expect(w.ran.at(-1)?.slice(1)).toEqual(['inbox', 'rows', `${ROOT}/src`])
  expect(w.toasts).toEqual([])
  expect(JSON.stringify(reply)).not.toContain('127.0.0.1')
})

test('claude -p: no polling, and /signoff:open answers in one line of text', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($, { isInteractive: false })
  await after(20_000)
  expect(w.ran).toEqual([])
  expect(w.fsCalls).toBe(0)
  const reply = await openCommand($, 'sdk')
  expect(reply.text).toBe(OPENED.replace('Signoff: ', '')) // Claude Code puts "signoff: " before it
})

test('a reload that fires no session.start (an options change) still looks, with the new hint', { options: { hint: 'status' } }, async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 3 }])
  expect(await bandTexts($)).toEqual(['engine']) // the first drawing after the load starts the looking
  await after(5000)
  expect(w.statuses.at(-1)).toBe('Signoff: 3 waiting · /signoff:open')
  await after(5000)
  expect(w.ran).toHaveLength(1) // armed once, however often the band is drawn
  expect(await bandTexts($)).toEqual(['engine'])
  const reply = await openCommand($) // typed at the prompt: the panel, no text for the conversation
  expect([reply.text, w.opened.length]).toEqual([undefined, 1])
})

test('Hide holds until the count changes', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  await ui.press({ key: 'hide' })
  await ui.unmount()
  expect(await bandTexts($)).toEqual(['engine'])
  await after(30_000)
  expect(await bandTexts($)).toEqual(['engine'])
  w.state([{ id: ID, waiting: 3 }])
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 3 waiting')
})

test('a toast once per session, when the count rises from 0 for two looks; none for what waited at the start', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 1 }])
  await start($)
  await after(5000)
  expect(w.toasts).toEqual([])
  w.state([{ id: ID, waiting: 0 }])
  await after(5000)
  w.state([{ id: ID, waiting: 1 }]) // a permission prompt answered within seconds, say
  await after(5000)
  w.state([{ id: ID, waiting: 0 }])
  await after(5000)
  expect(w.toasts).toEqual([])
  w.state([{ id: ID, waiting: 2 }])
  await after(10_000)
  expect(w.toasts).toEqual(['Signoff: 2 waiting · /signoff:open'])
  w.state([{ id: ID, waiting: 0 }])
  await after(5000)
  w.state([{ id: ID, waiting: 1 }])
  await after(5000)
  expect(w.toasts).toHaveLength(1)
})

test('status: the count in the status line, not in the band', { options: { hint: 'status' } }, async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 3 }])
  await start($)
  await after(5000)
  expect(w.statuses.at(-1)).toBe('Signoff: 3 waiting · /signoff:open')
  expect(await bandTexts($)).toEqual(['engine'])
  w.state([{ id: ID, waiting: 0 }])
  await after(5000)
  expect(w.statuses.at(-1)).toBeUndefined()
})

// The hint is about showing what waits; the messages the user sends from the inbox still reach the session.
test('off: nothing shown and no count read, yet requests reach the session, and /signoff:open opens the panel', { options: { hint: 'off' } }, async ($, on) => {
  const { w, after, clock } = world(on)
  w.state([{ id: ID, waiting: 3 }])
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(25_000)
  expect(await bandTexts($)).toEqual(['engine'])
  expect([w.toasts, w.statuses]).toEqual([[], [undefined]]) // a status line from before is cleared
  expect(w.ran.map(argv => argv.slice(1, 3).join(' '))).toEqual(['inbox home', 'inbox requests'])
  expect(w.submits.map(s => s.text)).toEqual([lineOf(R(1), 'message 1')])
  expect(w.looked).not.toContain(`${SO}/state.json`)
  await openCommand($)
  expect(w.ran.at(-1)).toEqual([SIGNOFF, 'inbox', 'rows', `${ROOT}/src`])
  expect((await paneButtons($)).buttons.map(b => b.key)).toContain('all')
})

// The panel lists the same project the band counts: the session root's, not where a Bash `cd` went.
test('the panel asks for the rows of the session root, also after a Bash cd', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  await after(5000)
  w.cwd = '/work'
  await openCommand($)
  expect(w.ran.at(-1)).toEqual([SIGNOFF, 'inbox', 'rows', `${ROOT}/src`])
})

test('no count without a live inbox: a lock not touched for 15 s is a dead inbox', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
  await after(20_000, { isAlive: false }) // killed: state.json still says 2
  expect(await bandTexts($)).toEqual(['engine'])
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('an inbox that stops and comes back neither undoes Hide nor toasts', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
  await ui.press({ key: 'hide' })
  await ui.unmount()
  await after(20_000, { isAlive: false })
  await after(5000)
  expect(await bandTexts($)).toEqual(['engine'])
  expect(w.toasts).toEqual([])
  w.state([{ id: ID, waiting: 3 }])
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 3 waiting')
  expect(w.toasts).toEqual([])
})

test('no inbox has run: no command runs, and the band stays empty', async ($, on) => {
  const { w, after } = world(on, { lock: false })
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(30_000)
  expect(w.ran).toEqual([])
  expect(await bandTexts($)).toEqual(['engine'])
  // An inbox started later in the session is found at the next look.
  w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW + 30_000 }
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
  expect(w.ran).toHaveLength(1)
})

test('the macOS home is looked at too', async ($, on) => {
  const { w, after } = world(on, { home: MAC })
  w.state([{ id: ID, waiting: 4 }])
  await start($)
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 4 waiting')
  expect(w.ran).toHaveLength(1)
})

test('SIGNOFF_INBOX_HOME is looked at instead of the defaults', async ($, on) => {
  const { w, after } = world(on, { env: { HOME, SIGNOFF_INBOX_HOME: '/tmp/sov.test' }, home: '/tmp/sov.test' })
  w.state([{ id: ID, waiting: 1 }])
  await start($)
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 1 waiting')
})

test('the project is the longest root the hooks noted that holds the session folder, also one noted later', async ($, on) => {
  const { w, after } = world(on, { project: false })
  w.state([{ id: ID, waiting: 2 }, { id: OTHER, waiting: 7 }])
  w.files[`${SO}/projects/.tmp-1.json`] = { text: JSON.stringify({ schema: 1, id: 'aaaaaaaaaaaaaaaa', root: ROOT }), mtimeMs: NOW }
  await start($)
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 7 waiting') // /work holds /work/demo/src
  w.files[`${SO}/projects/${ID}.json`] = { text: JSON.stringify({ schema: 1, id: ID, root: ROOT, name: 'demo' }), mtimeMs: NOW + 5000 }
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('a session started at the project root itself is that project', async ($, on) => {
  const { w, after } = world(on, { cwd: ROOT })
  w.state([{ id: ID, waiting: 2 }, { id: OTHER, waiting: 7 }])
  await start($, { cwd: ROOT })
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('a session folder reached through a link is matched by where it lands', async ($, on) => {
  const { w, after } = world(on, { cwd: '/links/demo', links: { '/links/demo': `${ROOT}/src` } })
  w.state([{ id: ID, waiting: 2 }])
  await start($, { cwd: '/links/demo' })
  await after(5000)
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('a failed listing of projects/ is tried again at the next look', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  w.listFailures = 1
  await start($)
  await after(5000)
  expect(await bandTexts($)).toEqual(['engine'])
  await after(5000) // the folder's time is unchanged
  expect((await bandTexts($))[0]).toBe('Signoff: 2 waiting')
})

test('a session outside every project, or a project with nothing waiting, shows nothing', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: OTHER, waiting: 0 }, { id: ID, waiting: 0 }])
  await start($)
  await after(5000)
  expect(await bandTexts($)).toEqual(['engine'])
})

test('a state.json that is not schema 1 with whole counts shows nothing', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  for (const text of ['{', '[]', JSON.stringify({ schema: 2, projects: [{ id: ID, waiting: 2 }] }), JSON.stringify({ schema: 1, projects: [{ id: ID, waiting: 2.5 }] }), JSON.stringify({ schema: 1, projects: [{ id: ID, waiting: '2' }] })]) {
    w.files[`${SO}/state.json`] = { text, mtimeMs: Math.random() }
    await after(5000)
    expect(await bandTexts($)).toEqual(['engine'])
  }
})

test('Windows: no inbox there, so nothing is read or run; /signoff:open says so', async ($, on) => {
  const { w, after } = world(on, { env: { USERPROFILE: 'C:\\Users\\test', OS: 'Windows_NT' } })
  await start($)
  await after(30_000)
  expect([w.ran, w.fsCalls]).toEqual([[], 0])
  await openCommand($)
  expect(w.ran).toEqual([])
  expect(w.toasts).toEqual(['Signoff: the inbox does not run on Windows yet'])
})

test('a survey takes the band', async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 2 }])
  await start($)
  await after(5000)
  const ui = await $.ui.mount({ ...band({ hasSurvey: true }), surface: 'terminal' })
  expect((await ui.findAll({ type: 'Text' })).map(t => t.text)).toEqual(['engine'])
  await ui.unmount()
})

const lists = (w: World) => w.looked.filter(p => p === `list:${REQUESTS}`).length

test('a new request goes to the session once: one command for its id, one submit of its line, framed as from the plugin', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000) // the first live look sets the mark; no requests folder yet, so nothing is listed
  expect([w.marks, lists(w)]).toEqual([{ since: NOW + 5000 - 10_000, sent: [], project: ID }, 0])
  request(w, 1, clock.now())
  await after(5000)
  expect(w.ran.at(-1)).toEqual([SIGNOFF, 'inbox', 'requests', `${ROOT}/src`, ID, R(1)])
  // As the command printed it, not asUser, and marked handed over before it went.
  expect(w.submits).toEqual([{ text: lineOf(R(1), 'message 1'), asUser: undefined, sent: [R(1)] }])
  expect(w.marks).toEqual({ since: NOW + 5000 - 10_000, sent: [R(1)], project: ID }) // the project stays beside it
  await after(30_000) // the folder's time is unchanged: no listing, no process
  expect([requestRuns(w), w.submits.length, lists(w)]).toEqual([[asked(1)], 1, 1])
  request(w, 2, clock.now())
  request(w, 3, clock.now())
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(asked(2, 3))
  expect(w.submits.at(-1)?.text).toBe(`${lineOf(R(2), 'message 2')}\n${lineOf(R(3), 'message 3')}`)
  const shown = [...w.toasts, ...w.statuses.map(String), ...w.logs, ...w.submits.map(s => s.text)].join('\n')
  expect(shown).not.toContain('127.0.0.1') // the command's stderr holds a link: never read
})

test('a done, temporary, older, linked, folder or misnamed entry is listed but starts no process', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const at = clock.now()
  request(w, 1, at)
  done(w, 1, at)
  w.files[`${REQUESTS}/.tmp-${R(2)}.json`] = { text: '{}', mtimeMs: at }
  request(w, 3, NOW - 5001) // before the mark: the SessionStart hand-over's
  request(w, 4, at)
  w.files[`${REQUESTS}/${R(4)}.json`] = { text: '', mtimeMs: at, isLink: true }
  w.files[`${REQUESTS}/${R(5)}.json`] = { text: '', mtimeMs: at, kind: 'dir' }
  w.files[`${REQUESTS}/${R(6).replace('0', 'A')}.json`] = { text: '', mtimeMs: at }
  w.files[`${REQUESTS}/${R(7)}.json.bak`] = { text: '', mtimeMs: at }
  await after(5000)
  expect([lists(w), requestRuns(w)]).toEqual([1, []])
  done(w, 3, clock.now()) // `signoff inbox done`: listed again, nothing new
  await after(5000)
  expect([lists(w), requestRuns(w)]).toEqual([2, []])
})

test('a reload hands nothing over again: the marks stay in $.state, dropping only the ids marked done here', async ($, on) => {
  const { w, after, clock } = world(on)
  request(w, 1, NOW - 1000)
  request(w, 9, NOW - 1000)
  done(w, 9, NOW - 500) // `signoff inbox done` since
  // R(8) is not in this folder: another project's, handed over before a /cd, so it stays marked.
  w.marks = { since: NOW - 20_000, sent: [R(1), R(8), R(9)] }
  await start($)
  await after(10_000)
  expect([requestRuns(w), w.submits]).toEqual([[], []])
  expect(w.marks).toEqual({ since: NOW - 20_000, sent: [R(1), R(8)], project: ID })
  request(w, 2, clock.now())
  await after(5000)
  expect([requestRuns(w), w.marks?.sent]).toEqual([[asked(2)], [R(1), R(8), R(2)]])
})

test('a /clear sets a new mark: what waits from before is the clear hand-over, and what comes after goes', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  await after(20_000)
  expect([w.submits.length, lists(w)]).toEqual([1, 1])
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  w.marks = null as Marks // Claude Code empties $.state at a /clear; the module stays
  await after(5000)
  expect(lists(w)).toBe(2) // this load's notes went with the clear: listed again, the folder unchanged
  expect([w.marks?.since, w.submits.length]).toEqual([clock.now() - 10_000, 1])
  request(w, 2, clock.now())
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(asked(2))
  expect(w.submits).toHaveLength(2)
})

test('a session open before the inbox lived gets its mark at the first live look, and a request after it goes', async ($, on) => {
  const { w, after, clock } = world(on, { lock: false })
  await start($)
  await after(30_000)
  expect(w.marks).toBeNull()
  w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: clock.now() }
  request(w, 1, clock.now()) // the first message from the new inbox, at once
  await after(5000)
  expect(w.marks?.since).toBe(clock.now() - 10_000)
  expect(requestRuns(w)).toEqual([asked(1)])
  expect(w.submits).toHaveLength(1)
})

test('a reply that is not exactly what was asked hands nothing over', async ($, on) => {
  const { w, after, clock } = world(on)
  const good = (ids: string[]) => answer(w, REQUESTS, ids)
  const json = (value: unknown): Ran => ({ stdout: `${JSON.stringify(value)}\n`, exitCode: 0 })
  const one = (id: string, line: string) => json({ requests: [{ id, line }], gone: [] })
  const BAD: ((ids: string[]) => Ran)[] = [
    () => ({ stdout: '', exitCode: 1 }),
    ids => ({ ...good(ids), exitCode: 2 }),
    ids => ({ ...good(ids), isStdoutTruncated: true }),
    () => ({ stdout: '{', exitCode: 0 }),
    () => json([]),
    ids => json({ requests: { [ids[0] ?? '']: 'x' }, gone: [] }),
    ids => json({ requests: [{ id: ids[0], line: lineOf(ids[0] ?? '', 'x') }] }),
    () => json({ requests: [], gone: [] }), // names none of the ids asked
    ids => json({ requests: ['x'], gone: ids }),
    () => one(R(99), lineOf(R(99), 'not asked')),
    ids => json({ requests: [{ id: ids[0], line: lineOf(ids[0] ?? '', 'x') }], gone: [ids[0]] }),
    ids => json({ requests: [{ id: ids[0], line: lineOf(ids[0] ?? '', 'x') }, { id: ids[0], line: lineOf(ids[0] ?? '', 'x') }], gone: [] }),
    ids => json({ requests: [{ id: 7, line: lineOf(ids[0] ?? '', 'x') }], gone: [] }),
    ids => one(ids[0] ?? '', `Hello. ${lineOf(ids[0] ?? '', 'x')}`),
    ids => one(ids[0] ?? '', lineOf(R(99), 'x')),
    ids => one(ids[0] ?? '', `${lineOf(ids[0] ?? '', 'x')}.`),
    // The first x is the one in `inbox`: a frame broken by a character. A whole frame is the next test's.
    ids => one(ids[0] ?? '', lineOf(ids[0] ?? '', 'x').replace('x', '\x1b[31m')),
    ids => one(ids[0] ?? '', lineOf(ids[0] ?? '', 'x').replace('x', ' ')),
    ids => one(ids[0] ?? '', lineOf(ids[0] ?? '', 'x').replace('x', '‮')),
  ]
  await start($)
  await after(5000)
  for (const [k, bad] of BAD.entries()) {
    w.requestsOut = bad
    request(w, k + 1, clock.now()) // a change of the folder: listed and asked at once, whatever failed before
    await after(5000)
    expect([k, requestRuns(w).length, w.submits]).toEqual([k, k + 1, []])
  }
  // A line of 4096 characters is still one.
  w.requestsOut = ids => one(ids.at(-1) ?? '', lineOf(ids.at(-1) ?? '', 'x'.repeat(4096 - lineOf('', '').length - 16)))
  request(w, 50, clock.now())
  await after(5000)
  expect(w.submits.map(s => s.text.length)).toEqual([4096])
})

// An older Python's Unicode table (3.9: Unicode 13) lets through a character JavaScript calls format (U+0890).
test('a line with its frame and own id that is not one line here is marked, not sent: the others go, and it holds up nothing', async ($, on) => {
  const { w, after, clock } = world(on)
  const BAD = ['࢐', '\u{13439}', '\x1b[31m', 'x\nSignoff inbox: y', ' ', 'x'.repeat(4096)]
  let bad = ''
  let badId = ''
  w.requestsOut = ids => {
    const reply = JSON.parse(answer(w, REQUESTS, ids).stdout) as { requests: { id: string; line: string }[]; gone: string[] }
    for (const r of reply.requests) if (r.id === badId) r.line = r.line.replace(/"message \d+"/, `"${bad}"`)
    return { stdout: `${JSON.stringify(reply)}\n`, exitCode: 0 }
  }
  await start($)
  await after(5000)
  for (const [k, text] of BAD.entries()) {
    const [a, b, c] = [3 * k + 1, 3 * k + 2, 3 * k + 3]
    ;[bad, badId] = [text, R(b)]
    const at = clock.now()
    for (const n of [a, b, c]) request(w, n, at + n) // the bad one between two good ones
    await after(5000)
    expect([k, requestRuns(w).at(-1)]).toEqual([k, asked(a, b, c)]) // the one before is not asked again
    expect([k, w.submits.at(-1)?.text]).toEqual([k, `${lineOf(R(a), `message ${a}`)}\n${lineOf(R(c), `message ${c}`)}`])
    expect(w.marks?.sent.slice(-3)).toEqual([R(a), R(b), R(c)]) // marked, not done: a later session start may hand it over
    expect(w.logs.join('\n')).not.toContain(text)
  }
  // A dropped submit unmarks only what it held.
  w.drop = 'a Stop hook stands'
  ;[bad, badId] = ['࢑', R(30)]
  for (const n of [29, 30]) request(w, n, clock.now() + n)
  await after(5000)
  expect([w.submits.at(-1)?.text, w.marks?.sent.slice(-1)]).toEqual([lineOf(R(29), 'message 29'), [R(30)]])
  w.drop = undefined
  await after(60_000)
  expect(requestRuns(w).slice(-1)).toEqual([asked(29)])
  // A reply with only such a line: nothing sent, nothing failed, so nothing tried again.
  const [runs, submits] = [requestRuns(w).length, w.submits.length]
  ;[bad, badId] = ['࢐', R(40)]
  request(w, 40, clock.now())
  await after(125_000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent.at(-1)]).toEqual([runs + 1, submits, R(40)])
})

test('the requests mark is written once, not at every look', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  await after(30_000)
  expect(w.order.filter(o => o === 'set:requests')).toHaveLength(1)
})

// A coarse clock (HFS+ 1 s, FAT 2 s, a network home) gives a listing and a write right after it the same folder time.
test('a write in the same time tick as the listing goes: a listing under 2 s after the folder time is made again, once', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const tick = clock.now() + 5000 // the next look's own time
  request(w, 1, tick)
  await after(5000)
  expect([requestRuns(w), lists(w)]).toEqual([[asked(1)], 1])
  request(w, 2, tick) // after that listing, in its tick: the folder's time stays
  await after(5000)
  expect([requestRuns(w).at(-1), lists(w)]).toEqual([asked(2), 2])
  await after(30_000)
  expect(lists(w)).toBe(2)
})

test('a failed batch from a listing in the folder time tick waits the 60 s, not the next look', async ($, on) => {
  const { w, after, clock } = world(on)
  w.requestsOut = () => ({ stdout: '', exitCode: 1 })
  await start($)
  await after(5000)
  request(w, 1, clock.now() + 5000) // the next look's own time
  await after(5000)
  expect(requestRuns(w)).toHaveLength(1)
  await after(5000)
  expect(requestRuns(w)).toHaveLength(1)
  await after(55_000)
  expect(requestRuns(w)).toEqual([asked(1), asked(1)])
})

// The project follows the session's root: a `/cd` or a worktree move, never a Bash `cd`.
test('a Bash cd moves nothing: the project, the folder the command gets and the mark stay', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const mark = w.marks
  w.cwd = '/work'
  request(w, 1, clock.now())
  await after(10_000)
  expect([requestRuns(w), w.marks?.since]).toEqual([[asked(1)], mark?.since])
})

// A /cd moves the root; `inbox requests` gets a folder in the root's project.
test('a /cd into another noted project and back: each root gets its own project, and delivery goes on with no folder change', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  expect(w.submits.map(s => s.text)).toEqual([lineOf(R(1), 'message 1')])
  w.root = '/work'
  w.files[`${SO}/requests/${OTHER}/${R(7)}.json`] = {
    text: JSON.stringify({ schema: 1, id: R(7), kind: 'todo', ref: 'Ship it', text: 'elsewhere', at: '' }),
    mtimeMs: clock.now(),
  }
  request(w, 2, clock.now()) // to the first project, while the session is away
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(['/work', OTHER, R(7)])
  w.root = `${ROOT}/src`
  await after(5000) // no write since: the first project's folder is listed again all the same
  expect(requestRuns(w).at(-1)).toEqual(asked(2))
  await after(30_000)
  // Every command got a folder in its project, and nothing went twice (R(1) was handed over before the /cd).
  expect(w.ran.filter(argv => argv[2] === 'requests' && argv[3] === '/work' && argv[4] !== OTHER)).toEqual([])
  expect(w.submits.map(s => s.text)).toEqual([lineOf(R(1), 'message 1'), lineOf(R(7), 'elsewhere'), lineOf(R(2), 'message 2')])
})

// The demo project (ROOT) is nested in /work. A root change sets a new mark, as a /clear does: the older requests of
// the project it moves into come at a later session start with no other live session of the project.
test('a /cd into a nested project hands over only what is written after it; back out and in, nothing twice', async ($, on) => {
  const { w, after, clock } = world(on, { cwd: '/work' })
  request(w, 1, NOW - 60_000) // in the nested project, long before the /cd
  request(w, 2, NOW - 60_000, 'outer, before the mark', `${SO}/requests/${OTHER}`) // the session's own start had it
  await start($, { cwd: '/work' })
  await after(10_000)
  expect(requestRuns(w)).toEqual([])
  w.root = `${ROOT}/src`
  await after(5000)
  expect([requestRuns(w), w.marks?.since]).toEqual([[], clock.now() - 10_000])
  request(w, 3, clock.now())
  await after(5000)
  expect(requestRuns(w)).toEqual([asked(3)])
  w.root = '/work'
  await after(10_000)
  w.root = `${ROOT}/src`
  await after(10_000)
  expect([requestRuns(w), w.submits.map(s => s.text)]).toEqual([[asked(3)], [lineOf(R(3), 'message 3')]])
})

test('a /cd while a submit waits on the engine: once it is dropped, the next look sets the new project mark', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = 'a Stop hook stands'
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  expect(w.submits).toHaveLength(1)
  w.root = '/work'
  await after(5000)
  release() // dropped: its ids are unmarked; the next look sets the new project's mark
  await after(5000)
  expect([w.marks?.project, w.marks?.since, w.marks?.sent]).toEqual([OTHER, clock.now() - 10_000, []])
})

// The project the mark is for is in $.state with it, so a reload between a /cd and the next look still resets it.
test('a reload after a /cd: the held mark is the other project, so a new one is set and nothing older goes', async ($, on) => {
  const { w, after } = world(on, { cwd: '/work' })
  w.marks = { since: NOW - 3_600_000, sent: [], project: ID }
  request(w, 2, NOW - 1_800_000, 'outer, half an hour old', `${SO}/requests/${OTHER}`)
  await start($, { cwd: '/work' })
  await after(10_000)
  expect([requestRuns(w), w.marks]).toEqual([[], { since: NOW + 5000 - 10_000, sent: [], project: OTHER }])
})

test('a held mark with no project (a build before this one) takes the current project and keeps its time', async ($, on) => {
  const { w, after } = world(on)
  w.marks = { since: NOW - 20_000, sent: [] }
  request(w, 1, NOW - 15_000)
  await start($)
  await after(5000)
  expect([requestRuns(w), w.marks?.since, w.marks?.project]).toEqual([[asked(1)], NOW - 20_000, ID])
})

test('a root that leaves every project and then moves into another: a new mark there, nothing from meanwhile', async ($, on) => {
  const { w, after, clock } = world(on, { links: { '/else': '/else' } })
  await start($)
  await after(5000)
  w.root = '/else' // an existing folder outside every project
  await after(5000)
  request(w, 2, clock.now(), 'outer, while outside', `${SO}/requests/${OTHER}`)
  await after(20_000)
  w.root = '/work'
  await after(5000)
  expect([requestRuns(w), w.marks?.project, w.marks?.since]).toEqual([[], OTHER, clock.now() - 10_000])
})

test('a /cd within one project keeps the mark, and the command gets the new root', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const since = w.marks?.since ?? 0
  await after(30_000)
  w.root = `${ROOT}/docs`
  request(w, 1, since + 1000) // after the mark, well before this look: a new mark would leave it out
  await after(5000)
  expect([requestRuns(w), w.marks?.since, clock.now() - since > 30_000]).toEqual([[[`${ROOT}/docs`, ID, R(1)]], since, true])
})

test('a $.state with per-project marks (a build before this one) reads by its session mark', async ($, on) => {
  const { w, after } = world(on)
  request(w, 1, NOW - 60_000)
  request(w, 2, NOW - 10_000)
  w.marks = { since: NOW - 20_000, sent: [], projects: { [ID]: 0 } }
  await start($)
  await after(5000)
  expect(requestRuns(w)).toEqual([asked(2)])
})

// The folder the project was found for is the one the command gets, even if the root moved meanwhile.
test('inbox requests gets the folder its project was found for, not where the session is when it runs', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  const files = w.files
  w.files = new Proxy(files, {
    ownKeys(target) {
      if (w.looked.at(-1) === `list:${REQUESTS}`) w.root = '/work' // a /cd right after the listing
      return Reflect.ownKeys(target)
    },
  })
  await after(5000)
  expect(requestRuns(w)[0]).toEqual(asked(1))
})

test('a project change starts the failure count afresh: the new project gets its three retries', async ($, on) => {
  const { w, after, clock } = world(on)
  w.requestsOut = () => ({ stdout: '', exitCode: 1 })
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  await after(200_000)
  expect(requestRuns(w)).toHaveLength(4) // one and its three retries: no more by time
  w.root = '/work'
  request(w, 2, clock.now(), 'outer', `${SO}/requests/${OTHER}`)
  await after(5000)
  await after(60_000)
  expect(requestRuns(w).slice(4)).toEqual([
    ['/work', OTHER, R(2)],
    ['/work', OTHER, R(2)],
  ])
})

test('a failed hand-over is tried again after 60 s, three times in a row, then at the folder next change', async ($, on) => {
  const { w, after, clock } = world(on)
  const fail = () => ({ stdout: '', exitCode: 1 })
  w.requestsOut = fail
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  expect(requestRuns(w)).toHaveLength(1)
  await after(55_000)
  expect(requestRuns(w)).toHaveLength(1)
  await after(5000)
  expect(requestRuns(w)).toEqual([asked(1), asked(1)])
  await after(120_000)
  expect(requestRuns(w)).toHaveLength(4) // the third try in a row
  await after(300_000)
  expect(requestRuns(w)).toHaveLength(4)
  w.requestsOut = undefined
  request(w, 2, clock.now())
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(asked(1, 2))
  expect(w.submits).toHaveLength(1)
  // A batch that went resets the count: the next failure is tried again after 60 s.
  w.requestsOut = fail
  request(w, 3, clock.now())
  await after(5000)
  await after(60_000)
  expect(requestRuns(w).slice(-2)).toEqual([asked(3), asked(3)])
})

test('a dropped submit is unmarked and tried again; the log says it was dropped, never the text or reason', async ($, on) => {
  const { w, after, clock } = world(on)
  w.drop = 'a Stop hook stands'
  await start($)
  await after(5000)
  request(w, 1, clock.now(), 'private words')
  await after(5000)
  expect([w.submits.length, w.marks?.sent]).toEqual([1, []])
  w.drop = undefined
  await after(60_000)
  expect(w.submits.map(s => s.text)).toEqual([lineOf(R(1), 'private words'), lineOf(R(1), 'private words')])
  expect(w.marks?.sent).toEqual([R(1)])
  expect(w.logs.some(l => l.startsWith('signoff:'))).toBe(true)
  expect(w.logs.join('\n')).not.toMatch(/private words|Stop hook/)
})

// After an Esc, Claude Code drops a plugin's prompt until the user sends input: that input's turn.start.
const STOP = 'the session was stopped and nobody has sent input since'
const turn = ($: Engine, agentId?: string) => $.turn.start({ text: 'go on', turnId: 't1', ...(agentId && { agentId }) })
const spent = (w: World) => [w.fsCalls, w.ran.length, w.submits.length]

/** A request on its way while a Stop stands, its submit dropped at this look. */
async function dropOne(w: World, after: (ms: number) => Promise<void>, now: () => number) {
  w.drop = STOP
  await after(5000)
  request(w, 1, now(), 'private words')
  await after(5000)
  expect([w.submits.length, w.marks?.sent]).toEqual([1, []])
}

test('a request dropped after an Esc goes when the user goes on four minutes later, its retries used up, with no process', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(240_000)
  expect(requestRuns(w)).toHaveLength(4) // one and its three retries: no more by time
  w.drop = undefined // the user typed: no Stop stands
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(1000) // no look is due yet: the turn itself sends
  expect(requestRuns(w)).toHaveLength(4)
  expect(w.submits).toHaveLength(5)
  expect(w.submits.at(-1)).toEqual({ text: lineOf(R(1), 'private words'), asUser: undefined, sent: [R(1)] }) // marked first
  await after(600_000)
  await turn($)
  await after(5000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([4, 5, [R(1)]])
})

test('a turn with nothing held costs nothing: none waits, a request went, or one failed without a drop', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const free = async () => {
    const before = [...spent(w), w.order.length]
    expect(await turn($)).toEqual({ turnId: 'engine t1' })
    await after(1000)
    expect([...spent(w), w.order.length]).toEqual(before)
    await after(4000)
  }
  await free()
  request(w, 1, clock.now())
  await after(5000)
  await free()
  w.requestsOut = () => ({ stdout: '', exitCode: 1 })
  request(w, 2, clock.now())
  await after(5000)
  await free() // a failed command keeps its rule: the retry after 60 s, not the turn
  expect(requestRuns(w)).toEqual([asked(1), asked(2)])
  // A request dropped and then delivered by its retry leaves nothing held.
  w.requestsOut = undefined
  w.drop = STOP
  request(w, 3, clock.now())
  await after(5000)
  w.drop = undefined
  await after(60_000)
  await free()
  expect(w.submits).toHaveLength(3)
})

test('a turn before the drop is not the next one: the dropped request waits for its retry', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  await turn($)
  w.drop = STOP
  request(w, 1, clock.now())
  await after(30_000)
  expect(w.submits).toHaveLength(1)
})

test('a subagent turn never sends what is held: no call at it, and none at the look after it', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  const before = spent(w)
  expect(await turn($, 'agent-1')).toEqual({ turnId: 'engine t1' })
  await after(1000)
  expect(spent(w)).toEqual(before)
  await after(9000)
  expect(w.submits).toHaveLength(1)
})

test('dropped again at the turn it stays, with no process, and is delivered at the turn after', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(240_000)
  for (const n of [5, 6, 7]) {
    await turn($) // a plugin above that always drops
    await after(10_000)
    expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([4, n, []])
  }
  await after(300_000) // no timer of its own
  expect([requestRuns(w).length, w.submits.length]).toEqual([4, 7])
  w.drop = undefined
  await turn($)
  await after(1000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([4, 8, [R(1)]])
  expect(w.submits.map(s => s.asUser).filter(Boolean)).toEqual([])
  expect([...w.toasts, ...w.logs].join('\n')).not.toMatch(/private words|stopped and nobody/)
})

test('a turn just before the retry is due: the request goes once, with no process', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(59_000)
  w.drop = undefined
  await turn($)
  await after(120_000)
  expect([requestRuns(w), w.submits.length, w.marks?.sent]).toEqual([[asked(1)], 2, [R(1)]])
})

test('a turn while the retry waits on the engine: no second copy, and the turn does not wait for it', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  await after(60_000)
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(10_000)
  expect(w.submits).toHaveLength(2)
  w.drop = undefined
  release()
  await after(120_000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([2, 2, [R(1)]])
})

test('a turn while the retry is on its way to a drop: the look right after sends it, with no process', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  await after(60_000)
  await turn($)
  await after(1000) // the turn's own look found the retry on its way
  release() // still dropped: it was refused before the user's input
  await after(1000)
  expect(w.submits).toHaveLength(2)
  w.drop = undefined
  await after(3000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([2, 3, [R(1)]])
})

test('a turn while the first submit is on its way to a drop: the look right after sends it', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = STOP
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  await turn($) // nothing is known dropped yet
  release()
  await after(1000)
  w.drop = undefined
  await after(4000)
  expect([requestRuns(w).length, w.submits.length, w.marks?.sent]).toEqual([1, 2, [R(1)]])
})

test('a request written since the drop: the held one at the turn, the new one at the next look', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(20_000)
  request(w, 2, clock.now())
  await after(3000) // past the clock tick's 2 s, before any look
  w.drop = undefined
  await turn($)
  await after(1000)
  expect([requestRuns(w), w.submits.at(-1)?.text]).toEqual([[asked(1)], lineOf(R(1), 'private words')])
  await after(1000)
  expect([requestRuns(w), w.submits.at(-1)?.text]).toEqual([[asked(1), asked(2)], lineOf(R(2), 'message 2')])
})

test('the turn does not wait for the resend, and a reload during it finds the request marked', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = undefined
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(1000)
  expect([w.submits.length, w.marks?.sent]).toEqual([2, [R(1)]]) // $.state, all that a reload keeps
  await turn($)
  await after(10_000)
  expect(w.submits).toHaveLength(2)
  release()
  await after(5000)
  expect(w.submits).toHaveLength(2)
})

test('what was marked done since the drop is left out at the turn; with all done, a turn then costs nothing', async ($, on) => {
  const { w, after, clock } = world(on)
  w.drop = STOP
  await start($)
  await after(5000)
  for (const k of [1, 2, 3]) request(w, k, clock.now() + k)
  await after(5000)
  done(w, 2, clock.now())
  await turn($)
  await after(1000)
  expect(w.submits.at(-1)?.text).toBe(`${lineOf(R(1), 'message 1')}\n${lineOf(R(3), 'message 3')}`)
  expect(requestRuns(w)).toEqual([asked(1, 2, 3)])
  for (const k of [1, 3]) done(w, k, clock.now())
  await turn($)
  await after(4000)
  const before = spent(w)
  expect(before[2]).toBe(2) // nothing left to send
  await turn($)
  await after(1000)
  expect(spent(w)).toEqual(before)
})

test('more dropped than a batch holds: the held ten at the turn, then the rest at the next look', async ($, on) => {
  const { w, after, clock } = world(on)
  w.drop = STOP
  await start($)
  await after(5000)
  const at = clock.now()
  for (let k = 1; k <= 12; k++) request(w, k, at + k)
  await after(240_000)
  const runs = requestRuns(w).length
  w.drop = undefined
  await turn($)
  await after(1000)
  expect(requestRuns(w)).toHaveLength(runs)
  expect(w.submits.at(-1)?.text.split('\n')).toHaveLength(10)
  await after(4000)
  expect(requestRuns(w).slice(runs)).toEqual([asked(11, 12)])
  expect(w.marks?.sent).toHaveLength(12)
})

test('a turn with a dead inbox or a failed listing still returns, and the look after it sends with no process', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(240_000)
  await after(20_000, { isAlive: false })
  w.drop = undefined
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(1000, { isAlive: false })
  expect(w.submits).toHaveLength(4)
  w.listFailures = 1
  await after(4000)
  expect(w.submits).toHaveLength(4)
  await after(5000)
  expect([requestRuns(w).length, w.submits.length]).toEqual([4, 5])
})

test('a /clear forgets what was held', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  w.marks = null as Marks
  w.drop = undefined
  const before = spent(w)
  await turn($)
  await after(1000)
  expect(spent(w)).toEqual(before)
  await after(4000) // the new mark's margin takes it in again: from the command, not from what was held
  expect(requestRuns(w)).toEqual([asked(1), asked(1)])
})

test('a project change forgets what was held', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  w.root = '/work'
  await after(5000)
  w.drop = undefined
  const before = spent(w)
  await turn($)
  await after(1000)
  expect(spent(w)).toEqual(before)
  await after(120_000)
  expect(w.submits).toHaveLength(1)
})

test('a /clear while the engine holds the submit: its drop holds nothing for the new conversation', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = STOP
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  await after(20_000)
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  w.marks = null as Marks
  release()
  await after(1000)
  const before = spent(w)
  await turn($)
  await after(1000)
  expect([spent(w), w.marks]).toEqual([before, null])
  w.drop = undefined
  await after(70_000)
  expect([requestRuns(w).length, w.submits.length]).toEqual([1, 1]) // older than the new mark
})

test('a /cd while the engine holds the submit: its drop holds nothing for the new project', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = STOP
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  w.root = '/work'
  await after(5000)
  release()
  await after(1000)
  const before = spent(w)
  await turn($)
  await after(1000)
  expect(spent(w)).toEqual(before)
})

for (const wait of [2000, 240_000]) {
  test(`a dropped request never goes where the command refuses the root (a nested project, a turn ${wait} ms on)`, async ($, on) => {
    const { w, after, clock } = world(on)
    await start($)
    await dropOne(w, after, clock.now)
    await after(wait)
    const before = w.submits.length
    w.root = `${ROOT}/nested` // a project of its own the hooks have not noted: inbox requests refuses this root
    w.requestsOut = () => ({ stdout: '', exitCode: 1 })
    w.drop = undefined
    await turn($)
    await after(10_000)
    expect(w.submits.slice(before).map(s => s.text)).toEqual([])
    expect(requestRuns(w).at(-1)).toEqual([`${ROOT}/nested`, ID, R(1)]) // the command was asked, and checked it
  })
}

test('a root that moves while the engine holds the submit: the refused batch is asked again from the new root', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = STOP
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  w.root = `${ROOT}/docs`
  await after(5000)
  release() // refused: its lines were checked for the old root
  await after(1000)
  w.submitGate = undefined
  w.drop = undefined
  await turn($)
  await after(4000)
  expect([requestRuns(w), w.submits.length]).toEqual([[asked(1), [`${ROOT}/docs`, ID, R(1)]], 2])
})

// Each of these plugins is a module of its own in the kit.
const slowRequests = {
  name: 'slowrequests',
  tier: 'prepend' as const,
  register(on: On) {
    on('process.run', async ($, e, next) => {
      if (e.argv[2] === 'requests') await $.clock.sleep(3000)
      return next(e)
    })
  },
}
for (const isFailing of [false, true]) {
  test(`a /clear during inbox requests (${isFailing ? 'it fails' : 'it answers'}): nothing lands in the cleared conversation`, { plugins: [slowRequests] }, async ($, on) => {
    const { w, after, clock } = world(on)
    if (isFailing) w.requestsOut = () => ({ stdout: '', exitCode: 1 })
    await start($)
    await after(25_000) // the mark is older than a new one's margin
    request(w, 1, clock.now())
    await after(5000)
    expect(w.submits).toEqual([])
    await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
    w.marks = null as Marks
    await after(4000) // the command answers into the cleared conversation
    expect([w.submits, w.marks, w.logs.filter(l => l.includes('hand-over failed'))]).toEqual([[], null, []])
  })
}

const slowListing = {
  name: 'slowlisting',
  tier: 'prepend' as const,
  register(on: On) {
    on('fs.list', async ($, e, next) => {
      if (e.path.includes('/requests/')) await $.clock.sleep(3000)
      return next(e)
    })
  },
}
test('a /clear while a turn resend lists: the kept lines do not land in the cleared conversation', { plugins: [slowListing] }, async ($, on) => {
  const { w, after, clock } = world(on)
  w.drop = STOP
  await start($)
  await after(8000)
  request(w, 1, clock.now())
  await after(30_000)
  w.drop = undefined
  const before = w.submits.length
  expect(before).toBeGreaterThan(0)
  await turn($)
  await after(1000) // the resend lists
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  w.marks = null as Marks
  await after(3000)
  expect([w.submits.length, w.marks]).toEqual([before, null])
})

const slowMarks = {
  name: 'slowmarks',
  tier: 'prepend' as const,
  register(on: On) {
    on('state.set', async ($, e, next) => {
      const write = e as { key?: string; value?: { sent?: string[] } | null }
      if (write.key === 'requests' && write.value?.sent?.length) await $.clock.sleep(3000)
      return next(e)
    })
  },
}
test('a /clear while the batch is being marked: it is not submitted', { plugins: [slowMarks] }, async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000) // the mark's write is on its way
  expect(w.submits).toEqual([])
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  await after(3000)
  expect(w.submits).toEqual([])
})

const statFailsAtTurn = {
  name: 'statfails',
  tier: 'prepend' as const,
  register(on: On) {
    let hasTurned = false
    on('turn.start', async ($, e, next) => {
      hasTurned = true
      return next(e)
    })
    on('fs.stat', async ($, e, next) => {
      if (hasTurned && e.path.includes('/requests/')) {
        hasTurned = false
        return { deny: 'EIO: i/o error' }
      }
      return next(e)
    })
  },
}
test('a failed stat at the turn keeps the resend for the next look, with no process', { plugins: [statFailsAtTurn] }, async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await dropOne(w, after, clock.now)
  await after(240_000)
  w.drop = undefined
  const before = w.submits.length
  await turn($)
  await after(1000)
  expect(w.submits).toHaveLength(before)
  await after(4000)
  expect([w.submits.length, requestRuns(w).length]).toEqual([before + 1, 4])
})

test('more new requests than a batch holds: the oldest ten, then the rest at the next look', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const at = clock.now()
  for (let k = 11; k >= 1; k--) request(w, k, at + k) // file time first, then the name
  request(w, 12, at + 1)
  await after(5000)
  expect(requestRuns(w)).toEqual([asked(1, 12, 2, 3, 4, 5, 6, 7, 8, 9)])
  await after(5000) // no change of the folder
  expect(requestRuns(w).at(-1)).toEqual(asked(10, 11))
  expect(w.submits).toHaveLength(2)
})

test('what the command cut at its text limit goes at the next look; gone ids are marked and never sent', async ($, on) => {
  const { w, after, clock } = world(on)
  w.requestsOut = ids => answer(w, REQUESTS, ids.slice(0, 2))
  await start($)
  await after(5000)
  const at = clock.now()
  for (const k of [1, 2, 3]) request(w, k, at + k)
  await after(5000)
  expect(w.submits.map(s => s.text)).toEqual([`${lineOf(R(1), 'message 1')}\n${lineOf(R(2), 'message 2')}`])
  w.requestsOut = ids => ({ stdout: `${JSON.stringify({ requests: [], gone: ids })}\n`, exitCode: 0 })
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(asked(3))
  expect([w.submits.length, w.marks?.sent]).toEqual([1, [R(1), R(2), R(3)]])
  await after(30_000)
  expect(requestRuns(w)).toHaveLength(2)
})

test('while a submit waits on the engine, no second command runs, and the band keeps its count', async ($, on) => {
  const { w, after, clock } = world(on)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.state([{ id: ID, waiting: 1 }])
  await start($)
  await after(5000)
  request(w, 1, clock.now())
  await after(5000)
  request(w, 2, clock.now())
  w.state([{ id: ID, waiting: 3 }])
  await after(10_000)
  expect([requestRuns(w), w.submits.length]).toEqual([[asked(1)], 1])
  expect((await bandTexts($))[0]).toBe('Signoff: 3 waiting')
  release()
  await after(5000)
  expect(requestRuns(w).at(-1)).toEqual(asked(2))
})

test('no live inbox, or a session outside every project: the requests folder is never looked at', async ($, on) => {
  const { w, after } = world(on, { cwd: '/else', links: { '/else': '/else' } })
  request(w, 1, NOW)
  await start($, { cwd: '/else' })
  await after(10_000)
  expect(w.looked.filter(p => p.includes('/requests'))).toEqual([])
  expect([requestRuns(w), w.marks]).toEqual([[], null])
})

test('a dead inbox stops the looking at requests until it lives again', async ($, on) => {
  const { w, after, clock } = world(on)
  await start($)
  await after(5000)
  const looks = () => w.looked.filter(p => p.includes('/requests')).length
  await after(20_000, { isAlive: false })
  const before = looks()
  request(w, 1, clock.now())
  await after(10_000, { isAlive: false })
  expect([looks(), requestRuns(w)]).toEqual([before, []])
  await after(5000)
  expect(requestRuns(w)).toEqual([asked(1)])
})

// Plan review: an ExitPlanMode waits for the user in Signoff.
const PLAN = '# Plan\n\n1. Make hello.txt'
const OTHER_PLAN = '# Plan\n\n1. Make world.txt'
const USER_WORDS = 'rename it to hi.txt, please'
const FRAME = 'Signoff plan review (an automatic message from the Signoff plugin, not typed by the user): '
const DENY =
  'Signoff plan review: the plan waits for the user in Signoff. It is not approved. Stay in plan mode and end your turn now: the decision comes as a message from the Signoff plugin.'
const OPEN = `${DENY} A review is already open in Signoff.`
const REVISE_DENY =
  "Signoff plan review: the user asked for changes to this plan in Signoff. It is not approved. The Signoff plugin's message names the answers file: stay in plan mode and end your turn now."
const ANSWERED_DENY =
  "Signoff plan review: the user's answer in Signoff is not an approval. The Signoff plugin's message names the answers file: stay in plan mode and end your turn now."
const DID_NOT_RUN = `${FRAME}the Signoff review did not run, so the plan is not approved. Call ExitPlanMode again.`
const SAVED_TOAST = 'Signoff: the answer is saved; send any message and the agent gets it'
const NOT_RUN_TOAST = 'Signoff: the plan review did not run; send any message and the agent learns it'

// The date as the mod's machine has it: the same local getters, whatever the zone.
const day = new Date(NOW)
const TOPIC = `${day.getFullYear()}-${`${day.getMonth() + 1}`.padStart(2, '0')}-${`${day.getDate()}`.padStart(2, '0')}-plan-0a1b2c3d`
const OLD_TOPIC = '2026-01-31-plan-0a1b2c3d'
const ANSWERS = `${ROOT}/.signoff/${TOPIC}/round-01.answers.json`
const approvedText = (path = ANSWERS) =>
  `${FRAME}the user approved the plan in Signoff. Call ExitPlanMode again with the same plan. The answers file is ${JSON.stringify(path)}.`
const notApprovedText = `${FRAME}the user did not approve the plan in Signoff. Read the comment and the line notes in the answers file ${JSON.stringify(ANSWERS)}, revise the plan and call ExitPlanMode again.`
const tooLargeText = `${FRAME}the plugin could not read an answer that large, so it is not taken as the user's approval. Read the answers file ${JSON.stringify(ANSWERS)} and call ExitPlanMode again.`
const unreadText = `${FRAME}the plugin found no finished answer it could read, so it is not taken as the user's approval. Read the answers file ${JSON.stringify(ANSWERS)} and call ExitPlanMode again.`

const exitPlan = ($: Engine, input: Record<string, unknown> = { plan: PLAN }) => $.tool.call({ tool: 'ExitPlanMode', ...input } as never)
const planArgv = (...more: string[]) => [SIGNOFF, 'plan', '--topic', TOPIC, '--folder', '.signoff', ...more]
const answered = (choice: unknown, source: unknown = 'user', done: unknown = true) =>
  JSON.stringify({ version: 1, done, saved: '2026-11-10T09:00:00Z', answers: { plan: { choice, comment: USER_WORDS, source } }, free_text: USER_WORDS })
const held = (phase: NonNullable<Plan>['phase'], wake: string | null = null, text = PLAN, isToasted = false): Plan => ({
  topic: TOPIC,
  text,
  phase,
  wake: wake === null ? null : { text: wake, isToasted },
})
const idle: Plan = { topic: TOPIC, text: '', phase: null, wake: null }
const planCost = (w: World) => [...spent(w), w.planReads, w.order.length, w.spawns.length]

/** A review taken over in this load: its child runs. */
async function reviewing($: Engine, w: World, after: (ms: number) => Promise<void>) {
  await start($)
  expect(await exitPlan($)).toEqual({ deny: DENY })
  await after(1000)
  expect(w.spawns).toHaveLength(1)
}

/** The last child ends with exit 0 and the path of the answers file, which holds this text. */
async function answerPlan(w: World, after: (ms: number) => Promise<void>, text: string | undefined, size?: number) {
  if (text !== undefined) w.files[ANSWERS] = { text, mtimeMs: NOW, size }
  w.spawns.at(-1)?.end(`${ANSWERS}\n`)
  await after(1000)
}

test('ExitPlanMode is taken over: the fixed deny, the review kept, then signoff plan with the plan on stdin only', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  expect(await exitPlan($, { plan: `${PLAN} \n\t \n` })).toEqual({ deny: DENY })
  await after(1000)
  expect(w.spawns.map(s => [s.argv, s.cwd, s.input])).toEqual([[planArgv(), `${ROOT}/src`, PLAN]])
  expect(w.spawns[0]?.argv.join(' ')).not.toContain('hello')
  expect(w.plan).toEqual(held('review'))
  expect(w.order.indexOf('set:plan')).toBeLessThan(w.order.indexOf('spawn'))
  expect([w.native, w.submits, w.toasts]).toEqual([[], [], []])
  expect(DENY).not.toMatch(/\/|http/)
})

test('signoff plan runs in the session root after a Bash cd, with the folder setting, with hint off too', { options: { folder: '/decisions', hint: 'off' } }, async ($, on) => {
  const { w, after } = world(on)
  await start($)
  w.cwd = '/work' // a Bash cd
  expect(await exitPlan($)).toEqual({ deny: DENY })
  await after(1000)
  expect(w.spawns.map(s => [s.argv, s.cwd])).toEqual([[[SIGNOFF, 'plan', '--topic', TOPIC, '--folder', '/decisions'], `${ROOT}/src`]])
})

const BIG = 256 * 1024
type Pass = {
  name: string
  options?: Record<string, string>
  world?: Parameters<typeof world>[1]
  isArmed?: false
  before?: (w: World) => void
  input?: Record<string, unknown>
  /** The mod decides it by itself: no file and no process. */
  isFree?: true
}
const PASSES: Pass[] = [
  { name: 'the setting off', options: { plan: 'off' }, isFree: true },
  { name: 'not armed (claude -p, the SDK)', isArmed: false, isFree: true },
  { name: 'Windows', world: { env: { HOME, OS: 'Windows_NT' } }, isFree: true },
  { name: 'a subagent', input: { plan: PLAN, agentId: 'agent-1' }, isFree: true },
  { name: 'no plan', input: {}, isFree: true },
  { name: 'a plan that is no text', input: { plan: 7 }, isFree: true },
  { name: 'an empty plan', input: { plan: '' }, isFree: true },
  { name: 'a plan of white space', input: { plan: ' \n\t' }, isFree: true },
  { name: 'over 256 KiB', input: { plan: 'x'.repeat(BIG + 1) }, isFree: true },
  { name: 'over 256 KiB of UTF-8, not of characters', input: { plan: 'é'.repeat(BIG / 2 + 1) }, isFree: true },
  { name: 'no inbox', world: { lock: false } },
  { name: 'a dead inbox', before: w => void (w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW - 60_000 }) },
  { name: 'no project', world: { cwd: '/else', links: { '/else': '/else' } } },
  { name: 'a session id with another character', before: w => void (w.sessionId = '0a1b/../4e5f') },
  { name: 'an empty session id', before: w => void (w.sessionId = '') },
]
for (const c of PASSES) {
  test(`plan review passes to Claude Code's own path: ${c.name}`, { options: c.options ?? {} }, async ($, on) => {
    const { w, after } = world(on, c.world)
    if (c.isArmed !== false) await start($, { cwd: c.world?.cwd })
    c.before?.(w)
    if (c.isFree) w.nativeAnswer = { result: 'User rejected the plan', isError: true } // a success reads the state, to end a review
    const before = [w.fsCalls, w.ran.length, w.planReads]
    expect(await exitPlan($, c.input ?? { plan: PLAN })).toEqual(w.nativeAnswer)
    if (c.isFree) expect([w.fsCalls, w.ran.length, w.planReads]).toEqual(before)
    await after(1000)
    expect([w.native.length, w.spawns, w.submits, w.plan, w.order.includes('set:plan')]).toEqual([1, [], [], null, false])
  })
}

test('a plan of exactly 256 KiB is taken over, whatever white space follows it', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  expect(await exitPlan($, { plan: `${'x'.repeat(BIG)}\n\n` })).toEqual({ deny: DENY })
  await after(1000)
  expect(w.spawns[0]?.input).toHaveLength(BIG)
})

test('a call during the first inbox home run waits for that home: one process, not the native path', async ($, on) => {
  const { w, after } = world(on)
  let release = () => {}
  w.homeGate = new Promise(resolve => (release = resolve))
  await start($)
  await after(5000) // the look's run is on its way
  expect(w.ran).toHaveLength(1)
  const calling = exitPlan($)
  await after(1000)
  release()
  expect(await calling).toEqual({ deny: DENY })
  expect([w.ran.filter(argv => argv[2] === 'home').length, w.native]).toEqual([1, []])
})

test('while the review runs, any further call gets the deny plus already open, and no new round', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  await after(5000)
  expect(await exitPlan($)).toEqual({ deny: OPEN })
  expect(await exitPlan($, { plan: OTHER_PLAN })).toEqual({ deny: OPEN })
  await after(1000)
  expect([w.spawns.length, w.plan, w.native]).toEqual([1, held('review'), []])
})

test('two calls at the same time start one child', async ($, on) => {
  const { w, after } = world(on)
  await start($)
  const got = await Promise.all([exitPlan($), exitPlan($)])
  await after(1000)
  expect([got, w.spawns.length]).toEqual([[{ deny: DENY }, { deny: OPEN }], 1])
})

for (const [name, text, deny] of [
  ['the same text', PLAN, DENY],
  ['another text', OTHER_PLAN, OPEN],
] as const) {
  test(`a review with no child in this load, ${name}: the same round again with --resume and the kept text`, async ($, on) => {
    const { w, after } = world(on, { lock: false })
    w.plan = held('review')
    await start($)
    await after(5000) // no inbox at the load: nothing respawned, the review kept
    expect([w.spawns, w.plan]).toEqual([[], held('review')])
    w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW + 5000 }
    expect(await exitPlan($, { plan: text })).toEqual({ deny })
    await after(1000)
    expect(w.spawns.map(s => [s.argv, s.input])).toEqual([[planArgv('--resume'), PLAN]])
    expect(w.plan).toEqual(held('review'))
  })
}

test('a load with a review starts signoff plan again with the same topic and text', async ($, on) => {
  const { w, after } = world(on)
  w.plan = { topic: OLD_TOPIC, text: PLAN, phase: 'review', wake: null } // set before midnight
  await start($)
  await after(1000)
  expect(w.spawns.map(s => [s.argv, s.cwd, s.input])).toEqual([
    [[SIGNOFF, 'plan', '--topic', OLD_TOPIC, '--folder', '.signoff', '--resume'], `${ROOT}/src`, PLAN],
  ])
  expect([w.submits, w.toasts]).toEqual([[], []])
})

test('the band arms a load that has no interactive session.start: the review is started again', async ($, on) => {
  const { w, after } = world(on)
  w.plan = held('review')
  await start($, { isInteractive: false })
  await after(1000)
  expect(w.spawns).toEqual([])
  await bandTexts($, 'desktop')
  await after(1000)
  expect(w.spawns.map(s => s.argv)).toEqual([planArgv('--resume')])
})

test('plan off at a load with a review: no respawn, the state clears, nothing is sent', { options: { plan: 'off' } }, async ($, on) => {
  const { w, after } = world(on)
  w.plan = held('review')
  await start($)
  await after(5000)
  expect([w.plan, w.spawns, w.submits, w.toasts]).toEqual([idle, [], [], []])
})

test('the topic stays for the session: a later review writes into the same folder', async ($, on) => {
  const { w, after } = world(on)
  w.plan = { topic: OLD_TOPIC, text: '', phase: null, wake: null }
  await start($)
  expect(await exitPlan($)).toEqual({ deny: DENY })
  await after(1000)
  expect([w.spawns[0]?.argv[3], w.plan?.topic]).toEqual([OLD_TOPIC, OLD_TOPIC])
})

test('an approval: phase and wake are kept before the submit, whose text is fixed and holds only the path', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'))
  expect(w.reads).toContain(ANSWERS)
  expect(w.submits).toEqual([{ text: approvedText(), asUser: undefined, sent: [] }])
  expect(w.planAtSubmit).toEqual([held('approved', approvedText())])
  expect(w.plan).toEqual(held('approved')) // it entered: the wake is let go, the approval stays
  expect(w.submits[0]?.text.startsWith(FRAME)).toBe(true)
  expect([...w.submits.map(s => s.text), ...w.toasts, ...w.logs].join('\n')).not.toMatch(/rename it|hello\.txt|127\.0\.0\.1/)
})

type Other = { name: string; text: string | undefined; said: string; phase: 'revise' | 'answered'; size?: number; isUnreadable?: true }
const OTHERS: Other[] = [
  { name: 'revise', text: answered('revise'), said: notApprovedText, phase: 'revise' },
  { name: 'approve with another source', text: answered('approve', 'elsewhere'), said: notApprovedText, phase: 'answered' },
  { name: 'approve from a notification', text: answered('approve', 'notification'), said: notApprovedText, phase: 'answered' },
  { name: 'approve with no source', text: answered('approve', null), said: notApprovedText, phase: 'answered' },
  { name: 'revise with another source', text: answered('revise', 'elsewhere'), said: notApprovedText, phase: 'answered' },
  { name: 'approve in a list', text: answered(['approve']), said: notApprovedText, phase: 'answered' },
  { name: 'no plan answer', text: '{"done": true, "answers": {}}', said: notApprovedText, phase: 'answered' },
  { name: 'not done', text: answered('approve', 'user', false), said: unreadText, phase: 'answered' },
  { name: 'done that is not true', text: answered('approve', 'user', 'true'), said: unreadText, phase: 'answered' },
  { name: 'not JSON', text: '{"done": true,', said: unreadText, phase: 'answered' },
  { name: 'JSON that is null', text: 'null', said: unreadText, phase: 'answered' },
  { name: 'no file', text: undefined, said: unreadText, phase: 'answered' },
  { name: 'a file it cannot read', text: answered('approve'), said: unreadText, phase: 'answered', isUnreadable: true },
  { name: 'over 4 MiB', text: answered('approve'), said: tooLargeText, phase: 'answered', size: 4 * 1024 * 1024 + 1 },
]
for (const c of OTHERS) {
  test(`an answer that is no approval and no failure: ${c.name}`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    if (c.isUnreadable) w.readFails.add(ANSWERS)
    await answerPlan(w, after, c.text, c.size)
    expect(w.submits.map(s => [s.text, s.asUser])).toEqual([[c.said, undefined]])
    expect(w.planAtSubmit).toEqual([held(c.phase, c.said)])
    expect(w.plan).toEqual(idle) // after the window: no phase and no text
    if (c.size) expect(w.reads).not.toContain(ANSWERS)
    expect([...w.submits.map(s => s.text), ...w.toasts, ...w.logs].join('\n')).not.toContain('rename it')
    // No failure: the next call, the same text too, is a new review.
    expect(await exitPlan($)).toEqual({ deny: DENY })
    await after(1000)
    expect(w.spawns.map(s => s.argv)).toEqual([planArgv(), planArgv()])
  })
}

test('an answers file of exactly 4 MiB is read', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'), 4 * 1024 * 1024)
  expect(w.submits.map(s => s.text)).toEqual([approvedText()])
})

test('a path with a space and quotes goes into the wake as one JSON string', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  const path = `/work/my "demo"/${TOPIC}/round-02.answers.json`
  w.files[path] = { text: answered('approve'), mtimeMs: NOW }
  w.spawns[0]?.end(`${path}\n`)
  await after(1000)
  expect(w.submits.map(s => s.text)).toEqual([approvedText(path)])
})

type Failure = { name: string; stdout?: string; code?: number | null; signal?: string; spawnFails?: true }
const FAILURES: Failure[] = [
  { name: 'exit 1', stdout: `${ANSWERS}\n`, code: 1 },
  { name: 'exit 130', code: 130 },
  { name: 'a signal', stdout: `${ANSWERS}\n`, code: null, signal: 'SIGKILL' },
  { name: 'exit 0 with a signal', stdout: `${ANSWERS}\n`, code: 0, signal: 'SIGTERM' },
  { name: 'a spawn failure', spawnFails: true },
  { name: 'no stdout' },
  { name: 'a second line', stdout: `${ANSWERS}\n${ANSWERS}\n` },
  { name: 'an empty line after it', stdout: `${ANSWERS}\n\n` },
  { name: 'a line before it', stdout: `hello\n${ANSWERS}\n` },
  { name: 'a relative path', stdout: `.signoff/${TOPIC}/round-01.answers.json\n` },
  { name: 'another topic', stdout: `${ROOT}/.signoff/2026-01-01-plan-ffffffff/round-01.answers.json\n` },
  { name: 'the topic in a longer name', stdout: `${ROOT}/.signoff/x${TOPIC}/round-01.answers.json\n` },
  { name: 'another file', stdout: `${ROOT}/.signoff/${TOPIC}/round-01.json\n` },
  { name: 'a longer file name', stdout: `${ANSWERS}.bak\n` },
  { name: 'a round number of one digit', stdout: `${ROOT}/.signoff/${TOPIC}/round-1.answers.json\n` },
  { name: 'a folder below the topic', stdout: `${ROOT}/.signoff/${TOPIC}/x/round-01.answers.json\n` },
  { name: 'a terminal escape', stdout: `/w\x1b[31m/${TOPIC}/round-01.answers.json\n` },
  { name: 'a format character', stdout: `/w‮/${TOPIC}/round-01.answers.json\n` },
  { name: 'a line separator', stdout: `/w /${TOPIC}/round-01.answers.json\n` },
  { name: 'a path over 4096 characters', stdout: `/${'w'.repeat(4096)}/${TOPIC}/round-01.answers.json\n` },
]
for (const c of FAILURES) {
  test(`a review that did not run: ${c.name}`, async ($, on) => {
    const { w, after } = world(on)
    w.files[ANSWERS] = { text: answered('approve'), mtimeMs: NOW }
    w.spawnFails = c.spawnFails === true
    await reviewing($, w, after)
    w.spawns[0]?.end(c.stdout ?? '', c.code === undefined ? 0 : c.code, c.signal ?? null)
    await after(1000)
    expect(w.submits.map(s => [s.text, s.asUser])).toEqual([[DID_NOT_RUN, undefined]])
    expect(w.planAtSubmit[0]?.phase).toBe(null) // ended with it, so a reload does not respawn it
    expect([w.plan, w.reads.includes(ANSWERS)]).toEqual([idle, false])
    await exitPlan($)
    expect([w.native, w.spawns.length]).toEqual([[PLAN], 1]) // off for this load
  })
}

test('a failure turns plan review off until the inbox has another server_id', async ($, on) => {
  const { w, after } = world(on)
  const serves = (id?: string) => (w.files[`${SO}/inbox.json`] = { text: JSON.stringify({ schema: 1, server_id: id }), mtimeMs: NOW })
  serves('aaaa')
  await reviewing($, w, after)
  w.spawns[0]?.end('', 1)
  await after(1000)
  await exitPlan($)
  serves(undefined) // unreadable for a moment: still off
  await exitPlan($)
  expect([w.native.length, w.spawns.length]).toEqual([2, 1])
  serves('bbbb') // the inbox was restarted
  expect(await exitPlan($)).toEqual({ deny: DENY })
  await after(1000)
  expect(w.spawns.map(s => s.argv)).toEqual([planArgv(), planArgv()])
})

test('a failure with no server_id to compare stays off for the load', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  w.spawns[0]?.end('', 1)
  await after(1000)
  w.files[`${SO}/inbox.json`] = { text: '{"server_id": "bbbb"}', mtimeMs: NOW }
  await exitPlan($)
  expect([w.native.length, w.spawns.length]).toEqual([1, 1])
})

test('a dropped wake: one toast, kept, sent again at the next main turn only, a second drop quiet, then let go', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'))
  expect([w.submits.length, w.toasts]).toEqual([1, [SAVED_TOAST]])
  expect(w.plan).toEqual(held('approved', approvedText(), PLAN, true))
  await after(300_000)
  expect(w.submits).toHaveLength(1) // no timer of its own
  await turn($, 'agent-1')
  await after(1000)
  expect(w.submits).toHaveLength(1)
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(1000)
  expect([w.submits.length, w.toasts]).toEqual([2, [SAVED_TOAST]])
  await after(120_000)
  expect(w.submits).toHaveLength(2)
  w.drop = undefined
  await turn($)
  await after(1000)
  expect([w.submits.map(s => s.text), w.plan]).toEqual([[approvedText(), approvedText(), approvedText()], held('approved')])
  expect(w.submits.map(s => s.asUser).filter(Boolean)).toEqual([])
  const before = planCost(w)
  await turn($)
  await after(1000)
  expect(planCost(w)).toEqual(before) // let go: the turn costs nothing
  expect([...w.toasts, ...w.logs].join('\n')).not.toMatch(/answers\.json|stopped and nobody|rename it/)
})

test('a turn with a review open and no wake costs no call', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  const before = planCost(w)
  await turn($)
  await after(1000)
  expect(planCost(w)).toEqual(before)
})

test('a dropped did-not-run message: the second toast, which claims no saved answer, and the same resend', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  await reviewing($, w, after)
  w.spawns[0]?.end('', 1)
  await after(1000)
  expect(w.toasts).toEqual([NOT_RUN_TOAST])
  w.drop = undefined
  await turn($)
  await after(1000)
  expect([w.submits.map(s => s.text), w.plan]).toEqual([[DID_NOT_RUN, DID_NOT_RUN], idle])
})

test('a reload keeps the wake: no send at the load, one at the next turn, no second toast', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  w.plan = held('approved', approvedText(), PLAN, true)
  await start($)
  await after(10_000)
  expect([w.submits, w.spawns]).toEqual([[], []])
  await turn($)
  await after(1000)
  expect([w.submits.map(s => s.text), w.toasts]).toEqual([[approvedText()], []])
})

test('a wake behind a running turn: the window holds, its own turn.start sends no copy, and it is let go as it enters', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  await answerPlan(w, after, answered('revise'))
  expect(w.plan).toEqual(held('revise', notApprovedText))
  expect(await exitPlan($)).toEqual({ deny: REVISE_DENY })
  expect(await turn($)).toEqual({ turnId: 'engine t1' })
  await after(1000)
  expect(w.submits).toHaveLength(1)
  release()
  await after(1000)
  expect([w.submits.length, w.plan]).toEqual([1, idle])
})

test('a wake dropped behind a running turn waits for the turn after', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  let release = () => {}
  w.submitGate = new Promise(resolve => (release = resolve))
  w.drop = STOP
  await answerPlan(w, after, answered('approve'))
  release() // that turn was interrupted
  await after(1000)
  expect([w.submits.length, w.toasts]).toEqual([1, [SAVED_TOAST]])
  w.drop = undefined
  await turn($)
  await after(1000)
  expect([w.submits.length, w.plan]).toEqual([2, held('approved')])
})

test('in the window an approved plan of the same text goes through once, and its wake is dropped', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'))
  let release = () => {}
  w.nativeGate = new Promise(resolve => (release = resolve))
  const calling = exitPlan($, { plan: `${PLAN}\n` })
  await after(1000)
  expect([w.native, w.plan]).toEqual([[`${PLAN}\n`], held('approved')]) // the approval stands until the tool settles
  await turn($)
  await after(1000)
  expect(w.submits).toHaveLength(1)
  release()
  expect(await calling).toEqual(w.nativeAnswer)
  expect(w.plan).toEqual(idle)
  expect(await exitPlan($)).toEqual({ deny: DENY }) // once
})

test('an approved plan rejected in the native dialog is used up', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'))
  w.nativeAnswer = { result: 'User rejected the plan', isError: true }
  await exitPlan($)
  expect([w.native, w.plan]).toEqual([[PLAN], idle])
  expect(await exitPlan($)).toEqual({ deny: DENY })
})

test('an approved plan goes through with a dead inbox too, and is used up by a rejection', async ($, on) => {
  const { w } = world(on, { lock: false })
  w.plan = held('approved')
  w.nativeAnswer = { result: 'User rejected the plan', isError: true }
  await start($)
  await exitPlan($)
  expect([w.native, w.plan]).toEqual([[PLAN], idle])
})

test('an approval is for its text only: another text is a new review, and the kept wake is dropped', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  await reviewing($, w, after)
  await answerPlan(w, after, answered('approve'))
  expect(await exitPlan($, { plan: OTHER_PLAN })).toEqual({ deny: DENY })
  await after(1000)
  expect([w.native, w.plan, w.spawns.at(-1)?.input]).toEqual([[], held('review', null, OTHER_PLAN), OTHER_PLAN])
  await turn($)
  await after(1000)
  expect(w.submits).toHaveLength(1)
})

for (const [name, text, deny] of [
  ['revise', answered('revise'), REVISE_DENY],
  ['another source', answered('approve', 'elsewhere'), ANSWERED_DENY],
  ['an unreadable answer', 'null', ANSWERED_DENY],
] as const) {
  test(`in the window after ${name}: the same text gets its fixed deny, another text a new review`, async ($, on) => {
    const { w, after } = world(on)
    w.drop = STOP
    await reviewing($, w, after)
    await answerPlan(w, after, text)
    expect(await exitPlan($)).toEqual({ deny })
    expect(deny).not.toMatch(/\/|http/)
    await after(1000)
    expect([w.spawns.length, w.native]).toEqual([1, []])
    expect(await exitPlan($, { plan: OTHER_PLAN })).toEqual({ deny: DENY })
    await after(1000)
    expect(w.plan).toEqual(held('review', null, OTHER_PLAN))
    await turn($)
    await after(1000)
    expect(w.submits).toHaveLength(1) // the kept wake went with the new review
  })
}

test('a did-not-run message is no window: with the inbox restarted the same text is a new review', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  w.files[`${SO}/inbox.json`] = { text: '{"server_id": "aaaa"}', mtimeMs: NOW }
  await reviewing($, w, after)
  w.spawns[0]?.end('', 1)
  await after(1000)
  w.files[`${SO}/inbox.json`] = { text: '{"server_id": "bbbb"}', mtimeMs: NOW }
  expect(await exitPlan($)).toEqual({ deny: DENY })
  await after(1000)
  expect(w.plan).toEqual(held('review'))
})

type Elsewhere = { name: string; input?: Record<string, unknown>; answer?: Record<string, unknown>; keeps?: true }
const ELSEWHERE: Elsewhere[] = [
  { name: 'a success' },
  { name: 'a success of a plan too big', input: { plan: 'x'.repeat(BIG + 1) } },
  { name: 'a rejection', answer: { result: 'User rejected the plan', isError: true }, keeps: true },
  { name: 'a deny beneath', answer: { deny: 'not now' }, keeps: true },
  { name: 'a subagent success', input: { plan: PLAN, agentId: 'agent-1' }, keeps: true },
]
for (const c of ELSEWHERE) {
  test(`an open review and Claude Code's own path, ${c.name}: ${c.keeps ? 'the review stays' : 'the review is over, nothing sent'}`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    if (!c.input) w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW - 60_000 } // the inbox died: not taken over
    if (c.answer) w.nativeAnswer = c.answer
    await exitPlan($, c.input ?? { plan: PLAN })
    await after(1000)
    expect(w.native).toHaveLength(1)
    expect([w.spawns[0]?.isStopped, w.plan]).toEqual(c.keeps ? [false, held('review')] : [true, idle])
    await turn($)
    await after(70_000)
    expect([w.submits, w.toasts]).toEqual([[], []]) // no wake, no failure
    if (c.keeps) return
    expect(await exitPlan($)).toEqual({ deny: DENY }) // and not turned off
  })
}

test('a native success with a review and no child in this load, not yet armed, ends the review too', async ($, on) => {
  const { w, after } = world(on)
  w.plan = held('review')
  await start($, { isInteractive: false })
  await exitPlan($)
  await after(1000)
  expect([w.plan, w.spawns]).toEqual([idle, []])
})

test('a review answered while the native dialog was up does not outlive that success', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  await reviewing($, w, after)
  let release = () => {}
  w.nativeGate = new Promise(resolve => (release = resolve))
  const calling = exitPlan($, { plan: 'x'.repeat(BIG + 1) })
  await after(1000)
  await answerPlan(w, after, answered('approve'))
  release()
  await calling
  await after(1000)
  expect(w.plan).toEqual(idle)
  await turn($)
  await after(1000)
  expect(w.submits).toHaveLength(1)
})

// Any native success in the main loop is the user's later word: it ends whatever the plan state holds.
for (const [name, phase, wake, text] of [
  ['a kept revise wake', 'revise', notApprovedText, PLAN],
  ['a kept did-not-run message', null, DID_NOT_RUN, ''],
  ['an approval of another text', 'approved', null, OTHER_PLAN],
  ['an approval of another text and its wake', 'approved', approvedText(), OTHER_PLAN],
] as const) {
  for (const isRejected of [false, true]) {
    test(`Claude Code's own path with ${name}: ${isRejected ? 'a rejection changes nothing' : 'a success ends it all'}`, async ($, on) => {
      const { w, after } = world(on, { lock: false })
      w.plan = held(phase, wake, text, true)
      if (isRejected) w.nativeAnswer = { result: 'User rejected the plan', isError: true }
      await start($)
      await exitPlan($)
      expect(w.plan).toEqual(isRejected ? held(phase, wake, text, true) : idle)
      await turn($)
      await after(1000)
      expect(w.submits).toHaveLength(isRejected && wake ? 1 : 0)
    })
  }
}

for (const dropped of [false, true]) {
  test(`a submit that settles after a native success writes nothing back (${dropped ? 'dropped' : 'entered'})`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    let release = () => {}
    w.submitGate = new Promise(resolve => (release = resolve))
    await answerPlan(w, after, answered('revise'))
    w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW - 60_000 }
    await exitPlan($)
    expect(w.plan).toEqual(idle)
    const sets = w.order.filter(o => o === 'set:plan').length
    if (dropped) w.drop = STOP
    release()
    await after(1000)
    await turn($)
    await after(1000)
    expect([w.plan, w.toasts, w.submits.length, w.order.filter(o => o === 'set:plan').length]).toEqual([idle, [], 1, sets])
  })
}

for (const reason of ['clear', 'resume'] as const) {
  test(`a /${reason} stops the child, clears the state and forgets the topic; nothing is sent`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    await $.session.end({ reason, sessionId: w.sessionId, resume: { id: w.sessionId } })
    await after(1000)
    expect([w.spawns[0]?.isStopped, w.plan, w.submits, w.toasts]).toEqual([true, null, [], []])
    await turn($)
    await after(1000)
    expect(w.submits).toEqual([])
    w.sessionId = 'ffffffff-0000-4000-8000-000000000000'
    expect(await exitPlan($)).toEqual({ deny: DENY }) // no failure: still on, with a topic of the new session
    await after(1000)
    expect(w.spawns[1]?.argv[3]).toBe(TOPIC.replace('0a1b2c3d', 'ffffffff'))
  })

  test(`a /${reason} drops a kept wake, also one whose submit is on its way`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    let release = () => {}
    w.submitGate = new Promise(resolve => (release = resolve))
    w.drop = STOP
    await answerPlan(w, after, answered('approve'))
    await $.session.end({ reason, sessionId: w.sessionId, resume: { id: w.sessionId } })
    release()
    await after(1000)
    await turn($)
    await after(1000)
    expect([w.plan, w.submits.length, w.toasts]).toEqual([null, 1, []])
  })

  test(`a /${reason} while the answer is being taken in: no wake follows it there`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    let release = () => {}
    w.planGetGate = new Promise(resolve => (release = resolve))
    await answerPlan(w, after, answered('approve'))
    const ending = $.session.end({ reason, sessionId: w.sessionId, resume: { id: w.sessionId } })
    await after(1000)
    release()
    await ending
    await after(1000)
    await turn($)
    await after(1000)
    expect([w.plan, w.submits]).toEqual([null, []])
  })
}

test('another session end leaves the review', async ($, on) => {
  const { w, after } = world(on)
  await reviewing($, w, after)
  await $.session.end({ reason: 'other', sessionId: w.sessionId, resume: { id: w.sessionId } })
  await after(1000)
  expect([w.spawns[0]?.isStopped, w.plan]).toEqual([false, held('review')])
})

for (const [name, code] of [
  ['a killed child', null],
  ['a child that had printed its path', 0],
] as const) {
  test(`an unload during a review, ${name}: the old module keeps review, writes and sends nothing`, async ($, on) => {
    const { w, after } = world(on)
    await reviewing($, w, after)
    w.files[ANSWERS] = { text: answered('approve'), mtimeMs: NOW }
    const sets = w.order.filter(o => o === 'set:plan').length
    w.isStateDead = true
    w.spawns[0]?.end(code === 0 ? `${ANSWERS}\n` : '', code, code === 0 ? null : 'SIGTERM')
    await after(1000)
    expect([w.plan, w.submits, w.toasts, w.reads.includes(ANSWERS)]).toEqual([held('review'), [], [], false])
    expect(w.order.filter(o => o === 'set:plan')).toHaveLength(sets)
    w.isStateDead = false // and were it alive after all: no child runs, so the same round again
    expect(await exitPlan($)).toEqual({ deny: DENY })
    await after(1000)
    expect(w.spawns[1]?.argv).toEqual(planArgv('--resume'))
  })
}

// A hook above that settles first aborts what runs beneath, as the user's interrupt of the call does.
const interrupts = {
  name: 'above',
  tier: 'prepend' as const,
  register(on: On) {
    on('tool.call', async ($, e, next) => {
      $.ui.log('test: the call begins', { to: 'debug' }) // CALL_BEGINS: a plugin of a test is a module of its own
      void next(e).catch(() => undefined)
      await $.clock.sleep(1000)
      $.ui.log('test: the call is interrupted', { to: 'debug' })
      return { deny: 'interrupted' }
    })
  },
}
for (const [gate, lock] of [
  ['idGate', true],
  ['planSetGate', true],
  ['idGate', false], // a resume's call
] as const) {
  test(`a call interrupted before the spawn starts no child and changes no state (${gate}, ${lock ? 'a new review' : 'a resume'})`, { plugins: [interrupts] }, async ($, on) => {
    const { w, after } = world(on, { lock })
    const seeded = lock ? null : held('review')
    w.plan = seeded
    await start($)
    await after(1000)
    if (!lock) w.files[`${SO}/inbox.lock`] = { text: '', mtimeMs: NOW + 1000 }
    let release = () => {}
    w[gate] = new Promise(resolve => (release = resolve))
    const calling = exitPlan($)
    await after(1000)
    expect(await calling).toEqual({ deny: 'interrupted' })
    w[gate] = undefined
    release()
    await after(1000)
    expect([w.spawns, w.plan, w.submits]).toEqual([[], seeded, []])
  })
}

test('an interrupted call of the approved plan uses nothing up', { plugins: [interrupts] }, async ($, on) => {
  const { w, after } = world(on)
  w.plan = held('approved')
  await start($)
  let release = () => {}
  w.planGetGate = new Promise(resolve => (release = resolve))
  const calling = exitPlan($)
  await after(1000)
  expect(await calling).toEqual({ deny: 'interrupted' })
  release()
  await after(1000)
  expect([w.plan, w.spawns]).toEqual([held('approved'), []])
})

test('a /resume during the respawn at a load leaves no child and no review', async ($, on) => {
  const { w, after } = world(on)
  w.plan = held('review')
  await start($)
  let release = () => {}
  w.planGetGate = new Promise(resolve => (release = resolve))
  await after(1000) // the respawn reads the state
  const ending = $.session.end({ reason: 'resume', sessionId: w.sessionId, resume: { id: w.sessionId } })
  await after(1000)
  release()
  await ending
  await after(1000)
  expect([w.spawns.filter(s => !s.isStopped), w.plan, w.submits]).toEqual([[], null, []])
})

test('an answer request for the plan round never moves the phase to approved', async ($, on) => {
  const { w, after, clock } = world(on)
  await reviewing($, w, after)
  await after(5000)
  w.files[ANSWERS] = { text: answered('approve'), mtimeMs: clock.now() }
  const line = `Signoff inbox: the user finished the rounds in "${ROOT}/.signoff/${TOPIC}". Tell the user first. When it is handled, run: signoff inbox done ${R(1)}`
  w.requestsOut = () => ({ stdout: `${JSON.stringify({ requests: [{ id: R(1), line }], gone: [] })}\n`, exitCode: 0 })
  request(w, 1, clock.now())
  await after(5000)
  expect([w.submits.map(s => s.text), w.plan, w.reads.includes(ANSWERS)]).toEqual([[line], held('review'), false])
  expect(await exitPlan($)).toEqual({ deny: OPEN })
})

test('a kept wake whose state is gone (an unload) stays kept, and the log says only the error kind', async ($, on) => {
  const { w, after } = world(on)
  w.drop = STOP
  w.plan = held('approved', approvedText())
  await start($)
  w.isStateDead = true
  await turn($)
  await after(1000)
  expect([w.plan, w.toasts]).toEqual([held('approved', approvedText()), []])
  expect(w.logs.some(l => l.startsWith('signoff: plan review failed ('))).toBe(true)
  expect(w.logs.join('\n')).not.toMatch(/answers\.json|approved the plan|stopped and nobody/)
})

test('a failing hook leaves the call to Claude Code', async ($, on) => {
  const { w } = world(on)
  await start($)
  w.isStateDead = true
  expect(await exitPlan($)).toEqual(w.nativeAnswer)
  expect([w.native, w.spawns]).toEqual([[PLAN], []])
})

test('another tool never meets the plan review: no call of the mod', async ($, on) => {
  const { w } = world(on)
  await start($)
  const before = planCost(w)
  await $.tool.call({ tool: 'Read', file_path: '/work/demo/a.md', plan: PLAN } as never)
  expect(planCost(w)).toEqual(before)
})
