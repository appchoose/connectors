#!/usr/bin/env bash
# Rejoue la pile Choose sur une nouvelle version upstream, puis la vérifie.
#
# Usage : choose/port.sh <tag-de-base> <branche-choose> <nouvelle-ref-upstream>
# Ex.   : choose/port.sh v9.5.6 origin/v9.5.6-choose v9.5.7
#
# La pile Choose = les commits <tag-de-base>..<branche-choose>.
# Résultat : une branche locale port/<nouvelle-ref>-choose, rien n'est poussé.
set -euo pipefail

# Ce script change de branche : il s'exécute depuis une copie pour ne pas
# être modifié pendant sa lecture par bash.
if [ -z "${CHOOSE_PORT_COPY:-}" ]; then
  copy=$(mktemp)
  cp "$0" "$copy"
  CHOOSE_PORT_COPY=1 exec bash "$copy" "$@"
fi

if [ $# -ne 3 ]; then
  sed -n '4,5p' "$0" >&2
  exit 2
fi
base=$1
stack=$2
target=$3
branch="port/${target//\//-}-choose"

cd "$(git rev-parse --show-toplevel)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "L'arbre de travail n'est pas propre : commitez ou remisez d'abord." >&2
  exit 1
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/elastic/connectors.git
fi
git fetch --quiet --tags upstream

if git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
  echo "La branche $branch existe déjà." >&2
  echo "Reprendre : git switch $branch && git cherry-pick --continue && choose/verify.sh" >&2
  echo "Recommencer : git branch -D $branch && $0 $*" >&2
  exit 1
fi

# rerere : une résolution de conflit faite une fois est rejouée automatiquement.
git config rerere.enabled true

echo "Pile Choose ($(git rev-list --count "$base..$stack") commits) -> $target"
git switch --quiet -c "$branch" "$target"

if ! git cherry-pick "$base..$stack"; then
  echo >&2
  echo "Conflit. Fichiers concernés :" >&2
  git diff --name-only --diff-filter=U >&2
  echo >&2
  echo "1. Résolvez en gardant l'intention du commit Choose." >&2
  echo "2. git add <fichiers> && git cherry-pick --continue" >&2
  echo "3. choose/verify.sh" >&2
  exit 1
fi

echo
echo "Différences entre l'ancienne et la nouvelle pile ('=' : identique, '!' : adapté) :"
git --no-pager range-diff --no-color "$base..$stack" "$target..HEAD" | grep -E '^[0-9-]+: '

choose/verify.sh
echo "Branche prête : $branch (non poussée)."
