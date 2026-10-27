# a2a-seller-agent

Une bibliothèque Python et un skill pour permettre à un agent personnel de gérer un catalogue d’occasion et de négocier via A2A 1.0. Le serveur et son stockage tournent dans l’environnement de l’utilisateur ; son agent expose le serveur avec le tunnel disponible sur cet environnement.

Exemple de demande au bot :

> Installe a2a-seller-agent et ajoute ma table de nuit à 300 EUR, négociable jusqu’à 230 EUR minimum.

Le bot traduit cette demande en commandes structurées. La bibliothèque valide les données, conserve le plancher privé et traite les échanges de façon déterministe. Elle n’appelle aucun modèle et n’interprète pas les montants par expressions régulières.

## Installation

Python 3.11 ou supérieur (requis par les versions de Starlette utilisées). Le paquet n’est pas encore publié sur PyPI. Pour cette version de développement, installer la branche dédiée :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install "git+https://github.com/Math1987/a2a-seller-agent.git@codex/seller-library"
.venv/bin/a2a-seller skill show
```

Depuis un checkout de cette branche, remplacer l’installation distante par `.venv/bin/python -m pip install .`. Pour reproduire un déploiement, utiliser le commit testé plutôt qu’une branche mobile.

Le [skill](skills/a2a-seller/SKILL.md) est aussi embarqué dans la distribution Python. L’installation du paquet ne l’active pas automatiquement dans le bot : lui faire lire le fichier ou la sortie de `a2a-seller skill show`, puis utiliser le mécanisme de skills de l’hôte.

Les exemples suivants utilisent l’exécutable de cet environnement. Par défaut, les données restent dans `~/.local/share/a2a-seller`. `--data-dir CHEMIN` sélectionne un autre stockage et doit précéder la sous-commande.

## Gérer les articles

```bash
.venv/bin/a2a-seller item add \
  --title "Table de nuit" \
  --description "Table de nuit en bois, un tiroir" \
  --condition "Rayure sur le plateau" \
  --price 300 --minimum-price 230 --currency EUR \
  --area "Lyon" --handover "Remise en main propre, créneau à convenir" \
  --request-id table-de-nuit-creation-1

