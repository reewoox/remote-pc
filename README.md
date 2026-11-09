# Remote PC

Contrôle ton PC Windows depuis ton iPhone, dans Safari. Il n'y a rien à installer sur l'iPhone.

## Démarrage
1. Double-clique sur `start.bat`. Au premier lancement, il installe les dépendances.
2. Quand le pare-feu Windows le demande, clique sur **Autoriser** (réseaux privés).
3. Sur l'iPhone, connecté au même Wi-Fi, scanne le QR code affiché ou ouvre l'URL et saisis le code.
4. Facultatif : dans Safari, Partager > **Sur l'écran d'accueil** pour l'ouvrir en plein écran, comme une app.

## Gestes
| Geste | Action |
|---|---|
| 1 doigt | déplacer la souris |
| tap / double tap | clic / double clic |
| appui long puis glisser | glisser-déposer, sélection |
| tap à 2 doigts | clic droit |
| glisser à 2 doigts | défilement |
| pincer | zoom |
| tap à 3 doigts | afficher ou masquer la barre |

## Options
- `start.bat --new-code` : génère un nouveau code d'accès.
- `start.bat --port 9000` : change le port (8765 par défaut).

## Depuis l'extérieur (4G, autre Wi-Fi)
N'ouvre pas le port sur ta box. Installe plutôt **Tailscale** (gratuit) sur le PC et sur l'iPhone, puis utilise l'IP Tailscale du PC : `http://100.x.x.x:8765`.

## Limites
Windows protège l'écran de connexion, les fenêtres UAC (administrateur) et Ctrl+Alt+Suppr : ils ne peuvent pas être contrôlés.
