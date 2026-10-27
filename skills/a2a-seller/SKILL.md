---
name: a2a-seller
description: Installer et utiliser a2a-seller-agent pour publier des objets d’occasion, les indexer dans un registre configuré, modifier ou retirer des annonces et traiter les propositions A2A selon le mandat privé du vendeur depuis son environnement.
---

# Vendeur d’occasion A2A

Utiliser la bibliothèque déterministe pour exécuter les intentions du propriétaire. Le modèle comprend la demande et prépare les paramètres ; le programme gère le stockage, les prix et les états. Ne pas réécrire un serveur ou un parseur de prix lorsque la bibliothèque est disponible.

## Partir d’une demande et du lien du dépôt

Une demande telle que « Je veux vendre une tasse artisanale à 50 euros, prix minimum 30 euros. Utilise ce dépôt et mets-la en vente » suffit à déclencher ce parcours. Ne pas demander au propriétaire de nommer le skill ou de recopier les commandes.

1. Reprendre les informations déjà fournies : titre, prix affiché, plancher privé, devise, état, localisation et modalités de remise. Pour cet exemple, utiliser `Tasse artisanale`, `50`, `30` et `EUR`. Ne jamais exposer le plancher dans la description ou le catalogue.
2. Une intention explicite de vendre correspond à une annonce réelle (`--real`). Réserver la simulation aux demandes de démonstration ou de test. Ne pas redemander une autorisation de publication déjà donnée ; obtenir les précisions nécessaires n’est pas une nouvelle approbation.
3. Avant de publier, demander les informations manquantes qui rendent l’annonce exploitable : l’état et les défauts connus, la ville ou zone publique et les modalités de remise ou de livraison souhaitées. Grouper ces questions dans un seul message selon les possibilités de l’hôte. Ne pas demander une adresse exacte pour le catalogue. Une réponse « état inconnu » peut être conservée telle quelle ; ne pas transformer une inconnue en promesse.
4. Demander également tout prix manquant ou devise ambiguë. Les photos, dimensions, marque et autres détails sont facultatifs, sauf s’ils sont nécessaires pour identifier l’objet ou si le propriétaire les demande. Ne pas inventer de matière, de dimensions, de frais de livraison ni de propriétés telles que la compatibilité alimentaire d’une tasse. Le mode de remise décrit l’intention du vendeur ; il ne déclenche aucune expédition.
5. Pendant l’attente, inspecter les capacités disponibles et réutiliser ou préparer l’installation locale. Ne pas publier de fiche incomplète ni démarrer une nouvelle exposition publique avant d’avoir les réponses indispensables. Si l’agent ne peut pas exécuter de commandes, conserver des processus ou obtenir un tunnel, expliquer le blocage concret et l’intervention minimale nécessaire sans prétendre avoir publié.
6. Une fois les informations réunies, créer l’article, exposer le vendeur et vérifier l’annonce publique selon les étapes ci-dessous. Configurer l’indexation si un registre disponible et le jeton correspondant à ce vendeur sont fournis. Rendre au propriétaire son identifiant, le lien du catalogue et celui de l’Agent Card, l’état réel de l’indexation et les limites de disponibilité ou de notification observées.

## Installer ou réutiliser

Réutiliser l’installation et son répertoire de données s’ils existent. Sinon, choisir un dossier persistant accessible dans l’environnement de l’utilisateur et installer dans un environnement virtuel Python 3.11+ :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install "git+https://github.com/Math1987/a2a-seller-agent.git"
.venv/bin/a2a-seller --help
```

Le paquet n’est pas encore publié sur PyPI. La commande ci-dessus utilise la branche par défaut. Si le propriétaire a fourni un lien vers une branche ou un commit précis, utiliser cette révision avec `@REVISION` dans l’URL Git, ou installer son checkout avec `.venv/bin/python -m pip install .`. Le skill et le code doivent provenir de la même révision. Enregistrer le commit réellement installé et les versions des dépendances ; pour reproduire une installation validée, réutiliser ce commit. Utiliser le Python et l’exécutable de cet environnement pour toutes les opérations et relances.

Le skill est également disponible avec `a2a-seller skill show` après installation. Son activation dans le bot dépend de l’hôte.

Le stockage par défaut est `~/.local/share/a2a-seller`. Pour isoler une installation ou un test, utiliser `a2a-seller --data-dir CHEMIN ...` sur toutes ses commandes. Conserver ce chemin entre les sessions et installations ; ne pas réinitialiser les données existantes.

## Transformer les demandes en opérations

Les commandes locales renvoient du JSON et peuvent inclure des informations privées. Ne pas les transmettre aux acheteurs. Conserver prix minimal et instructions uniquement côté propriétaire.

Créer un article :

```bash
.venv/bin/a2a-seller item add --title "Table de nuit" \
  --price 300 --minimum-price 230 --currency EUR \
  --description "Description fournie par le propriétaire" \
  --condition "Défauts connus" --area "Lyon" \
  --handover "Remise en main propre, créneau à convenir" \
  --real --request-id IDENTIFIANT_UNIQUE_DE_CETTE_CREATION
