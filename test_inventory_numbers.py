#!/usr/bin/env python3
"""
Test de réutilisation des numéros d'inventaire.
"""
import tempfile
import sqlite3
import os
from utils import get_next_inventory_number

def test_inventory_reuse():
    # Créer une DB temporaire pour le test
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('''CREATE TABLE inventaire (
            id INTEGER PRIMARY KEY, numero_inventaire TEXT UNIQUE
        )''')
        
        print("Test de réutilisation des numéros d'inventaire")
        print("=" * 60)
        
        # Étape 1: Créer PC-00001
        conn.execute('INSERT INTO inventaire (numero_inventaire) VALUES (?)', ('PC-00001',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après création PC-00001 : prochain = {next_num}")
        assert next_num == 'PC-00002', f"Expected PC-00002, got {next_num}"
        
        # Étape 2: Créer PC-00002
        conn.execute('INSERT INTO inventaire (numero_inventaire) VALUES (?)', ('PC-00002',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après création PC-00002 : prochain = {next_num}")
        assert next_num == 'PC-00003', f"Expected PC-00003, got {next_num}"
        
        # Étape 3: Créer PC-00003
        conn.execute('INSERT INTO inventaire (numero_inventaire) VALUES (?)', ('PC-00003',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après création PC-00003 : prochain = {next_num}")
        assert next_num == 'PC-00004', f"Expected PC-00004, got {next_num}"
        
        # Étape 4: Supprimer PC-00002
        conn.execute('DELETE FROM inventaire WHERE numero_inventaire = ?', ('PC-00002',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après suppression PC-00002 : prochain = {next_num}")
        assert next_num == 'PC-00002', f"Expected PC-00002 (réutilisation), got {next_num}"
        
        # Étape 5: Supprimer PC-00001
        conn.execute('DELETE FROM inventaire WHERE numero_inventaire = ?', ('PC-00001',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après suppression PC-00001 : prochain = {next_num}")
        assert next_num == 'PC-00001', f"Expected PC-00001 (réutilisation), got {next_num}"
        
        # Étape 6: Ajouter de nouveau PC-00001 et le numéro suivant doit être PC-00002
        conn.execute('INSERT INTO inventaire (numero_inventaire) VALUES (?)', ('PC-00001',))
        conn.commit()
        next_num = get_next_inventory_number(conn, 'PC')
        print(f"✓ Après réinsertion PC-00001 : prochain = {next_num}")
        assert next_num == 'PC-00002', f"Expected PC-00002, got {next_num}"
        
        print("=" * 60)
        print("✅ Tous les tests de réutilisation passent !")
        
    finally:
        conn.close()
        os.unlink(db_path)

if __name__ == '__main__':
    test_inventory_reuse()
