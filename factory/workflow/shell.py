"""Portable shell snippets for workflow ``FnNode`` / ``GateNode`` commands.

Workflow commands are executed with :func:`asyncio.create_subprocess_shell`, which
runs them under ``/bin/sh``. They therefore must be POSIX-sh compatible *and* must
only depend on utilities that exist on every platform the factory runs on.

GNU coreutils' ``timeout`` is **not** part of a stock macOS install (it ships as
``gtimeout`` if a user installs coreutils via Homebrew, and not at all otherwise).
Commands that invoke ``timeout`` directly abort with ``/bin/sh: timeout: command
not found`` on macOS, which silently turns benchmark gates into false negatives.

:data:`PORTABLE_TIMEOUT_PREAMBLE` defines a ``run_with_timeout`` shell function
that reproduces ``timeout``'s interface using whatever the host provides, falling
back to a pure-POSIX watchdog when no ``timeout`` binary exists at all. Prefix a
command string with the preamble and call ``run_with_timeout <seconds> <cmd...>``
in place of ``timeout <seconds> <cmd...>``.
"""

#: Name of the shell function defined by :data:`PORTABLE_TIMEOUT_PREAMBLE`.
TIMEOUT_FN = "run_with_timeout"

#: POSIX-sh definition of ``run_with_timeout <seconds> <command> [args...]``.
#:
#: Resolution order:
#:
#: 1. ``timeout`` — GNU coreutils, present on Linux and in most containers.
#: 2. ``gtimeout`` — the name coreutils installs under on macOS/Homebrew.
#: 3. A background-process watchdog built from ``sleep``/``kill``, which are
#:    mandated by POSIX and present on stock macOS.
#:
#: The fallback mirrors ``timeout``'s observable contract: it sends ``TERM`` when
#: the limit expires, escalates to ``KILL`` after a short grace period, and exits
#: ``124`` on timeout. The watchdog's own stdio is redirected to ``/dev/null`` so
#: it never holds open the pipe of an enclosing ``$(...)`` substitution — without
#: that, command substitution would block for the full timeout even when the
#: supervised command finished immediately.
PORTABLE_TIMEOUT_PREAMBLE = (
    "run_with_timeout() { "
    "_rwt_limit=$1; shift; "
    'if command -v timeout >/dev/null 2>&1; then timeout "$_rwt_limit" "$@"; '
    'elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$_rwt_limit" "$@"; '
    "else "
    "_rwt_flag=$(mktemp); "
    '"$@" & _rwt_pid=$!; '
    "( "
    'sleep "$_rwt_limit"; '
    'echo timeout > "$_rwt_flag"; '
    "kill -TERM $_rwt_pid; "
    "sleep 10; "
    "kill -KILL $_rwt_pid "
    ") >/dev/null 2>&1 & _rwt_watchdog=$!; "
    "wait $_rwt_pid; _rwt_status=$?; "
    "kill -TERM $_rwt_watchdog >/dev/null 2>&1; "
    'if [ -s "$_rwt_flag" ]; then _rwt_status=124; fi; '
    'rm -f "$_rwt_flag"; '
    "return $_rwt_status; "
    "fi; "
    "}; "
)


def with_portable_timeout(command: str) -> str:
    """Prepend the ``run_with_timeout`` definition to a shell ``command``.

    Parameters
    ----------
    command:
        A POSIX-sh command string that calls ``run_with_timeout`` instead of
        ``timeout``.

    Returns
    -------
    str
        ``command`` prefixed with :data:`PORTABLE_TIMEOUT_PREAMBLE`.
    """
    return PORTABLE_TIMEOUT_PREAMBLE + command
