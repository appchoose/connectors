# Fork Choose de `elastic/connectors`

Cette branche = un tag officiel `elastic/connectors` + une petite pile de
commits Choose. Monter de version consiste à rejouer cette pile sur un tag
plus récent : `choose/port.sh` le fait et refuse de conclure sans les tests.

## La pile Choose

| # | Commit | Effet | Fichiers upstream touchés |
|---|--------|-------|---------------------------|
| 1 | Allowing concurrent sync for connector | Un connecteur déjà en cours de sync de contenu peut en lancer un autre ; seule `service.max_concurrent_content_syncs` limite. | `services/content_sync_job_execution.py` |
| 2 | Allow all syncs to run as long as capacity permits | Idem au moment du claim : plus de `SyncJobStartError`, le conflit ES est réessayé 3 fois puis relancé. | `sync_job_runner.py` |
| 3 | apply workaround for incremental sync with PostgreSQL | Sync incrémental PG : ne relit que les lignes dont `updated_at` est dans la fenêtre `service.incremental_sync_interval_seconds` (120 s par défaut). Supprime `COUNT(*)` et la date de dernier commit en full sync (`_timestamp` = heure du sync). Hook `set_service_config`. | `sources/postgresql/datasource.py`, `sync_job_runner.py`, `config.yml.example` |
| 4 | add where condition on correct table | La condition `updated_at` vise la table du `FROM`. | `sources/postgresql/datasource.py` |
| 5 | add async `close` methods | Libère le moteur SQLAlchemy en fin de sync. | `sources/postgresql/datasource.py`, `client.py` |
| 6 | test(choose): pin fork behavior… | Aligne 7 tests upstream sur le comportement du fork. | `tests/…` |
| 7 | test(choose): cover the PostgreSQL incremental sync… | Tests du fork, dans `tests/choose/`. | — (nouveaux fichiers) |
| 8 | chore(choose): add port.sh and verify.sh | Outillage. | — (nouveaux fichiers) |
| 9 | fix(choose): restore table-mode incremental sync… | Réimporte `FETCH_LIMIT`, perdu au portage 9.4. | `sources/postgresql/datasource.py` |
| 10 | fix(choose): map incremental id_columns… | Même calcul des `_id` en full et en incrémental. | `sources/postgresql/datasource.py` |

Les tests de `tests/choose/` passent sur ce portage et sur `v9.0.2-choose-2`
(imports adaptés à l'ancienne arborescence).

## Bugs et limites connus

Figés par un test, pour qu'une correction se voie :

- **Full sync PG en mode table** (sans règles avancées) : `KeyError: 'columns'`.
  Le fork ne passe plus `row_count`. Déjà le cas en `v9.0.2-choose-2`.
  Test : `test_get_docs` (`xfail` strict).
- **Incrémental PG en mode table** : une seule page `LIMIT 1000`, pas de
  pagination. Test : `test_known_limitation_table_mode_reads_at_most_1000_rows`.
- **Règles avancées en incrémental** : la condition est collée en fin de
  requête. Avec `ORDER BY` ou `LIMIT`, le SQL produit est invalide. Avec
  `FROM schema.table`, la condition vise `"schema".updated_at`. Tests :
  `test_known_limitation_*`.
- Avec un `JOIN`, seule la table du `FROM` est regardée : un changement dans
  une table jointe n'est repris qu'au prochain full sync.
- L'incrémental n'émet que des opérations `index` : les suppressions ne
  passent pas par lui.

## Monter de version upstream

```bash
choose/port.sh v9.5.6 origin/<branche-choose-actuelle> v9.5.7
```

Le script :

1. récupère les tags de `elastic/connectors` (remote `upstream`) ;
2. crée la branche locale `port/v9.5.7-choose` depuis `v9.5.7` ;
3. cherry-pick la pile (`v9.5.6..<branche-choose-actuelle>`) ;
4. affiche le `range-diff` : `=` commit identique, `!` commit adapté ;
5. lance `choose/verify.sh`. Rien n'est poussé.

En cas de conflit, il s'arrête et liste les fichiers. Résolvez en gardant
l'intention du commit Choose, puis :

```bash
git add <fichiers> && git cherry-pick --continue && choose/verify.sh
```

`rerere` est activé : une résolution faite une fois est rejouée
automatiquement si vous recommencez.

Vérifier sans porter : `choose/verify.sh`, ou `FULL=1 choose/verify.sh` pour
toute la suite. La CI (`.github/workflows/choose-verify.yml`) lance
`FULL=1 choose/verify.sh` à chaque push d'une branche qui contient la pile.

Ce repo est public : la procédure de déploiement et tout détail
d'infrastructure restent dans les repos internes.

## Points de vigilance 9.0.2 → 9.5.6

Différences de configuration effective entre les deux versions, obtenues en
chargeant notre config de prod avec le `load_config` de chaque version. Toutes
nos clés existent encore.

- `elasticsearch.bulk.max_text_document_size` (nouveau, **3 MiB, actif**) :
  un document sans pièce jointe plus gros est ignoré et compté dans
  `docs_dropped_too_large`. Mettre `0` pour garder le comportement 9.0.
- `elasticsearch.bulk.error_monitor.*` (nouveau, **actif**) : un sync échoue
  au-delà de 15 % d'erreurs bulk, 10 erreurs consécutives ou 1000 au total.
- `elasticsearch.bulk.queue_refresh_timeout` : 600 → 1300 s.
- `elasticsearch.max_wait_duration` : 120 → 240 s.
- `service.worker_mode`, `service.service_types`, `service.fips_mode` :
  nouveaux, inactifs par défaut.
- `JOB_CHECK_INTERVAL` (code, non configurable) : 1 → 5 s. Une annulation de
  sync est vue jusqu'à 5 s plus tard.
