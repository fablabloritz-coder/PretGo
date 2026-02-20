"""
==============================================
  PRETGO — Gestion de Prêt de Matériel
  Application Flask avec base SQLite locale
==============================================
Pour lancer : python app.py
Puis ouvrir http://localhost:5000
"""

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, Response, session, g, send_file
)
from database import (
    get_db, init_db, reset_db, get_setting, set_setting,
    hash_password, verify_password, generate_recovery_code,
    DATABASE_PATH, DATA_DIR, DOCUMENTS_DIR, BACKUP_DIR, RECOVERY_CODE_PATH
)
from datetime import datetime, timedelta
from functools import wraps
import csv
import io
import json
import os
import re
import shutil
import unicodedata
import uuid
import zipfile
from werkzeug.utils import secure_filename

# Dossier d'upload pour les images de matériel
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'materiel')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'gestion_prets_materiel_secret_2024')


# Initialiser la base de données au démarrage avec affichage d'erreur explicite
with app.app_context():
    try:
        init_db()
    except Exception as e:
        import traceback
        print("\n[ERREUR INIT_DB]", e)
        traceback.print_exc()
        raise


# ============================================================
#  FILTRES JINJA PERSONNALISÉS
# ============================================================

@app.template_filter('format_date')
def format_date(value):
    """Formate une date en format français lisible."""
    if not value:
        return ''
    try:
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%d/%m/%Y à %H:%M')
    except Exception:
        return value


@app.template_filter('format_date_court')
def format_date_court(value):
    """Formate une date en format français court (JJ/MM/AAAA)."""
    if not value:
        return '—'
    try:
        dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return str(value)[:10]


@app.template_filter('format_heure')
def format_heure(value):
    """Extrait l'heure d'une date (HH:MM)."""
    if not value:
        return ''
    try:
        dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%H:%M')
    except Exception:
        return str(value)[11:16]


@app.template_filter('jours_ecoules')
def jours_ecoules(date_str):
    """Calcule le nombre de jours écoulés depuis une date."""
    if not date_str:
        return 0
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        delta = datetime.now() - dt
        return delta.days
    except Exception:
        return 0


def get_categories_personnes():
    """Récupère les catégories de personnes depuis la base."""
    conn = get_db()
    cats = conn.execute(
        'SELECT * FROM categories_personnes WHERE actif = 1 ORDER BY ordre, libelle'
    ).fetchall()
    conn.close()
    return cats


@app.template_filter('label_categorie')
def label_categorie(value):
    """Renvoie le libellé lisible d'une catégorie (via le cache du context_processor)."""
    cats = getattr(g, '_cats_personnes_cache', None)
    if cats is None:
        try:
            cats_list = get_categories_personnes()
            cats = {c['cle']: dict(c) for c in cats_list}
        except Exception:
            cats = {}
        g._cats_personnes_cache = cats
    cat = cats.get(value)
    if cat:
        return cat['libelle']
    return value.replace('_', ' ').title() if value else value


@app.template_filter('style_categorie')
def style_categorie(value):
    """Renvoie le style CSS inline pour un badge de catégorie."""
    cats = getattr(g, '_cats_personnes_cache', None)
    if cats is None:
        try:
            cats_list = get_categories_personnes()
            cats = {c['cle']: dict(c) for c in cats_list}
        except Exception:
            cats = {}
        g._cats_personnes_cache = cats
    cat = cats.get(value)
    if cat:
        return f"background-color:{cat['couleur_bg']};color:{cat['couleur_text']}"
    return 'background-color:#f1f3f4;color:#5f6368'


@app.template_filter('format_duree')
def format_duree(pret):
    """Formate la durée d'un prêt en texte lisible."""
    if not pret:
        return 'Durée par défaut'
    # Prêt de type "fin de journée"
    type_duree = pret['type_duree'] if pret['type_duree'] else None
    if type_duree == 'fin_journee':
        heure_fin = get_setting('heure_fin_journee', '17:45')
        return f'Fin de journée ({heure_fin.replace(":", "h")})'
    heures = pret['duree_pret_heures'] if pret['duree_pret_heures'] else None
    jours = pret['duree_pret_jours'] if pret['duree_pret_jours'] else None
    if heures is not None:
        if heures < 24:
            h = int(heures)
            m = int((heures - h) * 60)
            if m > 0:
                return f'{h}h{m:02d}'
            return f'{h} heure(s)'
        else:
            j = heures / 24
            if j == int(j):
                return f'{int(j)} jour(s)'
            return f'{j:.1f} jour(s)'
    elif jours is not None:
        return f'{jours} jour(s)'
    return 'Durée par défaut'


@app.template_filter('retour_theorique')
def retour_theorique_filter(pret):
    """Calcule la date de retour théorique."""
    if not pret or not pret['date_emprunt']:
        return ''
    try:
        dt = datetime.strptime(pret['date_emprunt'], '%Y-%m-%d %H:%M:%S')
        heures = pret['duree_pret_heures'] if pret['duree_pret_heures'] else None
        jours = pret['duree_pret_jours'] if pret['duree_pret_jours'] else None
        if heures is not None:
            retour = dt + timedelta(hours=heures)
        elif jours is not None:
            retour = dt + timedelta(days=jours)
        else:
            duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
            unite = get_setting('duree_alerte_unite', 'jours')
            if unite == 'heures':
                retour = dt + timedelta(hours=duree_defaut)
            else:
                retour = dt + timedelta(days=duree_defaut)
        return retour.strftime('%d/%m/%Y à %H:%M')
    except Exception:
        return ''


