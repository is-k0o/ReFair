# ReFair — Workday Patchnote — 2026-09-15

## Résumé

La journée du 15 septembre 2026 a fait évoluer ReFair du stade « contexte structurel + planner LLM » vers un premier pipeline réellement exploitable pour un smoke test sur trafic web autorisé réel.

Le point architectural principal a été une correction de trajectoire : C3 reste l’index structuré et déterministe de l’application, mais Astra ne doit pas être limité à ce résumé structurel. Pour raisonner correctement sur l’authorization, l’IDOR/BOLA, le multi-tenant, les workflows et la business logic, il doit aussi recevoir un sous-ensemble borné et redigé des vraies requêtes/réponses HTTP observées.

Architecture obtenue en fin de journée :

```text
Burp / Firefox
    ↓
RAW immutable observations
    ↓
normalization + structural extraction
    ↓
human-selected anchor operation
    ↓
C3b OperationContextSnapshot
    +
C4b/C4b.1 bounded HTTP evidence
    ↓
Astra shadow analysis
    ↓
transient grounded Hypothesis[]
    ↓
C4a planner
    ↓
PlannerDecision
```

Aucune exécution active de pentest n’a encore été ajoutée.

---

## État final du repository

Branche : `main`

HEAD final :

```text
de78d8d935ce04f254a6f9d561926afcb3af4e1b
Implemented ReFair V0.2-C4b.1
```

Constantes conservées :

```text
package version = 0.1.5
CURRENT_SCHEMA_VERSION = 6
NORMALIZER_VERSION = 2
STRUCTURAL_VERSION = 5
```

Suite finale :

```text
369 passed
```

Live OpenAI/Astra smoke :

```text
NOT RUN
```

---

# 1. V0.2-C4a — GPT-6 Astra shadow planner adapter

Commit :

```text
bfa83e6b0f0ef34e4baf791bfd5a9642c9345e8c
Implemented V0.2-C4a
```

## Objectif

Connecter le contexte structuré C3 à GPT-6 Astra sans donner au modèle le moindre pouvoir d’exécution.

```text
OperationContextSnapshot
    ↓
canonical JSON input
    ↓
OpenAI Responses API
    ↓
strict Structured Output
    ↓
deterministic local validation
    ↓
existing PlannerDecision
```

Ajouts principaux :

```text
refair/planner/shadow.py
refair/planner/__init__.py
tests/test_shadow_planner.py
```

Dépendance ajoutée :

```toml
openai>=3.13,<4
```

API publique :

```python
plan_shadow(
    snapshot: OperationContextSnapshot,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-6-astra",
    reasoning_effort: ReasoningEffort = "medium",
    max_output_tokens: int = 8192,
) -> PlannerDecision
```

Paramètres provider :

```text
Responses API
model = gpt-6-astra
reasoning.effort = medium
store = False
truncation = disabled
max_output_tokens = 8192
max_retries = 0
```

Aucun tool, conversation state, previous_response_id, streaming, temperature/top_p ou retry automatique.

Astra ne génère pas `decision id` ni `project_id`; ceux-ci sont assignés localement.

Sorties provider :

```text
EXPERIMENT
EXPLORATION
WAIT
```

puis conversion vers les contrats C1 existants :

```text
ExperimentProposal
ExplorationProposal
WaitDecision
```

Validation locale stricte : IDs hypothèse/lead/observation, actors, targets, grounding et relation lead→target. Déduplication exacte seulement, sans fuzzy/semantic dedupe.

---

# 2. Correctif Structured Outputs C4a

Commit :

```text
d2521bb4a6839728dc0ade4369a42ce0795e6fd8
Implemented the C4a Structured
```

Les unions Pydantic discriminées généraient `oneOf` + `discriminator`. Elles ont été remplacées par des unions ordinaires afin d’obtenir un schéma strict compatible utilisant `anyOf`, tout en conservant les `Literal[...]` de branche.

