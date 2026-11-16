#!/usr/bin/env bash
# Cut a giffy release: bump the version, test, commit, tag, push.
#
#   tools/release.sh 0.1.2            # full release
#   tools/release.sh 0.1.2 --dry-run  # bump + test only; changes nothing, pushes nothing
#
# Before running: add a "## v0.1.2 (YYYY-MM-DD)" entry to CHANGELOG.md. It may be
# uncommitted; the release commit picks it up. Everything else must be committed.
set -Eeuo pipefail
V="${1:-}"
DRY=0; [[ "${2:-}" == "--dry-run" ]] && DRY=1
die() { echo "release: $*" >&2; exit 1; }
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: tools/release.sh MAJOR.MINOR.PATCH [--dry-run]"
cd "$(git rev-parse --show-toplevel)"

# --- checks (nothing is changed until these pass) ---
command -v uv >/dev/null || die "uv is not installed"
[[ "$(git branch --show-current)" == main ]] || die "not on main"
[[ -z "$(git status --porcelain -- . ':!CHANGELOG.md')" ]] || die "uncommitted changes (only CHANGELOG.md may be dirty)"
git fetch -q origin --tags
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "main is not in sync with origin/main"
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && die "tag v$V already exists"
CUR="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
[[ "$(printf '%s\n%s\n' "$CUR" "$V" | sort -V | tail -1)" == "$V" && "$CUR" != "$V" ]] || die "v$V is not newer than v$CUR"
NOTES="$(awk -v h="## v$V " 'index($0, h) == 1 {on=1; next} on && /^## / {exit} on' CHANGELOG.md)"
[[ -n "${NOTES//[[:space:]]/}" ]] || die "CHANGELOG.md has no entry for v$V (add a '## v$V (date)' section)"

# --- bump; undo it if anything below fails, or at the end of a dry run ---
restore() { git checkout -q -- pyproject.toml uv.lock; }
trap 'echo "release: failed; version bump undone" >&2; restore' ERR
sed -i.bak "s/^version = \"$CUR\"/version = \"$V\"/" pyproject.toml && rm pyproject.toml.bak
uv lock -q
echo "release: v$CUR -> v$V"
uv run pytest -q
if [[ $DRY == 1 ]]; then
  trap - ERR; restore
  echo "release: dry run OK; nothing committed, tagged or pushed"
  exit 0
fi

# --- publish ---
git add pyproject.toml uv.lock CHANGELOG.md
git commit -q -m "release: v$V"
trap - ERR  # from here the commit exists; failures are reported, not undone
git tag -a "v$V" -m "v$V: $(printf '%s\n' "$NOTES" | sed -n '/[^[:space:]]/{s/^- //;p;q;}')"
git push -q origin main "v$V"
echo "release: v$V pushed; pin it with giffy @ git+https://github.com/chrxsluu/giffy@v$V"
