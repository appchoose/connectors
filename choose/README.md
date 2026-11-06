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

## Bugs connus

Tous sont déjà présents en production (`v9.4.4-choose`) et figés par un test,
pour qu'une correction se voie :

- **Full sync PG en mode table** (sans règles avancées) : `KeyError: 'columns'`.
  Le fork ne passe plus `row_count`, donc la requête de pagination est mal
  construite. Test : `test_get_docs` (`xfail` strict).
- **Sync incrémental PG en mode table** : `NameError: FETCH_LIMIT`, jamais
  importé dans `datasource.py`. Test : `test_known_bug_table_mode_incremental_sync_crashes`.
- **Règles avancées en incrémental** : la condition est collée en fin de
  requête. Avec `ORDER BY` ou `LIMIT`, le SQL produit est invalide. Avec
  `FROM schema.table`, la condition vise `"schema".updated_at`. Tests :
  `test_known_limitation_*`.
- L'incrémental n'émet que des opérations `index` : les suppressions ne
  passent pas par lui.

[Inference] Les deux premiers bugs rendent le mode table inutilisable. Il est
donc probable que seuls des connecteurs avec règles avancées tournent en prod.

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
toute la suite.

## Points de vigilance 9.4 → 9.5

Liste non exhaustive. Elle couvre `config.yml.example` et les fichiers touchés
par le fork :

- `elasticsearch.bulk.max_text_document_size` (nouveau, **3 MiB par défaut,
  actif**) : un document sans pièce jointe plus gros est ignoré et compté dans
  `docs_dropped_too_large`. Mettre `0` pour garder le comportement 9.4.
- `JOB_CHECK_INTERVAL` passe de 1 s à 5 s : l'annulation d'un sync est vue
  jusqu'à 5 s plus tard.
- `service.worker_mode` (nouveau, `false` par défaut) : aucun effet si non activé.