Vérifications locales :

```text
root object: PASS
anyOf present: PASS
oneOf absent: PASS
discriminator absent: PASS
additionalProperties=false: PASS
required fields complete: PASS
```

Aucun contrat C1/C2/C3 n’a été modifié.

---

# 3. Correction de trajectoire architecturale

Pendant la préparation du premier smoke, une limite importante a été identifiée : un contexte purement structurel retirait parfois précisément les relations métier dont Astra a besoin.

Exemple :

```http
GET /api/invoices/48291
```

```json
{
  "id": 48291,
  "ownerId": 153,
  "tenantId": 7,
  "status": "paid"
}
```

Réduit structurellement, cela pouvait devenir :

```text
id: NUMBER
ownerId: NUMBER
tenantId: NUMBER
status: STRING
```

Ce qui retire les relations utiles à l’IDOR/BOLA et à la business logic.

Nouvel invariant :

> Ne pas envoyer tout le RAW. Envoyer le bon RAW, au bon moment, relié à l’état structuré.

C3 reste donc l’index/squelette déterministe. C4 ajoute une vue HTTP bornée et redigée.

---

# 4. V0.2-C4b — bounded HTTP evidence + Astra hypothesis generation

Commit :

```text
2e6758bff716f309c315a1162112d83ca1ba9e37
Implemented V0.2-C4b
```

Nouveau pipeline :

```text
OperationContextSnapshot
    +
OperationEvidenceBundle
    ↓
analyze_shadow()
    ↓
Hypothesis[]
```

Nouveaux fichiers :

```text
refair/context/evidence.py
refair/planner/analysis.py
tests/test_operation_evidence.py
tests/test_shadow_analysis.py
```

### Evidence assembly

API :

```python
assemble_operation_evidence(
    repository: SQLiteRepository,
    snapshot: OperationContextSnapshot,
    *,
    limits: EvidenceLimits = DEFAULT_EVIDENCE_LIMITS,
) -> OperationEvidenceBundle
```

Propriétés :

- repository read-only obligatoire ;
- exact duplicate collapse seulement ;
- acteur inclus dans l’identité de déduplication ;
- IDs des observations dupliquées conservés ;
- UTF-8 strict puis Latin-1 lossless ;
- pas de truncate silencieux ;
- valeurs applicatives préservées.

### Redaction credentials

Headers redigés case-insensitivement :

```text
Authorization
Proxy-Authorization
Cookie
Set-Cookie
```

Exemple :

```http
Authorization: Bearer <REDACTED>
Cookie: session=<REDACTED>; csrf=<REDACTED>
```

Les IDs, tenant IDs, owner IDs, usernames, rôles, états et autres valeurs métier des bodies restent visibles.

### Shadow analysis

API :

```python
analyze_shadow(
    snapshot: OperationContextSnapshot,
    evidence: OperationEvidenceBundle,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-6-astra",
    reasoning_effort: ReasoningEffort = "medium",
    max_output_tokens: int = 8192,
) -> tuple[Hypothesis, ...]
```

Provider output minimal :

```json
{
  "hypotheses": [
    {
      "statement": "...",
      "supporting_evidence_ids": [],
      "contradicting_evidence_ids": []
    }
  ]
}
```

Astra ne fournit ni `Hypothesis.id`, ni `project_id`, ni `status`.

Localement :

```text
id = local
project_id = snapshot.project_id
status = PROPOSED
```

Références inconnues, overlap support/contradiction, hypothèse sans evidence et plus de 8 hypothèses sont rejetés. Zéro hypothèse est valide.

---

# 5. V0.2-C4b.1 — bounded workflow neighborhood

Commit :

```text
de78d8d935ce04f254a6f9d561926afcb3af4e1b
Implemented ReFair V0.2-C4b.1
```

## Objectif

C4b restait trop `operation-centric`. C4b.1 ajoute un voisinage temporel borné pour qu’Astra puisse voir une navigation du type :

