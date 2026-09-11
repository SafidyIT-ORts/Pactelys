#!/usr/bin/env python3
"""
build_prompt_templates.py (v2) — Prend le JSON produit par
import_gtm_plugins.py et un fichier de "recette", et produit les
system_prompt_final prêts pour la table PROMPT_TEMPLATES.

Gère DEUX types d'actions, à ne pas confondre :

  - "simple"   : la commande ne mobilise qu'un seul agent -> UNE seule
                 ligne en sortie, avec un seul system_prompt_final et un
                 seul modèle (celui de l'agent si précisé dans son
                 frontmatter, sinon le modèle par défaut donné).

  - "pipeline" : la commande orchestre plusieurs agents (ex: audit
                 technique -> priorisation -> déploiement) -> PLUSIEURS
                 lignes en sortie, une par étape, dans l'ordre, CHACUNE
                 avec son propre modèle. Ne jamais fusionner plusieurs
                 agents d'une orchestration en un seul prompt : ce sont
                 des rôles distincts, parfois avec des modèles différents
                 (ex: Haiku pour l'exécution, Sonnet pour la décision).

La sélection "quels fichiers combiner, dans quel ordre" reste une
décision humaine, à faire une fois dans recipes.json.

Usage :
    python build_prompt_templates.py \\
        --source gtm_source_files.json \\
        --recipes recipes.json \\
        --output prompt_templates.json
"""
import argparsea
import json
import os
import sys

DEFAULT_MODEL = "claude-sonnet-4-6"

# Alias volontairement simple : les fichiers agents utilisent souvent des
# noms courts ("haiku", "sonnet") dans leur frontmatter plutôt que le nom
# complet du modèle. Fait explicite ici pour ne rien deviner en silence.
MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}

EXAMPLE_RECIPES = [
    {
        "action_id": "generate_blog_article",
        "label": "Rédiger un article de blog",
        "type": "simple",
        "uses_web_search": False,
        "source_names": ["generate-blog", "blog-writer", "seo-writing"]
    },
    {
        "action_id": "seo_full_audit_orchestrated",
        "label": "Audit SEO complet orchestré (technique -> priorisation -> déploiement)",
        "type": "pipeline",
        "steps": [
            {
                "step_order": 1,
                "label": "Audit technique",
                "uses_web_search": True,
                "source_names": ["audit-technical", "technical-analyst"]
            },
            {
                "step_order": 2,
                "label": "Priorisation stratégique",
                "uses_web_search": False,
                "source_names": ["prioritize-keywords", "seo-director"]
            },
            {
                "step_order": 3,
                "label": "Déploiement des optimisations",
                "uses_web_search": False,
                "source_names": ["deploy-optimizations", "on-page-lead"]
            }
        ]
    }
]

CLIENT_CONTEXT_BLOCK = """

CONTEXTE CLIENT — SOURCE DE VÉRITÉ, À RESPECTER STRICTEMENT
--- Brand.md ---
{{BRAND_MD}}
--- S01.md (ICP) ---
{{S01_MD}}
--- Règles complémentaires (CLAUDE.md) ---
{{CLAUDE_MD}}
"""

PREVIOUS_STEP_BLOCK = """

RÉSULTAT DE L'ÉTAPE PRÉCÉDENTE — À UTILISER COMME CONTEXTE D'ENTRÉE
{{PREVIOUS_STEP_RESULT}}
"""


def load_source_files(path):
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    by_name = {}
    for e in entries:
        by_name.setdefault(e["name"], []).append(e)
    return by_name


def strip_frontmatter_body(raw_content):
    if raw_content.startswith("---"):
        parts = raw_content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw_content.strip()


def resolve_model(source_names, source_by_name, default_model):
    """Cherche si un des fichiers combinés (typiquement l'agent) précise
    un modèle dans son frontmatter. Le premier trouvé gagne. Si aucun
    fichier n'en précise, retombe sur default_model — jamais un choix
    silencieux non tracé : on log toujours d'où vient le modèle retenu."""
    for name in source_names:
        for entry in source_by_name.get(name, []):
            raw_model = (entry.get("model") or "").strip().lower()
            if raw_model:
                resolved = MODEL_ALIASES.get(raw_model, raw_model)
                return resolved, f"agent '{name}' (frontmatter: model={raw_model})"
    return default_model, "défaut (aucun agent combiné ne précisait de modèle)"


