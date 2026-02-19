// ============================================================
//  PRETGO — JavaScript
// ============================================================

// ============================================================
//  AUTOCOMPLÉTION POUR LA RECHERCHE DE PERSONNES
// ============================================================

function initAutocomplete() {
    const searchInput = document.getElementById('personne-search');
    const resultsDiv = document.getElementById('autocomplete-results');
    const personneIdInput = document.getElementById('personne_id');
    const selectedDiv = document.getElementById('personne-selected');

    if (!searchInput) return;

    let timeout = null;

    searchInput.addEventListener('input', function () {
        const query = this.value.trim();
        clearTimeout(timeout);

        if (query.length < 1) {
            resultsDiv.style.display = 'none';
            return;
        }

        timeout = setTimeout(() => {
            fetch('/api/personnes?q=' + encodeURIComponent(query))
                .then(response => response.json())
                .then(data => {
                    resultsDiv.innerHTML = '';

                    if (data.length === 0) {
                        resultsDiv.innerHTML =
                            '<div class="autocomplete-item"><em>Aucun résultat — '
                            + '<a href="/personnes/ajouter">Ajouter une personne</a></em></div>';
                        resultsDiv.style.display = 'block';
                        return;
                    }

                    data.forEach(personne => {
                        const item = document.createElement('div');
                        item.className = 'autocomplete-item';

                        const catLabel = personne.categorie_label || personne.categorie || '';

                        item.innerHTML =
                            '<div class="name">' + personne.nom + ' ' + personne.prenom + '</div>'
                            + '<div class="details">'
                            + '<span>' + catLabel + '</span>'
                            + (personne.classe ? ' — ' + personne.classe : '')
                            + '</div>';

                        item.addEventListener('click', () => {
                            personneIdInput.value = personne.id;
                            searchInput.value = personne.nom + ' ' + personne.prenom;
                            searchInput.style.display = 'none';

                            selectedDiv.innerHTML =
                                '<div class="alert alert-info d-flex justify-content-between align-items-center mb-0">'
                                + '<div>'
                                + '<strong>' + personne.nom + ' ' + personne.prenom + '</strong> '
                                + '<span class="ms-2">' + catLabel + '</span>'
                                + (personne.classe ? '<span class="ms-2">' + personne.classe + '</span>' : '')
                                + '</div>'
                                + '<button type="button" class="btn btn-sm btn-outline-secondary" onclick="resetPersonne()">'
                                + '<i class="bi bi-x-lg"></i> Changer'
                                + '</button>'
                                + '</div>';
                            selectedDiv.style.display = 'block';
                            resultsDiv.style.display = 'none';
                        });

                        resultsDiv.appendChild(item);
                    });

                    resultsDiv.style.display = 'block';
                })
                .catch(err => {
                    console.error('Erreur autocomplétion:', err);
                });
        }, 200);
    });

    // Fermer l'autocomplétion en cliquant ailleurs
    document.addEventListener('click', function (e) {
        if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {
            resultsDiv.style.display = 'none';
        }
    });
}

function resetPersonne() {
    const searchInput = document.getElementById('personne-search');
    const personneIdInput = document.getElementById('personne_id');
    const selectedDiv = document.getElementById('personne-selected');

    personneIdInput.value = '';
    searchInput.value = '';
    searchInput.style.display = 'block';
    selectedDiv.style.display = 'none';
    searchInput.focus();
}


// ============================================================
//  HORLOGE EN TEMPS RÉEL
// ============================================================

function updateClock() {
    const clockEl = document.getElementById('current-datetime');
    if (!clockEl) return;

    const now = new Date();
    const options = {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    };
    clockEl.textContent = now.toLocaleDateString('fr-FR', options);
}


// ============================================================
//  INITIALISATION AU CHARGEMENT DE LA PAGE
// ============================================================

document.addEventListener('DOMContentLoaded', function () {
    // Autocomplétion
    initAutocomplete();

    // Horloge
    updateClock();
    setInterval(updateClock, 1000);

    // Mettre en surbrillance le lien de navigation actif
    const currentPath = window.location.pathname;
    document.querySelectorAll('.btn-nav').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath || (currentPath.startsWith(href) && href !== '/')) {
            link.classList.add('active');
        } else if (href === '/' && currentPath === '/') {
            link.classList.add('active');
        }
    });

    // Auto-fermer les alertes après 5 secondes
    document.querySelectorAll('.alert-dismissible').forEach(alert => {
        setTimeout(() => {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) bsAlert.close();
        }, 5000);
    });
});
