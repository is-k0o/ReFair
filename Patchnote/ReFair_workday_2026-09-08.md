# ReFair — Patchnote du 8 septembre 2026

## État général

La journée a été consacrée à deux choses :

1. **figer l’architecture conceptuelle avant V0.2** autour du problème de contexte LLM ;
2. implémenter puis stabiliser **V0.2-A — RAW → NormalizedExchange**.

À la fin de la session, ReFair possède désormais une première couche de transformation déterministe hors LLM :

```text
Burp / Firefox
    ↓
Observation RAW immutable
    ↓
refair-process
    ↓
NormalizedExchange
```

La preuve brute reste la source de vérité.

---

## 1. Problème du contexte LLM identifié avant V0.2

Un risque architectural important a été discuté :

```text
1 requête HTTP
→ 1 appel LLM
→ relire tout l'historique
```

Cette approche a été explicitement rejetée.

Elle serait mauvaise pour :

- le coût API ;
- la latence ;
- la qualité du raisonnement ;
- la taille du contexte ;
- la capacité à retrouver les informations réellement pertinentes.

### Principe retenu

```text
RAW preservation ≠ RAW context
```

Toutes les observations RAW doivent pouvoir être conservées sans pour autant être renvoyées au modèle.

La majorité des requêtes HTTP devront produire :

```text
Observation persisted
State updated
LLM call: no
```

Le futur contexte du modèle devra être une **projection bornée de l’état**, pas l’historique HTTP complet.

---

## 2. Corrélation inter-endpoints : piège évité

Une simple stratégie :

```text
endpoint courant
+ quelques endpoints proches
```

a été jugée insuffisante.

En web security, des relations importantes peuvent être éloignées lexicalement mais reliées par le flux de données :

```text
/api/profile
    ↓ tenant_id
/api/search
    ↓ resource_id
/api/export
```

Pris séparément, ces endpoints peuvent paraître banals.

Pris ensemble, ils peuvent révéler :

- une frontière d’autorisation ;
- un problème de tenant isolation ;
- un workflow métier exploitable ;
- une chaîne IDOR/BOLA ;
- une anomalie inter-acteurs.

### Direction architecturale retenue

ReFair devra progressivement reconstruire un **modèle structurel de l’application**, puis construire le contexte LLM depuis ce modèle.

Schéma cible :

```text
RAW EVIDENCE
    ↓
NORMALIZATION
    ↓
STRUCTURAL RECON
    ↓
APPLICATION MODEL
    ↓
CONTEXT COMPILER
    ↓
ASTRA
    ↓
DYNAMIC TESTS
    ↓
RAW EVIDENCE
```

---

## 3. Recon structurel vs analyse dynamique

La distinction suivante a été retenue :

### Structural / Passive Recon

Objectif :

- reconstruire progressivement le fonctionnement du site ;
- identifier endpoints, paramètres, champs, actors, relations et variantes ;
- conserver les liens vers la preuve ;
- ne pas faire de conclusion pentest à chaque requête.

### Dynamic Analysis

Objectif :

- raisonner sur les vulnérabilités ;
- croiser les actors ;
- générer des hypothèses ;
- proposer des expériences minimales ;
- exploiter le modèle structurel existant.

Cette séparation doit limiter les appels coûteux au modèle sans perdre les relations inter-endpoints.

---

## 4. Idée multi-agent abandonnée

Une idée envisagée était d’utiliser :

- un modèle moins cher pour la reconstruction structurelle ;
- Astra pour le raisonnement pentest.

L’idée a finalement été abandonnée pour l’instant.

Raisons :

- coordination supplémentaire ;
- stratégies de contexte multiples ;
- divergence potentielle d’état ;
- résumés croisés ;
- plus de contrats de sortie ;
- plus de tests ;
- gain de coût non encore mesuré.

### Règle retenue

ReFair n’a pas vocation à devenir un système multi-agent généraliste.

But du projet :

> Donner à Astra un environnement de pentest contrôlé, une mémoire structurée utile et des garde-fous déterministes.

Principe de conception ajouté :

> Chaque ajout architectural doit justifier son coût.

Classification à garder pour les prochaines décisions :

- nécessaire maintenant ;
- utile bientôt ;
- optimisation prématurée ;
- scope creep.

