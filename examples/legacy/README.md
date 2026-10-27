# Prototype d'origine

Ces fichiers conservent le prototype monoarticle utilisé pour la première démonstration Grok. Ils ne sont pas installés dans le paquet Python et ne constituent pas le parcours supporté.

Utiliser désormais les commandes `a2a-seller` décrites dans le README principal. En particulier, ne pas exécuter cet ancien `watchdog.sh` : il contient les chemins d'une machine précise et peut arrêter des processus ngrok étrangers au projet. Les anciens programmes ne lisent pas le nouveau stockage multiarticles.

Les données réelles de l'ancien prototype restent séparées ; aucune migration implicite n'est effectuée. Recréer explicitement les annonces souhaitées avec la CLI, puis arrêter l'ancien service uniquement avec l'accord de son propriétaire.
