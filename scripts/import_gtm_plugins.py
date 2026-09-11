#!/usr/bin/env python3
"""
import_gtm_plugins.py — Récupère automatiquement les fichiers utiles
(commands, agents, skills) d'un ou plusieurs plugins du dépôt
gtmagents/gtm-agents, et produit un fichier JSON prêt à importer dans la
table SOURCE_FILES de la base de données.

Aucune copie manuelle nécessaire : le script télécharge le dépôt, lit les
fichiers, extrait le frontmatter (name/description/usage), et structure
tout automatiquement.

Usage :
    python import_gtm_plugins.py content-marketing seo email-marketing
    python import_gtm_plugins.py content-marketing --output content-marketing.json
    python import_gtm_plugins.py --all-in-list plugins_valides.txt

Sortie : un fichier JSON (liste d'objets), un par fichier source trouvé,
prêt à boucler dessus pour faire les INSERT en base (voir exemple SQL en
bas de ce fichier, fonction print_sql_example()).
"""
import argparse
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request

REPO_TARBALL_URL = "https://codeload.github.com/gtmagents/gtm-agents/tar.gz/main"


def download_and_extract_repo(dest_dir):
    """Télécharge l'archive du dépôt et l'extrait dans dest_dir. Retourne
    le chemin racine du dépôt extrait (ex: dest_dir/gtm-agents-main)."""
    tarball_path = os.path.join(dest_dir, "repo.tar.gz")
    print(f"Téléchargement du dépôt depuis {REPO_TARBALL_URL} ...", file=sys.stderr)
    urllib.request.urlretrieve(REPO_TARBALL_URL, tarball_path)

    with tarfile.open(tarball_path) as tar:
        tar.extractall(dest_dir)

    # Le dossier extrait s'appelle généralement gtm-agents-main
    for name in os.listdir(dest_dir):
        full = os.path.join(dest_dir, name)
        if os.path.isdir(full) and name.startswith("gtm-agents"):
            return full
    raise RuntimeError("Dossier du dépôt introuvable après extraction.")


def parse_frontmatter(raw_content):
    """Extrait le frontmatter YAML simple (entre --- ... ---) d'un fichier
    .md, sans dépendance externe (pas de vraie librairie YAML nécessaire
    pour ce format très simple clé: valeur)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw_content, re.DOTALL)
    if not match:
        return {}, raw_content
    frontmatter_text, body = match.groups()
    meta = {}
    for line in frontmatter_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


def collect_files_for_plugin(repo_root, plugin_name):
    """Parcourt commands/, agents/, skills/ d'un plugin donné et retourne
    une liste d'entrées structurées, une par fichier utile trouvé."""
    plugin_dir = os.path.join(repo_root, "plugins", plugin_name)
    if not os.path.isdir(plugin_dir):
        print(f"  ATTENTION : plugin '{plugin_name}' introuvable dans le dépôt, ignoré.",
              file=sys.stderr)
        return []

    entries = []

    # commands/ et agents/ : un seul niveau de fichiers .md
    for file_type, subfolder in (("command", "commands"), ("agent", "agents")):
        folder = os.path.join(plugin_dir, subfolder)
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(folder, fname)
            with open(fpath, encoding="utf-8") as f:
                raw = f.read()
            meta, _ = parse_frontmatter(raw)
            entries.append({
                "plugin_name": plugin_name,
                "file_type": file_type,
                "file_name": fname,
                "name": meta.get("name", fname.replace(".md", "")),
                "description": meta.get("description", ""),
                "usage": meta.get("usage", ""),
                # IMPORTANT : certains agents précisent leur propre modèle
                # (ex: "model: haiku" dans le frontmatter). Vide si non précisé
                # -> à ce moment-là on appliquera un modèle par défaut plus tard.
                "model": meta.get("model", ""),
                "raw_content": raw,
                "source_path": f"plugins/{plugin_name}/{subfolder}/{fname}",
            })

    # skills/ : un sous-dossier par skill, avec SKILL.md + éventuel assets/
    skills_folder = os.path.join(plugin_dir, "skills")
    if os.path.isdir(skills_folder):
        for skill_name in sorted(os.listdir(skills_folder)):
            skill_dir = os.path.join(skills_folder, skill_name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_md):
                with open(skill_md, encoding="utf-8") as f:
                    raw = f.read()
                meta, _ = parse_frontmatter(raw)
                entries.append({
                    "plugin_name": plugin_name,
                    "file_type": "skill",
                    "file_name": "SKILL.md",
                    "name": meta.get("name", skill_name),
                    "description": meta.get("description", ""),
                    "usage": "",
                    "raw_content": raw,
                    "source_path": f"plugins/{plugin_name}/skills/{skill_name}/SKILL.md",
                })

            assets_dir = os.path.join(skill_dir, "assets")
            if os.path.isdir(assets_dir):
                for aname in sorted(os.listdir(assets_dir)):
                    apath = os.path.join(assets_dir, aname)
                    if not os.path.isfile(apath):
                        continue
                    with open(apath, encoding="utf-8", errors="replace") as f:
                        raw = f.read()
                    entries.append({
                        "plugin_name": plugin_name,
                        "file_type": "asset",
                        "file_name": aname,
                        "name": f"{skill_name}/{aname}",
                        "description": f"Gabarit associé au skill '{skill_name}'",
                        "usage": "",
                        "raw_content": raw,
                        "source_path": f"plugins/{plugin_name}/skills/{skill_name}/assets/{aname}",
                    })

    return entries


