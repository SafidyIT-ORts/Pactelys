# Configuration Claude Code GTM

## Identité de l'entreprise
Entreprise : Pactelys | Secteur : Plateforme B2B d'opportunités professionnelles | Taille : 1 à 5 employés
CA cible : Moins de 1M EUR | Marché : France et Europe francophone

## Promesse centrale
Pactelys aide les professionnels à identifier et générer des opportunités B2B 
(clients, partenaires, fournisseurs) via une plateforme de mise en relation 
ciblée selon l'activité, les besoins et la localisation.

## ICP (Ideal Customer Profile)
Secteur cible : TPE, PME, indépendants et prestataires de services B2B
Taille entreprise : 1 à 250 salariés
Titre du contact : Dirigeant, gérant, directeur commercial ou responsable du développement
Zone géographique : France et Europe francophone
Signaux d'achat : Recherche de clients, partenaires, fournisseurs ou sous-traitants ; développement commercial ; lancement d'une nouvelle offre ; recrutement commercial

## Proposition de valeur
Problème résolu : Les professionnels perdent du temps à rechercher des clients, partenaires, fournisseurs ou opportunités sur plusieurs plateformes et réseaux dispersés.
Bénéfice principal : Centraliser les opportunités professionnelles sur une seule plateforme et faciliter les mises en relation ciblées.
Différenciateur clé : Pactelys regroupe plusieurs types d'opportunités B2B et propose un matching selon l'activité, les besoins et la localisation.

## Règles de comportement
- Toujours utiliser le vouvoiement dans les communications
- Ton : professionnel mais direct, jamais corporate
- Ne jamais pitcher avant d'avoir compris le problème
- Chaque email : maximum 80 mots, 1 seul CTA

## Règle de traçabilité
Tout chiffre utilisé dans un contenu doit être marqué [Source vérifiée] ou [Exemple illustratif], jamais présenté comme un fait sans étiquette.

## Règle de mesure
Ne pas se limiter au signup comme objectif de conversion. Le parcours à suivre est : inscription → profil complété → premier besoin/opportunité publié → première correspondance → premier contact.

## Règle d'autonomie du contenu
Chaque article, post ou email doit être compréhensible indépendamment des 
autres, sans supposer que le lecteur a vu un contenu précédent. Toujours 
rappeler explicitement ce qu'est Pactelys et le lien avec la promesse 
centrale, même dans le 4e article d'une série.

## Règle d'architecture de contenu
Les intentions de recherche les plus commerciales (ex: "alternative à un 
annuaire", "plateforme de mise en relation B2B") doivent devenir des pages 
pérennes dédiées, pas de simples articles de blog. Le blog sert de contenu 
satellite qui renforce ces pages via le maillage interne, pas l'inverse.

## Contexte obligatoire avant les commandes des plugins GTM
- Avant d'exécuter toute commande, skill ou agent provenant des plugins GTM (sales-prospecting, sales-pipeline, sales-enablement, content-marketing, seo, etc.), toujours lire automatiquement `Brand.md` et `S01.md` à la racine du projet, sans attendre une demande explicite.
- Utiliser leur contenu comme source de vérité pour le ton, le positionnement et le contexte stratégique avant de produire la moindre sortie.
- Si l'un des deux fichiers est introuvable ou vide, le signaler avant de poursuivre plutôt que de continuer sans ce contexte.

## Accès données de mots-clés (DataForSEO)
Avant de finaliser toute stratégie de mots-clés ou de figer un calendrier 
éditorial basé sur du SEO, exécute automatiquement :
python keyword_volume.py --keywords "mot-clé 1,mot-clé 2,..." --locale fr-FR
Ne jamais présenter un volume comme "estimation qualitative" si ce script 
peut être appelé pour obtenir la vraie donnée à la place.

## Sources de données pour les statistiques
- D'abord, consulte le site web du client : https://pactelys.fr — pour 
  les chiffres propres à son entreprise (nombre de clients, témoignages, résultats).
- Ensuite, si un argument nécessite une statistique sectorielle/marché 
  plus large, cherche sur le web une donnée réelle et sourcée sur le 
  secteur du client.
- Si aucune des deux sources ne donne de chiffre fiable, reformule 
  l'argument en qualitatif — ne jamais inventer un chiffre précis.