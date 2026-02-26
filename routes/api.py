"""PretGo — Blueprint : api"""
from flask import Blueprint, jsonify, request, url_for
from database import get_setting
from utils import get_app_db, admin_required, allowed_file, get_categories_personnes, UPLOAD_FOLDER
import json
import os
import uuid

bp = Blueprint('api', __name__)

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


