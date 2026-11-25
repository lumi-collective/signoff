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

/**
 * This session's plan review. topic: its topic folder's name, set at the first take-over and kept until a /clear or
 * a /resume. text: the plan as sent on stdin ('' once the review is over). phase: review while the user's answer is
 * awaited; approved until the pass uses it up; revise or answered (no approval) only while their wake is held.
 * wake: the fixed message to the model, held until it entered (isToasted: the user had the toast; isOut: a submit of
 * it went out and was not seen refused, so after a reload the engine may still hold it).
 * In review only: since, the time (ms) of the take-over, which an older answers file (by its time or its saved)
 * never answers; after, the highest round or plan number the topic folder held at the take-over, which a resumed
 * child's round must be above; and root, the session root it was taken over in, where every respawn runs. An
 * approval keeps root: it passes in that root only.
 */
export type PlanReview = {
  topic: string
  text: string
  phase: 'review' | 'approved' | 'revise' | 'answered' | null
  wake: { text: string; isToasted: boolean; isOut: boolean } | null
  since?: number
  root?: string
  after?: number
}

// The hooks module's session values (hooks/register.tsx): numbers, the panel's one-line titles and the plan review.
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
      /** Where the panel is drawn: its pane, the band (an open that was not placed), or nowhere. */
      where: 'pane' | 'band' | null
      /**
       * The inbox's requests to this session: the time (ms) from which a request file counts as new, set at the
       * first live look that finds the session root's project and again when the root moves into another one; that
       * project's id (absent in a state from before it); and the ids handed over not yet seen done (at most 1000, the
       * oldest dropped). Null until then.
       */
      requests: { since: number; sent: string[]; project?: string } | null
      /** The plan review (a reload keeps it, with its wake). Null until a first take-over. */
      plan: PlanReview | null
    }
  }
}
