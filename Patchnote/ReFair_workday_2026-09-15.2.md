# ReFair — Workday Patchnote .2 — 2026-09-15

## Addendum au patchnote principal du 15 septembre 2026

Ce document poursuit :

```text
ReFair_workday_2026-09-15.md
```

Le premier patchnote s’arrêtait avant le premier vrai smoke test Astra sur trafic PortSwigger réel.

Cette seconde partie documente donc exclusivement la phase de validation expérimentale réalisée en fin de journée :

```text
trafic réel autorisé
    ↓
Burp / ReFair passive capture
    ↓
normalization + structural extraction
    ↓
C3b OperationContextSnapshot
    ↓
C4b.1 OperationEvidenceBundle
    ↓
provider-only calibration sanitization
    ↓
GPT-6 Astra shadow analysis
    ↓
grounded PROPOSED hypothesis
```

Le résultat principal de la soirée est positif :

> ReFair a fourni à Astra suffisamment d’evidence réelle pour produire une hypothèse de contrôle d’accès précise, testable, référencée par des observation IDs, tout en s’arrêtant explicitement à la limite de ce qui avait effectivement été observé.

---

# 1. État repository en fin de journée

Branche :

```text
main
```

HEAD GitHub observé en fin de journée :

```text
5d75695ed6cb5540ce32a2d241b1162187756634
Patchnote 15-09
```

Dernier commit fonctionnel ReFair :

```text
de78d8d935ce04f254a6f9d561926afcb3af4e1b
Implemented ReFair V0.2-C4b.1
```

Aucun changement fonctionnel n’a été committé pendant les smoke tests de cette seconde partie.

Constantes inchangées :

```text
package version = 0.1.5
CURRENT_SCHEMA_VERSION = 6
NORMALIZER_VERSION = 2
STRUCTURAL_VERSION = 5
```

Suite de tests inchangée :

```text
369 passed
```

Le script local :

```text
_astra_smoke.py
```

a été utilisé uniquement comme harness de calibration/smoke et n’a pas été intégré au produit.

---

# 2. Premier smoke réel — IDOR simple

Un premier lab PortSwigger Access Control a été utilisé pour vérifier que la chaîne :

```text
passive evidence
    ↓
bounded evidence
    ↓
Astra
    ↓
security hypothesis
```

fonctionnait sur du trafic réel.

Cas observé :

```text
/my-account?id=<username>
```

Astra a correctement identifié une hypothèse d’autorisation horizontale autour de l’identifiant contrôlé par le client.

La validation humaine ultérieure a confirmé le mécanisme en accédant à :

```text
/my-account?id=carlos
```

depuis la session du compte légitime.

Le lab PortSwigger a alors été résolu.

Point important :

```text
Astra did not execute the request.
```

Le modèle a uniquement produit une hypothèse en shadow mode.

La tentative manuelle de confirmation n’a cependant pas été capturée par ReFair car le collector n’était plus actif à ce moment-là.

Conséquence :

```text
hypothesis generation smoke      ✅
post-experiment evaluator smoke  ❌ not captured
```

Décision : refaire un second test sur un lab plus complexe plutôt que reconstruire artificiellement la preuve du premier.

---

# 3. Deuxième smoke — workflow d’autorisation multi-step

Lab choisi :

```text
Multi-step process with no access control on one step
```

Objectif :

tester C4b.1 sur un scénario dans lequel la vulnérabilité ne dépend pas uniquement d’un paramètre isolé, mais d’un workflow et de plusieurs états/authentifications.

Avant le test, la base SQLite précédente a été archivée et une nouvelle base propre a été utilisée afin de limiter les contaminations inter-lab.

Principe expérimental :

```text
clean DB
    ↓
collector actif avant navigation
    ↓
navigation humaine naturelle
    ↓
admin session
    ↓
logout
    ↓
wiener session
    ↓
no deliberate exploit execution
    ↓
refair-process
    ↓
manual anchor selection
    ↓
C3b + C4b.1
    ↓
Astra
```

Le but n’était pas de donner à Astra la solution du lab.

Le but était de mesurer jusqu’où il pouvait raisonner à partir des seules preuves HTTP observées.

---

# 4. Capture réelle

Après navigation :

```text
Observations: 329
```