def calcul_depassement_heures(date_emprunt_str, duree_heures, duree_jours,
                              _duree_defaut=None, _unite_defaut=None):
    """Calcule le dépassement en heures. Retourne (est_depasse, heures_depassement).

    Les paramètres optionnels _duree_defaut et _unite_defaut permettent d'éviter
    des appels répétés à get_setting() quand la fonction est appelée en boucle.
    """
    try:
        dt = datetime.strptime(date_emprunt_str, '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        duree_defaut = _duree_defaut if _duree_defaut is not None else float(get_setting('duree_alerte_defaut', '7'))
        unite_defaut = _unite_defaut if _unite_defaut is not None else get_setting('duree_alerte_unite', 'jours')

        if duree_heures is not None:
            retour_theorique = dt + timedelta(hours=duree_heures)
        elif duree_jours is not None:
            retour_theorique = dt + timedelta(days=duree_jours)
        else:
            if unite_defaut == 'heures':
                retour_theorique = dt + timedelta(hours=duree_defaut)
            else:
                retour_theorique = dt + timedelta(days=duree_defaut)

        if now > retour_theorique:
            delta = now - retour_theorique
            total_h = delta.total_seconds() / 3600
            return True, total_h
        return False, 0
    except Exception:
        return False, 0


@app.context_processor
def utility_processor():
    """Variables disponibles dans tous les templates."""
    nb_alertes = 0
    try:
        conn = get_db()
        prets_actifs = conn.execute(
            'SELECT date_emprunt, duree_pret_jours, duree_pret_heures FROM prets WHERE retour_confirme = 0'
        ).fetchall()
        duree_def = float(get_setting('duree_alerte_defaut', '7'))
        unite_def = get_setting('duree_alerte_unite', 'jours')
        for p in prets_actifs:
            depasse, _ = calcul_depassement_heures(
                p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
                _duree_defaut=duree_def, _unite_defaut=unite_def
            )
            if depasse:
                nb_alertes += 1
        conn.close()
    except Exception:
        pass

    # Vérifier si admin connecté
    is_admin = bool(session.get('admin_logged_in'))

    # Charger les catégories de personnes pour tous les templates
    # et alimenter le cache g pour les filtres Jinja
    try:
        cats_list = get_categories_personnes()
        cats_personnes = {c['cle']: dict(c) for c in cats_list}
    except Exception:
        cats_personnes = {}
    g._cats_personnes_cache = cats_personnes

    return {
        'now': datetime.now,
        'nb_alertes': nb_alertes,
        'is_admin': is_admin,
        'cats_personnes': cats_personnes,
        'mode_scanner': get_setting('mode_scanner', 'les_deux'),
        'calcul_depassement_heures': calcul_depassement_heures,
    }


# ============================================================
#  DÉCORATEUR ADMIN
# ============================================================

def admin_required(f):
    """Décorateur pour protéger les routes administrateur."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Accès réservé à l\'administrateur. Veuillez vous connecter.', 'warning')
            return redirect(url_for('admin_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================
#  TABLEAU DE BORD
# ============================================================

@app.route('/')
def index():
    conn = get_db()

    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt DESC
    ''').fetchall()

    stats = {
        'actifs': len(prets_actifs),
        'retournes': conn.execute(
            'SELECT COUNT(*) FROM prets WHERE retour_confirme = 1'
        ).fetchone()[0],
        'personnes': conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1'
        ).fetchone()[0],
    }

    derniers_retours = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 1
        ORDER BY p.date_retour DESC
        LIMIT 5
    ''').fetchall()

    conn.close()
    return render_template(
        'index.html',
        prets_actifs=prets_actifs,
        stats=stats,
        derniers_retours=derniers_retours
    )


# ============================================================
#  NOUVEAU PRÊT
# ============================================================

@app.route('/nouveau-pret', methods=['GET', 'POST'])
def nouveau_pret():
    conn = get_db()

    if request.method == 'POST':
        personne_id = request.form.get('personne_id')
        notes = request.form.get('notes', '').strip()
        lieu_id = request.form.get('lieu_id', '').strip() or None

        # ── Récupération des items (multi-matériel) ──
        items_desc = request.form.getlist('items_description[]')
        items_mat = request.form.getlist('items_materiel_id[]')

        # Nettoyer les items vides
        items = []
        for i in range(len(items_desc)):
            desc = items_desc[i].strip() if i < len(items_desc) else ''
            mat_id = items_mat[i].strip() if i < len(items_mat) else ''
            if desc:
                items.append((desc, int(mat_id) if mat_id else None))

        # ── Gestion de la durée (heures ou jours) ──
        duree_type = request.form.get('duree_type', 'defaut')
        duree_pret_jours = None
        duree_pret_heures = None

        if duree_type == 'heures':
            h = request.form.get('duree_heures', '').strip()
            if h:
                try:
                    duree_pret_heures = float(h)
                except ValueError:
                    pass
        elif duree_type == 'jours':
            j = request.form.get('duree_jours', '').strip()
            if j:
                try:
                    duree_pret_jours = int(j)
                except ValueError:
                    pass
        elif duree_type == 'fin_journee':
            heure_fin = get_setting('heure_fin_journee', '17:45')
            h_fin, m_fin = (int(x) for x in heure_fin.split(':'))
            now = datetime.now()
            fin_journee = now.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
            if fin_journee > now:
                delta = (fin_journee - now).total_seconds() / 3600
                duree_pret_heures = round(delta, 2)
            else:
                duree_pret_heures = 0.5

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
        else:
            # Construire le descriptif combiné
            descriptif = ' + '.join(desc for desc, _ in items)
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            cursor = conn.execute(
                '''INSERT INTO prets (personne_id, descriptif_objets, date_emprunt,
                   notes, duree_pret_jours, duree_pret_heures, type_duree, lieu_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (personne_id, descriptif, now, notes, duree_pret_jours, duree_pret_heures, duree_type, lieu_id)
            )
            pret_id = cursor.lastrowid

            # Insérer chaque item dans pret_materiels
            for desc, mat_id in items:
                conn.execute(
                    'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                    (pret_id, mat_id, desc)
                )
                if mat_id:
                    conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mat_id,))

            conn.commit()
            flash('Prêt enregistré avec succès !', 'success')
            conn.close()
            return redirect(url_for('index'))

    personnes = conn.execute(
        'SELECT * FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    categories = conn.execute(
        'SELECT * FROM categories_materiel ORDER BY nom'
    ).fetchall()
    inventaire = conn.execute(
        "SELECT * FROM inventaire WHERE actif = 1 AND etat = 'disponible' ORDER BY type_materiel, numero_inventaire"
    ).fetchall()
    lieux = conn.execute(
        'SELECT * FROM lieux WHERE actif = 1 ORDER BY nom'
    ).fetchall()

    conn.close()
    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    return render_template(
        'nouveau_pret.html',
        personnes=personnes,
        categories=categories,
        inventaire=inventaire,
        lieux=lieux,
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux')
    )


# ============================================================
#  RETOUR DE MATÉRIEL
# ============================================================

@app.route('/retour')
def retour():
    conn = get_db()
    recherche = request.args.get('q', '').strip()

    if recherche:
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE p.retour_confirme = 0
            AND (pe.nom LIKE ? OR pe.prenom LIKE ? OR p.descriptif_objets LIKE ?)
            ORDER BY p.date_emprunt DESC
        ''', (f'%{recherche}%', f'%{recherche}%', f'%{recherche}%')).fetchall()
    else:
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE p.retour_confirme = 0
            ORDER BY p.date_emprunt DESC
        ''').fetchall()

    conn.close()
    return render_template('retour.html', prets=prets, recherche=recherche)


@app.route('/retour/<int:pret_id>', methods=['POST'])
def confirmer_retour(pret_id):
    conn = get_db()
    signature = request.form.get('signature', '')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Récupérer le materiel_id legacy avant de confirmer le retour
    pret = conn.execute('SELECT materiel_id FROM prets WHERE id = ?', (pret_id,)).fetchone()

    conn.execute(
        'UPDATE prets SET date_retour = ?, retour_confirme = 1, signature_retour = ? WHERE id = ?',
        (now, signature, pret_id)
    )
    # Libérer les matériels liés via pret_materiels (multi-matériel)
    mats = conn.execute(
        'SELECT materiel_id FROM pret_materiels WHERE pret_id = ? AND materiel_id IS NOT NULL',
        (pret_id,)
    ).fetchall()
    for m in mats:
        conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (m['materiel_id'],))
    # Rétrocompat : ancien champ materiel_id sur prets
    if pret and pret['materiel_id']:
        conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (pret['materiel_id'],))
    conn.commit()
    conn.close()
    flash('Retour confirmé avec succès !', 'success')
    return redirect(url_for('retour'))


@app.route('/retour/masse', methods=['POST'])
def retour_masse():
    """Confirme le retour de plusieurs prêts en une seule action."""
    pret_ids = request.form.getlist('pret_ids')
    if not pret_ids:
        flash('Aucun prêt sélectionné.', 'warning')
        return redirect(url_for('retour'))

    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    nb = 0
    for pid in pret_ids:
        if not pid.isdigit():
            continue
        pid = int(pid)
        pret = conn.execute(
            'SELECT materiel_id FROM prets WHERE id = ? AND retour_confirme = 0', (pid,)
        ).fetchone()
        if not pret:
            continue
        conn.execute(
            'UPDATE prets SET date_retour = ?, retour_confirme = 1, signature_retour = ? WHERE id = ?',
            (now, '', pid)
        )
        # Libérer matériels liés (multi-matériel)
        mats = conn.execute(
            'SELECT materiel_id FROM pret_materiels WHERE pret_id = ? AND materiel_id IS NOT NULL',
            (pid,)
        ).fetchall()
        for m in mats:
            conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (m['materiel_id'],))
        # Rétrocompat : ancien champ materiel_id
        if pret['materiel_id']:
            conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (pret['materiel_id'],))
        nb += 1
    conn.commit()
    conn.close()
    if nb:
        flash(f'{nb} retour(s) confirmé(s) avec succès !', 'success')
    else:
        flash('Aucun retour effectué.', 'warning')
    return redirect(url_for('retour'))


# ============================================================
#  RECHERCHE
# ============================================================

@app.route('/recherche')
def recherche():
    conn = get_db()
    q = request.args.get('q', '').strip()
    filtre_statut = request.args.get('statut', 'tous')
    resultats = []

    if q:
        query = '''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE (pe.nom LIKE ? OR pe.prenom LIKE ? OR p.descriptif_objets LIKE ? OR pe.classe LIKE ?)
        '''
        params = [f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%']

        if filtre_statut == 'actifs':
            query += ' AND p.retour_confirme = 0'
        elif filtre_statut == 'retournes':
            query += ' AND p.retour_confirme = 1'

        query += ' ORDER BY p.date_emprunt DESC'
        resultats = conn.execute(query, params).fetchall()

    conn.close()
    return render_template('recherche.html', resultats=resultats, q=q, filtre_statut=filtre_statut)


# ============================================================
#  GESTION DES PERSONNES
# ============================================================

@app.route('/personnes')
@admin_required
def personnes():
    conn = get_db()
    filtre = request.args.get('categorie', 'tous')
    recherche = request.args.get('q', '').strip()

    query = 'SELECT * FROM personnes WHERE actif = 1'
    params = []

    if filtre != 'tous':
        if filtre == '_autres':
            # Catégories non répertoriées
            cats_connues = get_categories_personnes()
            cles = [c['cle'] for c in cats_connues]
            if cles:
                placeholders = ','.join('?' * len(cles))
                query += f' AND categorie NOT IN ({placeholders})'
                params.extend(cles)
        else:
            query += ' AND categorie = ?'
            params.append(filtre)

    if recherche:
        query += ' AND (nom LIKE ? OR prenom LIKE ? OR classe LIKE ?)'
        params.extend([f'%{recherche}%', f'%{recherche}%', f'%{recherche}%'])

    query += ' ORDER BY categorie, nom, prenom'
    personnes_list = conn.execute(query, params).fetchall()

    # Comptages par catégorie (dynamique depuis la base)
    comptages = {}
    cats = get_categories_personnes()
    for cat in cats:
        comptages[cat['cle']] = conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
        ).fetchone()[0]
    # Comptage aussi des catégories non répertoriées
    cles_connues = [c['cle'] for c in cats]
    if cles_connues:
        placeholders = ','.join('?' * len(cles_connues))
        autres = conn.execute(
            f'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie NOT IN ({placeholders})',
            cles_connues
        ).fetchone()[0]
    else:
        autres = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]
    if autres > 0:
        comptages['_autres'] = autres
    comptages['total'] = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]

    conn.close()
    return render_template(
        'personnes.html',
        personnes=personnes_list,
        filtre=filtre,
        recherche=recherche,
        comptages=comptages,
        cats_list=cats
    )


@app.route('/personnes/ajouter', methods=['GET', 'POST'])
@admin_required
def ajouter_personne():
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip().upper()
        prenom = request.form.get('prenom', '').strip().title()
        categorie = request.form.get('categorie', '')
        classe = request.form.get('classe', '').strip()

        if not nom or not prenom or not categorie:
            flash('Veuillez remplir tous les champs obligatoires.', 'danger')
        else:
            conn = get_db()
            conn.execute(
                'INSERT INTO personnes (nom, prenom, categorie, classe) VALUES (?, ?, ?, ?)',
                (nom, prenom, categorie, classe)
            )
            conn.commit()
            conn.close()
            flash(f'{prenom} {nom} a été ajouté(e) avec succès !', 'success')
            return redirect(url_for('personnes'))

    cats = get_categories_personnes()
    return render_template('ajouter_personne.html', cats_personnes=cats)


@app.route('/personnes/modifier/<int:personne_id>', methods=['GET', 'POST'])
@admin_required
def modifier_personne(personne_id):
    conn = get_db()

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip().upper()
        prenom = request.form.get('prenom', '').strip().title()
        categorie = request.form.get('categorie', '')
        classe = request.form.get('classe', '').strip()

        if not nom or not prenom or not categorie:
            flash('Veuillez remplir tous les champs obligatoires.', 'danger')
        else:
            conn.execute(
                'UPDATE personnes SET nom=?, prenom=?, categorie=?, classe=? WHERE id=?',
                (nom, prenom, categorie, classe, personne_id)
            )
            conn.commit()
            flash('Personne modifiée avec succès !', 'success')
            conn.close()
            return redirect(url_for('personnes'))

    personne = conn.execute('SELECT * FROM personnes WHERE id = ?', (personne_id,)).fetchone()
    conn.close()

    if not personne:
        flash('Personne non trouvée.', 'danger')
        return redirect(url_for('personnes'))

    return render_template('modifier_personne.html', personne=personne,
                           cats_personnes=get_categories_personnes())


@app.route('/personnes/supprimer/<int:personne_id>', methods=['POST'])
@admin_required
def supprimer_personne(personne_id):
    conn = get_db()

    # Vérifier si la personne a des prêts en cours (non retournés)
    prets_actifs = conn.execute(
        'SELECT COUNT(*) FROM prets WHERE personne_id = ? AND retour_confirme = 0',
        (personne_id,)
    ).fetchone()[0]

    if prets_actifs > 0:
        personne = conn.execute(
            'SELECT nom, prenom FROM personnes WHERE id = ?', (personne_id,)
        ).fetchone()
        flash(
            f'Impossible de supprimer {personne["prenom"]} {personne["nom"]} : '
            f'{prets_actifs} prêt(s) en cours. Effectuez d\'abord le retour du matériel.',
            'danger'
        )
        conn.close()
        return redirect(url_for('personnes'))

    conn.execute('UPDATE personnes SET actif = 0 WHERE id = ?', (personne_id,))
    conn.commit()
    conn.close()
    flash('Personne supprimée avec succès.', 'success')
    return redirect(url_for('personnes'))


# ============================================================
#  IMPORT CSV
# ============================================================

@app.route('/personnes/importer', methods=['GET', 'POST'])
@admin_required
def importer_personnes():
    if request.method == 'POST':
        if 'fichier_csv' not in request.files:
            flash('Aucun fichier sélectionné.', 'danger')
            return redirect(request.url)

        fichier = request.files['fichier_csv']
        if fichier.filename == '':
            flash('Aucun fichier sélectionné.', 'danger')
            return redirect(request.url)

        if not fichier.filename.lower().endswith('.csv'):
            flash('Veuillez sélectionner un fichier CSV (.csv).', 'danger')
            return redirect(request.url)

        mode = request.form.get('mode', 'ajouter')  # 'ajouter' ou 'synchroniser'

        try:
            contenu = fichier.read().decode('utf-8-sig')

            # Détection automatique du délimiteur
            first_line = contenu.split('\n')[0]
            if ';' in first_line:
                delimiter = ';'
            elif '\t' in first_line:
                delimiter = '\t'
            else:
                delimiter = ','

            lecteur = csv.DictReader(io.StringIO(contenu), delimiter=delimiter)

            conn = get_db()
            ajoutes = 0
            mis_a_jour = 0
            ignores = 0
            ids_importes = set()

            # Pré-charger les catégories (hors boucle pour performance)
            cats_db = get_categories_personnes()
            cats_by_cle = {c['cle']: c['cle'] for c in cats_db}
            cats_by_libelle = {c['libelle'].lower(): c['cle'] for c in cats_db}
            synonymes = {
                'élève': 'eleve', 'etudiant': 'eleve', 'étudiant': 'eleve', 'elève': 'eleve',
                'professeur': 'enseignant', 'prof': 'enseignant',
            }

            for ligne in lecteur:
                # Accepter différents noms de colonnes
                nom = (ligne.get('nom') or ligne.get('Nom') or '').strip().upper()
                prenom = (ligne.get('prenom') or ligne.get('Prenom')
                          or ligne.get('Prénom') or ligne.get('prénom') or '').strip().title()
                categorie = (ligne.get('categorie') or ligne.get('Categorie')
                             or ligne.get('Catégorie') or ligne.get('catégorie') or '').strip().lower()
                classe = (ligne.get('classe') or ligne.get('Classe') or '').strip()

                # Ignorer les lignes vides ou les séparateurs de catégorie
                if not nom or not prenom:
                    continue
                if nom.startswith('#'):
                    continue

                if categorie in cats_by_cle:
                    categorie = cats_by_cle[categorie]
                elif categorie in cats_by_libelle:
                    categorie = cats_by_libelle[categorie]
                elif categorie in synonymes:
                    categorie = synonymes[categorie]
                elif not categorie:
                    categorie = 'non_enseignant'
                # Sinon : garder la valeur telle quelle (catégorie personnalisée)

                # Vérifier les doublons (même nom + prénom + catégorie)
                existant = conn.execute(
                    'SELECT id FROM personnes WHERE nom = ? AND prenom = ? AND categorie = ?',
                    (nom, prenom, categorie)
                ).fetchone()

                if existant:
                    ids_importes.add(existant['id'])
                    if mode == 'synchroniser':
                        # Mettre à jour la classe et réactiver si désactivée
                        conn.execute(
                            'UPDATE personnes SET classe = ?, actif = 1 WHERE id = ?',
                            (classe, existant['id'])
                        )
                        mis_a_jour += 1
                    else:
                        ignores += 1
                else:
                    # Vérifier aussi par nom + prénom seul (personne existante
                    # qui aurait changé de catégorie)
                    existant_autre = conn.execute(
                        'SELECT id FROM personnes WHERE nom = ? AND prenom = ? AND actif = 1',
                        (nom, prenom)
                    ).fetchone()

                    if existant_autre and mode == 'synchroniser':
                        # Mettre à jour catégorie + classe
                        conn.execute(
                            'UPDATE personnes SET categorie = ?, classe = ?, actif = 1 WHERE id = ?',
                            (categorie, classe, existant_autre['id'])
                        )
                        ids_importes.add(existant_autre['id'])
                        mis_a_jour += 1
                    else:
                        cursor = conn.execute(
                            'INSERT INTO personnes (nom, prenom, categorie, classe) VALUES (?, ?, ?, ?)',
                            (nom, prenom, categorie, classe)
                        )
                        ids_importes.add(cursor.lastrowid)
                        ajoutes += 1

            # Mode synchroniser : désactiver les absents (sauf prêts en cours)
            desactives = 0
            proteges = 0
            if mode == 'synchroniser' and ids_importes:
                placeholders = ','.join('?' * len(ids_importes))
                absentes = conn.execute(f'''
                    SELECT p.id, p.nom, p.prenom,
                           (SELECT COUNT(*) FROM prets
                            WHERE personne_id = p.id AND retour_confirme = 0) as prets_actifs
                    FROM personnes p
                    WHERE p.actif = 1 AND p.id NOT IN ({placeholders})
                ''', list(ids_importes)).fetchall()

                noms_proteges = []
                for absente in absentes:
                    if absente['prets_actifs'] > 0:
                        proteges += 1
                        noms_proteges.append(
                            f"{absente['prenom']} {absente['nom']} "
                            f"({absente['prets_actifs']} prêt(s))"
                        )
                    else:
                        conn.execute(
                            'UPDATE personnes SET actif = 0 WHERE id = ?',
                            (absente['id'],)
                        )
                        desactives += 1

                if noms_proteges:
                    flash(
                        f'⚠️ Personne(s) absente(s) du fichier mais conservée(s) '
                        f'car elles ont des prêts en cours : {", ".join(noms_proteges)}',
                        'warning'
                    )

            conn.commit()
            conn.close()

            # Construire le message récapitulatif
            parts = []
            if ajoutes > 0:
                parts.append(f'{ajoutes} ajoutée(s)')
            if mis_a_jour > 0:
                parts.append(f'{mis_a_jour} mise(s) à jour')
            if ignores > 0:
                parts.append(f'{ignores} doublon(s) ignoré(s)')
            if desactives > 0:
                parts.append(f'{desactives} désactivée(s) car absente(s) du fichier')

            if parts:
                flash(f'Import terminé : {", ".join(parts)}.', 'success')
            else:
                flash('Aucune donnée importée. Vérifiez le format du fichier.', 'warning')

            return redirect(url_for('personnes'))

        except Exception as e:
            flash(f"Erreur lors de l'import : {str(e)}", 'danger')
            return redirect(request.url)

    return render_template('importer.html')


@app.route('/telecharger-gabarit')
def telecharger_gabarit():
    """Générer un gabarit CSV dynamique basé sur les catégories de personnes configurées."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(['nom', 'prenom', 'categorie', 'classe'])

    # Charger les catégories de personnes depuis la base
    cats = get_categories_personnes()

    # Exemples pré-remplis par catégorie (1 exemple + lignes vides)
    exemples = {
        'eleve':         [('DUPONT', 'Marie', '3A'), ('MARTIN', 'Lucas', '4B')],
        'enseignant':    [('DUBOIS', 'Sophie', ''), ('LAURENT', 'Philippe', '')],
        'agent':         [('GARCIA', 'Antonio', '')],
        'non_enseignant':[('GIRARD', 'Marc', '')],
    }

    for cat in cats:
        cle = cat['cle']
        libelle = cat['libelle'].upper()
        writer.writerow([f'# ══════ {libelle} ══════', '', '', ''])

        # Écrire les exemples connus ou un exemple générique
        lignes_exemple = exemples.get(cle, [])
        if lignes_exemple:
            for nom, prenom, classe in lignes_exemple:
                writer.writerow([nom, prenom, cle, classe])
        else:
            writer.writerow(['NOM', 'Prenom', cle, ''])

        # Lignes vides pré-remplies avec la catégorie
        nb_vides = 18 if cle == 'eleve' else 8 if cle == 'enseignant' else 5
        for _ in range(nb_vides):
            writer.writerow(['', '', cle, ''])

    output.seek(0)
    bom = '\ufeff'
    return Response(
        bom + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename=gabarit_personnes.csv'
        }
    )


