"""PretGo — Blueprint : inventaire"""
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for
from database import get_setting
from utils import get_app_db, admin_required, query_inventaire, get_champs_personnalises, get_valeurs_champs, sauver_valeurs_champs
import csv
import io
import json

bp = Blueprint('inventaire', __name__)

@bp.route('/categories', methods=['GET', 'POST'])
@admin_required
def categories():
    conn = get_app_db()

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        prefixe = request.form.get('prefixe_inventaire', '').strip().upper()
        if nom:
            try:
                conn.execute('INSERT INTO categories_materiel (nom, prefixe_inventaire) VALUES (?, ?)', (nom, prefixe))
                conn.commit()
                flash(f'Catégorie « {nom} » ajoutée !', 'success')
            except Exception:
                flash('Cette catégorie existe déjà.', 'warning')
        return redirect(url_for('inventaire.categories'))

    categories_list = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()

    # Comptages de matériels par catégorie (pour la réaffectation à la suppression)
    comptages_mat = {}
    for cat in categories_list:
        comptages_mat[cat['nom']] = conn.execute(
            'SELECT COUNT(*) FROM inventaire WHERE actif = 1 AND type_materiel = ?',
            (cat['nom'],)
        ).fetchone()[0]

    return render_template('categories.html', categories=categories_list, comptages=comptages_mat)



@bp.route('/categories/prefixe/<int:cat_id>', methods=['POST'])
@admin_required
def modifier_prefixe_categorie(cat_id):
    conn = get_app_db()
    prefixe = request.form.get('prefixe_inventaire', '').strip().upper()
    conn.execute('UPDATE categories_materiel SET prefixe_inventaire = ? WHERE id = ?', (prefixe, cat_id))
    conn.commit()
    flash(f'Préfixe mis à jour : {prefixe if prefixe else "(aucun)"}', 'success')
    return redirect(url_for('inventaire.categories'))



@bp.route('/categories/supprimer/<int:cat_id>', methods=['POST'])
@admin_required
def supprimer_categorie(cat_id):
    conn = get_app_db()
    cat = conn.execute('SELECT nom FROM categories_materiel WHERE id = ?', (cat_id,)).fetchone()
    if not cat:
        flash('Catégorie introuvable.', 'danger')
        return redirect(url_for('inventaire.categories'))

    nb = conn.execute(
        'SELECT COUNT(*) FROM inventaire WHERE actif = 1 AND type_materiel = ?',
        (cat['nom'],)
    ).fetchone()[0]

    if nb > 0:
        # Vérifier si une catégorie de remplacement est fournie
        remplacement_id = request.form.get('remplacement_id', '').strip()
        if not remplacement_id:
            flash(f'Impossible de supprimer « {cat["nom"]} » : {nb} matériel(s) utilisent cette catégorie.', 'danger')
            return redirect(url_for('inventaire.categories'))
        # Récupérer le nom de la catégorie de remplacement
        cat_rempl = conn.execute(
            'SELECT nom FROM categories_materiel WHERE id = ?', (remplacement_id,)
        ).fetchone()
        if not cat_rempl or cat_rempl['nom'] == cat['nom']:
            flash('Catégorie de remplacement invalide.', 'danger')
            return redirect(url_for('inventaire.categories'))
        # Réaffecter tous les matériels
        conn.execute(
            'UPDATE inventaire SET type_materiel = ? WHERE actif = 1 AND type_materiel = ?',
            (cat_rempl['nom'], cat['nom'])
        )
        flash(f'{nb} matériel(s) réaffecté(s) vers « {cat_rempl["nom"]} ».', 'info')

    conn.execute('DELETE FROM categories_materiel WHERE id = ?', (cat_id,))
    conn.commit()
    flash(f'Catégorie « {cat["nom"]} » supprimée.', 'success')
    return redirect(url_for('inventaire.categories'))