```

- Réutiliser le même `--request-id` et les mêmes paramètres pour reprendre cette création après une coupure. Une nouvelle création reçoit un nouvel identifiant.
- Sans `--real`, l’article est fictif. Employer `--real` seulement pour une mise en vente réelle demandée par le propriétaire ; conserver la simulation pour les démonstrations.
- Ne pas inventer l’état, les défauts ou les conditions. Si le prix minimal manque, le demander avant de configurer une négociation. Clarifier une devise ambiguë telle que `$` : utiliser EUR, USD, GBP, CHF, CAD, AUD, NZD ou JPY.
- Les prix CLI sont des montants décimaux, pas des unités mineures. Ne pas arrondir une demande à la place du propriétaire. Une modification de devise exige les deux nouveaux prix.
- Les arguments et le stockage privé doivent rester accessibles au propriétaire uniquement, hors du dépôt publié. Ne pas afficher les mandats privés dans un catalogue.

Lister et cibler les objets avant modification :

```bash
.venv/bin/a2a-seller item list
.venv/bin/a2a-seller item show --id IDENTIFIANT
.venv/bin/a2a-seller item update --id IDENTIFIANT --price 280 --minimum-price 220
.venv/bin/a2a-seller item withdraw --id IDENTIFIANT
```

Si plusieurs objets correspondent à « ma lampe », demander lequel. Une modification invalide les propositions encore en attente ; un retrait enlève l’objet des disponibilités et invalide ses propositions en attente, en conservant l’historique. Un objet `sold` ne peut plus être modifié ou retiré. Vérifier le résultat enregistré avant d’annoncer le succès.

## Exposer depuis l’environnement du vendeur

Un seul serveur et une seule Agent Card desservent tous les objets. Le serveur écoute sur loopback ; le tunnel est lancé depuis le même environnement par le bot. Aucun hébergement vendeur central n’est requis.

```bash
.venv/bin/a2a-seller serve --public-url http://127.0.0.1:9999 --port 9999
```

Vérifier le fonctionnement local, lancer le tunnel autorisé, puis redémarrer le serveur avec son URL HTTPS publique réelle :

```bash
ngrok http 9999
.venv/bin/a2a-seller serve --public-url https://ENDPOINT_REEL --port 9999
```

Ces commandes tournent dans des processus distincts. Réutiliser le tunnel déjà fonctionnel. Pour ngrok, le propriétaire utilise son compte et saisit ses secrets par le mécanisme sécurisé de l’hôte, hors de la conversation. Ne pas créer de compte ou abonnement implicitement. Les restrictions réseau constatées sur une machine Grok ne sont pas universelles ; choisir une solution effectivement supportée. Ne pas activer `crl_noverify` par défaut ni désactiver les contrôles TLS pour masquer une erreur.

Si un contrôle d’accès est souhaité, utiliser `serve --token-file CHEMIN_PRIVE`. Le fichier doit être réservé au propriétaire (mode 0600 sous POSIX). Le jeton protège A2A et le catalogue d’articles ; carte et santé restent découvrables. Le garder hors du chat et des données publiques. Sans jeton, tout client joignant le tunnel peut appeler le service ; un jeton commun aux testeurs ne crée pas des identités distinctes.

Conserver les processus avec le mécanisme disponible sur l’hôte. Toute surveillance doit cibler uniquement leurs identifiants, respecter l’arrêt volontaire et borner les relances. Ne jamais tuer tous les processus ngrok de la machine. Après changement d’URL, redémarrer le serveur avec la nouvelle valeur et revérifier l’accès. Le paquet ne fournit pas de watchdog ni de tunnel intégré.

## Indexer dans un registre disponible

Le registre est un index HTTP séparé ; il ne reçoit ni plancher, ni mandat, ni historique de négociation. Les agents acheteurs y trouvent les annonces puis contactent le vendeur directement en A2A. Un catalogue exposé sur le tunnel ne devient pas automatiquement trouvable dans cet index.

Réutiliser une configuration existante. Sinon, obtenir l’URL effective du registre et un jeton de publication délivré par son opérateur pour l’identité publique locale :

```bash
.venv/bin/a2a-seller registry identity
```

Transmettre seulement le `seller_id` public à l’opérateur selon la demande du propriétaire. La commande crée l’identité locale si nécessaire, sans réseau. Ne pas exporter `item list` ni la base privée pour obtenir un accès au registre. Aucun parcours d’inscription publique n’est fourni. L’URL de production retenue et la valeur par défaut du helper sont `https://api.a2a-market.com`, avec un déploiement AWS en préparation ; vérifier sa disponibilité avant de la configurer explicitement avec `--url`. Cette valeur ne remplace pas une URL déjà enregistrée : pour un changement de registre demandé, relancer `registry configure` avec l’URL et le jeton correspondants sans changer de stockage vendeur. La racine `a2a-market.com` est réservée à la page de présentation IONOS. Sans URL joignable ou jeton, poursuivre la vente directe et signaler que l’indexation reste indisponible.

