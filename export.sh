#!/bin/sh
# Export the diff and the old and new contents of every touched file.
# Usage: BASE=<sha> HEAD=<sha> ./export.sh <repo dir> <output dir>
set -eu
repo=$1; out=$2
mkdir -p "$out/old" "$out/new"
git -C "$repo" diff -M -U3 "$BASE" "$HEAD" > "$out/diff.patch"
git -C "$repo" diff -M --name-status "$BASE" "$HEAD" > "$out/names.txt"
while IFS="$(printf '\t')" read -r st a b; do
  case $st in R*) old=$a; new=$b;; D) old=$a; new=;; A) old=; new=$a;; *) old=$a; new=$a;; esac
  if [ -n "$old" ]; then mkdir -p "$out/old/$(dirname "$old")"; git -C "$repo" show "$BASE:$old" > "$out/old/$old"; fi
  if [ -n "$new" ]; then mkdir -p "$out/new/$(dirname "$new")"; git -C "$repo" show "$HEAD:$new" > "$out/new/$new"; fi
done < "$out/names.txt"
echo "exported $(wc -l < "$out/names.txt" | tr -d ' ') files to $out"
