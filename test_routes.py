"""Test complet de toutes les routes PretGo."""
import sys, os
os.environ['TESTING'] = '1'
sys.path.insert(0, '.')
from app import app
from database import get_db, init_db, reset_db

app.config['TESTING'] = True
app.config['SECRET_KEY'] = 'test'

c = app.test_client()
errors = []
ok = 0


def get(url, expect=200, label=None):
    global ok
    label = label or f'GET {url}'
    try:
        r = c.get(url, follow_redirects=True)
        if r.status_code != expect:
            errors.append(f'{label}: got {r.status_code}, expected {expect}')
        else:
            ok += 1
    except Exception as e:
        errors.append(f'{label}: EXCEPTION {e}')


def post(url, data=None, expect=200, label=None):
    global ok
    label = label or f'POST {url}'
    try:
        r = c.post(url, data=data or {}, follow_redirects=True)
        if r.status_code != expect:
            errors.append(f'{label}: got {r.status_code}, expected {expect}')
        else:
            ok += 1
    except Exception as e:
        errors.append(f'{label}: EXCEPTION {e}')


# === Pages publiques ===
get('/')
get('/nouveau-pret')
get('/retour')
get('/recherche')
get('/recherche?q=test')
get('/historique')
get('/etiquettes')
get('/admin/login')
get('/api/inventaire')
get('/api/inventaire?q=test')
get('/api/personnes')

# === API Scan (nouveau) ===
get('/api/scan', label='API scan sans code')
get('/api/scan?code=INEXISTANT', label='API scan code inexistant')
get('/api/scan?code=', label='API scan code vide')

# === Admin login ===
post('/admin/login', {'password': 'admin'}, label='Admin login')

# === Pages admin ===
get('/admin')
get('/admin/reglages')
get('/personnes')
get('/inventaire')
get('/categories')
get('/categories-personnes')
get('/lieux')
get('/alertes')

# === Nettoyage complet des prêts et liens après reset_db ===
with app.app_context():
    conn = get_db()
    conn.execute("DELETE FROM pret_materiels")
    conn.execute("DELETE FROM prets")
    conn.commit()
    conn.close()

# === Créer une personne ===
post('/personnes/ajouter', {
    'nom': 'TestMasse', 'prenom': 'User', 'categorie': 'eleve'
}, label='Ajouter personne')

with app.app_context():
    conn = get_db()
    pers = conn.execute("SELECT id FROM personnes WHERE nom='TestMasse'").fetchone()
    pid = pers['id'] if pers else 1

    # === Créer un matériel directement en base ===
    conn.execute(
        "INSERT OR IGNORE INTO inventaire (type_materiel, marque, modele, numero_inventaire, numero_serie, etat, actif)"
        " VALUES ('ordinateur_portable','TestScan','Model1','SCAN-00001','SN-SCAN-001','disponible',1)"
    )
    conn.commit()
    mat = conn.execute("SELECT id FROM inventaire WHERE numero_inventaire='SCAN-00001'").fetchone()
    mat_id = mat['id'] if mat else None
    print(f'  Matériel créé: id={mat_id}')

    # === Créer 3 prêts (1 lié au matériel de test, 2 sans matériel) ===
    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 1',
        'items_materiel_id[]': '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 1')

    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 2',
        'items_materiel_id[]': '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 2')

    post('/nouveau-pret', {
        'personne_id': str(pid),
        'items_description[]': 'Objet masse 3',
        'items_materiel_id[]': str(mat_id) if mat_id else '',
        'duree_type': 'jours', 'duree_jours': '7'
    }, label='Créer prêt 3 (avec matériel)')

    # === Test API scan sur matériel en prêt ===
    r = c.get('/api/scan?code=SCAN-00001')
    data = r.get_json()
    if data and data.get('found') and data.get('type') == 'pret_actif':
        ok += 1
        print('  API scan matériel en prêt -> pret_actif OK')
    else:
        errors.append(f'API scan pret_actif: {data}')

    # === Récupérer les prêts actifs ===
    prets = conn.execute(
        "SELECT id FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
    ).fetchall()
    pret_ids = [str(p['id']) for p in prets]
    print(f'  {len(pret_ids)} prêts actifs créés')

    # === Test retour en masse vide ===
    post('/retour/masse', {}, label='Retour masse vide')

    # === Test retour en masse avec 2 prêts (multi-value form) ===
    if len(pret_ids) >= 2:
        r = c.post('/retour/masse', data={
            'pret_ids': pret_ids[:2]
        }, follow_redirects=True)
        if r.status_code == 200:
            ok += 1
        else:
            errors.append(f'Retour masse 2 prêts: got {r.status_code}')

        # Vérifier qu'il reste bien le bon nombre
        encore = conn.execute(
            "SELECT COUNT(*) as c FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
        ).fetchone()
        attendu = len(pret_ids) - 2
        if encore['c'] != attendu:
            errors.append(f'Retour masse: attendu {attendu} actifs, trouvé {encore["c"]}')
        else:
            ok += 1
            print(f'  Retour en masse OK: {attendu} prêt(s) restant(s)')

    # === Test retour individuel du dernier ===
    restant = conn.execute(
        "SELECT id FROM prets WHERE retour_confirme=0 AND personne_id=?", (pid,)
    ).fetchone()
    if restant:
        post(f'/retour/{restant["id"]}', {'signature': ''}, label='Retour individuel')

    # === API scan : matériel maintenant disponible ===
    r = c.get('/api/scan?code=SCAN-00001')
    data = r.get_json()
    if data and data.get('found') and data.get('type') == 'materiel':
        ok += 1
        print('  API scan matériel disponible -> materiel OK')
    else:
        errors.append(f'API scan materiel disponible: {data}')

    # === Vérifier libération matériel ===
    mat_check = conn.execute("SELECT etat FROM inventaire WHERE id=?", (mat_id,)).fetchone()
    if mat_check and mat_check['etat'] == 'disponible':
        ok += 1
        print('  Matériel libéré correctement après retour')
    else:
        errors.append(f'Matériel non libéré: etat={mat_check["etat"] if mat_check else "??"}')

    conn.close()

# === Autres pages ===
get('/fiche-vierge')
get('/historique?format=csv')

# === Résultats ===
total = ok + len(errors)
print(f'\n=== {ok}/{total} OK, {len(errors)} ERREURS ===')
for e in errors:
    print(f'  FAIL: {e}')
if not errors:
    print('Tous les tests passent !')
sys.exit(1 if errors else 0)
