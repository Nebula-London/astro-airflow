#!/usr/bin/env bash
# Make the renderer/checker dependencies available and export CV_PY (the interpreter to use).
# Usage:  source <skill-dir>/scripts/setup_env.sh [venv_dir]
# - A sandbox whose python3 is 3.12+ and already has the packages (e.g. Claude.ai) uses it as-is.
# - Otherwise a venv on JS_PY (Python 3.14+, see local_env.sh), default <job-search>/.jobsearch/venv (creates
#   .jobsearch, so run it from your job-search folder or set JOB_SEARCH_HOME). A venv on another Python version is
#   rebuilt. requirements.txt is installed with uv when present, else pip; no package cache is kept.
# Safe to source: never exits the calling shell; returns non-zero on failure.
_cv_here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
_cv_check='import sys, reportlab, pypdf; assert sys.version_info >= (3, 12)'
if [ -z "${1:-}" ] && python3 -c "$_cv_check" 2>/dev/null && ! command -v uv >/dev/null 2>&1; then
  CV_PY="$(python3 -c 'import sys; print(sys.executable)')"     # absolute: survives cd and fresh shells
else
  # shellcheck source=local_env.sh
  JS_CREATE=1 source "$_cv_here/local_env.sh" || { return 1 2>/dev/null || exit 1; }
  _cv_venv="${1:-$JS_LOCAL/venv}"; _cv_want="$(_js_ver "$JS_PY")"
  if [ -x "$_cv_venv/bin/python" ] && [ "$(_js_ver "$_cv_venv/bin/python")" != "$_cv_want" ]; then
    echo "rebuilding $_cv_venv on Python $_cv_want (was $(_js_ver "$_cv_venv/bin/python"))" >&2; rm -rf "$_cv_venv"
  fi
  [ -x "$_cv_venv/bin/python" ] || echo "installing the CV PDF libraries into $_cv_venv (once)" >&2
  _cv_tmp="$JS_LOCAL/tmp/pip-$$"; mkdir -p "$_cv_tmp"   # venv/pip scratch (pip leaves a cacert.pem): removed below
  if command -v uv >/dev/null 2>&1; then
    { [ -x "$_cv_venv/bin/python" ] || TMPDIR="$_cv_tmp" UV_NO_CACHE=1 uv venv --quiet --seed --python "$JS_PY" "$_cv_venv"; } &&
    TMPDIR="$_cv_tmp" UV_NO_CACHE=1 uv pip install --quiet --python "$_cv_venv/bin/python" -r "$_cv_here/requirements.txt"
  else
    { [ -x "$_cv_venv/bin/python" ] || TMPDIR="$_cv_tmp" "$JS_PY" -m venv "$_cv_venv"; } &&
    TMPDIR="$_cv_tmp" PIP_NO_CACHE_DIR=1 "$_cv_venv/bin/python" -m pip -q install --no-cache-dir \
      --disable-pip-version-check -r "$_cv_here/requirements.txt"
  fi && CV_PY="$_cv_venv/bin/python" ||
    { rm -rf "$_cv_tmp"; echo "setup_env: dependency install failed" >&2; return 1 2>/dev/null || exit 1; }
  rm -rf "$_cv_tmp"
fi
export CV_PY
export CV_SCRIPTS="$_cv_here"
echo "CV_PY=$CV_PY"
echo "CV_SCRIPTS=$CV_SCRIPTS"
"$CV_PY" -c 'import pymupdf' 2>/dev/null || command -v pdftoppm >/dev/null || \
  echo "note: neither pymupdf nor pdftoppm found; check_pdf.py will skip page images" >&2