.venv/bin/a2a-seller item list
.venv/bin/a2a-seller item show --id IDENTIFIANT_RETOURNE
.venv/bin/a2a-seller item update --id IDENTIFIANT_RETOURNE --price 280
.venv/bin/a2a-seller item withdraw --id IDENTIFIANT_RETOURNE
```

Les commandes renvoient du JSON ; une erreur produit du JSON sur stderr et un code de sortie non nul. Les sorties d’administration sont privées et peuvent contenir les prix minimaux. Ne pas les transférer à un acheteur.

Une création est **en simulation par défaut**. Ajouter `--real` seulement pour une annonce réelle demandée par le propriétaire. Même dans ce mode, la bibliothèque ne débite aucun paiement, ne réserve pas physiquement l’objet et n’organise pas de livraison.

Chaque article reçoit un identifiant stable et une révision. Une modification invalide les propositions encore en attente ; un retrait retire l’article du catalogue disponible et invalide ses propositions en attente. L’historique reste conservé. Réutiliser le même `--request-id` avec les mêmes paramètres après une coupure évite de créer deux fois l’article ; un nouvel acte utilise un nouvel identifiant de requête.

Les montants CLI sont décimaux exacts, sans arrondi implicite. Les devises prises en charge sont EUR, USD, GBP, CHF, CAD, AUD, NZD (deux décimales) et JPY (aucune décimale). Utiliser un code explicite : `$` n’identifie pas une devise. Dans les données et messages A2A, les montants sont des entiers en unités mineures : `25000` représente 250,00 EUR. Le prix minimal doit être positif et inférieur ou égal au prix affiché. Modifier la devise exige de fournir à nouveau les deux prix.

## Démarrer et exposer le vendeur

Pour un test local :

```bash
.venv/bin/a2a-seller serve --public-url http://127.0.0.1:9999 --port 9999
```

Le serveur écoute sur loopback. Pour une URL publique, le bot lance un tunnel dans son propre environnement, récupère son URL HTTPS, puis démarre ou redémarre le serveur avec cette URL :

```bash
ngrok http 9999
# Dans un autre processus, avec l’URL réellement renvoyée par ngrok :
.venv/bin/a2a-seller serve --public-url https://VOTRE-ENDPOINT.ngrok-free.app --port 9999
```

Le compte et l’authtoken ngrok appartiennent au vendeur. Les saisir par le mécanisme sécurisé de l’hôte ; ne pas les publier dans la conversation, le dépôt ou l’Agent Card. Le skill réutilise un tunnel fonctionnel, mais ne crée pas de compte ni d’abonnement. Une nouvelle URL nécessite de redémarrer le serveur avec la nouvelle valeur. Le programme ne fournit pas de service de tunnel ou de surveillance automatique des processus.

Pour un POC privé, `serve --token-file /chemin/prive/a2a-token` protège les requêtes A2A et le catalogue d’articles avec `Authorization: Bearer …`. Le fichier contient seulement le jeton ; il reste hors du dépôt et accessible au propriétaire uniquement (mode 0600 sous POSIX). La carte et le contrôle de santé restent découvrables. Sans ce paramètre, le service est accessible à tout client pouvant joindre le tunnel. Un jeton partagé limite l’accès au groupe de test ; ce n’est pas une identité distincte par acheteur.

Routes :

| Route | Contenu |
| --- | --- |
| `/.well-known/agent-card.json` | Une seule carte pour le vendeur, avec `list-items`, `get-item` et `negotiate-item`. |
| `/catalog.json` | Les articles disponibles, sans mandat privé. Convention de ce projet. |
| `/.well-known/ai-catalog.json` | Catalogue de découverte qui référence l’agent. |
| `/` | Interface A2A JSON-RPC v1.0. |
| `/healthz` | État du serveur. |

La carte et les deux catalogues ont des rôles différents : le catalogue de découverte trouve l’agent ; le catalogue d’articles trouve ses objets. Ajouter un article ne crée pas une nouvelle carte ni un nouveau tunnel.

## Acheter via A2A

Le client récupère la carte puis utilise l’interface annoncée. Les opérations suivantes sont notre schéma métier dans une `Part.data` A2A ; ce ne sont pas des méthodes A2A supplémentaires. Du texte contenant exactement l’objet JSON est aussi accepté.

Exemple complet contre le serveur local sans jeton :

```bash
curl --fail-with-body http://127.0.0.1:9999/ \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  --data '{"jsonrpc":"2.0","id":"rpc-1","method":"SendMessage","params":{"message":{"messageId":"catalogue-demo-1","role":"ROLE_USER","parts":[{"data":{"action":"list-items"}}]}}}'
```

Lire `result.message.contextId` et les données de `result.message.parts`. Pour poursuivre, ajouter ce `contextId` à `params.message` et choisir un nouveau `messageId`. Pour rejouer exactement une requête perdue, conserver ses identifiants et son contenu, y compris si le premier contexte n’a pas encore été reçu. Avec un serveur protégé, fournir également le bearer token par un mécanisme local qui ne l’expose pas dans la conversation.

```json
{"action":"list-items"}
```

```json
{"action":"get-item","item_id":"IDENTIFIANT_RETOURNE"}
```

Après avoir consulté les conditions et la révision courantes :

```json
{"action":"offer","item_id":"IDENTIFIANT_RETOURNE","price_minor":25000,"currency":"EUR","accepted_terms_revision":1}
```

Une offre sous le plancher est refusée avec une contre-proposition au prix affiché, sans communiquer le plancher. Une offre admissible est soumise au propriétaire. Une révision absente ou périmée renvoie les conditions courantes pour consultation, sans créer de proposition. Le montant soumis est plafonné au prix affiché si l’acheteur offre davantage.

Conserver le `contextId` renvoyé et utiliser un `messageId` unique par message. Lors d’une répétition du même message après un problème réseau, conserver son identifiant : un message identique est dédupliqué, et réutiliser cet identifiant pour d’autres paramètres est refusé. Les phrases ambiguës ne créent pas d’offre ; le bot acheteur doit traduire l’intention en données explicites.

```json
{"action":"proposal-status","proposal_id":"IDENTIFIANT_PROPOSITION"}
```

La lecture d’une proposition exige le contexte qui l’a créée. `contextId` corrèle une conversation ; il ne constitue pas une authentification d’acheteur. Ne pas partager les contextes entre utilisateurs.

## Validation par le vendeur

Le bot propriétaire utilise les commandes locales :

```bash
.venv/bin/a2a-seller notifications pending
# Après remise effective de la notification au propriétaire :
.venv/bin/a2a-seller notifications ack --id PROPOSITION --version 1

# Uniquement après sa décision explicite concernant cette version :
.venv/bin/a2a-seller proposal confirm --id PROPOSITION --version 1
.venv/bin/a2a-seller proposal reject --id PROPOSITION --version 1
.venv/bin/a2a-seller proposal counter --id PROPOSITION --version 1 --price 270
```

Les trois dernières commandes sont des alternatives. Une contre-proposition génère une nouvelle version ; l’acheteur doit soumettre une nouvelle offre explicite. Le bot ne confirme jamais une proposition simplement parce qu’il vient de la notifier. Une réponse ambiguë du propriétaire demande clarification.

`notifications pending` ne marque rien comme livré. L’acquittement n’est enregistré qu’après une notification réussie. Le bot peut utiliser une routine de son hôte pour consulter les nouveautés, ou faire cette consultation manuellement. Le paquet ne crée pas cette routine, ne réveille pas Grok et n’envoie pas lui-même de message dans sa conversation. L’acheteur retrouve la décision en consultant le statut A2A.

## Développement

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m build
```

Utiliser un répertoire de données de test pour les scénarios automatisés. Le prototype d’origine est conservé dans `examples/legacy/` pour référence ; ses scripts et son watchdog ne constituent pas l’installation du paquet. Le paquet n’importe pas automatiquement ses anciennes bases et configurations. Les données privées, jetons, bases SQLite et journaux ne doivent pas entrer dans une distribution.
