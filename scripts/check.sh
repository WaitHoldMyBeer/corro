#!/usr/bin/env bash
# One command that answers the screening questions a judge can ask of the
# repository: is Clio read-only, is anything hardcoded, is anything tracked
# that must not be, and does the code lint, type-check and pass its tests.
#
#   scripts/check.sh            run everything
#   scripts/check.sh --quick    skip the type check
#
# Exit status is non-zero if any step fails. Steps that cannot run on this
# machine say so in the summary instead of passing silently.

set -u
cd "$(dirname "$0")/.."

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed; see the README." >&2
  exit 2
fi

PYTEST=(uv run --quiet --with pytest python -m pytest -q -rs --tb=short -p no:cacheprovider)
PY_PATHS=()
for dir in server shared tests; do [ -d "$dir" ] && PY_PATHS+=("$dir"); done

names=()
results=()

step() {
  local name="$1"
  shift
  printf '\n== %s\n' "$name"
  local log status
  log="$(mktemp)"
  "$@" 2>&1 | tee "$log"
  status=${PIPESTATUS[0]}
  names+=("$name")
  if [ "$status" -ne 0 ] && [ "${ADVISORY:-0}" -eq 1 ]; then
    results+=("advisory")
  elif [ "$status" -ne 0 ]; then
    results+=("FAIL")
  elif grep -q "NOT RUN" "$log"; then
    results+=("SKIPPED")
  else
    results+=("ok")
  fi
  rm -f "$log"
}

# Pinned rule set (syntax errors, undefined names, unused imports), independent of
# any ruff configuration on the machine that runs this.
step "Lint (ruff: syntax, undefined names, unused imports)" \
  uvx --quiet ruff check --isolated --select E9,F --line-length 120 "${PY_PATHS[@]}"

if [ "$QUICK" -eq 0 ]; then
  uv sync --quiet --frozen 2>/dev/null || uv sync --quiet
  ADVISORY=1 step "Types (pyright, advisory)" \
    bash -c 'uvx --quiet pyright --pythonpath .venv/bin/python server shared | tail -n 25; exit "${PIPESTATUS[0]}"'
fi

step "Clio is read-only: no non-GET request can reach the API" "${PYTEST[@]}" tests/test_clio_readonly.py
step "Nothing about the demo matter is hardcoded" "${PYTEST[@]}" -s tests/test_no_case_literals.py
step "No case material, secret or local data is tracked" "${PYTEST[@]}" tests/test_repo_hygiene.py

# Measurements, printed in full: accuracy per verdict and latency, misses included.
step "Checker evaluation: synthetic ledger" "${PYTEST[@]}" -s tests/test_check_evaluation.py
step "Checker evaluation: ledger in the local database, sentences built at run time" \
  "${PYTEST[@]}" -s tests/test_check_evaluation_live.py

others=()
for file in tests/test_*.py; do
  case "$file" in
    tests/test_clio_readonly.py | tests/test_no_case_literals.py | tests/test_repo_hygiene.py) ;;
    tests/test_check_evaluation.py | tests/test_check_evaluation_live.py) ;;
    *) [ -e "$file" ] && others+=("$file") ;;
  esac
done
if [ "${#others[@]}" -gt 0 ]; then
  step "Tests" "${PYTEST[@]}" "${others[@]}"
fi

printf '\n== Summary\n'
failed=0
for i in "${!names[@]}"; do
  printf '  %-9s %s\n' "${results[$i]}" "${names[$i]}"
  [ "${results[$i]}" = "FAIL" ] && failed=1
done
if printf '%s\n' "${results[@]}" | grep -q SKIPPED; then
  echo "  A SKIPPED step verified nothing and is not a pass; its notice above says how to make it run"
  echo "  (both need a synced matter in the local database, which a fresh clone does not have)."
fi
if printf '%s\n' "${results[@]}" | grep -q advisory; then
  echo "  An advisory step reported problems but does not fail the check."
fi
exit "$failed"
