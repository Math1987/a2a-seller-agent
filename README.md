# a2a-seller-agent

Une bibliothèque Python et un skill pour permettre à un agent personnel de gérer un catalogue d’occasion et de négocier via A2A 1.0. Le serveur et son stockage tournent dans l’environnement de l’utilisateur ; son agent expose le serveur avec le tunnel disponible sur cet environnement.

## Utiliser avec son agent

Donner simplement le lien de ce dépôt à un agent capable de lire des liens et d’exécuter des commandes dans un environnement persistant :

> Je veux vendre une tasse artisanale à 50 euros, prix minimum 30 euros. Utilise https://github.com/Math1987/a2a-seller-agent et mets-la en vente.

**Pour l’agent qui reçoit une demande de vente : lire d’abord le [skill vendeur](skills/a2a-seller/SKILL.md), à la même révision que ce README, puis suivre ses instructions.** Le propriétaire n’a pas besoin de connaître le chemin du skill ni les commandes d’installation. Si le lien GitHub n’affiche pas le fichier complet, utiliser sa vue Raw ou lire le fichier dans un clone de cette révision.

Le skill guide l’installation, la collecte des informations manquantes, la création de l’annonce réelle et son exposition A2A. Il peut aussi synchroniser les annonces publiques vers un registre de recherche configuré avec un jeton vendeur. Pour la tasse ci-dessus, l’agent connaît déjà le titre, la devise, le prix affiché et le plancher privé ; il demande seulement les informations utiles qui manquent, notamment l’état, la ville et les modalités de remise. Une demande de mise en vente réelle utilise `--real` ; elle n’exige pas une seconde confirmation de publication. Chaque accord de vente reste soumis à la confirmation du propriétaire.

Cette entrée par le README ne constitue pas un mécanisme universel d’activation de skills. L’agent doit pouvoir suivre le lien et appliquer les instructions ; l’exécution persistante, le tunnel et les notifications dépendent de son environnement. Il signale précisément toute capacité manquante.

Le bot traduit cette demande en commandes structurées. La bibliothèque valide les données, conserve le plancher privé et traite les échanges de façon déterministe. Elle n’appelle aucun modèle et n’interprète pas les montants par expressions régulières.

## Installation

Python 3.11 ou supérieur (requis par les versions de Starlette utilisées). Le paquet n’est pas encore publié sur PyPI. Installer depuis la branche par défaut du dépôt :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install "git+https://github.com/Math1987/a2a-seller-agent.git"
.venv/bin/a2a-seller skill show
```

Si le lien fourni désigne une branche ou un commit précis, installer cette révision en ajoutant `@REVISION` à l’URL Git, ou installer son checkout avec `.venv/bin/python -m pip install .`. Ne pas mélanger un skill de développement avec le code de la branche par défaut. Pour reproduire un déploiement, utiliser le commit testé plutôt qu’une branche mobile.

Le [skill](skills/a2a-seller/SKILL.md) est aussi embarqué dans la distribution Python. L’agent peut le retrouver avec `a2a-seller skill show` et, si son hôte le permet, l’enregistrer pour les prochaines demandes. L’installation Python seule n’active pas automatiquement un skill dans le bot.

Les exemples suivants utilisent l’exécutable de cet environnement. Par défaut, les données restent dans `~/.local/share/a2a-seller`. `--data-dir CHEMIN` sélectionne un autre stockage et doit précéder la sous-commande.

## Gérer les articles

```bash
.venv/bin/a2a-seller item add \
  --title "Table de nuit" \
  --description "Table de nuit en bois, un tiroir" \
  --condition "Rayure sur le plateau" \
  --price 300 --minimum-price 230 --currency EUR \
  --area "Lyon" --handover "Remise en main propre, créneau à convenir" \
  --real --request-id table-de-nuit-creation-1

.venv/bin/a2a-seller item list
.venv/bin/a2a-seller item show --id IDENTIFIANT_RETOURNE
.venv/bin/a2a-seller item update --id IDENTIFIANT_RETOURNE --price 280
.venv/bin/a2a-seller item withdraw --id IDENTIFIANT_RETOURNE
```

Les commandes renvoient du JSON ; une erreur produit du JSON sur stderr et un code de sortie non nul. Les sorties d’administration sont privées et peuvent contenir les prix minimaux. Ne pas les transférer à un acheteur.

Une création est **en simulation par défaut**. Ajouter `--real` seulement pour une annonce réelle demandée par le propriétaire. Même dans ce mode, la bibliothèque ne débite aucun paiement, ne réserve pas physiquement l’objet et n’organise pas de livraison.

Chaque article reçoit un identifiant stable et une révision. Une modification invalide les propositions encore en attente ; un retrait retire l’article du catalogue disponible et invalide ses propositions en attente. L’historique reste conservé. Un article déjà vendu est terminal : il ne peut plus être modifié ou retiré. Réutiliser le même `--request-id` avec les mêmes paramètres après une coupure évite de créer deux fois l’article ; un nouvel acte utilise un nouvel identifiant de requête.

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

## Rendre les annonces trouvables dans un registre

Le helper `registry` utilise le contrat HTTP d’`a2a-market-registry`. Le registre est indépendant : d’autres agents peuvent l’utiliser avec leur propre client HTTP. Il conserve les annonces publiques et l’URL de la carte ; le serveur vendeur, le prix minimum et les décisions restent ici. Un acheteur recherche un objet dans cet index puis contacte directement son vendeur via A2A.

Il faut une instance réellement disponible, son URL et un jeton de publication provisionné par son opérateur pour le bon `seller_id`. L’URL de production retenue, également utilisée par défaut par le helper, est `https://api.a2a-market.com` ; son déploiement AWS est en préparation. La racine `https://a2a-market.com` est réservée à la page de présentation hébergée chez IONOS. Les exemples utilisent une URL à remplacer par celle du registre dont l’accès HTTPS a été vérifié. Aucun compte n’est créé et rien n’est publié tant que le registre n’est pas configuré. Une mise à jour du paquet ne change pas une URL déjà enregistrée : pour changer de registre, relancer `registry configure` avec son URL et le jeton correspondant, en conservant le répertoire de données du vendeur.

