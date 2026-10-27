# a2a-seller-agent

Agent vendeur A2A (Agent2Agent, protocole v1.0) pour une annonce **fictive** de test (`velo-test-001`), exposé par un tunnel ngrok.
Il est déterministe (aucun LLM) : analyse par mots-clés et expressions régulières, état dans SQLite.

## Fonctionnement
- `server.py` : serveur A2A (Starlette + uvicorn) sur 127.0.0.1:9999. Sert l'agent card (`/.well-known/agent-card.json`) et un ai-catalog (`/.well-known/ai-catalog.json`, aussi `/.well-known/ard.json`). L'URL publique est lue dans `public_base_url.txt`.
- `negotiation.py` : règles de négociation (faits publics, conditions, seuils privés, détection d'injections). L'identifiant de négociation est le `contextId` A2A. Historique dans `negotiations.db`.
- `admin.py` : outil du vendeur (`list`, `show`, `pending`, `notify-next`, `confirm`, `reject`, `counter`, `delete`, `reset-tests`). Rien n'est accepté sans confirmation explicite du vendeur.
- `watchdog.sh` : supervise le serveur et ngrok et relance en cas de coupure.

## Installation
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp private_config.example.json private_config.json   # puis ajuster; ne jamais committer
.venv/bin/python server.py
```
Pour l'exposer : `ngrok http 9999`, écrire l'URL publique dans `public_base_url.txt`, puis relancer le serveur. Le plan gratuit ngrok change d'URL à chaque redémarrage.

## Appel de test
JSON-RPC `SendMessage`, rôle `ROLE_USER`, en-tête `A2A-Version: 1.0`, vers l'URL de l'agent card.

## Sécurité
Ne jamais committer `private_config.json`, `negotiations.db`, le token ngrok ni les journaux (voir `.gitignore`). Les accords sont fictifs.
