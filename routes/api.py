"""PretGo — Blueprint : api"""
from flask import Blueprint, jsonify, request, url_for
from werkzeug.utils import secure_filename
from database import get_setting
from utils import get_app_db, admin_required, allowed_file, get_categories_personnes, UPLOAD_FOLDER, calcul_depassement_heures
from datetime import datetime
import json
import os
import string
import uuid

bp = Blueprint('api', __name__)


# ============================================================
# FABLAB SUITE — Manifest & Widgets (spec v1.0.0)
# ============================================================

APP_VERSION = "1.0.0"
SUITE_SPEC_VERSION = "1.0.0"
_app_started_at = datetime.now().isoformat()


@bp.route('/api/fabsuite/manifest')
def fabsuite_manifest():
    """Manifest FabLab Suite — décrit l'application, ses capacités et ses widgets."""
    return jsonify({
        "app": "pretgo",
        "name": "PretGo",
        "version": APP_VERSION,
        "suite_version": SUITE_SPEC_VERSION,
        "status": "running",
        "description": "Gestion des prêts de matériel pour établissements",
        "icon": "bi-box-arrow-right",
        "color": "#0d6efd",
        "url": request.host_url.rstrip('/'),
        "capabilities": ["loans", "inventory"],
        "widgets": [
            {
                "id": "active-loans",
                "label": "Prêts en cours",
                "description": "Nombre de prêts actuellement actifs",
                "endpoint": "/api/fabsuite/widget/active-loans",
                "type": "counter",
                "refresh_interval": 120
            },
            {
                "id": "overdue-loans",
                "label": "Prêts en retard",
                "description": "Liste des prêts dépassant la date de retour prévue",
                "endpoint": "/api/fabsuite/widget/overdue-loans",
                "type": "list",
                "refresh_interval": 120
            },
            {
                "id": "equipment-status",
                "label": "État du parc",
                "description": "Répartition des équipements par état",
                "endpoint": "/api/fabsuite/widget/equipment-status",
                "type": "chart",
                "refresh_interval": 300
            }
        ],
        "notifications": {
            "endpoint": "/api/fabsuite/notifications",
            "types": ["warning"]
        },
        "started_at": _app_started_at
    })


@bp.route('/api/fabsuite/health')
def fabsuite_health():
    """Health check rapide pour monitoring par FabHome."""
    try:
        conn = get_app_db()
        conn.execute("SELECT 1")
        return jsonify({"status": "ok"})
    except Exception:
        return jsonify({"status": "error"}), 503


@bp.route('/api/fabsuite/widget/active-loans')
def fabsuite_widget_active_loans():
    """Widget counter : nombre de prêts en cours."""
    conn = get_app_db()
    row = conn.execute(
        "SELECT COUNT(*) as total FROM prets WHERE retour_confirme = 0"
    ).fetchone()
    return jsonify({
        "value": row['total'] if row else 0,
        "label": "Prêts en cours",
        "unit": "prêts"
    })