```text
POST /login
GET /my-account
GET /api/users/123
POST /account/change-email
GET /admin
```

Ce voisinage est du contexte, pas une preuve de causalité.

### Scopes d’evidence

Chaque exchange porte maintenant :

```text
ANCHOR_OPERATION
WORKFLOW_CONTEXT
```

L’anchor reste choisi explicitement par l’humain.

### Occurrences et timestamps

Ajout de :

```text
HttpEvidenceOccurrence
```

avec :

```text
observation_id
observed_at
```

Les doublons exacts conservent tous les UUIDs et timestamps chronologiquement.

### EvidenceLimits finaux

```text
max_exchanges = 8
max_workflow_exchanges = 24
workflow_before_seconds = 300
workflow_after_seconds = 300
max_request_bytes = 32 KiB
max_response_bytes = 128 KiB
max_total_bytes = 512 KiB
```

### Fenêtre workflow

```text
anchor_start - 300 s
    →
anchor_end + 300 s
```

### Actor boundary

Le workflow context est limité aux actors déjà visibles dans l’anchor. Si l’anchor n’a que `actor_id = None`, seules les observations `None` sont admises.

### Same-authority boundary

Même :

```text
scheme
lowercase host
effective port
```

Pas de comparaison par simple préfixe URL.

### Metadata-before-RAW

Ajout de :

```python
observation_metadata_window(...)
```

Cette méthode ne lit que les métadonnées. Le RAW n’est chargé qu’après :

```text
project filter
actor filter
time filter
authority filter
static asset filter
workflow cap selection
```

### Static asset filtering

Filtre uniquement appliqué au `WORKFLOW_CONTEXT`.

Types filtrés :

```text
image/*
audio/*
video/*
font/*
text/css
text/javascript
application/javascript
application/x-javascript
application/wasm
```

Fallback extension :

```text
.css .js .png .jpg .jpeg .gif .svg .ico .webp .avif
.woff .woff2 .ttf .eot
```

Restent autorisés :

```text
.json
.xml
.map
```

### Sélection temporelle

Si le nombre de candidates dépasse le cap :

```text
temporal distance to anchor interval
then observed_at
then UUID
```

Les exchanges sélectionnés sont ensuite sérialisés chronologiquement.

Pas de scoring sémantique, embedding ou vector DB.

### Déduplication

Identité exacte :

```text
scope
actor
provenance
method
URL
response status
redacted request
redacted response
```

Les scopes et actors différents ne sont jamais fusionnés.

---

# 6. Nettoyage du modèle de coverage

L’ancien champ :

```text
available_exchange_count
```

était ambigu car il comptait les observations avant collapse exact.

Nouveaux champs :

```text
anchor_observation_count
workflow_candidate_observation_count
available_observation_count
unique_exchange_count
included_exchange_count
omitted_duplicate_count
omitted_by_exchange_limit_count
omitted_too_large_count
omitted_static_asset_count
omitted_outside_authority_count
omitted_by_workflow_limit_count
total_included_bytes
```

Invariants :

```text
available_observation_count
    = unique_exchange_count
    + omitted_duplicate_count
```

```text
unique_exchange_count
    = included_exchange_count
    + omitted_by_exchange_limit_count
    + omitted_too_large_count
```

Le workflow coverage distingue aussi les omissions static/authority/workflow-cap.

---

# 7. Astra prompt semantics après C4b.1

Le prompt d’analyse explique maintenant :

```text
ANCHOR_OPERATION
```

= evidence de l’opération choisie.

```text
WORKFLOW_CONTEXT
```

= navigation temporellement adjacente du même actor/context et de la même authority.

Invariant explicite :

> Temporal adjacency does not prove a causal workflow relationship.

Astra peut utiliser ce voisinage pour former des hypothèses sur l’authorization, l’IDOR/BOLA, l’isolation multi-tenant, l’object ownership, les rôles/comptes, la business logic et les state transitions, tout en restant groundé sur de vrais observation IDs.