Afficher l’identité publique locale à transmettre à l’opérateur, sans exporter les données d’administration :

```bash
.venv/bin/a2a-seller registry identity
```

Cette commande crée l’identité locale si nécessaire, sans requête réseau. Conserver le même répertoire de données ; un nouveau stockage crée un autre vendeur. Après réception du jeton dans un fichier privé `0600` :

```bash
.venv/bin/a2a-seller registry configure \
  --url https://registre.example \
  --agent-card-url https://VOTRE-ENDPOINT.ngrok-free.app/.well-known/agent-card.json \
  --token-file /chemin/prive/registry-token
.venv/bin/a2a-seller registry sync
.venv/bin/a2a-seller registry status
```

Le jeton du registre est distinct de l’authtoken ngrok et d’un éventuel jeton d’accès A2A. Ne pas le copier dans le chat, la carte ou une annonce. `configure` enregistre la configuration sans publier ; `sync` publie uniquement les articles `available` et `simulation: false`. Les annonces fictives restent utilisables pour les tests A2A locaux et n’entrent pas dans cet index public. Un contrôle d’accès A2A ne masque pas les champs publics volontairement publiés au registre ; l’acheteur aura encore besoin de l’accès approprié pour négocier avec un vendeur protégé.

Une fois configuré, le helper tente une synchronisation après les ajouts, modifications, retraits et confirmations de vente. `serve` lance un premier envoi puis un renouvellement toutes les 300 secondes pendant sa vie. Si la configuration est ajoutée alors que `serve` tourne déjà, redémarrer ce processus pour activer le renouvellement. À chaque démarrage, `serve` reprend l’URL de carte dérivée de son `--public-url`, notamment après changement d’URL ngrok.

Le registre expire les publications après 900 secondes sans renouvellement avec son réglage par défaut. Une erreur de synchronisation conserve l’opération locale réussie et produit un avertissement ; vérifier `registry status`, corriger la connexion et relancer `registry sync`. Ne pas recréer l’article pour compenser cette erreur. Une annonce retirée peut rester visible dans l’index jusqu’à sa prochaine synchronisation ou son expiration. La disponibilité et la révision définitives se vérifient toujours auprès du vendeur.

Rechercher sans jeton de publication, avec ou sans installation d’un vendeur local :

```bash
.venv/bin/a2a-seller registry search --url https://registre.example \
  --query "tasse artisanale" --area "Lyon" --currency EUR \
  --max-asking-price-minor 6000 --limit 20
```

Le prix maximal filtre le **prix affiché**, en unités mineures, et exige une devise. Un objet affiché plus cher mais négociable peut donc être exclu : le registre ne connaît pas son plancher. Les résultats contiennent `seller_id`, `item_id`, `agent_card_url` et `next_cursor` pour la pagination. Le client renvoie ce curseur avec les mêmes filtres. `search` n’envoie jamais le jeton de publication et ne crée pas de base vendeur. Avant d’ouvrir une URL reçue, l’agent applique sa propre politique d’accès réseau aux liens de ces annonces tierces. L’index ne vérifie pas la disponibilité des endpoints et ne garantit pas l’identité déclarée.

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

Les trois dernières commandes sont des alternatives. Plusieurs offres peuvent attendre la décision du propriétaire. Une confirmation réelle marque atomiquement l’article `sold`, accepte la proposition choisie et clôt les autres propositions encore ouvertes ; une autre confirmation concurrente ne peut pas vendre le même article une deuxième fois. L’objet quitte le catalogue disponible et le helper tente de mettre à jour le registre s’il est configuré. Une confirmation fictive reste `accepted_fictive` et ne marque pas l’article vendu.

Une contre-proposition génère une nouvelle version ; l’acheteur doit soumettre une nouvelle offre explicite. Le bot ne confirme jamais une proposition simplement parce qu’il vient de la notifier. Une réponse ambiguë du propriétaire demande clarification. L’état `sold` est un état du catalogue ; il n’effectue ni paiement ni remise de l’objet.

`notifications pending` ne marque rien comme livré. L’acquittement n’est enregistré qu’après une notification réussie. Le bot peut utiliser une routine de son hôte pour consulter les nouveautés, ou faire cette consultation manuellement. Le paquet ne crée pas cette routine, ne réveille pas Grok et n’envoie pas lui-même de message dans sa conversation. L’acheteur retrouve la décision en consultant le statut A2A.

## Développement

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m build
```

Utiliser un répertoire de données de test pour les scénarios automatisés. Le prototype d’origine est conservé dans `examples/legacy/` pour référence ; ses scripts et son watchdog ne constituent pas l’installation du paquet. Le paquet n’importe pas automatiquement ses anciennes bases et configurations. Les données privées, jetons, bases SQLite et journaux ne doivent pas entrer dans une distribution.
