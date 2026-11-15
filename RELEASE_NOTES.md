# Gestionnaire MCP v1.0.0

Première version publique du **Gestionnaire MCP**, une application de bureau Windows qui permet de
gérer en un seul endroit les serveurs MCP de vos agents de codage.

## Points forts

- **15 agents pris en charge** : Claude Code, Claude Desktop, OpenCode, Codex CLI, Gemini CLI,
  Qwen Code, VS Code (GitHub Copilot), GitHub Copilot CLI, Cursor, Windsurf, Cline, Roo Code,
  Kilo Code, Kiro et LM Studio.
- **Modifications sûres** : seule la clé des serveurs MCP est modifiée, le reste du fichier est
  conservé ; sauvegarde automatique avant chaque écriture (30 versions par agent) et restauration
  en un clic.
- **Vue d'ensemble** serveurs × agents : activer, désactiver ou copier un serveur par double-clic.
- **Conversion automatique** des formats lors des copies entre agents (JSON, JSONC, TOML, OpenCode,
  Gemini, Windsurf, `mcp-remote` pour Claude Desktop…).
- **Import / export JSON** : collez la configuration d'une documentation, quel que soit son format.
- **Test d'un serveur** : lancement et liste des outils exposés.
- **Redémarrage** des applications graphiques prises en charge pour appliquer les changements.

## Installation

Téléchargez `Gestionnaire-MCP-v1.0.exe` ci-dessous et lancez-le. Aucune installation n'est
nécessaire.

Depuis les sources : Python 3.11 ou plus récent avec Tkinter, puis `python gestion_mcp.py`.

## Limites connues

- Les configurations propres à un projet (`.mcp.json`, `opencode.json` de dossier,
  `.vscode/mcp.json`), les plugins et connecteurs claude.ai, Zed et Continue ne sont pas gérés.
- Le redémarrage automatique des applications n'est disponible que sous Windows ; les agents en
  ligne de commande doivent être relancés manuellement.
- Les commentaires d'un fichier JSONC sont supprimés à la première modification (après
  confirmation, avec sauvegarde).

Le détail des fonctionnalités se trouve dans [CHANGELOG.md](CHANGELOG.md).
