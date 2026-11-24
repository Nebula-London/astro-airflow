# Sourced by the launchers (shared by jobserve-new-jobs and targeted-cv-from-master; keep both copies identical).
# Finds the member's job-search folder and the Python to use, and keeps every file the skills write inside the folder:
#   JS_HOME   the job-search folder ($JOB_SEARCH_HOME, else the nearest folder holding .jobsearch/ at or above the cwd
#             or above $CV_WS, else the cwd) - the same rules as tracker.home()
#   JS_LOCAL  $JS_HOME/.jobsearch   (config, answer memory, python, venv, tmp, trash, pending rows, logs)
#   JS_PY     Python >= $JS_PY_WANT, first found of: $JOB_SEARCH_PYTHON; $JS_LOCAL/python (uv-managed); python3.14,
#             python3.15, python3 on PATH (the target first; a newer one only when it is missing). None: uv installs
#             $JS_PY_WANT into $JS_LOCAL/python (its cache in $JS_LOCAL/cache, removed after). No uv: a $JS_PY_MIN+
#             Python on PATH is used with a warning, else it fails with how to get uv. Nothing is installed
#             system-wide. Some endpoint-security tools kill interpreters run from the home folder: a $JS_LOCAL/python
#             that cannot run is removed, never re-downloaded (marker $JS_LOCAL/python-blocked), and a Python on PATH
#             is used instead (brew install python@3.14).
# Exports JOB_SEARCH_HOME, JS_PY, TMPDIR (once .jobsearch exists), PYTHONDONTWRITEBYTECODE, PIP_NO_CACHE_DIR.
# Set JS_CREATE=1 first to create .jobsearch. Creating it is refused in $HOME, / and skill folders.
JS_PY_WANT=3.14; JS_PY_MIN=3.12
_js_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_js_up() { local d="$1"; while :; do [ -d "$d/.jobsearch" ] && { echo "$d"; return 0; }; [ "$d" = / ] && return 1; d="$(dirname "$d")"; done; }
_js_skill() { local d="$1"; while :; do
  { [ -f "$d/SKILL.md" ] && [ -f "$d/scripts/tracker.py" ]; } && return 0
  { [ -f "$d/check_release.sh" ] && [ -d "$d/jobserve-new-jobs" ]; } && return 0
  [ "$d" = / ] && return 1; d="$(dirname "$d")"; done; }
_js_ver() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
_js_ge() { local a="${1#*.}" b="${2#*.}"; [ "${1%%.*}" -gt "${2%%.*}" ] || { [ "${1%%.*}" -eq "${2%%.*}" ] && [ "$a" -ge "$b" ]; }; }
_js_pick() { local want="$1" c p v; shift; for c in "$@"; do
  [ -n "$c" ] || continue; p="$(command -v "$c" 2>/dev/null)" || continue
  v="$(_js_ver "$p")" && _js_ge "$v" "$want" && { echo "$p"; return 0; }; done; return 1; }
_js_fail() { echo "$1" >&2; return 1 2>/dev/null || exit 1; }