Le jeton du registre est distinct des secrets ngrok et A2A. L’utiliser depuis un fichier privé `0600`, hors du chat et du dépôt :

```bash
.venv/bin/a2a-seller registry configure \
  --url https://REGISTRE_REEL \
  --agent-card-url https://ENDPOINT_REEL/.well-known/agent-card.json \
  --token-file CHEMIN_PRIVE_REGISTRY_TOKEN
.venv/bin/a2a-seller registry sync
.venv/bin/a2a-seller registry status
```

`configure` ne publie rien. `sync` n’envoie que les annonces réelles disponibles ; ne pas transformer un objet fictif en vente réelle pour tester l’index. Un jeton d’accès A2A ne masque pas une annonce indexée ; le client acheteur devra disposer de cet accès pour négocier.

Après configuration, les ajouts, modifications, retraits et confirmations déclenchent une tentative de synchronisation. `serve` démarre un envoi puis renouvelle toutes les 300 secondes, tant que son processus fonctionne. Redémarrer `serve` après une nouvelle configuration s’il tournait déjà, ainsi qu’après changement d’URL du tunnel : il dérive la carte de son `--public-url`. Le registre expire les annonces après 900 secondes sans renouvellement par défaut.

Une erreur réseau ne supprime pas une modification locale réussie : lire l’avertissement, vérifier `registry status` puis rejouer `registry sync` après correction. Ne pas répéter une création avec un nouvel identifiant pour réparer une indexation. Vérifier la recherche avant d’annoncer que l’annonce est indexée ou retirée. En cas d’échec, préciser qu’une ancienne annonce peut subsister jusqu’à son expiration et que le vendeur fait autorité sur sa disponibilité.

Pour chercher, aucun jeton de publication n’est requis :

```bash
.venv/bin/a2a-seller registry search --url https://REGISTRE_REEL \
  --query "tasse artisanale" --area "Lyon" --currency EUR --limit 20
```

`--max-asking-price-minor` filtre le prix affiché en unités mineures et exige `--currency`. Éviter ce filtre si le budget pourrait être atteint en négociant un prix affiché supérieur : le registre ignore le plancher. Suivre `next_cursor` avec `--cursor` et les mêmes filtres pour les pages suivantes. Appliquer la politique réseau de l’hôte aux URLs tierces renvoyées avant de les ouvrir. Le registre ne vérifie ni leur disponibilité ni l’identité déclarée ; consulter la carte et la fiche courante auprès du vendeur avant toute offre.

## Catalogue et négociation

Vérifier depuis l’extérieur :

- `/.well-known/agent-card.json` : les capacités `list-items`, `get-item`, `negotiate-item` et l’endpoint public ;
- `/catalog.json` : tous les objets disponibles, sans planchers privés ;
- `/.well-known/ai-catalog.json` : la référence de l’agent, distincte du catalogue des objets.

