# ReFair — Workday Note — 2026-09-06

## Résumé

Première vraie journée de bootstrap de **ReFair**.

Le projet est désormais initialisé, versionné sur GitHub et dispose de ses deux premiers jalons :

- **V0** — fondations déterministes ;
- **V0.1** — ingestion passive Burp/Montoya vers le cœur Python.

Repository source de vérité :

`is-k0o/ReFair`

---

## 1. Initialisation du repository

Le répertoire local `D:/ReFair` a été initialisé comme dépôt Git puis relié au repository GitHub :

```text
origin → https://github.com/is-k0o/ReFair.git
branch → main
```

Premier commit :

```text
6a619770b811e53da16c5bf709ba5a2ebb1c8e8d
Implemented ReFair V0
```

---

## 2. ReFair V0 — fondations déterministes

Le premier bootstrap a posé les briques de base du système.

### Modèles et état

Création des modèles principaux autour de :

- `Project`
- `Actor`
- `Observation`
- `Endpoint`
- `Entity`
- `Hypothesis`
- `Experiment`
- `ExperimentResult`
- `PolicyDecision`
- `RunBudget`
- `RunState`

### Evidence

Les `Observation` sont conçues comme des preuves brutes immutables.

SQLite impose aussi cette immutabilité via des triggers empêchant :

```text
UPDATE observation
DELETE observation
```

Le principe reste :

```text
Evidence
    ↓
Interpretation
```

et non l’inverse.

### Provenance

Trois provenances sont modélisées :

```text
BROWSER
HUMAN_REPEATER
AGENT_REPLAY
```

La concurrence active est séparée du trafic naturel du navigateur.

Invariant conservé :

```text
MAX_AGENT_INFLIGHT_REQUESTS = 1
```

### Budgets

Séparation entre :

#### API / modèle

```text
max_cost_usd
max_model_calls
max_input_tokens
max_output_tokens
warning_thresholds
```

#### HTTP

```text
max_requests
max_mutations
max_per_endpoint
max_identical_replays
```

### Assets

L’identité des assets statiques repose sur :

```text
décompression éventuelle
    ↓
SHA-256(content)
    ↓
Asset identity
```

L’URL reste une provenance / alias et non l’identité principale.

---

## 3. Correction importante — port 8081

Une erreur de modélisation a été identifiée après V0.

Le port `8081` n’appartient pas à ReFair.

Modèle correct :

```text
Burp / navigateur humain
└── 8081
    └── hors ReFair

ReFair
├── 8082 → Actor A
└── 8083 → Actor B
```

Conséquence :

- suppression de l’idée `listeners.human`;
- `8081` n’est plus représenté comme un listener ReFair ;
- seuls les listeners configurés sur les `Actor` appartiennent à ReFair.

Aucune abstraction artificielle du type :

```text
reserved_external_ports: [8081]
```

n’a été introduite.

---

## 4. ReFair V0.1 — passive Burp ingestion

Deuxième commit :

```text
05331795bf25a3211efd789ef6d696474b3ee822
Implement ReFair V0.1 passive Burp ingestion
```

Architecture implémentée :

```text
Firefox
    ↓
Burp actor listener
    ↓
passive Montoya callback
    ↓
bounded non-blocking queue
    ↓
localhost HTTP/base64
    ↓
Python collector
    ↓
deterministic actor mapping
    ↓
immutable Observation
    ↓
SQLite
```

---

## 5. Extension Montoya

Ajout d’une extension Java 21 sous :

```text
burp-extension/
```

Responsabilités volontairement limitées à :

```text
observe
serialize
transport
```

Elle ne fait pas :

- de raisonnement de vulnérabilité ;
- de Burp AI / Montoya AI ;
- de génération de trafic actif ;
- de crawling ;
- de mutation ;
- de normalisation ;
- d’écriture directe dans SQLite.

Le Java reste un adapter mince.

L’état et l’autorité restent côté Python.

### Fail-open

Le callback Burp ne dépend pas de la disponibilité du collector Python.

Le transport utilise :

- une queue bornée ;
- un worker asynchrone ;
- pas de retry infini ;
- pas de blocage du navigateur ;
- logs explicites en cas de drop ou erreur.

Donc :

```text
collector down
    ↓
event éventuellement perdu + log
    ↓
Burp / navigateur continue normalement
```

---

## 6. Bridge Java → Python

Transport local :

```text
HTTP + JSON
127.0.0.1:8765
```

Les requêtes/réponses brutes sont transportées en base64 afin de préserver les octets fournis par Montoya.

Le Java transmet notamment :

```text
listener_port
observed_at
method
url
response_status
raw_request_base64
raw_response_base64
```

Le Java ne décide pas de :

```text
actor_id
provenance
```

Ces champs restent sous contrôle Python.

---

## 7. Attribution déterministe des Actors