# ============================================================
#  HISTORIQUE
# ============================================================

@app.route('/historique')
def historique():
    conn = get_db()
    page = request.args.get('page', 1, type=int)
    par_page = 25
    offset = (page - 1) * par_page

    total = conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]

    prets = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        ORDER BY p.date_emprunt DESC
        LIMIT ? OFFSET ?
    ''', (par_page, offset)).fetchall()

    conn.close()

    total_pages = max(1, (total + par_page - 1) // par_page)

    return render_template(
        'historique.html',
        prets=prets,
        page=page,
        total_pages=total_pages,
        total=total
    )


# ============================================================
#  EXPORT CSV
# ============================================================

@app.route('/export')
@admin_required
def export_page():
    """Page de sélection des exports CSV."""
    conn = get_db()
    counts = {
        'personnes': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0],
        'prets_en_cours': conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 0').fetchone()[0],
        'historique': conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0],
        'inventaire': conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0],
    }
    # Compter alertes
    prets_actifs = conn.execute(
        'SELECT date_emprunt, duree_pret_jours, duree_pret_heures FROM prets WHERE retour_confirme = 0'
    ).fetchall()
    nb_alertes = 0
    duree_def = float(get_setting('duree_alerte_defaut', '7'))
    unite_def = get_setting('duree_alerte_unite', 'jours')
    for p in prets_actifs:
        depasse, _ = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_def
        )
        if depasse:
            nb_alertes += 1
    counts['alertes'] = nb_alertes
    conn.close()
    return render_template('export.html', counts=counts)


def _csv_response(output, filename_prefix):
    """Helper : crée une Response CSV avec BOM UTF-8 pour Excel."""
    output.seek(0)
    bom = '\ufeff'
    return Response(
        bom + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition':
                f'attachment; filename={filename_prefix}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        }
    )


@app.route('/export-prets')
@admin_required
def export_prets():
    conn = get_db()
    prets = conn.execute('''
        SELECT pe.nom, pe.prenom, pe.categorie, pe.classe,
               p.descriptif_objets, p.date_emprunt, p.date_retour,
               CASE WHEN p.retour_confirme = 1 THEN 'Oui' ELSE 'Non' END as retourne,
               p.notes, l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        ORDER BY p.date_emprunt DESC
    ''').fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Date retour', 'Retourné', 'Lieu', 'Notes'])

    for pret in prets:
        writer.writerow([
            pret['nom'], pret['prenom'], pret['categorie'], pret['classe'],
            pret['descriptif_objets'], pret['date_emprunt'],
            pret['date_retour'] or '', pret['retourne'],
            pret['lieu_nom'] or '', pret['notes'] or ''
        ])

    return _csv_response(output, 'export_historique_prets')


@app.route('/export-prets-en-cours')
@admin_required
def export_prets_en_cours():
    """Exporter uniquement les prêts non retournés."""
    conn = get_db()
    prets = conn.execute('''
        SELECT pe.nom, pe.prenom, pe.categorie, pe.classe,
               p.descriptif_objets, p.date_emprunt, p.notes, l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt DESC
    ''').fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Lieu', 'Notes'])

    for pret in prets:
        writer.writerow([
            pret['nom'], pret['prenom'], pret['categorie'], pret['classe'],
            pret['descriptif_objets'], pret['date_emprunt'],
            pret['lieu_nom'] or '', pret['notes'] or ''
        ])

    return _csv_response(output, 'export_prets_en_cours')


@app.route('/export-alertes')
@admin_required
def export_alertes():
    """Exporter les prêts en dépassement (alertes)."""
    conn = get_db()
    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    duree_def = float(get_setting('duree_alerte_defaut', '7'))
    unite_def = get_setting('duree_alerte_unite', 'jours')
    alertes_list = []
    for pret in prets_actifs:
        depasse, heures_dep = calcul_depassement_heures(
            pret['date_emprunt'], pret['duree_pret_heures'], pret['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_def
        )
        if depasse:
            if heures_dep < 24:
                dep_texte = f"{int(heures_dep)}h{int((heures_dep % 1) * 60):02d}"
            else:
                jours_dep = heures_dep / 24
                dep_texte = f"{int(jours_dep)} jour(s)"
            alertes_list.append({**dict(pret), 'depassement_texte': dep_texte})
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Dépassement', 'Lieu', 'Notes'])

    for a in alertes_list:
        writer.writerow([
            a['nom'], a['prenom'], a['categorie'], a['classe'],
            a['descriptif_objets'], a['date_emprunt'],
            a['depassement_texte'], a.get('lieu_nom', '') or '', a['notes'] or ''
        ])

    return _csv_response(output, 'export_alertes')


@app.route('/export-personnes')
@admin_required
def export_personnes():
    conn = get_db()
    personnes = conn.execute(
        'SELECT nom, prenom, categorie, classe FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe'])

    for p in personnes:
        writer.writerow([p['nom'], p['prenom'], p['categorie'], p['classe']])

    return _csv_response(output, 'export_personnes')


@app.route('/export-inventaire')
@admin_required
def export_inventaire():
    """Exporter l'inventaire matériel."""
    conn = get_db()
    items = conn.execute(
        'SELECT type_materiel, marque, modele, numero_serie, numero_inventaire, '
        'systeme_exploitation, etat, notes FROM inventaire WHERE actif = 1 '
        'ORDER BY type_materiel, numero_inventaire'
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Type', 'Marque', 'Modèle', 'N° série', 'N° inventaire',
                     'Système d\'exploitation', 'État', 'Notes'])

    for item in items:
        writer.writerow([
            item['type_materiel'], item['marque'], item['modele'],
            item['numero_serie'], item['numero_inventaire'],
            item['systeme_exploitation'], item['etat'], item['notes'] or ''
        ])

    return _csv_response(output, 'export_inventaire')


# ============================================================
#  API JSON (autocomplétion)
# ============================================================

@app.route('/api/personnes')
def api_personnes():
    conn = get_db()
    q = request.args.get('q', '').strip()

    if q:
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe
            FROM personnes
            WHERE actif = 1 AND (nom LIKE ? OR prenom LIKE ? OR classe LIKE ?)
            ORDER BY nom, prenom
            LIMIT 20
        ''', (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe
            FROM personnes
            WHERE actif = 1
            ORDER BY nom, prenom
        ''').fetchall()

    conn.close()

    # Enrichir avec le libellé de catégorie pour le front-end
    cats = {c['cle']: c['libelle'] for c in get_categories_personnes()}
    result = []
    for p in personnes:
        d = dict(p)
        d['categorie_label'] = cats.get(d['categorie'], d['categorie'].replace('_', ' ').title())
        result.append(d)
    return jsonify(result)


# ============================================================
#  CATÉGORIES DE MATÉRIEL (admin uniquement)
# ============================================================

@app.route('/categories', methods=['GET', 'POST'])
@admin_required
def categories():
    conn = get_db()

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
        conn.close()
        return redirect(url_for('categories'))

    categories_list = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()

    # Comptages de matériels par catégorie (pour la réaffectation à la suppression)
    comptages_mat = {}
    for cat in categories_list:
        comptages_mat[cat['nom']] = conn.execute(
            'SELECT COUNT(*) FROM inventaire WHERE actif = 1 AND type_materiel = ?',
            (cat['nom'],)
        ).fetchone()[0]

    conn.close()
    return render_template('categories.html', categories=categories_list, comptages=comptages_mat)


@app.route('/categories/prefixe/<int:cat_id>', methods=['POST'])
@admin_required
def modifier_prefixe_categorie(cat_id):
    conn = get_db()
    prefixe = request.form.get('prefixe_inventaire', '').strip().upper()
    conn.execute('UPDATE categories_materiel SET prefixe_inventaire = ? WHERE id = ?', (prefixe, cat_id))
    conn.commit()
    conn.close()
    flash(f'Préfixe mis à jour : {prefixe if prefixe else "(aucun)"}', 'success')
    return redirect(url_for('categories'))


@app.route('/categories/supprimer/<int:cat_id>', methods=['POST'])
@admin_required
def supprimer_categorie(cat_id):
    conn = get_db()
    cat = conn.execute('SELECT nom FROM categories_materiel WHERE id = ?', (cat_id,)).fetchone()
    if not cat:
        conn.close()
        flash('Catégorie introuvable.', 'danger')
        return redirect(url_for('categories'))

    nb = conn.execute(
        'SELECT COUNT(*) FROM inventaire WHERE actif = 1 AND type_materiel = ?',
        (cat['nom'],)
    ).fetchone()[0]

    if nb > 0:
        # Vérifier si une catégorie de remplacement est fournie
        remplacement_id = request.form.get('remplacement_id', '').strip()
        if not remplacement_id:
            flash(f'Impossible de supprimer « {cat["nom"]} » : {nb} matériel(s) utilisent cette catégorie.', 'danger')
            conn.close()
            return redirect(url_for('categories'))
        # Récupérer le nom de la catégorie de remplacement
        cat_rempl = conn.execute(
            'SELECT nom FROM categories_materiel WHERE id = ?', (remplacement_id,)
        ).fetchone()
        if not cat_rempl or cat_rempl['nom'] == cat['nom']:
            flash('Catégorie de remplacement invalide.', 'danger')
            conn.close()
            return redirect(url_for('categories'))
        # Réaffecter tous les matériels
        conn.execute(
            'UPDATE inventaire SET type_materiel = ? WHERE actif = 1 AND type_materiel = ?',
            (cat_rempl['nom'], cat['nom'])
        )
        flash(f'{nb} matériel(s) réaffecté(s) vers « {cat_rempl["nom"]} ».', 'info')

    conn.execute('DELETE FROM categories_materiel WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()
    flash(f'Catégorie « {cat["nom"]} » supprimée.', 'success')
    return redirect(url_for('categories'))


# ============================================================
#  CATÉGORIES DE PERSONNES (admin uniquement)
# ============================================================

@app.route('/categories-personnes', methods=['GET', 'POST'])
@admin_required
def categories_personnes_admin():
    conn = get_db()

    if request.method == 'POST':
        libelle = request.form.get('libelle', '').strip()
        icone = request.form.get('icone', 'bi-person').strip()
        couleur_bg = request.form.get('couleur_bg', '#f1f3f4').strip()
        couleur_text = request.form.get('couleur_text', '#5f6368').strip()

        if not libelle:
            flash('Le libellé est obligatoire.', 'danger')
        else:
            # Générer une clé à partir du libellé
            cle = libelle.lower().strip()
            cle = unicodedata.normalize('NFD', cle)
            cle = cle.encode('ascii', 'ignore').decode('ascii')
            cle = cle.replace(' ', '_').replace("'", '').replace('-', '_')
            # Ne garder que alphanumériques et underscores
            cle = ''.join(c for c in cle if c.isalnum() or c == '_')

            # Déterminer l'ordre (après le dernier)
            max_ordre = conn.execute('SELECT MAX(ordre) FROM categories_personnes').fetchone()[0] or 0

            try:
                conn.execute(
                    '''INSERT INTO categories_personnes (cle, libelle, icone, couleur_bg, couleur_text, ordre)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (cle, libelle, icone, couleur_bg, couleur_text, max_ordre + 1)
                )
                conn.commit()
                flash(f'Catégorie « {libelle} » ajoutée !', 'success')
            except Exception:
                flash('Cette catégorie existe déjà.', 'warning')
        conn.close()
        return redirect(url_for('categories_personnes_admin'))

    cats = conn.execute('SELECT * FROM categories_personnes ORDER BY ordre, libelle').fetchall()

    # Compter le nombre de personnes par catégorie
    comptages = {}
    for cat in cats:
        comptages[cat['cle']] = conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
        ).fetchone()[0]

    conn.close()
    return render_template('categories_personnes.html', categories=cats, comptages=comptages)