@bp.route('/api/fabsuite/widget/overdue-loans')
def fabsuite_widget_overdue_loans():
    """Widget list : prêts en retard avec nom de l'emprunteur."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, p.descriptif_objets,
               pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
    ''').fetchall()

    # Cache des settings pour performance
    duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')

    items = []
    for p in prets:
        est_depasse, heures = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_defaut, _unite_defaut=unite_defaut,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if est_depasse:
            jours = int(heures // 24)
            label = p['descriptif_objets'] or "Matériel"
            if len(label) > 50:
                label = label[:50] + "..."
            items.append({
                "label": f"{p['prenom']} {p['nom']} — {label}",
                "value": f"Retard : {jours}j" if jours >= 1 else f"Retard : {int(heures)}h",
                "status": "warning"
            })
    return jsonify({"items": items})


@bp.route('/api/fabsuite/widget/equipment-status')
def fabsuite_widget_equipment_status():
    """Widget chart : répartition des équipements par état."""
    conn = get_app_db()
    rows = conn.execute('''
        SELECT etat, COUNT(*) as total
        FROM inventaire WHERE actif = 1
        GROUP BY etat ORDER BY total DESC
    ''').fetchall()

    label_map = {
        'disponible': 'Disponible',
        'prete': 'Prêté',
        'hors_service': 'Hors service'
    }
    return jsonify({
        "type": "pie",
        "labels": [label_map.get(r['etat'], r['etat']) for r in rows],
        "values": [r['total'] for r in rows]
    })


@bp.route('/api/fabsuite/notifications')
def fabsuite_notifications():
    """Notifications : prêts en retard."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, p.descriptif_objets,
               pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
    ''').fetchall()

    duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')

    notifs = []
    for p in prets:
        est_depasse, heures = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_defaut, _unite_defaut=unite_defaut,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if est_depasse:
            jours = int(heures // 24)
            notifs.append({
                "id": f"overdue-loan-{p['id']}",
                "type": "warning",
                "title": f"Pret en retard : {p['prenom']} {p['nom']}",
                "message": f"{p['descriptif_objets'] or 'Materiel'} — retard de {jours}j {int(heures % 24)}h",
                "created_at": p['date_emprunt'],
                "link": f"/pret/{p['id']}"
            })
    return jsonify({"notifications": notifs})

@bp.route('/api/personnes')
def api_personnes():
    conn = get_app_db()
    q = request.args.get('q', '').strip()

    if q:
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe, email
            FROM personnes
            WHERE actif = 1 AND (nom LIKE ? OR prenom LIKE ? OR classe LIKE ? OR email LIKE ?)
            ORDER BY nom, prenom
            LIMIT 20
        ''', (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe, email
            FROM personnes
            WHERE actif = 1
            ORDER BY nom, prenom
        ''').fetchall()


    # Enrichir avec le libellé de catégorie pour le front-end
    cats = {c['cle']: c['libelle'] for c in get_categories_personnes()}
    result = []
    for p in personnes:
        d = dict(p)
        d['categorie_label'] = cats.get(d['categorie'], d['categorie'].replace('_', ' ').title())
        result.append(d)
    return jsonify(result)



@bp.route('/api/images-materiel')
@admin_required
def api_liste_images():
    """Retourne la liste des images disponibles dans le dossier uploads."""
    images = []
    if os.path.exists(UPLOAD_FOLDER):
        for f in sorted(os.listdir(UPLOAD_FOLDER)):
            if allowed_file(f):
                images.append(f)
    return jsonify(images)



@bp.route('/api/upload-image-materiel', methods=['POST'])
@admin_required
def api_upload_image():
    """Upload une nouvelle image dans la bibliothèque."""
    if 'image' not in request.files:
        return jsonify({'error': 'Aucun fichier envoyé'}), 400
    fichier = request.files['image']
    if fichier.filename == '':
        return jsonify({'error': 'Nom de fichier vide'}), 400
    if not allowed_file(fichier.filename):
        return jsonify({'error': 'Format non autorisé (jpg, png, gif, webp uniquement)'}), 400

    # Garder le nom original nettoyé, ajouter un suffixe unique si doublon
    nom_original = secure_filename(fichier.filename)
    nom_final = nom_original
    chemin = os.path.join(UPLOAD_FOLDER, nom_final)
    if os.path.exists(chemin):
        base, ext = os.path.splitext(nom_original)
        nom_final = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
        chemin = os.path.join(UPLOAD_FOLDER, nom_final)

    fichier.save(chemin)
    return jsonify({'filename': nom_final})



@bp.route('/api/supprimer-image-materiel', methods=['POST'])
@admin_required
def api_supprimer_image():
    """Supprime une image de la bibliothèque (si plus utilisée)."""
    filename = request.json.get('filename', '')
    if not filename or not allowed_file(filename):
        return jsonify({'error': 'Fichier invalide'}), 400
    chemin = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
    if os.path.exists(chemin):
        os.remove(chemin)
    return jsonify({'ok': True})



@bp.route('/api/inventaire')
def api_inventaire():
    """API JSON pour autocomplétion de matériel."""
    conn = get_app_db()
    q = request.args.get('q', '').strip()
    if q:
        items = conn.execute('''
            SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie, image, etat
            FROM inventaire WHERE actif = 1
            AND (numero_inventaire LIKE ? OR marque LIKE ? OR modele LIKE ?
                 OR type_materiel LIKE ? OR notes LIKE ? OR numero_serie LIKE ?)
            ORDER BY CASE WHEN etat = 'disponible' THEN 0 ELSE 1 END,
                     type_materiel, numero_inventaire LIMIT 20
        ''', (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        items = conn.execute('''
            SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie, image, etat
            FROM inventaire WHERE actif = 1
            ORDER BY type_materiel, numero_inventaire
        ''').fetchall()
    return jsonify([dict(i) for i in items])


@bp.route('/api/inventaire/random-scan')
@admin_required
def api_inventaire_random_scan():
    """API JSON : retourne un code d'inventaire aléatoire pour simuler un scan douchette."""
    conn = get_app_db()

    # Priorité aux matériels disponibles pour simuler un flux de prêt réaliste.
    item = conn.execute('''
        SELECT id, numero_inventaire, etat
        FROM inventaire
        WHERE actif = 1 AND etat = 'disponible'
        ORDER BY RANDOM()
        LIMIT 1
    ''').fetchone()

    if not item:
        item = conn.execute('''
            SELECT id, numero_inventaire, etat
            FROM inventaire
            WHERE actif = 1
            ORDER BY RANDOM()
            LIMIT 1
        ''').fetchone()

    if not item:
        return jsonify({
            'ok': False,
            'message': 'Aucun matériel actif trouvé pour simuler un scan.'
        }), 404

    return jsonify({
        'ok': True,
        'id': item['id'],
        'code': item['numero_inventaire'],
        'numero_inventaire': item['numero_inventaire'],
        'etat': item['etat'],
    })



@bp.route('/api/scan')
def api_scan():
    """API JSON : recherche un code scanné (numéro inventaire ou série) et renvoie l'URL de redirection.
    Supporte le nettoyage de préfixe/suffixe configuré dans les réglages."""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'found': False, 'message': 'Aucun code fourni.'})

    # Nettoyage préfixe/suffixe configuré
    prefixe = get_setting('scanner_prefixe', '').strip()
    suffixe = get_setting('scanner_suffixe', '').strip()
    code_clean = code
    if prefixe and code_clean.startswith(prefixe):
        code_clean = code_clean[len(prefixe):]
    if suffixe and code_clean.endswith(suffixe):
        code_clean = code_clean[:-len(suffixe)]
    code_clean = code_clean.strip()

    if not code_clean:
        return jsonify({'found': False, 'message': f'Code vide après nettoyage (préfixe/suffixe).'})

    conn = get_app_db()
    # 1) Chercher dans l'inventaire par numéro d'inventaire ou numéro de série
    mat = conn.execute(
        'SELECT id, type_materiel, marque, modele, numero_inventaire, etat FROM inventaire '
        'WHERE actif = 1 AND (numero_inventaire = ? OR numero_serie = ?)',
        (code_clean, code_clean)
    ).fetchone()

    # Si pas trouvé avec le code nettoyé, essayer avec le code brut
    if not mat and code_clean != code:
        mat = conn.execute(
            'SELECT id, type_materiel, marque, modele, numero_inventaire, etat FROM inventaire '
            'WHERE actif = 1 AND (numero_inventaire = ? OR numero_serie = ?)',
            (code, code)
        ).fetchone()

    if not mat:
        return jsonify({
            'found': False,
            'message': f'Aucun matériel trouvé pour « {code_clean} ».',
            'code_original': code,
            'code_nettoye': code_clean
        })

    # 2) Vérifier l'état du matériel
    if mat['etat'] == 'hors_service':
        label = mat['type_materiel']
        if mat['marque']:
            label += f" {mat['marque']}"
        if mat['modele']:
            label += f" {mat['modele']}"
        return jsonify({
            'found': True,
            'type': 'hors_service',
            'message': f'{label} — Matériel hors service',
            'url': url_for('inventaire.modifier_materiel', mat_id=mat['id']),
        })

    # 3) Vérifier s'il y a un prêt actif pour ce matériel
    pret = conn.execute('''
        SELECT p.id, pe.nom, pe.prenom FROM prets p
        JOIN pret_materiels pm ON pm.pret_id = p.id
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE pm.materiel_id = ? AND p.retour_confirme = 0
        LIMIT 1
    ''', (mat['id'],)).fetchone()

    # Rétrocompat legacy materiel_id
    if not pret:
        pret = conn.execute(
            '''SELECT p.id, pe.nom, pe.prenom FROM prets p
               JOIN personnes pe ON p.personne_id = pe.id
               WHERE p.materiel_id = ? AND p.retour_confirme = 0 LIMIT 1''',
            (mat['id'],)
        ).fetchone()


    label = mat['type_materiel']
    if mat['marque']:
        label += f" {mat['marque']}"
    if mat['modele']:
        label += f" {mat['modele']}"

    if pret:
        return jsonify({
            'found': True,
            'type': 'pret_actif',
            'message': f'{label} — Prêté à {pret["prenom"]} {pret["nom"]}',
            'url': url_for('prets.detail_pret', pret_id=pret['id']),
        })
    else:
        return jsonify({
            'found': True,
            'type': 'materiel',
            'message': f'{label} — {mat["etat"].replace("_", " ").title()}',
            'url': url_for('inventaire.modifier_materiel', mat_id=mat['id']),
        })


@bp.route('/api/parcourir-dossiers')
@admin_required
def api_parcourir_dossiers():
    """API JSON : liste les sous-dossiers d'un chemin donné pour l'explorateur de fichiers."""
    chemin = request.args.get('path', '').strip()

    # Si pas de chemin fourni : lister les lecteurs (Windows) ou /
    if not chemin:
        if os.name == 'nt':
            # Lister les lecteurs disponibles sur Windows
            drives = []
            for letter in string.ascii_uppercase:
                drive = f'{letter}:\\';
                if os.path.exists(drive):
                    drives.append({'name': f'{letter}:', 'path': drive})
            return jsonify({'current': '', 'parent': '', 'folders': drives, 'is_root': True})
        else:
            chemin = '/'

    # Normaliser le chemin
    chemin = os.path.normpath(chemin)

    # Vérifier que le chemin existe et est un dossier
    if not os.path.isdir(chemin):
        return jsonify({'error': f'Le dossier « {chemin} » n\'existe pas.'}), 404

    # Calculer le parent
    parent = os.path.dirname(chemin)
    # Sur Windows, si on est à la racine d'un lecteur (ex. C:\), parent = vide pour revenir aux lecteurs
    is_drive_root = (os.name == 'nt' and os.path.splitdrive(chemin)[1] in ('\\', '/', ''))
    if is_drive_root:
        parent = ''  # Retour à la liste des lecteurs

    # Lister les sous-dossiers
    folders = []
    try:
        for entry in sorted(os.scandir(chemin), key=lambda e: e.name.lower()):
            if entry.is_dir():
                try:
                    # Vérifier qu'on a accès en lecture
                    os.listdir(entry.path)
                    folders.append({'name': entry.name, 'path': entry.path})
                except PermissionError:
                    # Dossier inaccessible, l'afficher grisé
                    folders.append({'name': entry.name, 'path': entry.path, 'locked': True})
    except PermissionError:
        return jsonify({'error': f'Accès refusé au dossier « {chemin} ».'}), 403

    return jsonify({
        'current': chemin,
        'parent': parent,
        'folders': folders,
        'is_root': False
    })

