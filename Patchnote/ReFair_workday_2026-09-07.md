# ReFair — Patchnote du 7 septembre 2026

## État général

La journée a surtout servi à **valider ReFair V0.1 en conditions réelles**, puis à livrer une **V0.1.5 de stabilisation** et un micro-correctif final d’ergonomie.

À la fin de la session, la chaîne passive ReFair est fonctionnelle de bout en bout :

```text
Firefox Actor A/B
    ↓
Burp Proxy — listeners 8082 / 8083
    ↓
Extension Montoya Java
    ↓
Transport asynchrone borné
    ↓
Collector Python localhost
    ↓
Attribution déterministe actor_a / actor_b
    ↓
Observation immutable
    ↓
SQLite
```

Aucune fonctionnalité V0.2 n’a été commencée.

---

## 1. Environnement Python remis au propre

Le projet nécessite **Python >= 3.12**.

État initial constaté :

- Python 3.10 installé ;
- Python 3.11 via Miniconda ;
- aucun Python 3.12 disponible.

Python 3.12 a été installé puis un environnement virtuel dédié a été créé :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Version validée :

```text
Python 3.12.10
```

Installation du projet réussie :

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Le collector a ensuite démarré correctement sur :

```text
http://127.0.0.1:8765
```

---

## 2. Extension Montoya chargée et bridge validé

L’extension Java ReFair a été chargée dans Burp.

Collector configuré :

```text
http://127.0.0.1:8765/v1/observations/passive
```

Listeners Burp utilisés pour ReFair :

```text
8082 → Actor A
8083 → Actor B
```

Le listener humain 8081 reste hors du modèle ReFair.

Les profils Firefox dédiés ont été validés :

```text
Firefox A → Burp 8082
Firefox B → Burp 8083
```

---

## 3. Premier smoke test end-to-end réel de ReFair

Le collector a reçu de vrais événements Montoya avec :

```text
POST /v1/observations/passive
201 Created
```

La base SQLite a ensuite été inspectée directement.

Répartition observée pendant le test :

```text
Counter({'actor_a': 77, 'actor_b': 57})
```

Des preuves HTTP brutes request/response ont été confirmées comme persistées.

### Marqueurs explicites

Deux requêtes dédiées ont ensuite été utilisées pour vérifier sans ambiguïté l’attribution des listeners :

```text
https://example.com/refair-smoke-actor-a
https://example.com/refair-smoke-actor-b
```

Résultat :

```text
actor_a BROWSER GET 404 https://example.com/refair-smoke-actor-a
actor_b BROWSER GET 404 https://example.com/refair-smoke-actor-b
```

Les deux observations contenaient bien :

- la requête brute ;
- la réponse brute ;
- l’actor correct ;
- `provenance = BROWSER`.

### Conclusion

**V0.1 PASS — passive ingestion validée en conditions réelles.**

---

## 4. Warning Uvicorn / WebSocket identifié

Pendant le smoke test, Uvicorn affichait :

```text
Unsupported upgrade request.
No supported WebSocket library detected.
```

Le bridge continuait cependant à fonctionner normalement avec des réponses `201 Created`.

Cause retenue : le `HttpClient` Java ne forçait pas explicitement HTTP/1.1 vers le collector localhost.

Correction intégrée ensuite en V0.1.5 :

```java
.version(HttpClient.Version.HTTP_1_1)
```

Important :

- cela concerne uniquement le transport interne Java → Python ;
- les messages HTTP observés côté cible restent conservés selon la représentation fournie par Montoya ;
- aucune librairie WebSocket n’a été ajoutée pour masquer le symptôme.

---

## 5. ReFair V0.1.5 — stabilisation

Codex a implémenté la V0.1.5 comme patch de stabilisation, sans lancer V0.2.

### Changements principaux

- version Python : `0.1.0 → 0.1.5` ;
- version FastAPI : `0.1.0 → 0.1.5` ;
- version Gradle/JAR : `0.1.0 → 0.1.5` ;
- README mis à jour ;
- transport Java → collector forcé en HTTP/1.1 ;
- test Java local anti-régression pour vérifier l’absence de `Upgrade` et `HTTP2-Settings` ;
- couleurs Uvicorn désactivées pour éviter les séquences ANSI illisibles dans PowerShell ;
- résumé de démarrage du collector ajouté ;
- inspector SQLite read-only ajouté.

Tests rapportés par Codex :