@app.route('/categories-personnes/modifier/<int:cat_id>', methods=['POST'])
@admin_required
def modifier_categorie_personne(cat_id):
    conn = get_db()
    libelle = request.form.get('libelle', '').strip()
    icone = request.form.get('icone', 'bi-person').strip()
    couleur_bg = request.form.get('couleur_bg', '#f1f3f4').strip()
    couleur_text = request.form.get('couleur_text', '#5f6368').strip()

    if libelle:
        conn.execute(
            '''UPDATE categories_personnes SET libelle=?, icone=?, couleur_bg=?, couleur_text=?
               WHERE id=?''',
            (libelle, icone, couleur_bg, couleur_text, cat_id)
        )
        conn.commit()
        flash('Catégorie modifiée.', 'success')
    conn.close()
    return redirect(url_for('categories_personnes_admin'))


@app.route('/categories-personnes/supprimer/<int:cat_id>', methods=['POST'])
@admin_required
def supprimer_categorie_personne(cat_id):
    conn = get_db()
    cat = conn.execute('SELECT cle, libelle FROM categories_personnes WHERE id = ?', (cat_id,)).fetchone()
    if not cat:
        conn.close()
        flash('Catégorie introuvable.', 'danger')
        return redirect(url_for('categories_personnes_admin'))

    nb = conn.execute(
        'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
    ).fetchone()[0]

    if nb > 0:
        # Vérifier si une catégorie de remplacement est fournie
        remplacement_id = request.form.get('remplacement_id', '').strip()
        if not remplacement_id:
            flash(f'Impossible de supprimer « {cat["libelle"]} » : {nb} personne(s) utilisent cette catégorie.', 'danger')
            conn.close()
            return redirect(url_for('categories_personnes_admin'))
        # Récupérer la clé de la catégorie de remplacement
        cat_rempl = conn.execute(
            'SELECT cle, libelle FROM categories_personnes WHERE id = ?', (remplacement_id,)
        ).fetchone()
        if not cat_rempl or cat_rempl['cle'] == cat['cle']:
            flash('Catégorie de remplacement invalide.', 'danger')
            conn.close()
            return redirect(url_for('categories_personnes_admin'))
        # Réaffecter toutes les personnes
        conn.execute(
            'UPDATE personnes SET categorie = ? WHERE actif = 1 AND categorie = ?',
            (cat_rempl['cle'], cat['cle'])
        )
        flash(f'{nb} personne(s) réaffectée(s) vers « {cat_rempl["libelle"]} ».', 'info')

    conn.execute('DELETE FROM categories_personnes WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()
    flash(f'Catégorie « {cat["libelle"]} » supprimée.', 'success')
    return redirect(url_for('categories_personnes_admin'))


# ============================================================
#  ALERTES — PRÊTS EN DÉPASSEMENT
# ============================================================

@app.route('/alertes')
def alertes():
    conn = get_db()

    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    duree_def = float(duree_defaut)

    alertes_list = []
    for pret in prets_actifs:
        depasse, heures_dep = calcul_depassement_heures(
            pret['date_emprunt'], pret['duree_pret_heures'], pret['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_defaut
        )
        if depasse:
            # Formater le dépassement lisiblement
            if heures_dep < 24:
                dep_texte = f"{int(heures_dep)}h{int((heures_dep % 1) * 60):02d}"
            else:
                jours_dep = heures_dep / 24
                dep_texte = f"{int(jours_dep)} jour(s)"

            alertes_list.append({
                'pret': pret,
                'depassement_heures': heures_dep,
                'depassement_texte': dep_texte,
            })

    conn.close()
    return render_template('alertes.html', alertes=alertes_list,
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut)


# ============================================================
#  ADMINISTRATION — CONNEXION
# ============================================================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action', 'login')

        if action == 'login':
            password = request.form.get('password', '')
            stored_hash = get_setting('admin_password')
            if stored_hash and verify_password(password, stored_hash):
                # Migration automatique : rehacher avec l'algorithme sécurisé
                if not stored_hash.startswith(('scrypt:', 'pbkdf2:')):
                    set_setting('admin_password', hash_password(password))
                session['admin_logged_in'] = True

                # Première connexion ? Forcer le changement de mot de passe
                if get_setting('password_changed', '0') == '0':
                    flash('Bienvenue ! Veuillez personnaliser votre mot de passe administrateur.', 'info')
                    return redirect(url_for('admin_setup_password'))

                flash('Connexion administrateur réussie.', 'success')
                next_url = request.args.get('next') or url_for('admin_dashboard')
                if not next_url.startswith('/') or next_url.startswith('//'):
                    next_url = url_for('admin_dashboard')
                return redirect(next_url)
            else:
                flash('Mot de passe incorrect.', 'danger')

        elif action == 'recovery':
            code = request.form.get('recovery_code', '').strip().upper()
            stored_hash = get_setting('recovery_code_hash')
            if stored_hash and verify_password(code, stored_hash):
                # Code valide → permettre de définir un nouveau mot de passe
                session['recovery_validated'] = True
                flash('Code de récupération valide. Définissez votre nouveau mot de passe.', 'success')
                return redirect(url_for('admin_reset_password'))
            else:
                flash('Code de récupération incorrect.', 'danger')

    return render_template('admin_login.html',
                           password_changed=get_setting('password_changed', '0'))


@app.route('/admin/reset-password', methods=['GET', 'POST'])
def admin_reset_password():
    if not session.get('recovery_validated'):
        flash('Accès non autorisé.', 'danger')
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        nouveau = request.form.get('nouveau_mdp', '')
        confirm = request.form.get('confirm_mdp', '')
        if len(nouveau) < 4:
            flash('Le nouveau mot de passe doit faire au moins 4 caractères.', 'danger')
        elif nouveau != confirm:
            flash('Les deux mots de passe ne correspondent pas.', 'danger')
        else:
            set_setting('admin_password', hash_password(nouveau))
            # Régénérer un nouveau code de récupération
            new_code = generate_recovery_code()
            set_setting('recovery_code_hash', hash_password(new_code))
            session.pop('recovery_validated', None)
            flash('Mot de passe réinitialisé. Un nouveau code de récupération a été généré dans le dossier data/.', 'success')
            return redirect(url_for('admin_login'))

    return render_template('admin_reset_password.html')


@app.route('/admin/setup-password', methods=['GET', 'POST'])
@admin_required
def admin_setup_password():
    """Page de première personnalisation du mot de passe (après connexion avec le MDP par défaut)."""
    # Si le mot de passe a déjà été changé, pas besoin d'être ici
    if get_setting('password_changed', '0') == '1':
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        nouveau = request.form.get('nouveau_mdp', '')
        confirm = request.form.get('confirm_mdp', '')

        if len(nouveau) < 4:
            flash('Le mot de passe doit faire au moins 4 caractères.', 'danger')
        elif nouveau != confirm:
            flash('Les deux mots de passe ne correspondent pas.', 'danger')
        else:
            # Enregistrer le nouveau mot de passe
            set_setting('admin_password', hash_password(nouveau))
            set_setting('password_changed', '1')
            # Générer le code de récupération unique pour cette installation
            new_code = generate_recovery_code()
            set_setting('recovery_code_hash', hash_password(new_code))
            flash('Mot de passe personnalisé avec succès ! Votre code de récupération unique a été généré dans le dossier data/.', 'success')
            return redirect(url_for('admin_dashboard'))

    return render_template('admin_setup_password.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Déconnexion administrateur.', 'info')
    return redirect(url_for('index'))


# ============================================================
#  ADMINISTRATION — TABLEAU DE BORD
# ============================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    stats = {
        'personnes': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0],
        'prets_actifs': conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 0').fetchone()[0],
        'prets_total': conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0],
        'categories': conn.execute('SELECT COUNT(*) FROM categories_materiel').fetchone()[0],
        'inventaire': conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0],
    }
    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    conn.close()
    return render_template('admin_dashboard.html', stats=stats,
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut)


