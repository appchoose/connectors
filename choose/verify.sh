#!/usr/bin/env bash
# Vérifie que la pile Choose fonctionne sur l'arbre courant.
#
# Usage : choose/verify.sh          tests du fork + tests upstream qu'il touche
#         FULL=1 choose/verify.sh   toute la suite de tests de l'app
#
# VENV=<chemin> réutilise un virtualenv existant (défaut : le .venv du Makefile).
set -euo pipefail

app="$(git rev-parse --show-toplevel)/app/connectors_service"
cd "$app"

venv="${VENV:-.venv}"
if [ ! -x "$venv/bin/pytest" ]; then
  if [ -n "${VENV:-}" ]; then
    echo "VENV=$VENV ne contient pas pytest" >&2
    exit 1
  fi
  make .venv/bin/elastic-ingest .venv/bin/pytest
fi

if [ "${FULL:-0}" = "1" ]; then
  targets=(tests)
else
  # Tests du fork + tests upstream des fichiers que le fork modifie.
  targets=(
    tests/choose
    tests/sources/test_postgresql.py
    tests/test_sync_job_runner.py
    tests/services
  )
fi

"$venv/bin/python" -m pytest -q -p no:cacheprovider "${targets[@]}"
"$venv/bin/ruff" check tests/choose
"$venv/bin/ruff" format --check tests/choose
echo "OK : la pile Choose passe ses vérifications."
