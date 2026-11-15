# Gestionnaire MCP v1.0 - Serveur MCP de vos agents de codage

Application de bureau (Python / Tkinter) qui centralise la gestion des **serveurs MCP** déclarés
dans les fichiers de configuration de vos agents de codage : Claude Code, Claude Desktop, OpenCode,
Codex CLI, Gemini CLI, Qwen Code, VS Code (GitHub Copilot), GitHub Copilot CLI, Cursor, Windsurf,
Cline, Roo Code, Kilo Code, Kiro et LM Studio.

Pour chaque agent, l'application lit et écrit **uniquement la clé des serveurs MCP** de son fichier
de configuration ; tout le reste du fichier (modèles, providers, préférences…) est conservé. Une
sauvegarde est faite avant chaque écriture. Les copies d'un agent à l'autre sont converties
automatiquement au format de l'agent cible.

## Prérequis

- **Windows** (les emplacements de fichiers et le redémarrage automatique des applications sont
  prévus pour Windows ; le code contient aussi des emplacements macOS/Linux pour certains agents).
- Pour l'exécutable : rien d'autre.
- Pour lancer depuis les sources :
  - **Python 3.11 ou plus récent** (le module `tomllib`, requis pour lire la configuration TOML de
    Codex CLI, n'existe qu'à partir de 3.11) ;
  - **Tkinter** (inclus dans l'installateur Python officiel pour Windows).
  - Aucune dépendance externe : seule la bibliothèque standard est utilisée.
- Pour compiler l'exécutable : **PyInstaller** (`pip install pyinstaller`).
- Optionnel : **Node.js** (`npx`), nécessaire côté Claude Desktop lorsqu'un serveur distant (URL)
  y est converti en `npx -y mcp-remote <url>`.

## Installation

### Avec l'exécutable

Téléchargez `Gestionnaire-MCP-v1.0.exe` depuis la page des releases GitHub et lancez-le.

### Depuis les sources

```bat
git clone <url-du-depot>
cd GestionMCP_PixtrekCoder
python gestion_mcp.py
```

### Compiler l'exécutable

```bat
pip install pyinstaller
build.bat
```

L'exécutable est produit dans `dist\Gestionnaire-MCP-v1.0.exe` (configuration :
`Gestionnaire-MCP-v1.0.spec`, application fenêtrée sans console).

## Agents pris en charge

| Agent | Fichier (Windows) | Clé | Désactivation |
|---|---|---|---|
| Claude Code (CLI, VS Code, onglet Code de Desktop) | `%USERPROFILE%\.claude.json` | `mcpServers` | mis de côté* |
| Claude Desktop (chat) | `%LOCALAPPDATA%\Claude-Data\claude_*\claude_desktop_config.json` | `mcpServers` | mis de côté* |
| OpenCode | `%USERPROFILE%\.config\opencode\opencode.json` | `mcp` | `enabled: false` |
| Codex CLI | `%USERPROFILE%\.codex\config.toml` | `[mcp_servers.*]` | `enabled = false` |
| Gemini CLI / Qwen Code | `%USERPROFILE%\.gemini\settings.json` / `.qwen\settings.json` | `mcpServers` | mis de côté* |
| VS Code (GitHub Copilot) | `%APPDATA%\Code\User\mcp.json` | `servers` | mis de côté* |
| GitHub Copilot CLI | `%USERPROFILE%\.copilot\mcp-config.json` | `mcpServers` | mis de côté* |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` | `mcpServers` | mis de côté* |
| Windsurf | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | `mcpServers` | `disabled: true` |
| Cline / Roo Code / Kilo Code | `%APPDATA%\Code\User\globalStorage\<extension>\settings\…json` | `mcpServers` | `disabled: true` |
| Kiro | `%USERPROFILE%\.kiro\settings\mcp.json` | `mcpServers` | `disabled: true` |
| LM Studio | `%USERPROFILE%\.lmstudio\mcp.json` | `mcpServers` | mis de côté* |

\* L'agent n'a pas d'option « désactivé » : le serveur est retiré du fichier et gardé dans
`%APPDATA%\GestionMCP\reglages.json` ; « Activer » le remet.

Pour Claude Desktop, l'application cherche aussi `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`
et `%APPDATA%\Claude`, et signale les anciens fichiers ignorés qui contiennent encore des serveurs.

Un emplacement peut être changé par agent (menu **Agent › Utiliser un autre fichier…**).
Le menu **Fichier › Où sont les fichiers de chaque agent ?** affiche la liste complète.

## Utilisation

- **Vue d'ensemble** : tableau serveurs × agents. Double-clic sur une case = activer/désactiver,
  ou copier le serveur dans cet agent s'il n'y est pas.
- **Ajouter / Modifier** : formulaire simple (commande, arguments, variables, ou URL) et onglet
  **JSON** qui montre le format exact écrit pour l'agent. On peut y coller un JSON de n'importe
  quel format (Claude, OpenCode, VS Code…) : il est converti.
- **Copier vers…** : copie un ou plusieurs serveurs vers d'autres agents avec conversion
  (OpenCode `local/remote` + `command: [...]` ⇄ `command` + `args` ; URL → `npx mcp-remote` pour
  Claude Desktop ; `httpUrl` pour Gemini ; `serverUrl` pour Windsurf ; TOML pour Codex…).
- **Coller du JSON** : importe un ou plusieurs serveurs copiés depuis une documentation.
- **Fichier › Exporter les serveurs de l'agent…** : enregistre les serveurs de l'agent dans un
  fichier JSON.
- **Tester** : lance le serveur (local) ou interroge l'URL (distant) et liste ses outils.
- **Agent › Redémarrer l'application…** : ferme puis relance l'application graphique si elle est
  détectée (Claude Desktop, VS Code et ses extensions Cline/Roo/Kilo, Cursor, Windsurf, Kiro,
  LM Studio). Enregistre le travail avant de confirmer. Les agents en ligne de commande ne sont
  pas redémarrés automatiquement.
- **Agent › Restaurer une sauvegarde** : 30 dernières versions par agent
  (`%APPDATA%\GestionMCP\sauvegardes\<agent>`).

Après une modification, utilisez **Agent › Redémarrer l'application…** pour les applications
graphiques prises en charge ; pour les autres, relancez l'agent ou la session manuellement.

Les fichiers JSONC (avec commentaires) sont lus ; leurs commentaires sont supprimés à la première
modification, après confirmation.

### Données de l'application

| Élément | Emplacement |
|---|---|
| Réglages (emplacements personnalisés, serveurs mis de côté) | `%APPDATA%\GestionMCP\reglages.json` |
| Sauvegardes (30 par agent) | `%APPDATA%\GestionMCP\sauvegardes\<agent>\` |

### Non gérés

Configurations propres à un projet (`.mcp.json`, `opencode.json` de dossier, `.vscode/mcp.json`),
plugins et connecteurs claude.ai, Zed, Continue.

## Tests

Le dépôt ne contient pas de suite de tests automatisés. La version 1.0.0 a été validée par des
essais manuels de l'application.

## Licence

Distribué sous licence MIT. Voir le fichier [LICENSE](LICENSE).