# ============================================================
#  ADMINISTRATION — RÉGLAGES
# ============================================================

@app.route('/admin/reglages', methods=['GET', 'POST'])
@admin_required
def admin_reglages():
    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'duree_alerte':
            duree = request.form.get('duree_alerte_defaut', '7').strip()
            unite = request.form.get('duree_alerte_unite', 'jours')
            try:
                val = float(duree)
                if val > 0:
                    set_setting('duree_alerte_defaut', duree)
                    set_setting('duree_alerte_unite', unite)
                    label = 'heure(s)' if unite == 'heures' else 'jour(s)'
                    flash(f'Durée d\'alerte par défaut : {duree} {label}.', 'success')
                else:
                    flash('Veuillez entrer une valeur positive.', 'danger')
            except ValueError:
                flash('Veuillez entrer une valeur numérique valide.', 'danger')

        elif action == 'changer_mdp':
            ancien = request.form.get('ancien_mdp', '')
            nouveau = request.form.get('nouveau_mdp', '')
            confirm = request.form.get('confirm_mdp', '')

            stored_hash = get_setting('admin_password')
            if not verify_password(ancien, stored_hash):
                flash('L\'ancien mot de passe est incorrect.', 'danger')
            elif len(nouveau) < 4:
                flash('Le nouveau mot de passe doit faire au moins 4 caractères.', 'danger')
            elif nouveau != confirm:
                flash('Les deux mots de passe ne correspondent pas.', 'danger')
            else:
                set_setting('admin_password', hash_password(nouveau))
                # Régénérer le code de récupération à chaque changement de MDP
                new_code = generate_recovery_code()
                set_setting('recovery_code_hash', hash_password(new_code))
                flash('Mot de passe modifié. Un nouveau code de récupération a été généré dans data/.', 'success')

        elif action == 'impression':
            set_setting('impression_zebra_active', '1' if request.form.get('impression_zebra_active') else '0')
            set_setting('impression_zebra_methode', request.form.get('impression_zebra_methode', 'serial'))
            set_setting('impression_port', request.form.get('impression_port', 'COM3').strip())
            set_setting('impression_baud', request.form.get('impression_baud', '38400'))
            set_setting('impression_tearoff', request.form.get('impression_tearoff', '018').strip())
            set_setting('impression_zebra_url', request.form.get('impression_zebra_url', '').strip())
            set_setting('impression_zpl_template', request.form.get('impression_zpl_template', '').strip())
            set_setting('impression_etiquette_largeur', request.form.get('impression_etiquette_largeur', '51'))
            set_setting('impression_etiquette_hauteur', request.form.get('impression_etiquette_hauteur', '25'))
            set_setting('impression_colonnes', request.form.get('impression_colonnes', '4'))
            set_setting('impression_lignes', request.form.get('impression_lignes', '11'))
            set_setting('impression_police', request.form.get('impression_police', 'Arial'))
            set_setting('impression_taille_barcode', request.form.get('impression_taille_barcode', '60'))
            set_setting('impression_taille_texte', request.form.get('impression_taille_texte', '8'))
            set_setting('impression_taille_sous_texte', request.form.get('impression_taille_sous_texte', '6'))
            set_setting('impression_texte_libre', request.form.get('impression_texte_libre', '').strip())
            flash('Paramètres d\'impression enregistrés.', 'success')

        elif action == 'nom_etablissement':
            nom_etab = request.form.get('nom_etablissement', '').strip()
            set_setting('nom_etablissement', nom_etab)
            flash('Nom de l\'établissement enregistré.', 'success')

        elif action == 'mode_scanner':
            mode = request.form.get('mode_scanner', 'les_deux')
            if mode in ('webcam', 'douchette', 'les_deux'):
                set_setting('mode_scanner', mode)
                labels = {'webcam': 'Webcam uniquement', 'douchette': 'Douchette USB uniquement', 'les_deux': 'Webcam + Douchette'}
                flash(f'Mode de scan : {labels[mode]}.', 'success')
            else:
                flash('Mode de scanner invalide.', 'danger')

        elif action == 'heure_fin_journee':
            heure = request.form.get('heure_fin_journee', '17:45').strip()
            # Valider le format HH:MM
            if re.match(r'^\d{1,2}:\d{2}$', heure):
                h, m = heure.split(':')
                if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
                    set_setting('heure_fin_journee', heure)
                    flash(f'Heure de fin de journée : {heure.replace(":", "h")}.', 'success')
                else:
                    flash('Heure invalide.', 'danger')
            else:
                flash('Format d\'heure invalide (attendu HH:MM).', 'danger')

        return redirect(url_for('admin_reglages'))

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    zpl_default = '^XA^CI27^FO15,20^BY2^BCN,80,N^FD{numero_inventaire}^FS^FO25,130^A0,50,28^FD{numero_inventaire}^FS^XZ'
    return render_template('admin_reglages.html',
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut,
                           nom_etablissement=get_setting('nom_etablissement', ''),
                           imp_zebra_active=get_setting('impression_zebra_active', '0'),
                           imp_zebra_methode=get_setting('impression_zebra_methode', 'serial'),
                           imp_port=get_setting('impression_port', 'COM3'),
                           imp_baud=get_setting('impression_baud', '38400'),
                           imp_tearoff=get_setting('impression_tearoff', '018'),
                           imp_zebra_url=get_setting('impression_zebra_url', 'http://localhost:9100'),
                           imp_zpl_template=get_setting('impression_zpl_template', zpl_default),
                           imp_largeur=get_setting('impression_etiquette_largeur', '51'),
                           imp_hauteur=get_setting('impression_etiquette_hauteur', '25'),
                           imp_colonnes=get_setting('impression_colonnes', '4'),
                           imp_lignes=get_setting('impression_lignes', '11'),
                           imp_police=get_setting('impression_police', 'Arial'),
                           imp_taille_barcode=get_setting('impression_taille_barcode', '60'),
                           imp_taille_texte=get_setting('impression_taille_texte', '8'),
                           imp_taille_sous_texte=get_setting('impression_taille_sous_texte', '6'),
                           imp_texte_libre=get_setting('impression_texte_libre', ''),
                           mode_scanner=get_setting('mode_scanner', 'les_deux'),
                           heure_fin_journee=get_setting('heure_fin_journee', '17:45'))


# ============================================================
#  ADMINISTRATION — RÉINITIALISATION BDD
# ============================================================

@app.route('/admin/reset-db', methods=['POST'])
@admin_required
def admin_reset_db():
    confirmation = request.form.get('confirmation', '')
    if confirmation == 'REINITIALISER':
        reset_db()
        session.pop('admin_logged_in', None)
        flash('Base de données réinitialisée avec succès. Toutes les données ont été supprimées.', 'success')
        return redirect(url_for('index'))
    else:
        flash('La confirmation est incorrecte. Tapez REINITIALISER en majuscules.', 'danger')
        return redirect(url_for('admin_reglages'))


# ============================================================
#  ADMINISTRATION — GÉNÉRATION BASE DE DÉMONSTRATION
# ============================================================

