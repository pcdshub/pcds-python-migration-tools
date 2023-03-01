#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

PROJECT_NAME="$1"
REPO="pcdshub/$PROJECT_NAME"
LOCAL_DIR="$SCRIPT_DIR/wip/pcdshub/$PROJECT_NAME"
PROJECT_TYPE="$2"
BRANCH_NAME=ci_migrate_gha

if [ -z "$PROJECT_NAME" ]; then
  echo "No project name?"
  exit 1
fi

if [ -z "$PROJECT_TYPE" ]; then
  echo "No project type?"
  exit 1
fi

echo "Repo: $REPO"
echo "Project type: $PROJECT_TYPE"
echo "Local directory: $LOCAL_DIR"

# mkdir -p "${LOCAL_DIR}"
git clone "https://github.com/$REPO" "${LOCAL_DIR}"

cd "${LOCAL_DIR}" || exit 1

if [ ! -d .git ]; then
  echo "Not a repo? $LOCAL_DIR" 1>&2
  exit 1
fi

git checkout master
git branch -D "${BRANCH_NAME}" || echo "No existing migration branch"
git checkout -b "${BRANCH_NAME}" origin/master
echo y | gh repo fork --remote-name "$USER"
git remote add "$USER" "git@github.com:$USER/$PROJECT_NAME"
git fetch -a

python "$SCRIPT_DIR/update_twincat_repository.py" --project-type="$PROJECT_TYPE" "${LOCAL_DIR}" --write

while true; do
  git diff origin/master
  read -p "Ready to push and open PR? [y]/n) " yn

  if [[ -z "$yn" || "$yn" == "y" ]]; then
    break
  fi
  if [[ "$yn" == "n" ]]; then
    exit 1
  fi

done

git commit -am "MNT: finish up migration" || echo "No changes"

git remote -v
git push -u -v --force-with-lease "$USER" "${BRANCH_NAME}"
gh pr create \
  --title "CI: Migrate to GitHub Actions" \
  --body-file "$SCRIPT_DIR/templates/twincat/migration_pr.md" \
  --base master \
  --repo "$REPO"