---

# 8. Tests de la journée

Progression :

```text
Avant C4a          : 258 tests
Après C4a          : 308 tests
Après schema patch : 310 tests
Après C4b          : 355 tests
Après C4b.1        : 369 tests
```

Derniers tests ciblés :

```text
Evidence         : 22 passed
Shadow analysis  : 36 passed
Storage          : 6 passed
Full suite       : 369 passed
```

Couverture notable : schema strict, IDs invalides, actors invalides, target/grounding invalides, exact dedup, provider failures, credential redaction, value preservation, Latin-1 fallback, binary-body omission, workflow timeline, actor isolation, authority isolation, static asset filtering, temporal window, proximity cap, RAW retrieval boundary, duplicate occurrences et hypothesis grounding anchor + workflow.

---

# 9. Toujours hors scope

```text
active HTTP execution
policy engine
automatic operation selection
crawler
scanner
workflow graph
deterministic causal inference
semantic relevance ranking
embeddings
vector database
fuzzy dedupe
project-wide RAW scanning
hypothesis persistence
shadow-run persistence
automatic retry
multi-agent architecture
```

C4a planner n’a pas été modifié par C4b/C4b.1.

---

# 10. Limite connue non bloquante

`max_workflow_exchanges` est actuellement appliqué avant le collapse exact final des candidates workflow.

Une répétition importante du même trafic pourrait donc consommer une partie du cap avant déduplication.

Décision : ne pas patcher sans evidence réelle montrant un impact sur le contexte utile.

---

# 11. Prochaine étape

Le prochain jalon est un test réel contrôlé, pas une nouvelle couche d’architecture.

```text
1. démarrer un lab PortSwigger autorisé
2. utiliser Firefox/Burp/ReFair normalement
3. capturer la navigation dans SQLite
4. exécuter refair-process
5. choisir manuellement une opération intéressante
6. construire C3b OperationContextSnapshot
7. construire C4b.1 OperationEvidenceBundle
8. inspecter localement structure + redaction + workflow context
9. si le bundle est satisfaisant : premier appel réel gpt-6-astra
10. examiner les Hypothesis produites
```

Critères du premier smoke :

```text
- Astra comprend-il les transactions ?
- Les valeurs métier utiles sont-elles présentes ?
- Les credentials sont-ils correctement redacted ?
- Le workflow context est-il utile sans être surinterprété ?
- Astra produit-il des hypothèses précises et groundées ?
- Retourne-t-il zéro hypothèse quand l’evidence est insuffisante ?
- Invente-t-il des références ?
```

Le but n’est pas encore de résoudre automatiquement le lab.

Le but est de valider expérimentalement :

> ReFair donne-t-il à Astra suffisamment d’evidence structurée et réelle pour raisonner comme un pentester utile ?

---

# 12. Milestone status

```text
V0.1    passive Burp bridge                     ✅
V0.2-A  normalization                           ✅
V0.2-B1 exact operations                        ✅
V0.2-B2 structural body extraction              ✅
V0.2-C1 analytic/planner contracts              ✅
V0.2-C2 deterministic exploration leads         ✅
V0.2-C3a bounded structural context              ✅
V0.2-C3b SQLite context assembly                 ✅
V0.2-C4a Astra shadow planner                    ✅
C4a     Structured Outputs compatibility patch  ✅
V0.2-C4b bounded RAW HTTP evidence               ✅
V0.2-C4b.1 bounded workflow neighborhood         ✅
Live Astra smoke                                ⏭ NEXT
```

---

## Architecture à retenir

```text
LLM proposes.
Code constrains.
Evidence decides.
State remembers.
Human authorizes dangerous edge cases.
```

Et après la correction de trajectoire du jour :

```text
Structure tells Astra what ReFair knows.
Evidence lets Astra understand what actually happened.
```