@app.route('/admin/generer-demo', methods=['POST'])
@admin_required
def admin_generer_demo():
    """Génère des données de démonstration dynamiques, adaptées aux catégories configurées."""
    import random
    conn = get_db()

    # ══════════════════════════════════════════════════════════
    #  1. LECTURE DYNAMIQUE DES CATÉGORIES EXISTANTES
    # ══════════════════════════════════════════════════════════

    # ── Catégories de personnes ──
    cats_personnes = conn.execute(
        'SELECT cle, libelle FROM categories_personnes WHERE actif = 1 ORDER BY ordre'
    ).fetchall()
    cats_personnes_cles = [c['cle'] for c in cats_personnes]

    # ── Catégories de matériel (avec préfixes) ──
    cats_materiel = conn.execute(
        'SELECT id, nom, prefixe_inventaire FROM categories_materiel ORDER BY nom'
    ).fetchall()

    if not cats_personnes_cles:
        flash("Aucune catégorie de personnes active. Créez-en au moins une avant de générer la démo.", 'warning')
        return redirect(url_for('admin_dashboard'))
    if not cats_materiel:
        flash("Aucune catégorie de matériel. Créez-en au moins une avant de générer la démo.", 'warning')
        return redirect(url_for('admin_dashboard'))

    # ══════════════════════════════════════════════════════════
    #  2. PERSONNES DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    # Banque de noms réutilisable
    noms_banque = [
        ('Martin', 'Lucas'), ('Dubois', 'Emma'), ('Bernard', 'Léo'),
        ('Petit', 'Chloé'), ('Robert', 'Nathan'), ('Richard', 'Jade'),
        ('Moreau', 'Hugo'), ('Simon', 'Manon'), ('Laurent', 'Thomas'),
        ('Leroy', 'Camille'), ('Roux', 'Enzo'), ('David', 'Léa'),
        ('Bertrand', 'Mathis'), ('Morel', 'Sarah'), ('Dupont', 'Marie'),
        ('Lefebvre', 'Pierre'), ('Garcia', 'Sophie'), ('Fournier', 'Jean'),
        ('Girard', 'François'), ('Bonnet', 'Catherine'), ('Mercier', 'Julie'),
        ('Boyer', 'Antoine'), ('Blanc', 'Clara'), ('Guérin', 'Maxime'),
        ('Faure', 'Alice'), ('Rousseau', 'Arthur'), ('Fontaine', 'Inès'),
        ('Chevalier', 'Raphaël'), ('Robin', 'Louise'), ('Masson', 'Gabriel'),
    ]
    classes_demo = [
        '2nde 1', '2nde 3', '2nde 5', '1ère STI2D', '1ère G1', '1ère G2',
        'Tle S1', 'Tle S2', 'Tle STMG', 'Tle L', 'BTS SIO 1', 'BTS SIO 2',
    ]

    # Données spécifiques par clé de catégorie connue (avec classe ou non)
    cats_avec_classe = {'eleve', 'etudiant', 'stagiaire', 'apprenti'}

    # Répartition : première catégorie = 50 %, les autres se partagent le reste
    nb_total_personnes = min(len(noms_banque), 24)
    nb_par_cat = {}
    if len(cats_personnes_cles) == 1:
        nb_par_cat[cats_personnes_cles[0]] = nb_total_personnes
    else:
        nb_premiere = max(4, nb_total_personnes // 2)
        reste = nb_total_personnes - nb_premiere
        nb_par_cat[cats_personnes_cles[0]] = nb_premiere
        nb_autres = max(1, len(cats_personnes_cles) - 1)
        par_autre = max(2, reste // nb_autres)
        for cle in cats_personnes_cles[1:]:
            nb_par_cat[cle] = par_autre

    idx_nom = 0
    personnes_ids = []
    for cle in cats_personnes_cles:
        nb = nb_par_cat.get(cle, 2)
        a_classe = cle in cats_avec_classe
        for i in range(nb):
            if idx_nom >= len(noms_banque):
                break
            nom, prenom = noms_banque[idx_nom]
            classe = random.choice(classes_demo) if a_classe else ''
            try:
                cursor = conn.execute(
                    'INSERT INTO personnes (nom, prenom, categorie, classe, actif) VALUES (?, ?, ?, ?, 1)',
                    (nom, prenom, cle, classe)
                )
                personnes_ids.append(cursor.lastrowid)
            except Exception:
                personnes_ids.append(None)
            idx_nom += 1

    # ══════════════════════════════════════════════════════════
    #  3. MATÉRIELS DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    # Banque d'exemples de matériels par nom de catégorie connu
    exemples_materiels = {
        'informatique': [
            ('Dell', 'Latitude 5530', 'Windows 11'),
            ('Dell', 'Latitude 3520', 'Windows 10'),
            ('HP', 'ProBook 450 G9', 'Windows 11'),
            ('Lenovo', 'ThinkPad T14', 'Linux Ubuntu'),
            ('Apple', 'iPad 10e gen', 'iPadOS 17'),
            ('Samsung', 'Galaxy Tab S9', 'Android 14'),
        ],
        'audio/vidéo': [
            ('Epson', 'EB-992F', ''),
            ('BenQ', 'MH560', ''),
            ('Jabra', 'Evolve2 75', ''),
            ('Logitech', 'C920 HD Pro', ''),
        ],
        'réseau': [
            ('Cisco', 'Catalyst 2960', 'IOS 15'),
            ('TP-Link', 'Archer AX73', ''),
        ],
        'sport': [
            ('Decathlon', 'Chronomètre W500', ''),
            ('Garmin', 'Forerunner 55', ''),
        ],
        'livres': [
            ('Hachette', 'Manuel Maths Tle', ''),
            ('Nathan', 'Physique-Chimie 1ère', ''),
        ],
        'outils': [
            ('Fluke', 'T6-600', ''),
            ('Bosch', 'GSR 18V-28', ''),
        ],
        'fournitures': [
            ('Brother', 'HL-L2350DW', ''),
            ('Canon', 'PIXMA TS3350', ''),
        ],
    }

    # Exemples génériques pour catégories inconnues
    exemples_generiques = [
        ('Modèle A', 'Standard', ''),
        ('Modèle B', 'Premium', ''),
        ('Modèle C', 'Basique', ''),
    ]

    materiels_ids = []
    materiels_info = []  # (id, type_mat, marque, modele) pour les descriptifs de prêts

    for cat in cats_materiel:
        nom_cat = cat['nom']
        prefixe = (cat['prefixe_inventaire'] or 'INV').upper()

        # Trouver le prochain numéro d'inventaire pour ce préfixe
        last = conn.execute(
            "SELECT numero_inventaire FROM inventaire "
            "WHERE numero_inventaire LIKE ? ORDER BY id DESC LIMIT 1",
            (f'{prefixe}-%',)
        ).fetchone()
        if last:
            try:
                next_num = int(last['numero_inventaire'].split('-', 1)[1]) + 1
            except (IndexError, ValueError):
                next_num = conn.execute(
                    "SELECT COUNT(*) FROM inventaire WHERE numero_inventaire LIKE ?",
                    (f'{prefixe}-%',)
                ).fetchone()[0] + 1
        else:
            next_num = 1

        # Choisir les exemples adaptés à cette catégorie
        nom_lower = nom_cat.lower().strip()
        exemples = exemples_materiels.get(nom_lower, exemples_generiques)

        # Générer 2 à 4 matériels par catégorie (proportionnel)
        nb_items = min(len(exemples), max(2, 6 if nom_lower == 'informatique' else 3))
        items_choisis = exemples[:nb_items]

        sn_counter = 1
        for marque, modele, os_val in items_choisis:
            numero_inv = f'{prefixe}-{next_num:05d}'
            numero_serie = f'SN-{prefixe}-{sn_counter:03d}'
            try:
                cursor = conn.execute(
                    'INSERT INTO inventaire (type_materiel, marque, modele, numero_serie, '
                    'numero_inventaire, systeme_exploitation, etat, actif) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
                    (nom_cat, marque, modele, numero_serie, numero_inv, os_val, 'disponible')
                )
                mid = cursor.lastrowid
                materiels_ids.append(mid)
                materiels_info.append((mid, nom_cat, marque, modele))
            except Exception:
                materiels_ids.append(None)
            next_num += 1
            sn_counter += 1

    # ══════════════════════════════════════════════════════════
    #  4. PRÊTS DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    valid_personnes = [pid for pid in personnes_ids if pid is not None]
    valid_materiels = [(mid, t, m, mo) for mid, t, m, mo in materiels_info if mid is not None]

    now = datetime.now()
    notes_prets = [
        'Projet SNT', 'TP en salle', 'Stage en entreprise', 'Présentation orale',
        'Journée portes ouvertes', 'Formation à distance', 'Examens',
        'Cours de rattrapage', 'Atelier pratique', 'Semaine de projet',
        'TP réseau', 'Sortie scolaire', 'Intervention extérieure', '',
    ]

    if valid_personnes and valid_materiels:
        # ── Lieux existants pour affectation aléatoire ──
        lieux_ids = [row['id'] for row in conn.execute(
            'SELECT id FROM lieux WHERE actif = 1'
        ).fetchall()]

        def random_lieu():
            """Retourne un lieu_id aléatoire ou None (50 % de chance)."""
            if lieux_ids and random.random() < 0.5:
                return random.choice(lieux_ids)
            return None

        # Prêts en cours (environ 30 % des matériels)
        nb_prets_en_cours = max(2, len(valid_materiels) // 3)
        indices_mat = list(range(len(valid_materiels)))
        random.shuffle(indices_mat)

        for i in range(min(nb_prets_en_cours, len(indices_mat), len(valid_personnes))):
            mid, type_mat, marque, modele = valid_materiels[indices_mat[i]]
            pid = valid_personnes[i % len(valid_personnes)]
            descriptif = f'{marque} {modele}'
            jours_ago = random.randint(0, 10)
            duree = random.choice([1, 3, 5, 7, 14])
            note = random.choice(notes_prets)
            date_emprunt = (now - timedelta(days=jours_ago)).strftime('%Y-%m-%d %H:%M:%S')
            lieu = random_lieu()

            cursor = conn.execute(
                'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                'retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id) '
                'VALUES (?, ?, ?, 0, ?, ?, ?, ?)',
                (pid, descriptif, date_emprunt, duree, mid, note, lieu)
            )
            pret_id = cursor.lastrowid
            # Créer l'entrée pret_materiels correspondante
            conn.execute(
                'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                (pret_id, mid, descriptif)
            )
            conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mid,))

        # Prêts retournés (historique, sur les matériels restants)
        indices_retournes = indices_mat[nb_prets_en_cours:]
        nb_prets_retournes = min(len(indices_retournes), max(3, len(valid_materiels) // 2))

        for i in range(nb_prets_retournes):
            mid, type_mat, marque, modele = valid_materiels[indices_retournes[i]]
            pid = valid_personnes[(nb_prets_en_cours + i) % len(valid_personnes)]
            descriptif = f'{marque} {modele}'
            jours_ago = random.randint(10, 60)
            duree = random.choice([3, 7, 14, 30])
            retour_jours_ago = max(1, jours_ago - random.randint(1, duree))
            note = random.choice(notes_prets)
            date_emprunt = (now - timedelta(days=jours_ago)).strftime('%Y-%m-%d %H:%M:%S')
            date_retour = (now - timedelta(days=retour_jours_ago)).strftime('%Y-%m-%d %H:%M:%S')
            lieu = random_lieu()

            cursor = conn.execute(
                'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                'date_retour, retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id) '
                'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)',
                (pid, descriptif, date_emprunt, date_retour, duree, mid, note, lieu)
            )
            pret_id = cursor.lastrowid
            conn.execute(
                'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                (pret_id, mid, descriptif)
            )

        # ── Historique enrichi : plusieurs prêts passés sur certains équipements ──
        indices_historique = random.sample(
            list(range(len(valid_materiels))),
            k=min(max(3, len(valid_materiels) * 2 // 5), len(valid_materiels))
        )
        for idx in indices_historique:
            mid, type_mat, marque, modele = valid_materiels[idx]
            descriptif = f'{marque} {modele}'
            nb_anciens = random.randint(2, 4)
            base_jours = 60

            for j in range(nb_anciens):
                pid = random.choice(valid_personnes)
                jours_debut = base_jours + random.randint(5, 30)
                duree = random.choice([1, 3, 5, 7, 14])
                jours_retour = max(base_jours, jours_debut - duree)
                note = random.choice(notes_prets)
                date_emprunt = (now - timedelta(days=jours_debut)).strftime('%Y-%m-%d %H:%M:%S')
                date_retour = (now - timedelta(days=jours_retour)).strftime('%Y-%m-%d %H:%M:%S')
                lieu = random_lieu()

                cursor = conn.execute(
                    'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                    'date_retour, retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id) '
                    'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)',
                    (pid, descriptif, date_emprunt, date_retour, duree, mid, note, lieu)
                )
                pret_id = cursor.lastrowid
                conn.execute(
                    'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                    (pret_id, mid, descriptif)
                )
                base_jours = jours_debut + random.randint(5, 20)

        # ── Quelques prêts multi-matériels pour la démo ──
        if len(valid_materiels) >= 3 and len(valid_personnes) >= 2:
            accessoires = ['Câble HDMI', 'Souris sans fil', 'Chargeur', 'Sacoche', 'Adaptateur USB-C', 'Rallonge']
            for _ in range(min(3, len(valid_personnes) // 4 + 1)):
                pid = random.choice(valid_personnes)
                nb_items = random.randint(2, 4)
                items_desc = random.sample(accessoires, min(nb_items - 1, len(accessoires)))
                # Premier item = un matériel de l'inventaire (libre)
                libre = conn.execute(
                    "SELECT id, marque, modele FROM inventaire WHERE etat = 'disponible' AND actif = 1 LIMIT 1"
                ).fetchone()
                if libre:
                    desc_principal = f"{libre['marque']} {libre['modele']}"
                    all_desc = [desc_principal] + items_desc
                    descriptif_combine = ' + '.join(all_desc)
                    date_emprunt = (now - timedelta(days=random.randint(0, 5))).strftime('%Y-%m-%d %H:%M:%S')
                    lieu = random_lieu()

                    cursor = conn.execute(
                        'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                        'retour_confirme, duree_pret_jours, notes, lieu_id) '
                        'VALUES (?, ?, ?, 0, ?, ?, ?)',
                        (pid, descriptif_combine, date_emprunt, random.choice([3, 7, 14]),
                         random.choice(notes_prets), lieu)
                    )
                    pret_id = cursor.lastrowid
                    # Premier item lié à l'inventaire
                    conn.execute(
                        'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                        (pret_id, libre['id'], desc_principal)
                    )
                    conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (libre['id'],))
                    # Items supplémentaires (texte libre)
                    for desc in items_desc:
                        conn.execute(
                            'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, NULL, ?)',
                            (pret_id, desc)
                        )

    conn.commit()
    conn.close()

    nb_pers = len([p for p in personnes_ids if p is not None])
    nb_mat = len([m for m in materiels_ids if m is not None])
    flash(f'Base de démonstration générée : {nb_pers} personnes, {nb_mat} matériels et des prêts de test.', 'success')
    return redirect(url_for('admin_dashboard'))


# ============================================================
#  DÉTAIL D'UN PRÊT
# ============================================================

@app.route('/pret/<int:pret_id>')
def detail_pret(pret_id):
    conn = get_db()
    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        conn.close()
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('index'))

    # Charger les items multi-matériel
    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire, inv.image
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()

    # Rétrocompat : ancien champ materiel_id (pour les prêts créés avant multi-matériel)
    materiel_legacy = None
    if not pret_items and pret['materiel_id']:
        materiel_legacy = conn.execute('''
            SELECT image, marque, modele, numero_inventaire
            FROM inventaire WHERE id = ?
        ''', (pret['materiel_id'],)).fetchone()

    conn.close()

    return render_template('detail_pret.html', pret=pret,
                           pret_items=pret_items, materiel_legacy=materiel_legacy)


# ============================================================
#  MODIFICATION D'UN PRÊT EN COURS
# ============================================================

@app.route('/pret/modifier/<int:pret_id>', methods=['GET', 'POST'])
def modifier_pret(pret_id):
    conn = get_db()

    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        conn.close()
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('index'))

    if pret['retour_confirme']:
        conn.close()
        flash('Ce prêt est déjà retourné, il ne peut plus être modifié.', 'warning')
        return redirect(url_for('detail_pret', pret_id=pret_id))

    if request.method == 'POST':
        personne_id = request.form.get('personne_id', '').strip()
        notes = request.form.get('notes', '').strip()
        lieu_id = request.form.get('lieu_id', '').strip() or None

        # ── Récupération des items (multi-matériel) ──
        items_desc = request.form.getlist('items_description[]')
        items_mat = request.form.getlist('items_materiel_id[]')
        items = []
        for i in range(len(items_desc)):
            desc = items_desc[i].strip() if i < len(items_desc) else ''
            mat_id = items_mat[i].strip() if i < len(items_mat) else ''
            if desc:
                items.append((desc, int(mat_id) if mat_id else None))

        # ── Gestion de la durée ──
        duree_type = request.form.get('duree_type', 'defaut')
        duree_pret_jours = None
        duree_pret_heures = None

        if duree_type == 'heures':
            h = request.form.get('duree_heures', '').strip()
            if h:
                try:
                    duree_pret_heures = float(h)
                except ValueError:
                    pass
        elif duree_type == 'jours':
            j = request.form.get('duree_jours', '').strip()
            if j:
                try:
                    duree_pret_jours = int(j)
                except ValueError:
                    pass
        elif duree_type == 'fin_journee':
            heure_fin = get_setting('heure_fin_journee', '17:45')
            h_fin, m_fin = (int(x) for x in heure_fin.split(':'))
            now = datetime.now()
            fin_journee = now.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
            if fin_journee > now:
                delta = (fin_journee - now).total_seconds() / 3600
                duree_pret_heures = round(delta, 2)
            else:
                duree_pret_heures = 0.5

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
        else:
            descriptif = ' + '.join(desc for desc, _ in items)

            # Libérer les anciens matériels liés (pret_materiels)
            anciens_mats = conn.execute(
                'SELECT materiel_id FROM pret_materiels WHERE pret_id = ? AND materiel_id IS NOT NULL',
                (pret_id,)
            ).fetchall()
            for am in anciens_mats:
                conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (am['materiel_id'],))
            # Rétrocompat ancien champ
            if pret['materiel_id']:
                conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (pret['materiel_id'],))

            # Supprimer anciens items et recréer
            conn.execute('DELETE FROM pret_materiels WHERE pret_id = ?', (pret_id,))
            for desc, mat_id in items:
                conn.execute(
                    'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                    (pret_id, mat_id, desc)
                )
                if mat_id:
                    conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mat_id,))

            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                '''UPDATE prets SET personne_id=?, descriptif_objets=?, notes=?,
                   duree_pret_jours=?, duree_pret_heures=?, type_duree=?, materiel_id=NULL,
                   lieu_id=?, date_modification=?
                   WHERE id=?''',
                (personne_id, descriptif, notes, duree_pret_jours, duree_pret_heures,
                 duree_type, lieu_id, now, pret_id)
            )
            conn.commit()
            conn.close()
            flash('Prêt modifié avec succès.', 'success')
            return redirect(url_for('detail_pret', pret_id=pret_id))

    # Charger les items existants
    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()

    personnes = conn.execute(
        'SELECT * FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    categories = conn.execute(
        'SELECT * FROM categories_materiel ORDER BY nom'
    ).fetchall()
    lieux = conn.execute(
        'SELECT * FROM lieux WHERE actif = 1 ORDER BY nom'
    ).fetchall()

    conn.close()
    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    return render_template(
        'modifier_pret.html',
        pret=pret,
        pret_items=pret_items,
        personnes=personnes,
        categories=categories,
        lieux=lieux,
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux')
    )