@bp.route('/inventaire')
@admin_required
def inventaire():
    filtre_type = request.args.get('type', 'tous')
    recherche = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    par_page = 50
    items, types, comptages, total, total_pages, page = query_inventaire(
        filtre_type, recherche, page=page, par_page=par_page
    )

    # Colonnes dynamiques depuis les champs personnalisés "matériel"
    custom_columns = [dict(c) for c in get_champs_personnalises('materiel')]
    items_list = [dict(i) for i in items]
    if items_list and custom_columns:
        conn = get_app_db()
        ids = [i['id'] for i in items_list]
        placeholders = ','.join(['?'] * len(ids))
        values_rows = conn.execute(f'''
            SELECT vcp.entite_id, cp.nom_champ, vcp.valeur
            FROM valeurs_champs_personnalises vcp
            JOIN champs_personnalises cp ON cp.id = vcp.champ_id
            WHERE cp.entite = 'materiel' AND cp.actif = 1 AND vcp.entite_id IN ({placeholders})
        ''', ids).fetchall()

        values_map = {}
        for row in values_rows:
            entite_id = row['entite_id']
            if entite_id not in values_map:
                values_map[entite_id] = {}
            values_map[entite_id][row['nom_champ']] = row['valeur']

        for item in items_list:
            item['custom_values'] = values_map.get(item['id'], {})
    else:
        for item in items_list:
            item['custom_values'] = {}

    return render_template('inventaire.html', items=items_list, types=types,
                           filtre_type=filtre_type, recherche=recherche, comptages=comptages,
                           page=page, total_pages=total_pages, total=total,
                           custom_columns=custom_columns)



