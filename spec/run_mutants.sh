#!/usr/bin/env bash
# Plants each bug listed in spec/mutants/manifest.txt, runs the unittest suite and
# the spec, and requires a failure. Every planted file is restored afterwards.
#
#   spec/run_mutants.sh            all mutants
#   spec/run_mutants.sh M3 M8      only these ids
#
# Exit 0 only when the unchanged code passes, every active mutant is caught, and
# every pending mutant still survives. PYTHON overrides the interpreter.
set -u

ROOT=$(git rev-parse --show-toplevel) || exit 2
cd "$ROOT" || exit 2
PY=${PYTHON:-python3}
MANIFEST=spec/mutants/manifest.txt
LOGS=$(mktemp -d "${TMPDIR:-/tmp}/run_mutants.XXXXXX")

export DRY_RUN=true HYPOTHESIS_PROFILE=ci
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_CHAT_ID_CHANNEL

APPLIED=""   # patch currently planted, so an interrupted run can restore it

restore() {
  if [ -n "$APPLIED" ]; then
    git apply -R "$APPLIED" || echo "ERROR: could not restore $APPLIED; run: git apply -R $APPLIED" >&2
    APPLIED=""
  fi
}
trap 'restore' EXIT
trap 'restore; exit 130' INT TERM

# Prints one word: pass, caught, or broken (the checks could not run: import or collection error).
run_checks() {
  log=$1
  "$PY" -m unittest discover -s .github/scripts -p 'test_*.py' >"$log" 2>&1
  u=$?
  "$PY" -m pytest spec -q -x -p no:cacheprovider >>"$log" 2>&1
  p=$?
  if grep -q "unittest.loader._FailedTest" "$log" || [ "$p" -gt 1 ]; then
    echo broken
  elif [ "$u" -ne 0 ] || [ "$p" -ne 0 ]; then
    echo caught
  else
    echo pass
  fi
}

# The patched files' git blob hashes, to prove a restore is exact.
target_files() { sed -n 's|^+++ b/||p' "$1"; }
hash_targets() { target_files "$1" | while read -r f; do git hash-object "$f"; done; }

wanted() {
  [ $# -le 1 ] && return 0
  id=$1; shift
  for w in "$@"; do [ "$w" = "$id" ] && return 0; done
  return 1
}

baseline=$(run_checks "$LOGS/baseline.log")
if [ "$baseline" != pass ]; then
  echo "ERROR: the checks do not pass on unchanged code ($baseline); mutants would prove nothing." >&2
  cat "$LOGS/baseline.log" >&2
  exit 2
fi

failures=0
printf '%-36s %-8s %s\n' MUTANT STATUS RESULT
while read -r status patch _desc; do
  case "$status" in ''|'#'*) continue ;; esac
  id=${patch%%_*}
  wanted "$id" "$@" || continue
  path=spec/mutants/$patch

  if ! git apply --check "$path" 2>"$LOGS/$id.apply"; then
    printf '%-36s %-8s %s\n' "$patch" "$status" "STALE: patch no longer applies; regenerate it"
    failures=$((failures + 1))
    continue
  fi
  before=$(hash_targets "$path")
  git apply "$path" && APPLIED=$path
  result=$(run_checks "$LOGS/$id.log")
  restore
  if [ "$(hash_targets "$path")" != "$before" ]; then
    echo "ERROR: $patch did not restore cleanly; stopping." >&2
    exit 2
  fi

  case "$status:$result" in
    active:caught)  verdict="ok: caught" ;;
    pending:pass)   verdict="ok: survives, as expected while pending" ;;
    active:pass)    verdict="FAIL: survived; the checks no longer catch this bug"; failures=$((failures + 1)) ;;
    pending:caught) verdict="FAIL: now caught; mark it active in $MANIFEST"; failures=$((failures + 1)) ;;
    *:broken)       verdict="FAIL: checks could not run with this patch (see log)"; failures=$((failures + 1)) ;;
    *)              verdict="FAIL: unknown status '$status'"; failures=$((failures + 1)) ;;
  esac
  printf '%-36s %-8s %s\n' "$patch" "$status" "$verdict"
  case "$verdict" in FAIL*) tail -n 20 "$LOGS/$id.log" | sed 's/^/    /' ;; esac
done < "$MANIFEST"

echo "logs: $LOGS"
if [ "$failures" -ne 0 ]; then
  echo "$failures mutant check(s) failed" >&2
  exit 1
fi
echo "all mutant checks passed"
