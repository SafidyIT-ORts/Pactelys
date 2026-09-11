#!/usr/bin/env python3
"""
load_to_postgres.py — Dernière étape du pipeline : insère le JSON produit
par build_prompt_templates.py dans la table prompt_templates de
PostgreSQL.

IMPORTANT — où et comment lancer ce script :
- À lancer manuellement (ou via une tâche planifiée simple) sur le même
  serveur Docker/VPS que PostgreSQL, PAS depuis WordPress, PAS depuis n8n.
- C'est un script de maintenance interne (au même titre qu'une migration
  de base de données) : il se connecte directement à PostgreSQL, il ne
  passe pas par l'API Fastify (ce n'est pas une action cliente, personne
  d'externe ne doit pouvoir la déclencher).
- La chaîne de connexion vient de la variable d'environnement DATABASE_URL
  (déjà utilisée par Fastify normalement — même base, mêmes identifiants).

Pré-requis table (à adapter/valider avec le schéma réel choisi par
l'équipe Fastify — voici une proposition minimale) :

    CREATE TABLE IF NOT EXISTS prompt_templates (
        id SERIAL PRIMARY KEY,
        action_id VARCHAR(150) NOT NULL,
        label VARCHAR(255),
        type VARCHAR(20) NOT NULL,          -- 'simple' | 'pipeline'
        step_order INT NOT NULL DEFAULT 1,
        model VARCHAR(100) NOT NULL,
        tools_config JSONB DEFAULT '{}',
        source_names_used JSONB,
        system_prompt_final TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (action_id, step_order)
    );

Usage :
    export DATABASE_URL="postgresql://user:password@localhost:5432/pactelys"
    python load_to_postgres.py --input prompt_templates.json
    python load_to_postgres.py --input prompt_templates.json --dry-run
"""
import argparse
import json
import os
import sys


def get_connection():
    try:
        import psycopg2
    except ImportError:
        sys.exit(
            "La librairie psycopg2 n'est pas installée.\n"
            "Installe-la avec : pip install psycopg2-binary --break-system-packages"
        )
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Variable d'environnement DATABASE_URL manquante.")
    return psycopg2.connect(dsn)


def upsert_rows(conn, rows, dry_run=False):
    if dry_run:
        print(f"[DRY RUN] {len(rows)} lignes seraient insérées/mises à jour "
              f"(aucune écriture réelle effectuée).", file=sys.stderr)
        for r in rows:
            print(f"  - {r['action_id']} (étape {r['step_order']}, "
                  f"modèle: {r['model']})", file=sys.stderr)
        return

    query = """
        INSERT INTO prompt_templates
            (action_id, label, type, step_order, model, tools_config,
             source_names_used, system_prompt_final, updated_at)
        VALUES (%(action_id)s, %(label)s, %(type)s, %(step_order)s,
                %(model)s, %(tools_config)s, %(source_names_used)s,
                %(system_prompt_final)s, NOW())
        ON CONFLICT (action_id, step_order) DO UPDATE SET
            label = EXCLUDED.label,
            type = EXCLUDED.type,
            model = EXCLUDED.model,
            tools_config = EXCLUDED.tools_config,
            source_names_used = EXCLUDED.source_names_used,
            system_prompt_final = EXCLUDED.system_prompt_final,
            updated_at = NOW();
    """

    with conn.cursor() as cur:
        for r in rows:
            cur.execute(query, {
                "action_id": r["action_id"],
                "label": r["label"],
                "type": r["type"],
                "step_order": r["step_order"],
                "model": r["model"],
                "tools_config": json.dumps(r["tools_config"]),
                "source_names_used": json.dumps(r["source_names_used"]),
                "system_prompt_final": r["system_prompt_final"],
            })
    conn.commit()
    print(f"{len(rows)} lignes insérées/mises à jour dans prompt_templates.",
          file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True,
                         help="Fichier JSON produit par build_prompt_templates.py")
    parser.add_argument("--dry-run", action="store_true",
                         help="Affiche ce qui serait fait, sans écrire en base")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = json.load(f)

    if not rows:
        print("Aucune ligne à insérer (fichier vide).", file=sys.stderr)
        return

    if args.dry_run:
        upsert_rows(None, rows, dry_run=True)
        return

    conn = get_connection()
    try:
        upsert_rows(conn, rows, dry_run=False)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