Répartition :

```text
actor_a: 329
BROWSER: 329
```

Traitement :

```text
normalized_processed: 323
normalization_pending: 0

structural_processed: 323
structural_pending: 0

warnings: 0
```

Conclusion :

```text
passive bridge        ✅
actor attribution     ✅
normalization         ✅
structural extraction ✅
pending               0
warnings              0
```

---

# 5. Bruit navigateur observé

La capture réelle a confirmé qu’un navigateur dédié génère énormément de trafic hors cible :

```text
Mozilla telemetry
Firefox remote settings
push.services.mozilla.com
firefox-portal-detection.com
Google suggestions
static assets
images
Academy lab WebSocket/header traffic
```

Ce bruit n’a pas empêché C4b.1 de fonctionner car le workflow evidence applique déjà :

```text
same project
same actor context
same authority
bounded time window
static filtering
exact dedupe
```

Décision :

> Le scope passif global reste un sujet de backlog, mais il n’est pas bloquant pour le pipeline actuel.

---

# 6. Scope — clarification architecturale

La discussion du smoke a renforcé une distinction importante :

```text
PASSIVE / RELEVANCE SCOPE
```

= ce que ReFair collecte ou considère pertinent pour l’analyse.

et :

```text
ACTIVE AUTHORIZATION SCOPE
```

= ce vers quoi ReFair a réellement le droit d’envoyer une requête active.

Le scope Burp pourra éventuellement devenir une source ou un signal fort pour le premier.

Il ne doit pas automatiquement devenir une autorisation d’exécution active.

Invariant proposé :

> Burp in-scope ne signifie pas automatiquement ReFair active-authorized.

Aucune modification n’a été implémentée aujourd’hui.

---

# 7. Triplets d’observations réels

Le trafic du lab a révélé un phénomène clair :

de nombreuses requêtes apparaissent exactement trois fois avec :

```text
same timestamp
same method
same URL
same response status
same sizes
```

Exemples :

```text
POST /login
GET /my-account?id=administrator
GET /admin
POST /my-account/change-email
GET /logout
GET /my-account?id=wiener
```

La cause exacte n’a pas été diagnostiquée.

Hypothèses possibles à investiguer plus tard :

```text
duplicate extension callback registration
multiple passive hooks
bridge-side duplication
Burp-side lifecycle behavior
other integration duplication
```

Décision :

> Ne pas patcher sans root cause.

C4b.1 exact-dedupe a néanmoins correctement collapsé ces répétitions tout en conservant les occurrence IDs/timestamps.

---

# 8. Confirmation d’une limite C4b.1 déjà connue

Le patchnote principal avait noté une limite théorique :

```text
max_workflow_exchanges
```

est appliqué avant le collapse exact final.

Le smoke réel a confirmé que cette limite est effectivement observable.

Avec les triplets, le cap par défaut :

```text
max_workflow_exchanges = 24
```

aurait pu être consommé avant que le contexte utile complet soit sélectionné.

Pour le smoke uniquement, le harness a utilisé :

```python
EvidenceLimits(
    max_exchanges=8,
    max_workflow_exchanges=128,
    workflow_before_seconds=300,
    workflow_after_seconds=300,
    max_request_bytes=32 * 1024,
    max_response_bytes=128 * 1024,
    max_total_bytes=512 * 1024,
)
```

Ce n’est pas une modification produit.

C’est un override de calibration.

Backlog :

```text
candidate selection
    ↓
exact dedupe
    ↓
workflow unique-exchange cap
```

ou autre stratégie équivalente à étudier.

---

# 9. Trafic métier observé

Le dump filtré a exposé deux contextes authentifiés successifs dans le même browser actor.

Séquence simplifiée :

```text
POST /login
GET /my-account?id=administrator
POST /my-account/change-email
GET /admin                    → 200
GET /logout

POST /login
GET /my-account?id=wiener
POST /my-account/change-email
GET /admin                    → 401
```

Observation importante :

```text
actor_a
```

représente bien un navigateur/profil/contexte d’exécution stable.

Il ne doit pas être réinterprété comme :

```text
actor_a == authenticated principal
```

Une future notion d’auth/session state pourra éventuellement être dérivée :

