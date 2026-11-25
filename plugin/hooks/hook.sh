#!/bin/sh
# Runs Signoff's hook for the inbox (hooks.json). Claude Code runs it in every session, through sh on macOS and Linux
# and Git Bash on Windows (without Git Bash, its commands do nothing in PowerShell). The hook always exits 0: exit 2
# would block the prompt or the stop. hooks.json execs this script, so $PPID is Claude Code.

# shortcut: the inbox needs Unix sockets, so it never runs on Windows and there is nothing to note: no Python starts
# there. Drop these lines once the inbox runs on Windows.
if [ "$OS" = Windows_NT ]; then
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
# which stays the one resolver: SIGNOFF_INBOX_HOME and a relative HOME go on to Python, and so do no uname and Windows
# once a default home has a lock.
if [ -z "$SIGNOFF_INBOX_HOME" ]; then
  case "$HOME" in /*)
    mac="$HOME/Library/Application Support/signoff"
    case "$XDG_STATE_HOME" in
      /*) xdg="$XDG_STATE_HOME/signoff" ;;
      *) xdg="$HOME/.local/state/signoff" ;;
    esac
    # No lock in either default home: no inbox has run here (none runs on Windows). Tested before uname, a process.
    [ -e "$mac/inbox.lock" ] || [ -e "$xdg/inbox.lock" ] || exit 0
    home=
    case "$(uname -s 2>/dev/null)" in
      Darwin) home=$mac ;;
      ""|CYGWIN*|*_NT-*) ;;  # no uname, or Windows with OS scrubbed (a native Python looks in LOCALAPPDATA)
      *) home=$xdg ;;
    esac
    if [ -n "$home" ]; then
      case "$1" in
        # SessionStart runs Python while the lock is there, live or not: the hand-over, and the session file an
        # inbox started later shows.
        start|session) [ -e "$home/inbox.lock" ] || exit 0 ;;
        # The hooks keep waiting.flag non-empty while a session may wait: with it empty or absent, there is no wait
        # to end. Tested first, so a tool call starts no process.
        clear) [ -s "$home/waiting.flag" ] && live || exit 0 ;;
        *) live || exit 0 ;;
      esac
    fi ;;
  esac
fi

script="${0%/*}/../signoff/signoff.py"
command -v python3 >/dev/null 2>&1 && SIGNOFF_CLAUDE_PID="$PPID" python3 -I "$script" inbox hook
exit 0