L’objectif est explicitement d’éviter un **Swarmblight v2**.

---

# V0.2-A — RAW → NormalizedExchange

## 5. Première implémentation V0.2-A

Commit :

```text
79341130b310c7fa193a9d9331d7a012cffda957
Implemented the bounded V0.2-A
```

V0.2-A ajoute :

- migration SQLite minimale ;
- `NormalizedExchange` frozen ;
- normalisation déterministe ;
- stockage du derived state ;
- processor CLI séparé ;
- tests dédiés.

Public package version conservée :

```text
0.1.5
```

SQLite schema version :

```text
1
```

---

## 6. Migration SQLite

Ajout d’un système minimal basé sur :

```text
PRAGMA user_version
```

Pas d’Alembic.
Pas de SQLAlchemy.
Pas de dépendance supplémentaire.

### Comportement

```text
legacy schema = user_version 0
        ↓
migration
        ↓
schema version 1
```

La migration :

- ajoute `normalized_exchanges` ;
- ne recrée pas les Observations ;
- ne réécrit pas `raw_request` ;
- ne réécrit pas `raw_response` ;
- conserve les triggers d’immutabilité ;
- refuse une DB créée par une version de schéma plus récente.

Test ajouté pour vérifier que la migration conserve les Observations **byte-for-byte**.

---

## 7. `NormalizedExchange`

Nouveau modèle frozen contenant uniquement une projection déterministe.

Champs :

```text
observation_id
normalizer_version

scheme
host
port
path

query_parameter_names
raw_query_sha256

request_content_type
request_body_kind
request_body_size
request_body_sha256

response_content_type
response_body_kind
response_body_size
response_body_sha256

warnings
```

### Non stocké dans la couche normalisée

- raw HTTP ;
- cookies ;
- Authorization ;
- valeurs de query ;
- valeurs JSON ;
- valeurs de forms ;
- contenu des réponses.

Le RAW reste disponible uniquement via l’Observation source.

---

## 8. Body classification minimale

`BodyKind` reste volontairement petit :

```text
EMPTY
JSON
FORM
MULTIPART
TEXT
OTHER
```

Aucune extraction de schéma JSON n’a été ajoutée.

Aucune inférence de paramètres ou d’entités.

---

## 9. Processor hors collector

Ajout :

```text
refair-process
```

ou :

```powershell
python -m refair.process --config config.example.yaml
```

Le processor utilise la même DB configurée mais reste complètement séparé du collector.

### Invariant

```text
collector:
receive
validate
attribute
persist RAW

processor:
read RAW
normalize
persist derived state
```

Le trafic navigateur/Burp n’attend jamais la normalisation.

---

## 10. Validation initiale sur la DB réelle

Première passe V0.2-A :

```text
processed: 159
pending: 0
warnings: 0
```

Deuxième passe immédiate :

```text
processed: 0
pending: 0
warnings: 0
```

État :

```text
159 observations RAW
159 normalized rows
schema version 1
```

Tests :

```text
Baseline: 49 passed
Final:    67 passed
```

---

# Stabilisation V0.2-A

## 11. Trois problèmes corrigés avant V0.2-B

Avant de considérer V0.2-A comme gelée, trois points ont été corrigés.

### A. Query parameter names trop normalisés

L’implémentation initiale utilisait :

```python
unquote_plus(...)
```

sur les noms des paramètres.

Problème :

```text
?id=1
?%69d=1
```

pouvaient être fusionnés dans la même représentation structurelle.

Même problème potentiel pour :

```text
?a+b=1
?a%20b=1
```

Pour un outil websec, cette fusion est trop agressive.

### Correction

Les noms sont désormais conservés tels qu’ils apparaissent dans `Observation.url`.

Exemple :

```text
?%69d=1&a+b=2&a%20b=3
```

devient :

```text
("%69d", "a+b", "a%20b")
```

Aucun :

- percent-decoding ;
- plus-decoding ;
- lowercase ;
- reordering ;
- stockage de valeurs.

---

## 12. Normalizer versioning

Comme la sémantique de normalisation a changé :

```text
NORMALIZER_VERSION
1 → 2
```

Important :