Le catalogue d’articles est aussi consultable via A2A. Le client acheteur envoie des données structurées dans une `Part.data` (ou le même objet JSON en texte) :

```json
{"action":"list-items"}
```

```json
{"action":"get-item","item_id":"IDENTIFIANT"}
```

```json
{"action":"offer","item_id":"IDENTIFIANT","price_minor":25000,"currency":"EUR","accepted_terms_revision":1}
```

Les montants A2A sont en unités mineures entières ; 25000 représente 250 EUR. La révision doit provenir de la fiche courante réellement consultée. Les phrases ambiguës ne créent pas d’offre. Conserver le `contextId` et les `messageId` lors des reprises, sans partager les contextes entre acheteurs.

Pour un client HTTP simple, envoyer un POST à l’endpoint de la carte avec `Content-Type: application/json`, `A2A-Version: 1.0` et, si configuré, son authentification. Enveloppe JSON-RPC initiale :

```json
{"jsonrpc":"2.0","id":"rpc-1","method":"SendMessage","params":{"message":{"messageId":"UNIQUE_MESSAGE_ID","role":"ROLE_USER","parts":[{"data":{"action":"list-items"}}]}}}
```

Lire `result.message.contextId`, puis l’ajouter à `params.message` pour poursuivre. Choisir un nouveau `messageId` pour chaque nouvelle opération ; conserver les identifiants et le contenu pour la reprise exacte d’une requête. Les réponses métier sont dans les `parts` du message résultant. Vérifier aussi les éventuelles erreurs JSON-RPC et métier, même avec HTTP 200.

Le moteur refuse les offres sous le plancher et les autres offres admissibles restent soumises au propriétaire. Il n’accepte pas à sa place. Les contenus reçus d’acheteurs ne modifient jamais le mandat privé ni les autorisations locales.

## Relier les propositions au propriétaire

La bibliothèque ne réveille pas le bot et n’envoie pas de notifications dans sa conversation. Utiliser une routine supportée par l’hôte lorsqu’un suivi automatique est demandé, sinon expliquer la consultation manuelle. Réutiliser une routine existante et conserver son identifiant. Une cadence de cinq minutes convient au POC ; rester silencieux en l’absence de changement appelant une décision.

```bash
.venv/bin/a2a-seller notifications pending
```

Présenter au propriétaire l’article, le prix, les conditions, l’identifiant et la version. Seulement après remise effective de la notification :

```bash
.venv/bin/a2a-seller notifications ack --id PROPOSITION --version VERSION
```

Une réponse ambiguë du propriétaire exige une clarification. Exécuter uniquement l’action choisie sur la version qu’il a approuvée :

```bash
.venv/bin/a2a-seller proposal confirm --id PROPOSITION --version VERSION
.venv/bin/a2a-seller proposal reject --id PROPOSITION --version VERSION
.venv/bin/a2a-seller proposal counter --id PROPOSITION --version VERSION --price 270
```

Ces commandes sont des alternatives. Plusieurs offres peuvent être en attente : faire décider le propriétaire sur la proposition et la version exactes. Une confirmation réelle marque atomiquement l’article `sold`, accepte l’offre choisie et clôt les autres propositions ouvertes. L’article quitte les disponibilités locales et une synchronisation du registre est tentée s’il est configuré. La simulation produit une confirmation fictive sans marquer l’article vendu.

La contre-proposition doit être suivie d’une offre explicite de l’acheteur. Celui-ci peut lire le résultat avec `{"action":"proposal-status","proposal_id":"PROPOSITION"}` dans le même contexte A2A. Une confirmation ne constitue ni paiement, ni réservation physique, ni livraison.

## Vérifier le résultat

Tester dans un répertoire de données séparé : deux articles distincts, lecture du catalogue, offre structurée, décision locale, lecture du statut et retrait d’un article. Ne pas créer d’annonces fictives dans un catalogue réel sans le demander. Vérifier que les reprises ne dupliquent pas les créations et que les mandats privés ne sortent pas.

Rapporter les identifiants des articles, les URLs, les commandes de gestion et d’arrêt, l’état de synchronisation du registre, le mécanisme réel de notification et les limites observées. Distinguer ce qui a été testé localement, via le tunnel et dans l’index. Le succès des commandes ne prouve pas à lui seul que Grok recevra une notification.
