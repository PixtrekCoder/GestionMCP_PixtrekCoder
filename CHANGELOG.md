# Changelog

Toutes les évolutions notables de ce projet sont consignées dans ce fichier.
Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le projet suit
le [versionnage sémantique](https://semver.org/lang/fr/).

## [1.0.0] - 2026-11-15

Première version publique.

### Ajouté

- Application de bureau Tkinter de gestion des serveurs MCP pour 15 agents de codage :
  Claude Code, Claude Desktop (chat), OpenCode, Codex CLI, Gemini CLI, Qwen Code,
  VS Code (GitHub Copilot), GitHub Copilot CLI, Cursor, Windsurf, Cline, Roo Code, Kilo Code,
  Kiro et LM Studio.
- Détection automatique des agents installés (fichier de configuration, dossier ou commande
  présents) et option pour afficher aussi les agents non détectés.
- Lecture et écriture limitées à la clé des serveurs MCP : le reste du fichier est conservé.
  Prise en charge du JSON, du JSONC (commentaires et virgules finales) et du TOML (Codex CLI).
- Écriture atomique et sauvegarde automatique avant chaque modification (30 versions par agent),
  avec restauration depuis l'application.
- Vue d'ensemble serveurs × agents : activation, désactivation ou copie par double-clic.
- Ajout et modification d'un serveur par formulaire (local : commande, arguments, variables
  d'environnement, dossier de travail ; distant : URL HTTP/SSE et en-têtes) ou par JSON.
- Conversion automatique entre les formats des agents lors des copies et des imports
  (OpenCode `local/remote`, `httpUrl` Gemini/Qwen, `serverUrl` Windsurf, sections TOML Codex,
  passerelle `npx -y mcp-remote` pour les URL dans Claude Desktop…).
- Désactivation native (`enabled` / `disabled`) lorsque l'agent la prend en charge, sinon mise
  de côté du serveur dans les réglages de l'application.
- Import de JSON collé depuis une documentation (formats `mcpServers`, `servers`, `mcp`,
  `mcp_servers`, `context_servers`, serveur seul) et export des serveurs d'un agent en JSON.
- Test d'un serveur : handshake MCP `initialize` + `tools/list` pour un serveur local,
  requête `initialize` en HTTP pour un serveur distant.
- Redémarrage, sous Windows, des applications graphiques prises en charge (Claude Desktop,
  VS Code, Cursor, Windsurf, Kiro, LM Studio).
- Emplacement de fichier personnalisable par agent et liste des emplacements de tous les agents.
- Avertissements : fichiers illisibles, anciens fichiers Claude Desktop ignorés, fichiers
  OpenCode fusionnés, commentaires JSONC qui seront perdus.
- Script `build.bat` et fichier `.spec` PyInstaller pour produire un exécutable Windows unique.
