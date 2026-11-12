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

type File = { text: string; mtimeMs: number }
type World = {
  files: Record<string, File>
  ran: string[][]
  toasts: string[]
  statuses: (string | undefined)[]
  logs: string[]
  fsCalls: number
  listFailures: number
  openExit: number
  /** What `signoff inbox rows <cwd>` prints and exits with. */
  rowsOut: { stdout: string; exitCode: number; isStdoutTruncated?: boolean }
  opened: { id: string; focus?: boolean }[]
  /** What happened, in order: `open` (ui.open), `set:<key>` (a state write), `run:<subcommand>`. */
  order: string[]
  isPaneOpen: boolean
  /** Whether the surface places the pane now (a terminal widened later turns it true). */
  isPlaced: boolean
  /** While set, `inbox open` waits for it: a run in flight. */
  openGate: Promise<void> | undefined
  state: (projects: { id: string; waiting: number }[]) => void
  beat: () => void
}

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
    listFailures: 0,
    openExit: 0,
    rowsOut: { stdout: `${JSON.stringify(ROWS)}\n`, exitCode: 0 },
    opened: [],
    order: [],
    isPaneOpen: false,
    isPlaced,
    openGate: undefined,
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
    const file = w.files[e.path]
    const isDir = !file && (dirOf(e.path).length > 0 || e.path.startsWith(ROOT) || e.path === '/work' || e.path in links)
    if (!file && !isDir) return missing
    const mtimeMs = file ? file.mtimeMs : Math.max(0, ...dirOf(e.path).map(f => w.files[f]?.mtimeMs ?? 0))
    const realPath = links[e.path] ?? e.path
    return { value: { kind: file ? 'file' : 'dir', size: file ? file.text.length : 0, mtimeMs, isLink: e.path in links, ...(e.resolve ? { realPath } : {}) } }
  })
  on('fs.read', (_, e) => {
    w.fsCalls++
    const file = w.files[e.path]
    return file ? { value: file.text } : missing
  })
  on('fs.list', (_, e) => {
    w.fsCalls++
    if (w.listFailures > 0) return (w.listFailures--, { deny: 'EIO: i/o error' })
    const names = dirOf(e.path).map(f => f.slice(e.path.length + 1)).filter(n => !n.includes('/'))
    return { value: names.map(name => ({ name, kind: 'file' as const, size: 1, mtimeMs: now, isLink: false })) }
  })
  on('process.run', async (_, e) => {
    const argv = [...e.argv]
    w.ran.push(argv)
    w.order.push(`run:${argv[2]}`)
    if (argv.slice(1).join(' ') === 'inbox home') return { value: ok(`${home}\n`) }
    if (argv.slice(1, 3).join(' ') === 'inbox open') {
      await w.openGate
      return { value: ok('', `signoff: no browser opened; open ${LINK} yourself (it works once)\n`, w.openExit) }
    }
    if (argv.slice(1).join(' ') === `inbox rows ${cwd}`)
      return { value: { ...ok(w.rowsOut.stdout, `signoff: ${LINK}\n`, w.rowsOut.exitCode), isStdoutTruncated: w.rowsOut.isStdoutTruncated ?? false } }
    return { value: ok('', 'unexpected', 2) }
  })
  on('state.set', (_, e, next) => (w.order.push(`set:${(e as { key?: string }).key}`), next(e)))
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
  on('ui.log', (_, e) => (w.logs.push(e.text), { value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: cwd }))
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
    { key: 'close', text: 'Close', hotkey: 'q' },
  ])
  const panel = await $.ui.mount({ ...pane(), surface: 'terminal' })
  // A round's row opens the item the inbox named for it, as in the browser.
  const pressed = [
    ['app:item:3/0/1', inApp(3, '0/1')],
    ['app:round:3/0', inApp(3, '0/2')],
    ['app:round:5/1', inApp(5, '1/0')],
    ['round:3/0', target(3, '0/2')],
  ] as const
  for (const [key, argv] of pressed) {
    await panel.press({ key })
    expect(w.ran.at(-1)).toEqual(argv)
  }
  expect(w.toasts).toEqual([APP_OPENED, APP_OPENED, APP_OPENED, OPENED]) // not "the question is open": an older app shows the list
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
  expect(await appButtons()).toBe(4) // the system's Applications folder counts too
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

test('off: nothing shown, nothing read or run, and /signoff:open still opens the panel', { options: { hint: 'off' } }, async ($, on) => {
  const { w, after } = world(on)
  w.state([{ id: ID, waiting: 3 }])
  await start($)
  await after(30_000)
  expect(await bandTexts($)).toEqual(['engine'])
  expect([w.ran, w.fsCalls, w.toasts, w.statuses]).toEqual([[], 0, [], [undefined]]) // a status line from before is cleared
  await openCommand($)
  expect(w.ran).toEqual([[expect.stringMatching(/\/bin\/signoff$/), 'inbox', 'rows', `${ROOT}/src`]])
  expect((await paneButtons($)).buttons.map(b => b.key)).toContain('all')
  await after(30_000)
  expect([w.ran.length, w.fsCalls]).toEqual([1, 2]) // the opening's look for the app, one per Applications folder
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