@bp.route('/inventaire/ajouter', methods=['GET', 'POST'])
@admin_required
def ajouter_materiel():
    form_data = {}
    if request.method == 'POST':
        type_mat = request.form.get('type_materiel', '').strip()
        marque = request.form.get('marque', '').strip()
        modele = request.form.get('modele', '').strip()
        numero_serie = request.form.get('numero_serie', '').strip()
        numero_inv = request.form.get('numero_inventaire', '').strip()
        os_val = request.form.get('systeme_exploitation', '').strip()
        notes = request.form.get('notes', '').strip()
        image = request.form.get('image', '').strip()

        # Conserver les valeurs saisies en cas d'erreur
        form_data = {
            'type_materiel': type_mat, 'marque': marque, 'modele': modele,
            'numero_serie': numero_serie, 'numero_inventaire': numero_inv,
            'systeme_exploitation': os_val, 'notes': notes, 'image': image
        }

        if not type_mat:
            flash('Le type de matériel est obligatoire.', 'danger')
        else:
            # Générer un numéro d'inventaire automatique si vide
            conn = get_app_db()
            if not numero_inv:
                # Récupérer le préfixe de la catégorie
                cat_row = conn.execute(
                    'SELECT prefixe_inventaire FROM categories_materiel WHERE nom = ?',
                    (type_mat,)
                ).fetchone()
                prefix = (cat_row['prefixe_inventaire'] if cat_row and cat_row['prefixe_inventaire'] else 'INV').upper()

                # Trouver le prochain numéro pour ce préfixe
                last = conn.execute(
                    "SELECT numero_inventaire FROM inventaire "
                    "WHERE numero_inventaire LIKE ? "
                    "ORDER BY id DESC LIMIT 1",
                    (f'{prefix}-%',)
                ).fetchone()
                if last:
                    try:
                        num = int(last['numero_inventaire'].split('-', 1)[1]) + 1
                    except (IndexError, ValueError):
                        num = conn.execute(
                            "SELECT COUNT(*) FROM inventaire WHERE numero_inventaire LIKE ?",
                            (f'{prefix}-%',)
                        ).fetchone()[0] + 1
                else:
                    num = 1
                numero_inv = f'{prefix}-{num:05d}'
                form_data['numero_inventaire'] = numero_inv

            try:
                conn.execute(
                    '''INSERT INTO inventaire (type_materiel, marque, modele,
                       numero_serie, numero_inventaire, systeme_exploitation, notes, image)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (type_mat, marque, modele, numero_serie, numero_inv, os_val, notes, image)
                )
                conn.commit()
                mat_id_new = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                # Sauvegarder les champs personnalisés
                sauver_valeurs_champs(mat_id_new, 'materiel', request.form)
                flash(f'Matériel {numero_inv} ajouté avec succès !', 'success')
                return redirect(url_for('inventaire.inventaire'))
            except Exception:
                flash(f'Le numéro d\'inventaire {numero_inv} existe déjà.', 'danger')

    conn = get_app_db()
    categories = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()
    champs_custom = get_champs_personnalises('materiel')
    return render_template('ajouter_materiel.html', categories=categories, form=form_data,
                           champs_custom=champs_custom)



@bp.route('/inventaire/modifier/<int:mat_id>', methods=['GET', 'POST'])
@admin_required
def modifier_materiel(mat_id):
    conn = get_app_db()

    if request.method == 'POST':
        materiel_existant = conn.execute('SELECT * FROM inventaire WHERE id = ?', (mat_id,)).fetchone()
        if not materiel_existant:
            flash('Matériel non trouvé.', 'danger')
            return redirect(url_for('inventaire.inventaire'))

        type_mat = request.form.get('type_materiel', '').strip()
        marque = request.form.get('marque', '').strip()
        modele = request.form.get('modele', '').strip()
        numero_serie = request.form.get('numero_serie', '').strip()
        numero_inv = request.form.get('numero_inventaire', '').strip()
        os_val = request.form.get('systeme_exploitation', materiel_existant['systeme_exploitation'] or '').strip()
        etat = request.form.get('etat', 'disponible')
        notes = request.form.get('notes', '').strip()
        image = request.form.get('image', '').strip()

        # Conserver le n° existant si le champ est vidé
        if not numero_inv:
            ancien = conn.execute('SELECT numero_inventaire FROM inventaire WHERE id = ?', (mat_id,)).fetchone()
            numero_inv = ancien['numero_inventaire'] if ancien else f'AUTO-{mat_id:04d}'

        conn.execute(
            '''UPDATE inventaire SET type_materiel=?, marque=?, modele=?,
               numero_serie=?, numero_inventaire=?, systeme_exploitation=?,
               etat=?, notes=?, image=? WHERE id=?''',
            (type_mat, marque, modele, numero_serie, numero_inv, os_val, etat, notes, image, mat_id)
        )
        conn.commit()
        # Sauvegarder les champs personnalisés
        sauver_valeurs_champs(mat_id, 'materiel', request.form)
        flash('Matériel modifié avec succès !', 'success')
        return redirect(url_for('inventaire.inventaire'))

    materiel = conn.execute('SELECT * FROM inventaire WHERE id = ?', (mat_id,)).fetchone()
    categories = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()
    if not materiel:
        flash('Matériel non trouvé.', 'danger')
        return redirect(url_for('inventaire.inventaire'))
    champs_custom = get_champs_personnalises('materiel')
    valeurs_custom = get_valeurs_champs(mat_id, 'materiel')
    return render_template('modifier_materiel.html', materiel=materiel, categories=categories,
                           champs_custom=champs_custom, valeurs_custom=valeurs_custom)



@bp.route('/inventaire/supprimer/<int:mat_id>', methods=['POST'])
@admin_required
def supprimer_materiel(mat_id):
    conn = get_app_db()
    # Vérifier si le matériel est actuellement prêté (legacy + pret_materiels)
    pret_actif = conn.execute(
        '''SELECT COUNT(*) FROM (
            SELECT id FROM prets WHERE materiel_id = ? AND retour_confirme = 0
            UNION
            SELECT p.id FROM prets p
            JOIN pret_materiels pm ON pm.pret_id = p.id
            WHERE pm.materiel_id = ? AND p.retour_confirme = 0
        )''',
        (mat_id, mat_id)
    ).fetchone()[0]
    if pret_actif > 0:
        flash('Impossible de supprimer ce matériel : il est actuellement prêté. Effectuez d\'abord le retour.', 'danger')
        return redirect(url_for('inventaire.inventaire'))
    conn.execute('UPDATE inventaire SET actif = 0 WHERE id = ?', (mat_id,))
    conn.commit()
    flash('Matériel supprimé.', 'success')
    return redirect(url_for('inventaire.inventaire'))



@bp.route('/inventaire/historique/<int:mat_id>')
@admin_required
def historique_materiel(mat_id):
    """Affiche l'historique chronologique de tous les prêts d'un équipement."""
    conn = get_app_db()

    materiel = conn.execute(
        'SELECT * FROM inventaire WHERE id = ?', (mat_id,)
    ).fetchone()

    if not materiel:
        flash('Matériel non trouvé.', 'danger')
        return redirect(url_for('inventaire.inventaire'))

    # Tous les prêts liés à ce matériel (legacy + pret_materiels), sans doublons
    prets = conn.execute('''
        SELECT DISTINCT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN pret_materiels pm ON pm.pret_id = p.id
        WHERE p.materiel_id = ? OR pm.materiel_id = ?
        ORDER BY p.date_emprunt DESC
    ''', (mat_id, mat_id)).fetchall()

    # Statistiques rapides
    stats = {
        'total': len(prets),
        'en_cours': sum(1 for p in prets if not p['retour_confirme']),
        'retournes': sum(1 for p in prets if p['retour_confirme']),
    }

    return render_template('historique_materiel.html',
                           materiel=materiel, prets=prets, stats=stats)



@bp.route('/inventaire/importer', methods=['GET', 'POST'])
@admin_required
def importer_inventaire():
    if request.method == 'POST':
        if 'fichier_csv' not in request.files:
            flash('Aucun fichier sélectionné.', 'danger')
            return redirect(request.url)

        fichier = request.files['fichier_csv']
        if not fichier.filename.lower().endswith('.csv'):
            flash('Veuillez sélectionner un fichier CSV.', 'danger')
            return redirect(request.url)

        try:
            contenu = fichier.read().decode('utf-8-sig')
            first_line = contenu.split('\n')[0]
            delimiter = ';' if ';' in first_line else (',' if ',' in first_line else '\t')

            lecteur = csv.DictReader(io.StringIO(contenu), delimiter=delimiter)
            conn = get_app_db()
            ajoutes = 0
            doublons = 0

            # Charger les catégories de matériel existantes pour la normalisation
            categories_mat = [row['nom'] for row in conn.execute(
                'SELECT nom FROM categories_materiel ORDER BY nom'
            ).fetchall()]
            # Construire un index insensible à la casse
            cat_lower_map = {c.lower(): c for c in categories_mat}

            for ligne in lecteur:
                num_inv = (ligne.get('numero_inventaire') or ligne.get('Numero inventaire')
                           or ligne.get('N° inventaire') or '').strip()
                if not num_inv or num_inv.startswith('#'):
                    continue

                type_mat = (ligne.get('type_materiel') or ligne.get('Type')
                            or ligne.get('type') or '').strip()
                marque = (ligne.get('marque') or ligne.get('Marque') or '').strip()
                modele = (ligne.get('modele') or ligne.get('Modele') or ligne.get('Modèle') or '').strip()
                num_serie = (ligne.get('numero_serie') or ligne.get('Numero serie')
                             or ligne.get('N° série') or '').strip()
                os_val = (ligne.get('systeme_exploitation') or ligne.get('OS')
                          or ligne.get('Système') or '').strip()
                notes = (ligne.get('notes') or ligne.get('Notes') or '').strip()

                # Normaliser le type par rapport aux catégories existantes
                type_lower = type_mat.lower()
                if type_lower in cat_lower_map:
                    type_mat = cat_lower_map[type_lower]

                existant = conn.execute(
                    'SELECT id FROM inventaire WHERE numero_inventaire = ?', (num_inv,)
                ).fetchone()

                if existant:
                    doublons += 1
                else:
                    conn.execute(
                        '''INSERT INTO inventaire (type_materiel, marque, modele,
                           numero_serie, numero_inventaire, systeme_exploitation, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (type_mat if type_mat else (categories_mat[0] if categories_mat else 'Autre'),
                         marque, modele, num_serie, num_inv, os_val, notes)
                    )
                    ajoutes += 1

            conn.commit()
            msg = f'{ajoutes} matériel(s) importé(s).'
            if doublons:
                msg += f' {doublons} doublon(s) ignoré(s).'
            flash(msg, 'success')
            return redirect(url_for('inventaire.inventaire'))

        except Exception as e:
            flash(f"Erreur lors de l'import : {str(e)}", 'danger')
            return redirect(request.url)

    return render_template('importer_inventaire.html')



@bp.route('/telecharger-gabarit-inventaire')
def telecharger_gabarit_inventaire():
    """Gabarit CSV dynamique basé sur les catégories de matériel configurées."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(['type_materiel', 'marque', 'modele', 'numero_serie',
                     'numero_inventaire', 'systeme_exploitation', 'notes'])

    # Charger les catégories de matériel depuis la base
    conn = get_app_db()
    categories = conn.execute('SELECT nom, prefixe_inventaire FROM categories_materiel ORDER BY nom').fetchall()

    # Exemples connus par type (marque, modele, n° serie, OS)
    exemples = {
        'Ordinateur':       [('HP', 'EliteBook 840', 'SN-HP-001', 'Windows 11'),
                             ('Dell', 'Latitude 5520', 'SN-DELL-002', 'Windows 11')],
        'Vidéoprojecteur':  [('Epson', 'EB-W52', '', '')],
        'Casque audio':     [('Logitech', 'H390', '', '')],
    }

    for idx, cat in enumerate(categories):
        nom = cat['nom']
        prefixe = cat['prefixe_inventaire'] or 'INV'
        libelle_section = nom.upper()
        writer.writerow([f'# ══════ {libelle_section} ══════', '', '', '', '', '', ''])

        # Numéro d'inventaire de base pour cette section
        base_num = (idx + 1) * 100 + 1

        # Écrire les exemples connus ou un exemple générique
        lignes_exemple = exemples.get(nom, [])
        if lignes_exemple:
            for i, (marque, modele, ns, os_val) in enumerate(lignes_exemple):
                num_inv = f'{prefixe}-{base_num + i:05d}'
                writer.writerow([nom, marque, modele, ns, num_inv, os_val, ''])
            start = len(lignes_exemple)
        else:
            num_inv = f'{prefixe}-{base_num:05d}'
            writer.writerow([nom, '', '', '', num_inv, '', ''])
            start = 1

        # Lignes vides pré-remplies avec la catégorie
        for i in range(start, start + 5):
            num_inv = f'{prefixe}-{base_num + i:05d}'
            writer.writerow([nom, '', '', '', num_inv, '', ''])

    output.seek(0)
    bom = '\ufeff'
    return Response(
        bom + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=gabarit_inventaire.csv'}
    )



@bp.route('/etiquettes')
def etiquettes():
    """Page centralisée d'impression d'étiquettes (accessible sans admin)."""
    filtre_type = request.args.get('type', 'tous')
    recherche = request.args.get('q', '').strip()
    items, types, comptages = query_inventaire(filtre_type, recherche)

    zebra_active = get_setting('impression_zebra_active', '0') == '1'
    return render_template('etiquettes.html', items=items, types=types,
                           filtre_type=filtre_type, recherche=recherche,
                           comptages=comptages, zebra_active=zebra_active)



@bp.route('/imprimer/etiquettes')
def imprimer_etiquettes():
    """Page d'étiquettes imprimable (PDF via navigateur)."""
    ids = request.args.get('ids', '')
    if not ids:
        flash('Aucun matériel sélectionné pour l\'impression.', 'warning')
        return redirect(url_for('inventaire.etiquettes'))

    id_list = [i.strip() for i in ids.split(',') if i.strip().isdigit()]
    if not id_list:
        flash('Identifiants invalides.', 'danger')
        return redirect(url_for('inventaire.etiquettes'))

    conn = get_app_db()
    placeholders = ','.join(['?'] * len(id_list))
    items = conn.execute(f'''
        SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie
        FROM inventaire WHERE id IN ({placeholders})
    ''', id_list).fetchall()

    # Paramètres d'impression
    largeur = int(get_setting('impression_etiquette_largeur', '51'))
    hauteur = int(get_setting('impression_etiquette_hauteur', '25'))
    colonnes = int(get_setting('impression_colonnes', '4'))
    lignes = int(get_setting('impression_lignes', '11'))
    police = get_setting('impression_police', 'Arial')
    taille_barcode = int(get_setting('impression_taille_barcode', '60'))
    taille_texte = int(get_setting('impression_taille_texte', '8'))
    taille_sous_texte = int(get_setting('impression_taille_sous_texte', '6'))
    texte_libre = get_setting('impression_texte_libre', '')

    return render_template('imprimer_etiquettes.html',
                           items=[dict(i) for i in items],
                           largeur=largeur, hauteur=hauteur,
                           colonnes=colonnes, lignes=lignes,
                           police=police, taille_barcode=taille_barcode,
                           taille_texte=taille_texte,
                           taille_sous_texte=taille_sous_texte,
                           texte_libre=texte_libre)



@bp.route('/imprimer/zebra', methods=['POST'])
def imprimer_zebra():
    """Imprimer des étiquettes via imprimante Zebra (port série)."""
    try:
        if get_setting('impression_zebra_active', '0') != '1':
            return jsonify({'success': False, 'error': 'L\'impression Zebra n\'est pas activée.'}), 400

        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get('ids', [])
        ids = [int(value) for value in raw_ids if str(value).isdigit()]
        if not ids:
            return jsonify({'success': False, 'error': 'Aucun matériel sélectionné.'}), 400

        conn = get_app_db()
        placeholders = ','.join(['?'] * len(ids))
        items = conn.execute(f'''
            SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie
            FROM inventaire WHERE id IN ({placeholders})
        ''', ids).fetchall()

        if not items:
            return jsonify({'success': False, 'error': 'Matériels non trouvés.'}), 404

        port = get_setting('impression_port', 'COM3')
        baud = int(get_setting('impression_baud', '38400'))
        tearoff = get_setting('impression_tearoff', '018')
        legacy_zpl = '^XA^CI27^FO15,20^BY2^BCN,80,N^FD{numero_inventaire}^FS^FO25,130^A0,50,28^FD{numero_inventaire}^FS^XZ'
        previous_default_zpl = '^XA^CI27^FO20,15^BY2^BCN,65,N,N,N^FD{numero_inventaire}^FS^FO20,90^A0N,26,24^FD{numero_inventaire}^FS^FO20,120^A0N,20,18^FD{type} {marque} {modele}^FS^FO20,145^A0N,16,16^FD{texte_libre}^FS^XZ'
        zpl_default = '^XA^CI27^FO20,15^BY2^BCN,{barcode_height},N,N,N^FD{numero_inventaire}^FS^FO20,{y_num}^A0N,{text_height},{text_width}^FD{numero_inventaire}^FS^FO20,{y_sub}^A0N,{sub_height},{sub_width}^FD{type} {marque} {modele}^FS^FO20,{y_free}^A0N,{free_height},{free_width}^FD{texte_libre}^FS^XZ'
        zpl_template = get_setting('impression_zpl_template', zpl_default)
        if zpl_template in (legacy_zpl, previous_default_zpl):
            zpl_template = zpl_default
        texte_libre = get_setting('impression_texte_libre', '')

        # Tailles dynamiques issues des sliders (impact Zebra uniquement)
        barcode_slider = max(20, min(80, int(get_setting('impression_taille_barcode', '60'))))
        texte_slider = max(4, min(16, int(get_setting('impression_taille_texte', '8'))))
        sous_slider = max(3, min(12, int(get_setting('impression_taille_sous_texte', '6'))))

        barcode_height = int(round(barcode_slider * 1.1))
        text_height = texte_slider * 3
        text_width = max(12, int(text_height * 0.9))
        sub_height = sous_slider * 3
        sub_width = max(10, int(sub_height * 0.9))
        free_height = max(10, (sous_slider - 1) * 3)
        free_width = max(10, int(free_height * 0.9))

        y_num = 20 + barcode_height + 8
        y_sub = y_num + text_height + 6
        y_free = y_sub + sub_height + 4

        zpl_payloads = []
        zpl_commands = []
        for item in items:
            zpl = zpl_template.format(
                numero_inventaire=item['numero_inventaire'] or '',
                type=item['type_materiel'] or '',
                marque=item['marque'] or '',
                modele=item['modele'] or '',
                numero_serie=item['numero_serie'] or '',
                texte_libre=texte_libre,
                barcode_height=barcode_height,
                text_height=text_height,
                text_width=text_width,
                sub_height=sub_height,
                sub_width=sub_width,
                free_height=free_height,
                free_width=free_width,
                y_num=y_num,
                y_sub=y_sub,
                y_free=y_free
            )
            zpl_payloads.append(zpl)
            zpl_commands.append(f'~TA{tearoff}{zpl}')

        methode = get_setting('impression_zebra_methode', 'serial')

        if methode == 'http':
            zebra_url = get_setting('impression_zebra_url', 'http://localhost:9100').strip()
            if zebra_url and '://' not in zebra_url:
                zebra_url = f'http://{zebra_url}'
            if zebra_url.endswith('/'):
                zebra_url = zebra_url.rstrip('/')
            try:
                import urllib.request
                import urllib.error
                import urllib.parse

                parsed_url = urllib.parse.urlparse(zebra_url)
                web_z_print_mode = parsed_url.path.lower().endswith('/print-code.php') or parsed_url.path.lower() == '/print-code.php'

                diagnostics = []
                for index, item in enumerate(items, start=1):
                    if web_z_print_mode:
                        post_data = urllib.parse.urlencode({
                            'qtt': 1,
                            'code': zpl_payloads[index - 1]
                        }).encode('utf-8')
                        req = urllib.request.Request(zebra_url, data=post_data, method='POST')
                        req.add_header('Content-Type', 'application/x-www-form-urlencoded; charset=utf-8')
                    else:
                        zpl = zpl_commands[index - 1]
                        req = urllib.request.Request(zebra_url, data=zpl.encode('utf-8'), method='POST')
                        req.add_header('Content-Type', 'text/plain; charset=utf-8')

                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            status = getattr(resp, 'status', None) or resp.getcode()
                            content_type = resp.headers.get('Content-Type', '')
                            preview = resp.read(220).decode('utf-8', errors='replace').strip().replace('\n', ' ')
                    except urllib.error.HTTPError as e:
                        status = e.code
                        content_type = (e.headers.get('Content-Type', '') if e.headers else '')
                        try:
                            preview = e.read(220).decode('utf-8', errors='replace').strip().replace('\n', ' ')
                        except Exception:
                            preview = str(e)

                    diagnostics.append({
                        'label': index,
                        'status': status,
                        'content_type': content_type,
                        'preview': preview[:160]
                    })

                    if web_z_print_mode:
                        if status >= 400:
                            return jsonify({
                                'success': False,
                                'error': f'Erreur Web-Z-Print : HTTP {status}.',
                                'diagnostic': {
                                    'mode': 'Web-Z-Print (print-code.php)',
                                    'url': zebra_url,
                                    'label': index,
                                    'status': status,
                                    'content_type': content_type,
                                    'preview': preview[:160]
                                }
                            }), 502
                        continue

                    content_type_lower = (content_type or '').lower()
                    preview_lower = (preview or '').lower()
                    is_html = 'text/html' in content_type_lower or '<!doctype html' in preview_lower or '<html' in preview_lower

                    if is_html:
                        return jsonify({
                            'success': False,
                            'error': 'L\'URL HTTP configurée renvoie du HTML, pas un endpoint ZPL.',
                            'diagnostic': {
                                'mode': 'HTTP ZPL',
                                'url': zebra_url,
                                'label': index,
                                'status': status,
                                'content_type': content_type,
                                'preview': preview[:160]
                            }
                        }), 502

                    if status >= 400:
                        return jsonify({
                            'success': False,
                            'error': f'Erreur HTTP Zebra : {status}.',
                            'diagnostic': {
                                'mode': 'HTTP ZPL',
                                'url': zebra_url,
                                'label': index,
                                'status': status,
                                'content_type': content_type,
                                'preview': preview[:160]
                            }
                        }), 502

                mode_label = 'Web-Z-Print (print-code.php)' if web_z_print_mode else 'HTTP ZPL'
                return jsonify({'success': True,
                                'message': f'{len(items)} étiquette(s) envoyée(s) via HTTP ({mode_label}).',
                                'diagnostic': {
                                    'url': zebra_url,
                                    'sent_count': len(zpl_commands),
                                    'mode': mode_label,
                                    'last_status': diagnostics[-1]['status'] if diagnostics else None,
                                    'last_content_type': diagnostics[-1]['content_type'] if diagnostics else ''
                                }})
            except Exception as e:
                return jsonify({'success': False, 'error': f'Erreur HTTP : {str(e)}'}), 500

        try:
            from zebra_print import envoyer_zpl
            resultat = envoyer_zpl(port, baud, zpl_commands)
            if resultat['success']:
                return jsonify({'success': True,
                                'message': f'{len(items)} étiquette(s) envoyée(s) à l\'imprimante.'})
            return jsonify({'success': False, 'error': resultat['error']}), 500
        except ImportError:
            return jsonify({'success': False,
                            'error': 'Module pyserial non installé. Exécutez : pip install pyserial'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    except KeyError as e:
        return jsonify({'success': False,
                        'error': f'Template ZPL invalide : variable manquante {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'Erreur serveur : {str(e)}'}), 500



@bp.route('/lieux', methods=['GET', 'POST'])
@admin_required
def gestion_lieux():
    conn = get_app_db()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'ajouter':
            nom = request.form.get('nom', '').strip()
            if nom:
                try:
                    conn.execute('INSERT INTO lieux (nom) VALUES (?)', (nom,))
                    conn.commit()
                    flash(f'Lieu « {nom} » ajouté.', 'success')
                except Exception:
                    flash('Ce lieu existe déjà.', 'danger')
        elif action == 'modifier':
            lieu_id = request.form.get('lieu_id')
            nom = request.form.get('nom', '').strip()
            if lieu_id and nom:
                conn.execute('UPDATE lieux SET nom = ? WHERE id = ?', (nom, lieu_id))
                conn.commit()
                flash('Lieu modifié.', 'success')
        elif action == 'supprimer':
            lieu_id = request.form.get('lieu_id')
            if lieu_id:
                # Vérifier s'il est utilisé
                nb = conn.execute('SELECT COUNT(*) FROM prets WHERE lieu_id = ?', (lieu_id,)).fetchone()[0]
                if nb > 0:
                    conn.execute('UPDATE lieux SET actif = 0 WHERE id = ?', (lieu_id,))
                    flash('Lieu masqué (utilisé dans des prêts existants).', 'warning')
                else:
                    conn.execute('DELETE FROM lieux WHERE id = ?', (lieu_id,))
                    flash('Lieu supprimé.', 'success')
                conn.commit()
        return redirect(url_for('inventaire.gestion_lieux'))

    lieux = conn.execute('SELECT * FROM lieux ORDER BY actif DESC, nom').fetchall()
    return render_template('lieux.html', lieux=lieux)


