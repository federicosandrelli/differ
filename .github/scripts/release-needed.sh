#!/usr/bin/env bash
# Writes release=true to $GITHUB_OUTPUT when $SHA is the tip of main and no
# tag points at it yet, and release=false otherwise.
set -euo pipefail

tip=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)
if [ "$tip" != "$SHA" ]; then
  echo "::notice::Skipping the release: $SHA is no longer the tip of main ($tip)."
  echo "release=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

tags=$(gh api --paginate "repos/$GITHUB_REPOSITORY/git/matching-refs/tags/" \
  --jq ".[] | select(.object.sha == \"$SHA\") | .ref")
if [ -n "$tags" ]; then
  echo "::notice::Skipping the release: $SHA already has a tag ($tags)."
  echo "release=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

echo "release=true" >> "$GITHUB_OUTPUT"