```text
public package version = 0.1.5
SQLite schema version  = 1
normalizer version      = 2
```

Ces trois notions restent séparées.

---

## 13. Reprocessing version-aware

Avant stabilisation, une observation était considérée traitée dès qu’une ligne normalisée existait.

Après correction :

```text
missing normalized row
→ pending

normalized version < target version
→ stale / pending

normalized version == target version
→ done

normalized version > target version
→ do not downgrade
```

Le derived state peut être remplacé par une version plus récente.

Le RAW reste immutable.

---

## 14. UPSERT sécurisé

Sémantique :

```text
no row
→ insert

existing older version
→ update derived state

same version
→ idempotent

existing newer version
→ preserve newer row
```

Aucune modification de la table `observations`.

---

## 15. Processor borné par batches

Le processor ne matérialise plus tout le corpus RAW en mémoire.

Batch size :

```text
PROCESS_BATCH_SIZE = 250
```

Boucle :

```text
fetch <= 250 pending/stale
→ normalize
→ persist
→ next batch
```

Pas de :

- async ;
- queue ;
- worker pool ;
- concurrency.

Le niveau de complexité reste volontairement minimal.

---

## 16. Validation réelle après stabilisation

DB avant :

```text
normalizer v1: 159 rows
```

Premier run :

```text
processed: 159
pending: 0
warnings: 0
```

Deuxième run :

```text
processed: 0
pending: 0
warnings: 0
```

Vérifications :

```text
raw_observations_unchanged: true
schema_version: 1
normalizer v2: 159 rows
```

Tests :

```text
Baseline: 67 passed
Final:    68 passed
```

Commit :

```text
21ef16fccb82aa29739256312b940b580cbed0c0
V0.2-A stabilization patch
```

---

## 17. Nuance importante à conserver pour la suite

La notion de :

```text
exact query representation
```

dans `NormalizedExchange` signifie :

> exacte par rapport à `Observation.url`.

Elle ne signifie pas nécessairement :

> octets littéraux du request-target tels qu’ils étaient sur le wire.

La source finale de vérité reste :

```text
Observation.raw_request
```

Donc si une future hypothèse websec dépend d’une ambiguïté de parsing, d’encodage ou de request-target :

```text
NormalizedExchange
→ aide à orienter l’analyse

RAW request
→ décide
```

### Décision

Ne pas complexifier V0.2-A pour résoudre cela maintenant.

Cette nuance devra simplement être conservée dans la documentation/architecture et exploitée quand un cas réellement pertinent apparaîtra.

---

# État de fin de journée

## V0.2-A considérée gelée

```text
RAW Observation
    ↓
deterministic normalization
    ↓
NormalizedExchange
```

Validé :

- migration legacy → schema 1 ;
- RAW immutable ;
- projection sans secrets ;
- query names conservés de manière conservative ;
- normalizer versionné ;
- stale reprocessing ;
- no downgrade ;
- processor borné ;
- real DB validation ;
- 159 RAW conservées ;
- 159 normalized V2 ;
- 68 tests Python.

### Non implémenté

Toujours volontairement différé :

- endpoint aggregation ;
- route templates ;
- route inference ;
- entity extraction ;
- JSON/form schema extraction ;
- application graph ;
- data-flow reconstruction ;
- semantic events ;
- novelty ranking ;
- context compiler ;
- Astra / LLM ;
- active traffic ;
- replay ;
- crawler ;
- browser automation.

---

# Prochaine étape

## V0.2-B — Structural Application Model

Objectif de la prochaine phase :

```text
NormalizedExchange
    ↓
exact endpoints
parameters
response fields
actors
evidence-backed relations
    ↓
Structural Application Model
```

Point de vigilance principal :

> Ne pas réduire le modèle à des relations locales ou lexicales entre endpoints.

Le futur modèle devra pouvoir représenter des relations longues comme :

```text
/api/profile
    ↓ tenant_id
/api/search
    ↓ resource_id
/api/export
```

sans charger tout l’historique RAW dans le contexte du modèle.

La conception de V0.2-B devra rester minimale et éviter toute dérive vers :

- multi-agent ;
- moteur de connaissance généraliste ;
- graph database prématurée ;
- route inference trop agressive ;
- architecture type Swarmblight.