Le collector Python mappe :

```text
8082 → actor_a
8083 → actor_b
```

Tout listener non configuré est ignoré :

```text
8081 → ignored
unknown listener → ignored
```

Ces événements ne créent aucune `Observation`.

La provenance créée par ce bridge est imposée côté Python :

```text
BROWSER
```

Le bridge ne fait donc pas confiance à une attribution fournie par Java.

---

## 8. Collector Python

Ajout de :

```text
refair/bridge/
```

Le collector :

1. charge la configuration ;
2. ouvre / initialise SQLite ;
3. reçoit un échange passif ;
4. valide le payload ;
5. résout `listener_port → Actor` ;
6. rejette les listeners non ReFair ;
7. décode les bytes ;
8. construit une `Observation` immutable ;
9. attribue le `project_id` stable ;
10. persiste dans SQLite.

La configuration dispose désormais d’un `project.id` stable afin de ne pas recréer une identité de projet à chaque démarrage.

Le collector est loopback-only par défaut.

---

## 9. Limites et validation d’entrée

Le bridge traite son entrée locale comme non fiable.

Contrôles ajoutés notamment sur :

- schéma Pydantic strict ;
- base64 invalide ;
- status HTTP invalide ;
- taille maximale d’échange ;
- listener inconnu ;
- bind non-loopback.

Limite actuelle :

```text
16 MiB par échange
```

---

## 10. Requêtes sans réponse

V0.1 ne persiste que les échanges HTTP complétés.

Une requête sans réponse n’est pas encore enregistrée.

Motif :

```text
request arrives
    ↓
ne pas créer une Observation incomplète
    ↓
ne pas la modifier plus tard avec la réponse
```

Cela violerait l’invariant d’immutabilité.

Une future version pourra introduire un modèle séparé si les request-only evidence deviennent nécessaires.

---

## 11. Tests

État rapporté après implémentation V0.1 :

```text
Python baseline : 26 tests passed
Python final    : 39 tests passed
Java            : 5 tests passed
Gradle build    : successful
Python compile  : successful
```

JAR généré localement :

```text
burp-extension/build/libs/refair-burp-extension-0.1.0.jar
```

SHA-256 rapporté :

```text
4F4E1866412152EFD0A9523D010DA596EFDE0EFBDE6EE144284D50F9ADC810A2
```

Le JAR compilé n’est pas versionné.

---

## 12. `.gitignore`

Le `.gitignore` a été vérifié avant le push.

Il exclut notamment :

```text
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.pyright/

.venv/
venv/
.testdeps/

.gradle/
build/
dist/
*.class
out/

*.sqlite
*.sqlite3
*.db

.coverage
.coverage.*
coverage.xml
htmlcov/

.env
.env.*
config.local.yaml

.idea/
.vscode/
.DS_Store
Thumbs.db
```

Le fichier suivant reste volontairement versionné :

```text
burp-extension/gradle/wrapper/gradle-wrapper.jar
```

car il fait partie du Gradle Wrapper.

---

## 13. État du projet en fin de journée

### Implémenté

```text
deterministic domain models
immutable raw evidence
SQLite persistence
asset SHA-256 identity
RunBudget / RunState
active concurrency guard
actor-specific listeners
stable project UUID
passive Montoya bridge
bounded async transport
Python collector
listener → actor attribution
passive BROWSER observations
bridge validation
Python + Java tests
```

### Explicitement non implémenté

```text
active HTTP execution
ExperimentProposal execution
full Policy Engine
scope enforcement for active traffic
rate limiting runtime
normalization
semantic events
endpoint extraction
JS analysis
anomaly detection
LLM / Astra
Burp AI
crawler
browser automation
replay
multi-agent
RAG / vector DB
UI
```

---

## 14. Prochaine étape

Avant de concevoir V0.2 :

### Smoke test réel

Faire traverser de vrais échanges :

```text
Firefox Actor A
    → Burp 8082
    → Montoya
    → Python collector
    → SQLite

Firefox Actor B
    → Burp 8083
    → Montoya
    → Python collector
    → SQLite
```

À vérifier :

- attribution correcte de `actor_a` / `actor_b` ;
- `provenance = BROWSER` ;
- conservation des raw bytes ;
- aucune observation créée depuis `8081` ;
- comportement correct si le collector est arrêté ;
- logs en cas de queue pleine / transport failure.

Une fois ce chemin validé en conditions réelles, le prochain jalon logique sera probablement :

```text
raw passive evidence
    ↓
normalization
    ↓
deduplication
    ↓
semantic events
```

sans encore brancher de LLM.

---

## Commits du jour

```text
6a619770b811e53da16c5bf709ba5a2ebb1c8e8d
Implemented ReFair V0

05331795bf25a3211efd789ef6d696474b3ee822
Implement ReFair V0.1 passive Burp ingestion
```