# ============================================================
#  SUPPRESSION D'UN PRÊT
# ============================================================

@app.route('/pret/supprimer/<int:pret_id>', methods=['POST'])
@admin_required
def supprimer_pret(pret_id):
    conn = get_db()
    pret = conn.execute('SELECT materiel_id, retour_confirme FROM prets WHERE id = ?', (pret_id,)).fetchone()
    if pret and not pret['retour_confirme']:
        # Libérer multi-matériels
        mats = conn.execute(
            'SELECT materiel_id FROM pret_materiels WHERE pret_id = ? AND materiel_id IS NOT NULL',
            (pret_id,)
        ).fetchall()
        for m in mats:
            conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (m['materiel_id'],))
        # Rétrocompat ancien champ
        if pret['materiel_id']:
            conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (pret['materiel_id'],))
    conn.execute('DELETE FROM pret_materiels WHERE pret_id = ?', (pret_id,))
    conn.execute('DELETE FROM prets WHERE id = ?', (pret_id,))
    conn.commit()
    conn.close()
    flash('Prêt supprimé.', 'success')
    return redirect(url_for('historique'))


# ============================================================
#  BIBLIOTHÈQUE D'IMAGES MATÉRIEL
# ============================================================

@app.route('/api/images-materiel')
@admin_required
def api_liste_images():
    """Retourne la liste des images disponibles dans le dossier uploads."""
    images = []
    if os.path.exists(UPLOAD_FOLDER):
        for f in sorted(os.listdir(UPLOAD_FOLDER)):
            if allowed_file(f):
                images.append(f)
    return jsonify(images)


@app.route('/api/upload-image-materiel', methods=['POST'])
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


@app.route('/api/supprimer-image-materiel', methods=['POST'])
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


# ============================================================
#  INVENTAIRE MATÉRIEL
# ============================================================

def _query_inventaire(filtre_type='tous', recherche='', etat_only=None):
    """Helper : interroge l'inventaire avec filtres. Renvoie (items, types, comptages, conn)."""
    conn = get_db()
    query = 'SELECT * FROM inventaire WHERE actif = 1'
    params = []

    if etat_only:
        query += ' AND etat = ?'
        params.append(etat_only)

    if filtre_type != 'tous':
        query += ' AND type_materiel = ?'
        params.append(filtre_type)

    if recherche:
        query += ' AND (numero_inventaire LIKE ? OR marque LIKE ? OR modele LIKE ? OR numero_serie LIKE ?)'
        params.extend([f'%{recherche}%'] * 4)

    query += ' ORDER BY type_materiel, numero_inventaire'
    items = conn.execute(query, params).fetchall()

    types = conn.execute(
        'SELECT DISTINCT type_materiel FROM inventaire WHERE actif = 1 ORDER BY type_materiel'
    ).fetchall()

    comptages = {'total': conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0]}
    for t in types:
        comptages[t['type_materiel']] = conn.execute(
            'SELECT COUNT(*) FROM inventaire WHERE actif = 1 AND type_materiel = ?',
            (t['type_materiel'],)
        ).fetchone()[0]

    conn.close()
    return items, types, comptages


@app.route('/inventaire')
@admin_required
def inventaire():
    filtre_type = request.args.get('type', 'tous')
    recherche = request.args.get('q', '').strip()
    items, types, comptages = _query_inventaire(filtre_type, recherche)
    return render_template('inventaire.html', items=items, types=types,
                           filtre_type=filtre_type, recherche=recherche, comptages=comptages)


@app.route('/inventaire/ajouter', methods=['GET', 'POST'])
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
            conn = get_db()
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
                flash(f'Matériel {numero_inv} ajouté avec succès !', 'success')
                conn.close()
                return redirect(url_for('inventaire'))
            except Exception:
                flash(f'Le numéro d\'inventaire {numero_inv} existe déjà.', 'danger')
                conn.close()

    conn = get_db()
    categories = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()
    conn.close()
    return render_template('ajouter_materiel.html', categories=categories, form=form_data)


@app.route('/inventaire/modifier/<int:mat_id>', methods=['GET', 'POST'])
@admin_required
def modifier_materiel(mat_id):
    conn = get_db()

    if request.method == 'POST':
        type_mat = request.form.get('type_materiel', '').strip()
        marque = request.form.get('marque', '').strip()
        modele = request.form.get('modele', '').strip()
        numero_serie = request.form.get('numero_serie', '').strip()
        numero_inv = request.form.get('numero_inventaire', '').strip()
        os_val = request.form.get('systeme_exploitation', '').strip()
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
        flash('Matériel modifié avec succès !', 'success')
        conn.close()
        return redirect(url_for('inventaire'))

    materiel = conn.execute('SELECT * FROM inventaire WHERE id = ?', (mat_id,)).fetchone()
    categories = conn.execute('SELECT * FROM categories_materiel ORDER BY nom').fetchall()
    conn.close()
    if not materiel:
        flash('Matériel non trouvé.', 'danger')
        return redirect(url_for('inventaire'))
    return render_template('modifier_materiel.html', materiel=materiel, categories=categories)


@app.route('/inventaire/supprimer/<int:mat_id>', methods=['POST'])
@admin_required
def supprimer_materiel(mat_id):
    conn = get_db()
    # Vérifier si le matériel est actuellement prêté
    pret_actif = conn.execute(
        'SELECT COUNT(*) FROM prets WHERE materiel_id = ? AND retour_confirme = 0',
        (mat_id,)
    ).fetchone()[0]
    if pret_actif > 0:
        flash('Impossible de supprimer ce matériel : il est actuellement prêté. Effectuez d\'abord le retour.', 'danger')
        conn.close()
        return redirect(url_for('inventaire'))
    conn.execute('UPDATE inventaire SET actif = 0 WHERE id = ?', (mat_id,))
    conn.commit()
    conn.close()
    flash('Matériel supprimé.', 'success')
    return redirect(url_for('inventaire'))


# ============================================================
#  HISTORIQUE D'UN ÉQUIPEMENT
# ============================================================