```text
Python : 48/48
Java   : 6/6
```

---

## 6. Nouvel inspector read-only

Ajout d’un outil opérateur :

```text
refair-inspect
```

Commandes principales :

```powershell
refair-inspect --config config.example.yaml summary
refair-inspect --config config.example.yaml list --limit 20
refair-inspect --config config.example.yaml show <uuid>
refair-inspect --config config.example.yaml show <uuid> --raw-preview 500
```

### Comportement

`summary` affiche notamment :

- nombre total d’observations ;
- répartition par actor ;
- répartition par provenance.

`list` affiche une vue compacte :

- UUID ;
- timestamp ;
- actor ;
- provenance ;
- méthode ;
- status ;
- host/path ;
- tailles request/response.

Les query strings longues sont masquées par défaut.

`show` affiche les métadonnées d’une observation.

Les raw bytes ne sont jamais affichés par défaut.

Une preview explicite est disponible et bornée à :

```text
4096 bytes maximum
```

Le repository SQLite peut maintenant être ouvert en mode `read_only=True`.

---

## 7. Micro-patch final V0.1.5

Un manque d’ergonomie a été identifié :

```text
refair-inspect list
```

n’affichait pas l’UUID de l’observation, alors que :

```text
refair-inspect show <uuid>
```

en a besoin.

Correction appliquée :

- chaque ligne de `list` commence désormais par l’UUID complet ;
- l’UUID peut être copié directement dans `show`.

Test ajouté :

```text
list --limit 1
→ récupère UUID
→ show <UUID>
→ observation retrouvée
```

Tests finaux :

```text
49 passed
```

Aucune modification Java, bridge, schema, version ou V0.2 dans ce micro-patch.

---

## 8. Git / commits du jour

Commit V0.1.5 :

```text
2db249fb2b2269e6f4ed27545864b30822264dff
Implemented the ReFair V0.1.5 stabilization patch
```

Nettoyage du fichier Burp local accidentellement commité :

```text
1afe84d42cd23c2c9f6b793114d0882fce81a982
Keep local Burp project settings out of Git
```

Correction de la règle `.gitignore` :

```text
d0555cf
Fix Burp settings ignore rules
```

Micro-patch inspector :

```text
228447daa5985c5fb85ccf5e8525e31d4a27c72a
V0.1.5 micro-patch
```

État final de `main` :

```text
228447daa5985c5fb85ccf5e8525e31d4a27c72a
```

---

## 9. Configuration Burp locale

Les project settings Burp ont été sauvegardés localement afin de retrouver facilement les listeners ReFair.

Le fichier local Burp est désormais explicitement ignoré par Git.

Règles finales :

```gitignore
# Local Burp configuration
refair-burp-project-settings.json
refair/refair-project-settings.json
```

Le fichier de settings Burp ne fait plus partie du repository.

---

## 10. État de fin de journée

### Validé

- Python 3.12 + `.venv` dédié ;
- installation editable ReFair ;
- collector localhost opérationnel ;
- extension Montoya chargée ;
- Firefox A → 8082 ;
- Firefox B → 8083 ;
- attribution `actor_a` / `actor_b` validée ;
- `provenance = BROWSER` validée ;
- raw request/response persistées ;
- ingestion end-to-end validée ;
- HTTP/1.1 interne stabilisé ;
- inspector read-only livré ;
- raw preview bornée ;
- query strings masquées par défaut ;
- UUID utilisable directement `list → show` ;
- settings Burp sauvegardés localement ;
- fichier Burp exclu proprement de Git.

### Délibérément non commencé

V0.2 reste entièrement à faire :

- normalisation RAW ;
- déduplication ;
- réduction du bruit ;
- événements sémantiques ;
- extraction d’endpoints ;
- analyse JS ;
- anomaly detection ;
- LLM / OpenAI API ;
- planner / evaluator ;
- trafic actif ;
- replay ;
- crawler ;
- automatisation navigateur.

---

## Prochaine étape

Reprendre à partir de :

```text
ReFair V0.1.5
passive ingestion stable + validated
```

Puis concevoir **V0.2 — RAW → NORMALIZED → SEMANTIC EVENTS**, avec priorité à :

1. réduction du bruit réseau ;
2. normalisation déterministe ;
3. déduplication sans perte de preuve ;
4. maintien d’un lien explicite vers les observations RAW immutables ;
5. aucun LLM tant que cette couche n’est pas propre.