def print_sql_example(entries, out=sys.stderr):
    """Affiche un exemple de requête SQL d'insertion (à adapter au schéma
    réel de la table SOURCE_FILES une fois créée)."""
    if not entries:
        return
    print("\n--- Exemple de requête SQL (à adapter à votre schéma) ---", file=out)
    print("""
CREATE TABLE IF NOT EXISTS source_files (
    id SERIAL PRIMARY KEY,
    plugin_name VARCHAR(100),
    file_type VARCHAR(20),
    file_name VARCHAR(150),
    name VARCHAR(150),
    description TEXT,
    usage TEXT,
    raw_content TEXT,
    source_path VARCHAR(255) UNIQUE,
    last_synced_at TIMESTAMP DEFAULT NOW()
);

-- Puis, pour chaque entrée du JSON généré :
INSERT INTO source_files
    (plugin_name, file_type, file_name, name, description, usage, raw_content, source_path)
VALUES (%(plugin_name)s, %(file_type)s, %(file_name)s, %(name)s,
        %(description)s, %(usage)s, %(raw_content)s, %(source_path)s)
ON CONFLICT (source_path) DO UPDATE SET
    raw_content = EXCLUDED.raw_content,
    last_synced_at = NOW();
""", file=out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="*", help="Noms des plugins à importer (ex: content-marketing seo)")
    parser.add_argument("--from-list", help="Fichier texte listant un nom de plugin par ligne")
    parser.add_argument("--output", default="gtm_source_files.json", help="Fichier JSON de sortie")
    parser.add_argument("--show-sql", action="store_true", help="Affiche un exemple de schéma + requête SQL")
    args = parser.parse_args()

    plugin_names = list(args.plugins)
    if args.from_list:
        with open(args.from_list, encoding="utf-8") as f:
            plugin_names += [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not plugin_names:
        sys.exit("Indique au moins un nom de plugin, ou un fichier avec --from-list.")

    with tempfile.TemporaryDirectory() as tmp:
        repo_root = download_and_extract_repo(tmp)

        all_entries = []
        for plugin_name in plugin_names:
            print(f"Import de '{plugin_name}'...", file=sys.stderr)
            entries = collect_files_for_plugin(repo_root, plugin_name)
            print(f"  -> {len(entries)} fichiers trouvés (commands + agents + skills + assets)",
                  file=sys.stderr)
            all_entries.extend(entries)

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_entries, f, ensure_ascii=False, indent=2)

        print(f"\n{len(all_entries)} fichiers écrits dans {args.output}", file=sys.stderr)

        if args.show_sql:
            print_sql_example(all_entries)


if __name__ == "__main__":
    main()