if [ -n "${JOB_SEARCH_HOME:-}" ]; then
  case "$JOB_SEARCH_HOME" in /*) JS_HOME="$JOB_SEARCH_HOME" ;; *) JS_HOME="$(pwd -P)/$JOB_SEARCH_HOME" ;; esac
  [ -d "$JS_HOME" ] && JS_HOME="$(cd "$JS_HOME" && pwd -P)"
else
  JS_HOME="$(_js_up "$(pwd -P)")" || { [ -n "${CV_WS:-}" ] && [ -d "$(dirname "$CV_WS")" ] &&
    JS_HOME="$(_js_up "$(cd "$(dirname "$CV_WS")" && pwd -P)")"; } || JS_HOME="$(pwd -P)"
fi
JS_LOCAL="$JS_HOME/.jobsearch"
_js_make() {   # create .jobsearch (chmod 700) unless JS_HOME is $HOME, / or a skill folder
  [ -d "$JS_LOCAL" ] && return 0
  if [ "$JS_HOME" = "$(cd ~ && pwd -P)" ] || [ "$JS_HOME" = / ] || _js_skill "$JS_HOME"; then
    _js_fail "FAIL: $JS_HOME is not a job-search folder: run from your job-search folder (e.g. cd ~/job-search) or set JOB_SEARCH_HOME"
    return 1
  fi
  mkdir -p "$JS_LOCAL" && chmod 700 "$JS_LOCAL" &&
    echo "created $JS_LOCAL (settings, caches and scratch files for the job-search skills)" >&2
}
[ -n "${JS_CREATE:-}" ] && { _js_make || return 1 2>/dev/null || exit 1; }

JS_PY=""
if [ -n "${JOB_SEARCH_PYTHON:-}" ]; then
  JS_PY="$(_js_pick "$JS_PY_MIN" "$JOB_SEARCH_PYTHON")" ||
    { _js_fail "FAIL: JOB_SEARCH_PYTHON=$JOB_SEARCH_PYTHON is not Python $JS_PY_MIN or newer"; return 1 2>/dev/null || exit 1; }
else
  _js_own=""; for _p in "$JS_LOCAL"/python/cpython-3.*/bin/python3; do if [ -x "$_p" ]; then _js_own="$_p"; fi; done
  _js_blocked() {   # the folder's own Python exists but cannot run here: drop it and remember not to fetch it again
    rm -rf "$JS_LOCAL/python"; : > "$JS_LOCAL/python-blocked"
    echo "WARN: the Python in $JS_LOCAL/python cannot run on this machine (killed, likely by security software);" \
         "removed. Use one outside your home folder instead: brew install python@3.14 (or set JOB_SEARCH_PYTHON)" >&2; }
  if [ -n "$_js_own" ] && [ -z "$(_js_ver "$_js_own")" ]; then _js_blocked; _js_own=""; fi
  JS_PY="$(_js_pick "$JS_PY_WANT" "$_js_own" python3.14 python3.15 python3)" || JS_PY=""
  if [ -z "$JS_PY" ] && [ ! -e "$JS_LOCAL/python-blocked" ] && command -v uv >/dev/null 2>&1; then
    _js_make || return 1 2>/dev/null || exit 1
    echo "installing Python $JS_PY_WANT into $JS_LOCAL/python (once; uv, nothing system-wide)" >&2
    mkdir -p "$JS_LOCAL/tmp" &&
    TMPDIR="$JS_LOCAL/tmp" UV_CACHE_DIR="$JS_LOCAL/cache/uv" UV_PYTHON_INSTALL_DIR="$JS_LOCAL/python" \
      uv python install "$JS_PY_WANT" --no-bin --quiet >&2 &&
    JS_PY="$(UV_CACHE_DIR="$JS_LOCAL/cache/uv" UV_PYTHON_INSTALL_DIR="$JS_LOCAL/python" \
      uv python find --managed-python --no-python-downloads "$JS_PY_WANT" 2>/dev/null)" || JS_PY=""
    for _p in "$JS_LOCAL"/python/cpython-"$JS_PY_WANT".*/bin/python3; do if [ -z "$JS_PY" ] && [ -x "$_p" ]; then JS_PY="$_p"; fi; done
    rm -rf "$JS_LOCAL/cache"
    if [ -n "$JS_PY" ] && [ -z "$(_js_ver "$JS_PY")" ]; then _js_blocked; JS_PY=""
    elif [ -z "$JS_PY" ]; then echo "WARN: uv could not install Python $JS_PY_WANT (offline or a proxy?)" >&2; fi
  fi
  if [ -z "$JS_PY" ]; then
    JS_PY="$(_js_pick "$JS_PY_MIN" python3.13 python3.12 python3)" ||
      { if [ -e "$JS_LOCAL/python-blocked" ]; then _js_fail "FAIL: needs Python $JS_PY_WANT (or $JS_PY_MIN+) outside the home folder on this machine: brew install python@3.14, then re-run"
        else _js_fail "FAIL: needs Python $JS_PY_WANT (or $JS_PY_MIN+). Install uv once (brew install uv, or: curl -LsSf https://astral.sh/uv/install.sh | sh) and re-run: it puts Python $JS_PY_WANT inside $JS_LOCAL/python"; fi
        return 1 2>/dev/null || exit 1; }
    if [ -e "$JS_LOCAL/python-blocked" ]; then _js_fix="brew install python@3.14"; else _js_fix="install uv and re-run"; fi
    if [ ! -d "$JS_LOCAL" ] || [ "$(cat "$JS_LOCAL/python-warned" 2>/dev/null)" != "$JS_PY" ]; then   # once per interpreter
      echo "WARN: using Python $(_js_ver "$JS_PY") ($JS_PY); $JS_PY_WANT is recommended: $_js_fix" >&2
      [ -d "$JS_LOCAL" ] && printf '%s\n' "$JS_PY" > "$JS_LOCAL/python-warned"
    fi
  fi
fi
export JOB_SEARCH_HOME="$JS_HOME" JS_PY PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
if [ -d "$JS_LOCAL" ]; then mkdir -p "$JS_LOCAL/tmp" && export TMPDIR="$JS_LOCAL/tmp"; fi
