/**
 * The panel's list, as `signoff inbox rows <cwd>` gives it: this project's open rounds by their run's number and
 * their index, each with its items not decided yet and the item its own row opens. Titles are the agent's text, one
 * line, at most 200 characters; no path, link or token of Signoff's own (a URL the agent wrote stays). isFailed: the
 * command did not answer with that shape.
 */
export type PanelRows = {
  rounds: { run: number; round: number; title: string; item: number; items: { item: number; title: string }[] }[]
  /** Rounds and items the reply left out (it holds 24 at most). */
  more: number
  /** The inbox's own number, sent back when a row is opened; 0 when the reply named none (no round listed). */
  inbox: number
  isFailed: boolean
}

// The hooks module's session values (hooks/register.tsx): numbers, and the panel's one-line titles.
declare module 'claude-code' {
  interface PluginState {
    signoff: {
      /** What waits on the user in this session's project, from state.json; 0 while no inbox lives. */
      count: number
      /** The count the band was hidden at; null when not hidden. */
      hiddenFor: number | null
      /** Whether this session has had its toast. */
      isToasted: boolean
      /** The panel's list from its last opening; null while it is read. */
      rows: PanelRows | null
      /** Whether the macOS app was installed at the panel's last opening. */
      hasApp: boolean
      /** Where the panel is drawn: its pane, the band (an open that was not placed), or nowhere. */
      where: 'pane' | 'band' | null
    }
  }
}
