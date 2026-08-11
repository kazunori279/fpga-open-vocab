#!/usr/bin/env bash
#
# Copy this subtree out to the public repository.
#
#     tools/export_public.sh              # export and show what changed
#     tools/export_public.sh --commit     # ...and commit it there
#     tools/export_public.sh --commit --push
#
# Destination defaults to ~/Documents/GitHub/fpga-open-vocab; override with
# FGX_PUBLIC=/some/path.
#
# Why a script rather than two remotes
# ------------------------------------
# Development happens in a private monorepo of unrelated personal projects, and
# this project is one directory inside it. The public repository is that
# directory and nothing else. So the two cannot be branches of each other, and
# every push is a subtree copy.
#
# The copy itself is three lines. The reason this is a script is the part that
# is *not* the copy: an exclusion that lives in someone's memory gets forgotten
# exactly once, and once is enough when the mistake is public and permanent.
# Everything below the copy is a tripwire, and each one is here because it
# guards something that has actually been wrong in this tree at some point:
#
#   - Third-party binaries. vendor/plasm_led/ and firmware/vendor/*.uf2 were
#     committed for months before anyone read the "Copyright (C) 2013 - 2025
#     Efinix Inc. All rights reserved." header on the Efinity reports. They are
#     gitignored now; this fails if they come back.
#   - schematic.pdf. Not ours to redistribute, and *.pdf is gitignored -- which
#     is precisely the kind of protection that lasts until someone types
#     `git add -f`.
#   - The shipped export. The opposite failure: model/runs/ is gitignored and
#     the export is re-admitted by a four-line negation chain. Break that chain
#     and nothing errors, the public repository just quietly stops containing
#     the model. So this asserts the three files are *present*.
#   - Secrets. Cheap to check, and the check is worth nothing on the day it
#     finds nothing.
#
# The export is taken from HEAD, not from the working tree. A dirty tree means
# what got published does not correspond to any commit anyone can point at, so
# that is a hard error rather than a warning.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
DEST=${FGX_PUBLIC:-$HOME/Documents/GitHub/fpga-open-vocab}
COMMIT=0
PUSH=0
for a in "$@"; do
    case "$a" in
        --commit) COMMIT=1 ;;
        --push)   PUSH=1; COMMIT=1 ;;
        *) echo "export_public: unknown argument '$a'" >&2; exit 2 ;;
    esac
done

cd "$SRC"

if [ -n "$(git status --porcelain -- .)" ]; then
    echo "export_public: the source tree has uncommitted changes." >&2
    echo "  Publishing them would put content in the public repository that no" >&2
    echo "  commit here describes. Commit or stash first:" >&2
    git status --short -- . >&2
    exit 1
fi

[ -d "$DEST/.git" ] || {
    echo "export_public: $DEST is not a git repository." >&2
    echo "  Clone the public repo there, or set FGX_PUBLIC." >&2
    exit 1
}

SRC_COMMIT=$(git rev-parse --short HEAD)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# HEAD:<prefix> is this subdirectory's own tree, so the archive needs no prefix
# stripping -- its root is this directory.
#
# Run from the repository toplevel, and not from here. Both git archive and git
# ls-tree apply the current subdirectory as an implicit pathspec, so the obvious
# spelling of this line produces an empty archive and exits 0 while doing it.
ROOT=$(git rev-parse --show-toplevel)
PREFIX=$(git rev-parse --show-prefix)
git -C "$ROOT" archive "HEAD:${PREFIX%/}" | tar -x -C "$STAGE"

n=$(find "$STAGE" -type f | wc -l | tr -d ' ')
[ "$n" -gt 100 ] || {
    echo "export_public: the archive holds only $n files; refusing to publish it." >&2
    exit 1
}

# --- tripwires, all against the staged copy ---------------------------------
fail=0

deny=$(cd "$STAGE" && find . \( -path './vendor/*' -o -path './firmware/vendor/*' \
                                -o -name '*.pdf' -o -name '*.uf2' \) -print)
if [ -n "$deny" ]; then
    echo "export_public: files that must not be published are in HEAD:" >&2
    echo "$deny" | sed 's/^/  /' >&2
    fail=1
fi

for f in weights.bin testvec.bin export.json; do
    [ -f "$STAGE/model/runs/so400m-full-a05/export/$f" ] || {
        echo "export_public: the shipped export is missing $f." >&2
        echo "  Check the negation chain in .gitignore -- losing it is silent." >&2
        fail=1
    }
done

hits=$(cd "$STAGE" && grep -rInE \
        'AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{36}|BEGIN [A-Z ]*PRIVATE KEY' \
        . 2>/dev/null || true)
if [ -n "$hits" ]; then
    echo "export_public: something that looks like a credential:" >&2
    echo "$hits" | sed 's/^/  /' >&2
    fail=1
fi

[ "$fail" -eq 0 ] || exit 1

# --- copy --------------------------------------------------------------------
# --delete so that deleting a file here deletes it there. Without it the public
# repository accumulates whatever this one has dropped, which is how the two
# drift apart while both looking healthy.
rsync -a --delete --exclude '.git/' "$STAGE"/ "$DEST"/

cd "$DEST"
git add -A
if git diff --cached --quiet; then
    echo "export_public: public repo already matches $SRC_COMMIT, nothing to do."
    exit 0
fi

git -c color.ui=always diff --cached --stat | tail -40
echo
if [ "$COMMIT" -eq 0 ]; then
    echo "export_public: staged in $DEST, not committed. Re-run with --commit."
    exit 0
fi

git commit -q -m "Sync from source tree at $SRC_COMMIT"
echo "export_public: committed $(git rev-parse --short HEAD) in $DEST"
[ "$PUSH" -eq 1 ] && git push -q && echo "export_public: pushed"
exit 0