```text
actor_a
    ├── anonymous
    ├── authenticated context A
    ├── logout
    └── authenticated context B
```

Mais aucune nouvelle abstraction n’est nécessaire pour le jalon actuel.

---

# 10. Anchor retenue

Anchor humaine :

```text
GET /admin
```

Endpoint ID :

```text
0bebd3ad-0551-59c6-b905-5069d3052361
```

Operation ID :

```text
3c727587-26ce-5acb-a0b9-126a62685937
```

La même opération contenait des outcomes différents :

```text
GET /admin → 200
GET /admin → 401
```

selon le contexte authentifié observé.

C’était une bonne frontière pour tester un raisonnement d’autorisation.

---

# 11. Bundle C4b.1 réel

Résultat obtenu avec le cap workflow temporairement élevé :

```text
anchor observations     : 9
workflow candidates     : 323
available observations  : 117
unique exchanges        : 37
included exchanges      : 37
exact duplicates omitted: 80
workflow-limit omitted  : 0
static omitted          : 48
outside authority       : 167
total bytes             : 95503
```

Lecture :

```text
323 workflow candidates
    ↓
167 outside authority
48 static
80 exact duplicates
    ↓
37 unique included exchanges
```

Le smoke confirme donc que les mécanismes C4b.1 de :

```text
authority restriction
static filtering
exact dedupe
bounded workflow context
```

fonctionnent sur du trafic navigateur réel.

---

# 12. Exchanges envoyables significatifs

Inventaire simplifié :

```text
ANCHOR
GET /admin → 401
GET /admin → 200

WORKFLOW
GET /
GET /login
POST /login
GET /my-account?id=administrator
POST /my-account/change-email
GET /logout
GET /product?productId=4
GET /my-account
POST /login
GET /my-account?id=wiener
POST /my-account/change-email
```

Le bundle contenait aussi plusieurs :

```text
GET /academyLabHeader → 101
```

qui sont surtout du bruit de lab/WebSocket.

Ils ne sont pas bloquants aujourd’hui.

Backlog possible :

```text
generic relevance treatment for upgrade/101 traffic
```

mais aucun filtre spécifique PortSwigger ne doit entrer dans le produit.

---

# 13. Premier abort de calibration — lab title leakage

Avant tout appel Astra, un contrôle local a cherché si le titre explicite du lab était présent dans le provider evidence.

Résultat :

```text
ABORT: PortSwigger lab title leaked into model evidence.
No Astra request was sent.
```

L’investigation a montré que le titre :

```html
<title>Multi-step process with no access control on one step</title>
```

était présent dans presque toutes les réponses HTML du lab :

```text
/admin
/
/login
/my-account
/product
...
```

Conclusion :

> supprimer un échange contaminé aurait supprimé une grande partie du vrai workflow.

La bonne approche pour ce smoke n’était donc pas un filtering d’exchange.

---

# 14. Provider-only calibration sanitization

Une copie transitoire du bundle a été utilisée exclusivement pour l’appel provider.

Les objets suivants sont restés inchangés :

```text
SQLite RAW observations     unchanged
C4b.1 original bundle      unchanged
repository                 unchanged
```

Deux neutralisations locales ont été appliquées.

## 14.1 Sensitive form values

Les valeurs de champs form sensibles ont été remplacées localement :

```text
password
passwd
pass
csrf
csrf_token
token
access_token
refresh_token
api_key
apikey
secret
session
sessionid
...
```

La substitution conserve la longueur des bytes afin de respecter :

```text
HttpEvidenceMessage.byte_count
```

Important :

C4b.1 protège déjà :

```text
Authorization
Proxy-Authorization
Cookie
Set-Cookie
```

mais ce smoke confirme qu’une politique provider-safe sur les secrets contenus dans les bodies reste à formaliser.

## 14.2 Exact lab title redaction

Seul le titre exact du challenge PortSwigger a été neutralisé dans la copie provider.

Principe :

```text
exact known lab title
    ↓
same-byte-length replacement
```

Aucun mot générique tel que :

```text
admin
role
authorization
401
upgrade
username
```

n’a été retiré.

Le but était uniquement d’éviter de donner explicitement la réponse du challenge au modèle.

Après neutralisation :

