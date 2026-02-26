# 📦 PretGo

Application web de gestion de prêt de matériel pour **établissements scolaires** — Python/Flask, SQLite, zéro installation requise. Interface moderne et tactile, base de données locale, aucun cloud requis.

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-green?logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local-lightgrey?logo=sqlite&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-purple?logo=bootstrap&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 🎯 À quoi ça sert ?

Vous gérez le matériel informatique d'un établissement scolaire (collège, lycée…) et vous avez besoin de :

- **Suivre qui a emprunté quoi** et depuis quand
- **Être alerté des retards** de restitution
- **Gérer un inventaire** de matériel avec numéros de série, étiquettes, images
- **Importer/exporter** facilement des listes de personnes et de matériel en CSV
- **Imprimer des étiquettes** avec code-barres pour le matériel
- **Consulter des statistiques** détaillées sur l’activité de prêt
- **Personnaliser l’interface** : thème couleur, mode sombre, champs dynamiques

Cette application tourne **en local** sur votre poste — aucun serveur externe, aucune donnée dans le cloud.

---

## ✨ Fonctionnalités

### Gestion des prêts
- Création rapide de prêt avec autocomplétion des personnes et du matériel
- Durée configurable : heures, jours, fin de journée (heure configurable), ou durée par défaut
- Retour en un clic avec mise à jour automatique de l'état du matériel
- Détail, modification et suppression de chaque prêt
- Historique complet des prêts restitués

### Gestion des personnes
- Fiche personne avec nom, prénom, catégorie (élève, enseignant, agent…) et classe
- Catégories personnalisables avec icônes, couleurs et badges visuels
- Import CSV en masse (avec gabarit dynamique reflétant les catégories configurées)
- Suppression logique (soft delete) — les personnes avec des prêts actifs sont protégées

### Inventaire de matériel
- Fiche matériel : type, marque, modèle, n° de série, n° d'inventaire, état, image
- Numérotation automatique par préfixe de catégorie (ex : `PC-00001`, `VID-00012`)
- Catégories de matériel personnalisables avec préfixes
- Import CSV en masse (avec gabarit dynamique reflétant les catégories configurées)
- Synchronisation automatique de l'état (disponible / prêté) avec les prêts

### Étiquettes & impression
- Génération d'étiquettes avec code-barres pour le matériel inventorié
- Mise en page optimisée pour impression sur planches d'étiquettes
- Support optionnel d'imprimante Zebra (ZPL via port série)

### Alertes & suivi
- Tableau de bord avec alertes de prêts en dépassement
- Badge de notification dans la barre de navigation
- Export CSV des alertes en cours


