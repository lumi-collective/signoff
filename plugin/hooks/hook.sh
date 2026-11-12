#!/bin/sh
# Runs Signoff's hook for the inbox (hooks.json), or with "monitor" its monitor (monitors/monitors.json). Claude Code
# runs both in every session, through sh on macOS and Linux and Git Bash on Windows (without Git Bash, their
# commands do nothing in PowerShell). The hook always exits 0: exit 2 would block the prompt or the stop. The monitor
# never ends while its session lives: Claude Code tells the model about a monitor that ended, which costs every
# session a turn (verified 2026-11-07, CLI 2.1.319). hooks.json and monitors.json exec this script, so $PPID is
# Claude Code.

# The monitor's way to do nothing: sleep until the command in $1 succeeds (no $1: until the session ends). It looks
# for Claude Code by $PPID, which Git Bash does not have: there it sleeps on.
wait_for() {
  while :; do
    sleep 30
    [ "$OS" = Windows_NT ] || kill -0 "$PPID" 2>/dev/null || exit 0
    [ -n "$1" ] && "$1" && return 0
  done
}
monitor=
if [ "$1" = monitor ]; then
  monitor=1
  SIGNOFF_SINCE=$(date +%s); export SIGNOFF_SINCE  # requests from before the session are SessionStart's
fi
# shortcut: the inbox needs Unix sockets, so it never runs on Windows and there is nothing to note: no Python starts
# there (the monitor sleeps). Drop these lines once the inbox runs on Windows.
if [ "$OS" = Windows_NT ]; then
  [ -n "$monitor" ] && wait_for
  exit 0
fi

# The macOS app finds this plugin's signoff command through <home>/launcher, a link a new session's SessionStart
# keeps (hooks.json: startup only, as a resumed session may run an older plugin; an update moves the plugin). The
# home as inbox_home() has it; a relative one is off. Silent, as every hook here.
link_launcher() {
  case "$SIGNOFF_INBOX_HOME" in
    /*) h=$SIGNOFF_INBOX_HOME ;;
    "") case "$HOME" in /*) h="$HOME/Library/Application Support/signoff" ;; *) return ;; esac ;;
    *) return ;;
  esac
  t=$(CDPATH='' cd "${0%/*}/../bin" 2>/dev/null && pwd -P) || return
  # Not from a git checkout (--plugin-dir, or a directory marketplace, which runs in place): the user's app would
  # run whatever the checkout holds. A worktree has a .git file. shortcut: only a .git beside the plugin folder
  # counts; a copy outside git, or a repo rooted at the plugin folder, still writes the link.
  t=${t%/bin}
  [ -e "${t%/*}/.git" ] && return
  t="$t/bin/signoff"
  [ -f "$t" ] && [ -x "$t" ] || return
  # The inbox's rule for its home: a folder of the user's own, not a link. Made 0700 only when absent.
  [ -e "$h" ] || [ -L "$h" ] || mkdir -m 700 "$h" 2>/dev/null || return
  # shellcheck disable=SC3067  # -O is not POSIX, but macOS sh (bash), bash and dash have it
  [ ! -L "$h" ] && [ -d "$h" ] && [ -O "$h" ] || return
  l="$h/launcher"
  [ "$(readlink "$l" 2>/dev/null)" = "$t" ] && return
  # shortcut: only a link to something that is not a folder is replaced: mv would move into a folder, and a file
  # there may be the user's. The app then refuses it and says why; replace it by hand if that ever comes up.
  if [ -L "$l" ]; then [ -d "$l" ] && return; elif [ -e "$l" ]; then return; fi
  # A new link, then a rename over the old one: a reader never sees none. Readers skip .tmp-* (one may stay when mv
  # fails).
  ln -s "$t" "$h/.tmp-launcher-$$" 2>/dev/null && mv -f "$h/.tmp-launcher-$$" "$l" 2>/dev/null
}
# The two tests come first, so a session without the app starts no process for this.
if [ "$1" = start ] && { [ -d /Applications/Signoff.app ] || [ -d "$HOME/Applications/Signoff.app" ]; } &&
  [ "$(uname -s 2>/dev/null)" = Darwin ]; then
  link_launcher
fi

# Whether the inbox lives: it touches inbox.lock at each look (STATE_EVERY, 2 s), and a stopped or killed one leaves
# it old. -mmin -1 is within 60 s for BSD, GNU and busybox find. A find that fails (none on PATH, no -mmin) leaves the
# lock alone to decide, as before.
live() {
  [ -e "$home/inbox.lock" ] || return 1
  t=$(find "$home/inbox.lock" -prune -mmin -1 2>/dev/null) || return 0
  [ -n "$t" ]
}

# Most sessions have no live inbox: stop before starting Python (about 50 ms, and on a Mac without the Command Line
# Tools /usr/bin/python3 would offer to install them). This mirrors only inbox_home()'s macOS and Linux defaults,
# which stays the one resolver: SIGNOFF_INBOX_HOME, a relative HOME or no uname go on to Python.
if [ -z "$SIGNOFF_INBOX_HOME" ]; then
  case "$HOME" in /*)
    home=
    case "$(uname -s 2>/dev/null)" in
      Darwin) home="$HOME/Library/Application Support/signoff" ;;
      ""|CYGWIN*|*_NT-*) ;;  # no uname, or Windows with OS scrubbed (a native Python looks in LOCALAPPDATA)
      *) case "$XDG_STATE_HOME" in
           /*) home="$XDG_STATE_HOME/signoff" ;;
           *) home="$HOME/.local/state/signoff" ;;
         esac ;;
    esac
    if [ -n "$home" ]; then
      case "$1" in
        # SessionStart runs Python while the lock is there, live or not: the hand-over, and the session file an
        # inbox started later shows.
        start|session) [ -e "$home/inbox.lock" ] || exit 0 ;;
        # The hooks keep waiting.flag non-empty while a session may wait: with it empty or absent, there is no wait
        # to end. Tested first, so a tool call starts no process.
        clear) [ -s "$home/waiting.flag" ] && live || exit 0 ;;
        monitor) live || wait_for live ;;
        *) live || exit 0 ;;
      esac
    fi ;;
  esac
fi

script="${0%/*}/../signoff/signoff.py"
if [ -n "$monitor" ]; then
  command -v python3 >/dev/null 2>&1 && exec python3 -I "$script" inbox monitor
  wait_for  # no Python
fi
command -v python3 >/dev/null 2>&1 && SIGNOFF_CLAUDE_PID="$PPID" python3 -I "$script" inbox hook
exit 0