```text
=== CONTAMINATION CHECK ===
PASS: explicit PortSwigger lab title removed.
```

---

# 15. Premier vrai Astra workflow smoke

Appel :

```text
GPT-6 Astra
shadow analysis
reasoning_effort = medium
max_output_tokens = 4096
```

Toujours :

```text
no tools
no execution
store = false
truncation = disabled
provider output locally validated
```

Résultat :

```text
[1] PROPOSED
```

Hypothèse Astra :

> POST /admin-roles may not independently enforce the administrator restriction applied to GET /admin, potentially allowing the NORMAL account wiener to upgrade itself using username=wiener and action=upgrade. The administrator-facing form establishes this endpoint and these parameters, while the wiener-context GET /admin returns 401. This is an unverified authorization-boundary hypothesis: the supplied traffic contains no /admin-roles exchange or resulting role change.

Supporting observation IDs :

```text
33fac62a-46c6-4e9d-a717-c5c577664d18
53793c13-71a5-49f1-b9ea-6ae7aadcd791
cccf7a70-30c5-403c-8058-b3a2e2d9f8ea
ee95c9fa-6866-430e-9465-8240f2b09bb3
```

Contradicting evidence :

```text
none
```

---

# 16. Évaluation du raisonnement Astra

Le résultat est important car le bundle ne contenait aucune requête :

```text
POST /admin-roles
```

Astra a néanmoins déduit depuis les HTML et le contraste d’autorisation :

```text
/admin-roles
POST
username
action=upgrade
wiener
privileged role transition
possible missing independent authorization check
```

Mais il n’a pas prétendu avoir confirmé la vulnérabilité.

Il a explicitement indiqué :

```text
the supplied traffic contains no /admin-roles exchange
or resulting role change
```

C’est exactement le comportement souhaité.

---

# 17. Ce qu’Astra ne pouvait pas savoir

Le mécanisme exact du lab contient une étape de confirmation supplémentaire.

Conceptuellement :

```text
POST /admin-roles
username=...
action=upgrade
    ↓
confirmation step
    ↓
POST /admin-roles
...
confirmed=true
```

Or cette séquence n’avait jamais été capturée.

Donc les éléments suivants ne doivent pas être considérés comme des erreurs du modèle :

```text
confirmed=true absent
exact vulnerable confirmation step absent
```

Ils étaient :

```text
NOT OBSERVABLE FROM PROVIDED EVIDENCE
```

Un modèle respectant ReFair ne devait pas inventer cette étape.

Le bon comportement était précisément :

```text
evidence stops here
    ↓
hypothesis stops here
```

---

# 18. Validation du principe "Evidence decides"

Ce smoke fournit une validation expérimentale importante du principe :

```text
LLM proposes.
Code constrains.
Evidence decides.
State remembers.
Human authorizes dangerous edge cases.
```

Astra a fait :

```text
observed admin boundary
    ↓
observed privileged form semantics
    ↓
inferred precise authorization hypothesis
    ↓
kept status PROPOSED
    ↓
explicitly stated missing evidence
```

Il n’a pas fait :

```text
guess hidden workflow state
declare vulnerability confirmed
invent observation IDs
execute a request
generate broad fuzzing advice
```

---

# 19. Première preuve réelle de la valeur du workflow context

Le scénario simple IDOR pouvait être compris depuis une opération presque isolée.

Le second smoke montre une propriété plus intéressante :

```text
same browser actor
multiple authentication states
/admin 200 vs 401
admin-facing HTML
account navigation
login/logout sequence
form semantics
```

Le modèle a utilisé cette histoire pour construire une hypothèse sur une opération qui n’avait elle-même pas encore été exécutée.

Cela donne une première validation pratique de :

```text
WORKFLOW_CONTEXT
```

comme mécanisme de réduction de l’incertitude structurelle.

---

# 20. Leçons techniques du smoke

## Confirmé utile

```text
same-authority workflow neighborhood
exact dedupe
static filtering
bounded RAW evidence
application values preserved
observation IDs retained
provider strict structured output
local evidence-reference validation
shadow-only model invocation
```

## Confirmé à améliorer

```text
workflow cap before exact dedupe
body credential/secret handling
triplicate observation root cause
101 /academyLabHeader noise
calibration contamination from lab metadata
```