def build_fused_prompt(source_names, source_by_name, include_previous_step=False):
    pieces = []
    missing = []
    for name in source_names:
        matches = source_by_name.get(name)
        if not matches:
            missing.append(name)
            continue
        entry = matches[0]
        pieces.append(f"--- {entry['file_type'].upper()} : {name} ---\n"
                       f"{strip_frontmatter_body(entry['raw_content'])}")

    prompt = "\n\n".join(pieces)
    if include_previous_step:
        prompt += PREVIOUS_STEP_BLOCK
    prompt += CLIENT_CONTEXT_BLOCK
    return prompt, missing


def process_simple(recipe, source_by_name, default_model):
    model, model_source = resolve_model(recipe["source_names"], source_by_name, default_model)
    prompt, missing = build_fused_prompt(recipe["source_names"], source_by_name)
    if missing:
        print(f"  ATTENTION '{recipe['action_id']}' : fichiers introuvables : {missing}",
              file=sys.stderr)
    print(f"  Modèle retenu : {model}  (source : {model_source})", file=sys.stderr)

    return [{
        "action_id": recipe["action_id"],
        "label": recipe["label"],
        "type": "simple",
        "step_order": 1,
        "model": model,
        "tools_config": {"web_search": True} if recipe.get("uses_web_search") else {},
        "source_names_used": recipe["source_names"],
        "system_prompt_final": prompt,
    }]


def process_pipeline(recipe, source_by_name, default_model):
    rows = []
    for step in recipe["steps"]:
        model, model_source = resolve_model(step["source_names"], source_by_name, default_model)
        prompt, missing = build_fused_prompt(
            step["source_names"], source_by_name,
            include_previous_step=(step["step_order"] > 1)
        )
        if missing:
            print(f"  ATTENTION '{recipe['action_id']}' étape {step['step_order']} : "
                  f"fichiers introuvables : {missing}", file=sys.stderr)
        print(f"  Étape {step['step_order']} ({step['label']}) — "
              f"modèle retenu : {model}  (source : {model_source})", file=sys.stderr)

        rows.append({
            "action_id": recipe["action_id"],
            "label": f"{recipe['label']} — étape {step['step_order']} : {step['label']}",
            "type": "pipeline",
            "step_order": step["step_order"],
            "model": model,
            "tools_config": {"web_search": True} if step.get("uses_web_search") else {},
            "source_names_used": step["source_names"],
            "system_prompt_final": prompt,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--recipes", required=True)
    parser.add_argument("--output", default="prompt_templates.json")
    parser.add_argument("--default-model", default=DEFAULT_MODEL,
                         help="Modèle à utiliser si aucun agent combiné n'en précise un")
    args = parser.parse_args()

    if not os.path.exists(args.recipes):
        with open(args.recipes, "w", encoding="utf-8") as f:
            json.dump(EXAMPLE_RECIPES, f, ensure_ascii=False, indent=2)
        print(f"'{args.recipes}' n'existait pas — un exemple (1 action simple + "
              f"1 action pipeline à 3 étapes) a été créé. Modifie-le puis relance.",
              file=sys.stderr)
        return

    source_by_name = load_source_files(args.source)

    with open(args.recipes, encoding="utf-8") as f:
        recipes = json.load(f)

    all_rows = []
    for recipe in recipes:
        recipe_type = recipe.get("type", "simple")
        print(f"Construction de '{recipe['action_id']}' (type: {recipe_type})...", file=sys.stderr)
        if recipe_type == "simple":
            all_rows.extend(process_simple(recipe, source_by_name, args.default_model))
        elif recipe_type == "pipeline":
            all_rows.extend(process_pipeline(recipe, source_by_name, args.default_model))
        else:
            print(f"  ATTENTION : type inconnu '{recipe_type}', action ignorée.", file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)

    print(f"\n{len(all_rows)} lignes PROMPT_TEMPLATES écrites dans {args.output}", file=sys.stderr)
    print("Note : une action 'pipeline' produit plusieurs lignes (même action_id, "
          "step_order différent) -> à exécuter dans n8n dans l'ordre de step_order, "
          "en injectant le résultat de l'étape N dans {{PREVIOUS_STEP_RESULT}} de l'étape N+1.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