### Administration
- **Première connexion guidée** : mot de passe par défaut `1234`, personnalisation obligatoire au premier lancement
- Code de récupération **unique par installation**, généré lors de la personnalisation du mot de passe
- Accès protégé par mot de passe (hachage sécurisé scrypt/pbkdf2)
- Réglages : durée par défaut, heure de fin de journée, nom de l'établissement, taille des étiquettes
- **Personnalisation du bip sonore** lors des scans webcam : choix du volume et du type de bip (sine, carré, triangle, dent de scie), test instantané, réglages persistants sur toutes les pages
- Réinitialisation de la base de données
- Génération de données de démonstration dynamiques (s'adapte aux catégories configurées)
### Ergonomie du scan
- Bouton de scan toujours visible dans la barre de navigation
- Icône contextuelle selon le mode (webcam ou douchette)
- Bip sonore configurable à chaque scan webcam (volume/type)
- Test du bip en direct dans les réglages admin

### Import / Export
- Export CSV : prêts (tous / en cours), personnes, inventaire, alertes
- Import CSV : personnes, inventaire (avec détection des doublons)
- Gabarits CSV dynamiques : les fichiers téléchargeables reflètent automatiquement les catégories configurées dans l'application

---

## 🚀 Installation

### Prérequis

- **Windows 10 ou supérieur**
- **Connexion Internet** (uniquement pour l'installation)
- Aucune installation de Python n'est nécessaire — un Python portable est téléchargé automatiquement

### Installation rapide (recommandée)

1. **Téléchargez** le dossier du projet (ou clonez le dépôt)
2. **Double-cliquez sur `installer.bat`** — télécharge Python portable + Flask (~15 Mo)
3. **Double-cliquez sur `lancer.bat`** — lance le serveur et ouvre le navigateur
4. Connectez-vous avec le mot de passe par défaut **`1234`** puis personnalisez-le

> 💡 Aucune installation système, aucun droit administrateur requis. Tout est contenu dans le dossier du projet.

### Installation manuelle (développeurs)

```bash
# Cloner le dépôt
git clone https://github.com/fablabloritz-coder/PretGo.git
cd PretGo

# Créer un environnement virtuel
python -m venv .venv

# Activer l'environnement virtuel
# Windows :
.venv\Scripts\activate
# Linux / macOS :
source .venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt

# Lancer l'application
python app.py
```

L'application est accessible sur **http://localhost:5000**.

---

## 📁 Structure du projet

```
├── app.py                  # Application Flask principale (~3700 lignes)
├── database.py             # Gestion SQLite (init, migrations, helpers)
├── test_routes.py          # Tests automatisés (67 tests)
├── requirements.txt        # Dépendances Python (Flask)
├── installer.bat           # Télécharge Python portable + installe Flask
├── lancer.bat              # Lance l'app avec le Python embarqué
├── python/                 # Python portable (créé par installer.bat)
├── data/
│   └── code_recuperation.txt   # Code de récupération admin (généré à la personnalisation)
├── static/
│   ├── css/
│   │   └── style.css       # Styles personnalisés
│   ├── js/
│   │   └── app.js          # JavaScript front-end
│   ├── uploads/             # Images de matériel uploadées
│   └── exemple_personnes.csv
└── templates/               # Templates Jinja2 (24 fichiers)
    ├── base.html            # Layout principal (navbar, Bootstrap 5, mode sombre)
    ├── index.html           # Tableau de bord / prêts en cours
    ├── nouveau_pret.html    # Formulaire de nouveau prêt
    ├── retour.html          # Interface de retour
    ├── inventaire.html      # Liste du matériel
    ├── personnes.html       # Liste des personnes
    └── ...
```

---

## ⚙️ Configuration

Au premier lancement, l'application crée automatiquement :
- La base de données `data/gestion_prets.db`
- Les tables et migrations nécessaires

### Accès administrateur

1. **Première connexion** : connectez-vous avec le mot de passe par défaut **`1234`**
2. L'application vous demande immédiatement de **personnaliser votre mot de passe**
3. Un **code de récupération unique** est alors généré et sauvegardé dans `data/code_recuperation.txt`
4. **Conservez ce code** — il est le seul moyen de réinitialiser votre mot de passe en cas d'oubli

> ⚠️ Le code de récupération est régénéré à chaque changement de mot de passe. Pensez à noter le nouveau code.


### Réglages disponibles

| Paramètre | Description | Valeur par défaut |
|-----------|-------------|-------------------|
| Durée d'alerte | Durée avant qu'un prêt soit considéré en retard | 7 jours |
| Unité de durée | Jours ou heures | Jours |
| Heure de fin de journée | Limite pour le mode "fin de journée" | 17:45 |
| Nom de l'établissement | Affiché sur les étiquettes | — |
| Taille code-barres | Hauteur des codes-barres sur les étiquettes | 50 |
| Volume du bip | Volume du bip sonore lors du scan webcam | 15% |
| Type de bip | Forme d'onde du bip (sine, carré, triangle, dent de scie) | Sine |
| Couleur primaire | Couleur du thème de l'interface | #4361ee |
| Mode sombre | Activer/désactiver le thème sombre | Désactivé |

---

## 🖥️ Technologies

| Composant | Technologie |
|-----------|------------|
| Backend | **Python 3** / **Flask 3.0** |
| Base de données | **SQLite** (locale, sans serveur) |
| Frontend | **Bootstrap 5.3** / **Bootstrap Icons** |
| Codes-barres | **JsBarcode** (client-side) |
| Graphiques | **Chart.js** (statistiques interactives) |
| Sécurité | **scrypt/pbkdf2** (hachage des mots de passe) |

**Aucune dépendance lourde** — seul Flask est requis côté Python. L'interface charge Bootstrap et les icônes via CDN.

**Déploiement autonome** — `installer.bat` télécharge un Python portable (~10 Mo) dans le dossier du projet. Aucune installation système requise, aucun droit administrateur nécessaire. Idéal pour les environnements scolaires restreints.

---

## 🔒 Sécurité

- **Première connexion sécurisée** : mot de passe par défaut `1234` avec personnalisation obligatoire. Le code de récupération n'est généré qu'après cette étape, garantissant un code **unique par installation**.
- **Mots de passe** hachés avec scrypt/pbkdf2 (via werkzeug). Migration automatique depuis l'ancien format SHA-256.
- **Clé secrète Flask** configurable via variable d'environnement `FLASK_SECRET_KEY` (une valeur par défaut est fournie pour simplifier le démarrage).
- **Protection contre l'open redirect** sur le paramètre `next` après authentification.
- **Autocomplétion sécurisée** : les données utilisateur sont insérées dans le DOM via `textContent` (pas `innerHTML`) pour prévenir les injections XSS.
- **Encodage URL** systématique des paramètres de recherche dans les liens.

---

## 📸 Aperçu

<img width="1912" height="1076" alt="image" src="https://github.com/user-attachments/assets/beee0068-c3bc-4680-9ca2-a835db76bb93" />


### Tableau de bord
Liste des prêts en cours avec indicateur de durée et alertes de dépassement.

### Nouveau prêt
Formulaire avec autocomplétion des personnes, sélection du matériel inventorié, choix de la durée, modal d'engagement.

### Inventaire
Tableau filtrable avec image, type, marque/modèle, n° de série, état et actions rapides.

### Étiquettes
Génération d'étiquettes avec code-barres, optimisées pour l'impression.

---

## 🤝 Contribution

Les contributions sont les bienvenues ! N'hésitez pas à :

1. **Fork** le projet
2. Créer une **branche** pour votre fonctionnalité (`git checkout -b feature/ma-fonctionnalite`)
3. **Commit** vos changements (`git commit -m "Ajout de ma fonctionnalité"`)
4. **Push** sur votre branche (`git push origin feature/ma-fonctionnalite`)
5. Ouvrir une **Pull Request**

### Idées d'améliorations

- 📅 Système de réservation / planification
- 📧 Notifications par email (rappels, retards)
- 👥 Mode multi-utilisateurs avec rôles (admin, gestionnaire, consultant)
- 🔧 Gestion du cycle de vie du matériel (maintenance, garantie, rebut)
- 🌐 Mode hors-ligne (PWA)

---

## 📄 Licence

Ce projet est distribué sous licence **MIT**. Voir le fichier [LICENSE](LICENSE) pour plus de détails.

---

## 🙏 Remerciements

- [Flask](https://flask.palletsprojects.com/) — micro-framework web Python
- [Bootstrap](https://getbootstrap.com/) — framework CSS responsive
- [Bootstrap Icons](https://icons.getbootstrap.com/) — icônes SVG
- [JsBarcode](https://github.com/lindell/JsBarcode) — génération de codes-barres côté client
- [Chart.js](https://www.chartjs.org/) — graphiques interactifs