## Non bloquant / backlog

```text
Burp passive scope integration
auth/session-state inference
WebSocket upgrade relevance
automatic anchor selection
evaluator
policy engine
executor
```

---

# 21. Secret handling — correction de formulation

Le smoke rappelle qu’il serait faux de dire :

```text
all credentials stay local
```

avec l’implémentation C4b.1 actuelle.

Ce qui est vrai aujourd’hui :

```text
Authorization headers   redacted
Proxy-Authorization     redacted
Cookie                  redacted
Set-Cookie              redacted
```

Ce qui demande encore une politique explicite :

```text
password in form body
CSRF/token-like values in form body
application-specific reusable secrets
credentials embedded in JSON/body
```

Le harness local les a neutralisés pour ce smoke.

Ce comportement n’est pas encore une feature produit.

---

# 22. Calibration metadata vs production evidence

Le titre PortSwigger est un cas particulier de calibration :

```text
challenge metadata
```

qui révèle directement la classe de vulnérabilité.

Ce n’est pas une raison pour créer un filtre produit du type :

```text
remove security words
remove access-control strings
remove <title>
```

Une vraie application peut contenir des titres ou textes métier utiles.

Décision :

> Le nettoyage du lab title reste un mécanisme de harness/calibration, pas une règle générique ReFair.

---

# 23. Pas de nouveau scope creep

Les résultats positifs du smoke ne justifient pas encore :

```text
automatic browser control
automatic endpoint mutation
crawler
active executor
multi-agent
vector DB
workflow graph
semantic search infrastructure
auth principal model
full session reconstruction
```

Le système actuel vient précisément de produire un résultat intéressant sans ces couches.

Décision :

> Consolider les preuves observées avant d’élargir l’architecture.

---

# 24. Prochaines expériences possibles

## Option A — workflow complet

Refaire le lab en capturant aussi :

```text
POST /admin-roles
confirmation response
POST /admin-roles ... confirmed=true
```

Puis observer si Astra affine naturellement :

```text
generic endpoint authorization hypothesis
    ↓
multi-step authorization inconsistency hypothesis
```

## Option B — evaluator

Après une expérience humaine contrôlée :

```text
baseline observation
experiment observation
    ↓
comparison
    ↓
SUPPORTED / REFUTED / INCONCLUSIVE
```

Ce serait la suite logique pour tester :

```text
Evidence decides
```

après la phase :

```text
LLM proposes
```

## Option C — fix only evidenced friction

Étudier séparément :

```text
triplicate capture root cause
workflow pre-dedupe cap
provider-safe secret policy
```

sans ajouter d’autres abstractions.

---

# 25. Milestone status après live smoke

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

Live passive capture                             ✅
Live C3b assembly                                ✅
Live C4b.1 evidence assembly                     ✅
Live Astra hypothesis generation                 ✅
Grounded evidence IDs                            ✅
Explicit uncertainty boundary                    ✅

Automatic active execution                       ⏸
Evaluator                                        ⏸
Policy engine                                    ⏸
```

---

# 26. Résultat de la journée

Le premier patchnote du jour terminait sur la question :

> ReFair donne-t-il à Astra suffisamment d’evidence structurée et réelle pour raisonner comme un pentester utile ?

Après les live smokes, la réponse est désormais :

```text
YES — at least for the tested authorization / workflow cases.
```

Avec une nuance essentielle :

```text
Astra is only as good as the evidence boundary supplied to it.
```

Et le second smoke a montré un comportement particulièrement sain :

> Astra a produit une hypothèse précise sur `/admin-roles`, mais a refusé implicitement de prétendre connaître une étape qu’il n’avait jamais observée.

C’est exactement la direction recherchée pour ReFair :

```text
not autonomous guessing

but

bounded evidence
    ↓
security reasoning
    ↓
explicit uncertainty
    ↓
minimal next experiment
```

---

## Architecture à retenir après le premier vrai smoke

```text
Structure tells Astra what ReFair knows.
Evidence lets Astra understand what actually happened.
Missing evidence limits what Astra is allowed to conclude.
```

Et toujours :

```text
LLM proposes.
Code constrains.
Evidence decides.
State remembers.
Human authorizes dangerous edge cases.
```