@app.route('/inventaire/historique/<int:mat_id>')
@admin_required
def historique_materiel(mat_id):
    """Affiche l'historique chronologique de tous les prêts d'un équipement."""
    conn = get_db()

    materiel = conn.execute(
        'SELECT * FROM inventaire WHERE id = ?', (mat_id,)
    ).fetchone()

    if not materiel:
        conn.close()
        flash('Matériel non trouvé.', 'danger')
        return redirect(url_for('inventaire'))

    # Tous les prêts liés à ce matériel, du plus récent au plus ancien
    prets = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.materiel_id = ?
        ORDER BY p.date_emprunt DESC
    ''', (mat_id,)).fetchall()

    # Statistiques rapides
    stats = {
        'total': len(prets),
        'en_cours': sum(1 for p in prets if not p['retour_confirme']),
        'retournes': sum(1 for p in prets if p['retour_confirme']),
    }

    conn.close()
    return render_template('historique_materiel.html',
                           materiel=materiel, prets=prets, stats=stats)


# ============================================================
#  HISTORIQUE D'UNE PERSONNE (emprunts)
# ============================================================

@app.route('/personnes/historique/<int:personne_id>')
def historique_personne(personne_id):
    """Affiche l'historique chronologique de tous les emprunts d'une personne."""
    conn = get_db()

    personne = conn.execute(
        'SELECT * FROM personnes WHERE id = ?', (personne_id,)
    ).fetchone()

    if not personne:
        conn.close()
        flash('Personne non trouvée.', 'danger')
        return redirect(url_for('personnes'))

    # Tous les prêts de cette personne, du plus récent au plus ancien
    prets = conn.execute('''
        SELECT p.*, l.nom AS lieu_nom
        FROM prets p
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.personne_id = ?
        ORDER BY p.date_emprunt DESC
    ''', (personne_id,)).fetchall()

    # Pour chaque prêt, récupérer les items (pret_materiels)
    prets_data = []
    for pret in prets:
        items = conn.execute('''
            SELECT pm.description, pm.materiel_id, i.numero_inventaire, i.marque, i.modele
            FROM pret_materiels pm
            LEFT JOIN inventaire i ON pm.materiel_id = i.id
            WHERE pm.pret_id = ?
        ''', (pret['id'],)).fetchall()
        prets_data.append({
            'pret': pret,
            'materiels': items if items else [],
        })

    # Statistiques rapides
    stats = {
        'total': len(prets),
        'en_cours': sum(1 for p in prets if not p['retour_confirme']),
        'retournes': sum(1 for p in prets if p['retour_confirme']),
    }

    conn.close()
    return render_template('historique_personne.html',
                           personne=personne, prets_data=prets_data, stats=stats)


@app.route('/inventaire/importer', methods=['GET', 'POST'])
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
            conn = get_db()
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
            conn.close()
            msg = f'{ajoutes} matériel(s) importé(s).'
            if doublons:
                msg += f' {doublons} doublon(s) ignoré(s).'
            flash(msg, 'success')
            return redirect(url_for('inventaire'))

        except Exception as e:
            flash(f"Erreur lors de l'import : {str(e)}", 'danger')
            return redirect(request.url)

    return render_template('importer_inventaire.html')


@app.route('/telecharger-gabarit-inventaire')
def telecharger_gabarit_inventaire():
    """Gabarit CSV dynamique basé sur les catégories de matériel configurées."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(['type_materiel', 'marque', 'modele', 'numero_serie',
                     'numero_inventaire', 'systeme_exploitation', 'notes'])

    # Charger les catégories de matériel depuis la base
    conn = get_db()
    categories = conn.execute('SELECT nom, prefixe_inventaire FROM categories_materiel ORDER BY nom').fetchall()
    conn.close()

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


@app.route('/api/inventaire')
def api_inventaire():
    """API JSON pour autocomplétion de matériel."""
    conn = get_db()
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
    conn.close()
    return jsonify([dict(i) for i in items])


@app.route('/api/scan')
def api_scan():
    """API JSON : recherche un code scanné (numéro inventaire ou série) et renvoie l'URL de redirection."""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'found': False, 'message': 'Aucun code fourni.'})

    conn = get_db()
    # 1) Chercher dans l'inventaire par numéro d'inventaire ou numéro de série
    mat = conn.execute(
        'SELECT id, type_materiel, marque, modele, numero_inventaire, etat FROM inventaire '
        'WHERE actif = 1 AND (numero_inventaire = ? OR numero_serie = ?)',
        (code, code)
    ).fetchone()

    if not mat:
        conn.close()
        return jsonify({'found': False, 'message': f'Aucun matériel trouvé pour « {code} ».'})

    # 2) Vérifier s'il y a un prêt actif pour ce matériel
    pret = conn.execute('''
        SELECT p.id FROM prets p
        JOIN pret_materiels pm ON pm.pret_id = p.id
        WHERE pm.materiel_id = ? AND p.retour_confirme = 0
        LIMIT 1
    ''', (mat['id'],)).fetchone()

    # Rétrocompat legacy materiel_id
    if not pret:
        pret = conn.execute(
            'SELECT id FROM prets WHERE materiel_id = ? AND retour_confirme = 0 LIMIT 1',
            (mat['id'],)
        ).fetchone()

    conn.close()

    label = mat['type_materiel']
    if mat['marque']:
        label += f" {mat['marque']}"
    if mat['modele']:
        label += f" {mat['modele']}"

    if pret:
        return jsonify({
            'found': True,
            'type': 'pret_actif',
            'message': f'{label} — Prêt actif trouvé',
            'url': url_for('detail_pret', pret_id=pret['id']),
        })
    else:
        return jsonify({
            'found': True,
            'type': 'materiel',
            'message': f'{label} — {mat["etat"]}',
            'url': url_for('modifier_materiel', mat_id=mat['id']),
        })


# ============================================================
#  PAGE ÉTIQUETTES (publique)
# ============================================================

@app.route('/etiquettes')
def etiquettes():
    """Page centralisée d'impression d'étiquettes (accessible sans admin)."""
    filtre_type = request.args.get('type', 'tous')
    recherche = request.args.get('q', '').strip()
    items, types, comptages = _query_inventaire(filtre_type, recherche)

    zebra_active = get_setting('impression_zebra_active', '0') == '1'
    return render_template('etiquettes.html', items=items, types=types,
                           filtre_type=filtre_type, recherche=recherche,
                           comptages=comptages, zebra_active=zebra_active)


# ============================================================
#  IMPRESSION D'ÉTIQUETTES
# ============================================================

@app.route('/imprimer/etiquettes')
def imprimer_etiquettes():
    """Page d'étiquettes imprimable (PDF via navigateur)."""
    ids = request.args.get('ids', '')
    if not ids:
        flash('Aucun matériel sélectionné pour l\'impression.', 'warning')
        return redirect(url_for('etiquettes'))

    id_list = [i.strip() for i in ids.split(',') if i.strip().isdigit()]
    if not id_list:
        flash('Identifiants invalides.', 'danger')
        return redirect(url_for('etiquettes'))

    conn = get_db()
    placeholders = ','.join(['?'] * len(id_list))
    items = conn.execute(f'''
        SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie
        FROM inventaire WHERE id IN ({placeholders})
    ''', id_list).fetchall()
    conn.close()

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


@app.route('/imprimer/zebra', methods=['POST'])
def imprimer_zebra():
    """Imprimer des étiquettes via imprimante Zebra (port série)."""
    if get_setting('impression_zebra_active', '0') != '1':
        return jsonify({'success': False, 'error': 'L\'impression Zebra n\'est pas activée.'}), 400

    ids = request.json.get('ids', []) if request.is_json else []
    if not ids:
        return jsonify({'success': False, 'error': 'Aucun matériel sélectionné.'}), 400

    conn = get_db()
    placeholders = ','.join(['?'] * len(ids))
    items = conn.execute(f'''
        SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie
        FROM inventaire WHERE id IN ({placeholders})
    ''', ids).fetchall()
    conn.close()

    if not items:
        return jsonify({'success': False, 'error': 'Matériels non trouvés.'}), 404

    # Charger la config Zebra
    port = get_setting('impression_port', 'COM3')
    baud = int(get_setting('impression_baud', '38400'))
    tearoff = get_setting('impression_tearoff', '018')
    zpl_template = get_setting('impression_zpl_template',
        '^XA^CI27^FO15,20^BY2^BCN,80,N^FD{numero_inventaire}^FS^FO25,130^A0,50,28^FD{numero_inventaire}^FS^XZ')

    texte_libre = get_setting('impression_texte_libre', '')

    # Construire les commandes ZPL
    zpl_commands = []
    for item in items:
        zpl = zpl_template.format(
            numero_inventaire=item['numero_inventaire'] or '',
            type=item['type_materiel'] or '',
            marque=item['marque'] or '',
            modele=item['modele'] or '',
            numero_serie=item['numero_serie'] or '',
            texte_libre=texte_libre
        )
        zpl_commands.append(f'~TA{tearoff}{zpl}')

    # Envoi selon la méthode configurée
    methode = get_setting('impression_zebra_methode', 'serial')

    if methode == 'http':
        # Envoi via HTTP
        zebra_url = get_setting('impression_zebra_url', 'http://localhost:9100')
        try:
            import urllib.request
            for zpl in zpl_commands:
                req = urllib.request.Request(zebra_url, data=zpl.encode('utf-8'),
                                            method='POST')
                req.add_header('Content-Type', 'application/x-www-form-urlencoded')
                urllib.request.urlopen(req, timeout=10)
            return jsonify({'success': True,
                            'message': f'{len(items)} étiquette(s) envoyée(s) via HTTP.'})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Erreur HTTP : {str(e)}'}), 500
    else:
        # Envoi via port série
        try:
            from zebra_print import envoyer_zpl
            resultat = envoyer_zpl(port, baud, zpl_commands)
            if resultat['success']:
                return jsonify({'success': True,
                                'message': f'{len(items)} étiquette(s) envoyée(s) à l\'imprimante.'})
            else:
                return jsonify({'success': False, 'error': resultat['error']}), 500
        except ImportError:
            return jsonify({'success': False,
                            'error': 'Module pyserial non installé. Exécutez : pip install pyserial'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
#  GESTION DES LIEUX
# ============================================================

@app.route('/lieux', methods=['GET', 'POST'])
@admin_required
def gestion_lieux():
    conn = get_db()
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
        conn.close()
        return redirect(url_for('gestion_lieux'))

    lieux = conn.execute('SELECT * FROM lieux ORDER BY actif DESC, nom').fetchall()
    conn.close()
    return render_template('lieux.html', lieux=lieux)


# ============================================================
#  SAUVEGARDE & RESTAURATION COMPLÈTE
# ============================================================

@app.route('/admin/sauvegarder')
@admin_required
def admin_sauvegarder():
    """Exporter toute la base + uploads dans un fichier .pretgo (zip)."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'PretGo_sauvegarde_{timestamp}.pretgo'
    zip_path = os.path.join(BACKUP_DIR, filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Base de données SQLite
        if os.path.exists(DATABASE_PATH):
            zf.write(DATABASE_PATH, 'gestion_prets.db')
        # Images matériel
        uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'materiel')
        if os.path.exists(uploads_dir):
            for f in os.listdir(uploads_dir):
                fpath = os.path.join(uploads_dir, f)
                if os.path.isfile(fpath):
                    zf.write(fpath, f'uploads/materiel/{f}')
        # Documents (fiches de prêt)
        if os.path.exists(DOCUMENTS_DIR):
            for f in os.listdir(DOCUMENTS_DIR):
                fpath = os.path.join(DOCUMENTS_DIR, f)
                if os.path.isfile(fpath):
                    zf.write(fpath, f'documents/{f}')
        # Code de récupération
        if os.path.exists(RECOVERY_CODE_PATH):
            zf.write(RECOVERY_CODE_PATH, 'code_recuperation.txt')

    # Envoyer en téléchargement
    return send_file(zip_path, as_attachment=True, download_name=filename,
                     mimetype='application/zip')


@app.route('/admin/restaurer', methods=['POST'])
@admin_required
def admin_restaurer():
    """Restaurer depuis un fichier .pretgo."""
    if 'fichier_pretgo' not in request.files:
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_reglages'))

    fichier = request.files['fichier_pretgo']
    if not fichier.filename.lower().endswith('.pretgo'):
        flash('Veuillez sélectionner un fichier .pretgo valide.', 'danger')
        return redirect(url_for('admin_reglages'))

    try:
        # Sauvegarder dans un temp
        temp_path = os.path.join(BACKUP_DIR, 'restauration_temp.zip')
        fichier.save(temp_path)

        with zipfile.ZipFile(temp_path, 'r') as zf:
            names = zf.namelist()
            if 'gestion_prets.db' not in names:
                flash('Fichier .pretgo invalide (base de données manquante).', 'danger')
                os.remove(temp_path)
                return redirect(url_for('admin_reglages'))

            # Restaurer la base
            zf.extract('gestion_prets.db', DATA_DIR)

            # Restaurer les images
            uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'materiel')
            os.makedirs(uploads_dir, exist_ok=True)
            for name in names:
                if name.startswith('uploads/materiel/'):
                    fname = name.split('/')[-1]
                    if fname:
                        with zf.open(name) as src, open(os.path.join(uploads_dir, fname), 'wb') as dst:
                            dst.write(src.read())
                elif name.startswith('documents/'):
                    fname = name.split('/')[-1]
                    if fname:
                        with zf.open(name) as src, open(os.path.join(DOCUMENTS_DIR, fname), 'wb') as dst:
                            dst.write(src.read())
                elif name == 'code_recuperation.txt':
                    zf.extract(name, DATA_DIR)

        os.remove(temp_path)
        # Réinitialiser les migrations (s'assurer que les nouvelles colonnes existent)
        init_db()
        session.pop('admin_logged_in', None)
        flash('Base restaurée avec succès ! Veuillez vous reconnecter.', 'success')
        return redirect(url_for('admin_login'))

    except Exception as e:
        flash(f'Erreur lors de la restauration : {str(e)}', 'danger')
        return redirect(url_for('admin_reglages'))


# ============================================================
#  FICHES DE PRÊT (IMPRIMABLES)
# ============================================================

@app.route('/pret/<int:pret_id>/fiche')
def fiche_pret(pret_id):
    """Générer une fiche de prêt pré-remplie imprimable."""
    conn = get_db()
    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        conn.close()
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('index'))

    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire, inv.numero_serie
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()

    conn.close()
    nom_etablissement = get_setting('nom_etablissement', '')
    return render_template('fiche_pret.html', pret=pret, pret_items=pret_items,
                           nom_etablissement=nom_etablissement)


@app.route('/fiche-vierge')
def fiche_pret_vierge():
    """Fiche de prêt vierge avec champs à remplir manuellement."""
    nom_etablissement = get_setting('nom_etablissement', '')
    return render_template('fiche_pret_vierge.html', nom_etablissement=nom_etablissement)


# ============================================================
#  LANCEMENT DE L'APPLICATION
# ============================================================

if __name__ == '__main__':
    print()
    print("=" * 55)
    print("   PRETGO — Gestion de Prêt de Matériel")
    print("   ----------------------------")
    print("   Ouvrez votre navigateur à l'adresse :")
    print("   http://localhost:5000")
    print("=" * 55)
    print()
    app.run(host='0.0.0.0', port=5000, debug=False)
