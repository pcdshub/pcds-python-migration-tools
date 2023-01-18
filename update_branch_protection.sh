#!/bin/bash

fail() { echo "ERROR: $*" >&2; exit 1; }

set -e

# gh api  -H "Accept: application/vnd.github+json" /rate_limit

# Show branch protection settings:
# gh api graphql \
#     -f query="" \
#     -f operationName=showBranchProtection \
#     -F owner=:owner -F repo=:repo

org="pcdshub"
repo="pcds-ci-test-repo-python"
graphql="query=$(cat branch_protection.graphql)"

# Get the repository node ID
repositoryId="$(gh api --hostname "github.com" "repos/$org/$repo" --jq .node_id)"
[[ -n "$repositoryId" ]] || fail "could not determine repo nodeId"

set -x
gh api graphql --hostname "github.com" \
  -F owner="$org" \
  -F repo="$repo" \
  -F repositoryId="$repositoryId" \
  -F branchPattern="master" \
  -f requiredStatusChecks[]="standard / Conda (3.10) / Python 3.10: conda" \
  -f requiredStatusChecks[]="standard / Conda (3.9, true) / Python 3.9: conda" \
  -f requiredStatusChecks[]="standard / Documentation / Python 3.9: documentation building" \
  -f requiredStatusChecks[]="standard / Pip (3.10) / Python 3.10: pip" \
  -f requiredStatusChecks[]="standard / Pip (3.9, true) / Python 3.9: pip" \
  -f requiredStatusChecks[]="standard / pre-commit checks / pre-commit" \
  -f operationName=addBranchProtection \
  -f "$graphql" ||
  || fail "Failed to add branch protection"

echo "Done"
