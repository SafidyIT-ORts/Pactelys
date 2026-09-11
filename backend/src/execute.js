/**
 * routes/execute.js — Route POST /v1/actions/execute
 *
 * LA PIÈCE CENTRALE : remplace les 8+ étapes n8n par 1 seul appel.
 *
 * n8n envoie juste : { task_id, org_id }
 * Fastify fait TOUT :
 *   1. Récupère la tâche depuis PostgreSQL
 *   2. Vérifie que l'action est activée pour l'organisation
 *   3. Récupère le(s) prompt template(s) (simple OU pipeline multi-étapes)
 *   4. Récupère les fichiers markdown du client (Brand.md, S01.md, CLAUDE.md)
 *   5. Injecte les variables dans le prompt ({{BRAND_MD}}, {{S01_MD}}, etc.)
 *   6. Appelle Claude (via claudeBrain.js) — gère les retries si nécessaire
 *   7. Sauvegarde le résultat dans tasks
 *   8. Renvoie la réponse propre à n8n
 *
 * POURQUOI CETTE ARCHITECTURE :
 *   - n8n = orchestrateur léger (2-3 nœuds max), pas un moteur de prompt
 *   - Fastify = cerveau — gère la logique, les erreurs, le multi-step pipeline
 *   - Zéro fragmentation : si Claude plante, Fastify renvoie une erreur claire
 *   - Les pipelines multi-étapes fonctionnent réellement (bug n8n corrigé)
 *
 * RÉSOUT LES 3 BUG IDENTIFIÉS DANS LE WORKFLOW N8N :
 *   ❌ Bug 1 : steps[0].step_order == 1 toujours vrai → corrigé : on itère sur TOUTES les étapes
 *   ❌ Bug 2 : .replaceAll() fragile sur les clés des .md → corrigé : injection serveur, clés normalisées
 *   ❌ Bug 3 : n8n appelle Claude directement → corrigé : Fastify appelle Claude, n8n ne touche plus à l'IA
 */

import claudeBrain from '../services/claudeBrain.js'

// ============================================================
// CONSTANTES
// ============================================================

/** Clés de remplacement dans les templates, normalisées pour éviter les bugs de casse */
const PLACEHOLDER_MAP = {
  '{{BRAND_MD}}':  'brand',
  '{{S01_MD}}':    's01',
  '{{CLAUDE_MD}}': 'claude',
}

// ============================================================
// PLUGIN FASTIFY
// ============================================================

/**
 * @param {import('fastify').FastifyInstance} fastify
 */
export async function executeRoutes(fastify) {

  // ----------------------------------------------------------
  // POST /v1/actions/execute
  // ----------------------------------------------------------
  fastify.post('/v1/actions/execute', {
    schema: {
      tags:        ['actions'],
      summary:     'Exécute une tâche IA en bout en bout (appelé par n8n)',
      description: `
        Remplace les 8+ étapes n8n par 1 appel. Fastify fait tout en interne :
        récupération du template, injection des .md, appel Claude, sauvegarde.
        n8n reçoit juste le résultat propre et peut enchaîner l'action finale.
      `.trim(),
      body: {
        type: 'object',
        required: ['task_id', 'org_id'],
        properties: {
          task_id: { type: 'string', format: 'uuid', description: 'ID de la tâche à exécuter' },
          org_id:  { type: 'string', format: 'uuid', description: 'ID de l\'organisation' },
          model:   { type: 'string', description: 'Modèle Claude (optionnel, surcharge le template)' },
        },
      },
      response: {
        200: {
          type: 'object',
          properties: {
            success:       { type: 'boolean' },
            task_id:       { type: 'string' },
            action_id:     { type: 'string' },
            status:        { type: 'string' },
            result:        { type: 'string', description: 'Résultat généré par Claude' },
            steps_executed:{ type: 'number', description: 'Nombre d\'étapes pipeline exécutées' },
            model_used:    { type: 'string' },
            duration_ms:   { type: 'number' },
          },
        },
      },
    },
  }, async (request, reply) => {
    const startTime = Date.now()
    const { task_id, org_id, model: modelOverride } = request.body

    const db = await fastify.pg.connect()
    try {

      // ---- 1. MARQUER LA TÂCHE EN COURS ----
      await db.query(
        `UPDATE tasks SET status = 'in_progress', updated_at = NOW() WHERE id = $1 AND org_id = $2`,
        [task_id, org_id]
      )

      // ---- 2. RÉCUPÉRER LA TÂCHE ----
      const taskResult = await db.query(
        `SELECT t.id, t.action_id, t.params, t.status, t.org_id
         FROM tasks t
         WHERE t.id = $1 AND t.org_id = $2`,
        [task_id, org_id]
      )

      if (taskResult.rowCount === 0) {
        await _markTaskError(db, task_id, 'Tâche introuvable ou appartient à une autre organisation')
        return reply.code(404).send({ success: false, error: 'Tâche introuvable.' })
      }

      const task = taskResult.rows[0]

      // ---- 3. VÉRIFIER QUE L'ACTION EST ACTIVÉE POUR CETTE ORG ----
      const authCheck = await db.query(
        `SELECT 1 FROM client_enabled_actions WHERE org_id = $1 AND action_id = $2`,
        [org_id, task.action_id]
      )

      if (authCheck.rowCount === 0) {
        await _markTaskError(db, task_id, `Action "${task.action_id}" non activée pour cette organisation`)
        return reply.code(403).send({
          success: false,
          error: `Action "${task.action_id}" non activée pour votre organisation.`,
        })
      }

      // ---- 4. RÉCUPÉRER TOUS LES STEPS DU TEMPLATE (SIMPLE OU PIPELINE) ----
      // Trie par step_order ASC pour respecter l'ordre du pipeline
      const templateResult = await db.query(
        `SELECT step_order, model, tools_config, system_prompt_final
         FROM prompt_templates
         WHERE action_id = $1 AND is_active = true
         ORDER BY step_order ASC`,
        [task.action_id]
      )

      if (templateResult.rowCount === 0) {
        await _markTaskError(db, task_id, `Aucun template actif pour l'action "${task.action_id}"`)
        return reply.code(404).send({ success: false, error: `Template introuvable pour l'action "${task.action_id}".` })
      }

      const steps = templateResult.rows
      const isPipeline = steps.length > 1  // ← CORRECTION BUG N8N : on vérifie le NOMBRE d'étapes, pas steps[0]

      request.log.info({
        task_id,
        action_id: task.action_id,
        steps_count: steps.length,
        is_pipeline: isPipeline,
      }, '[Execute] Tâche démarrée')

      // ---- 5. RÉCUPÉRER LES FICHIERS MARKDOWN DU CLIENT ----
      let clientFiles
      try {
        clientFiles = await claudeBrain.readClientConfig(org_id)
      } catch (fileErr) {
        // Fallback : essayer avec les fichiers bruts en BDD si les fichiers disque sont absents
        request.log.warn({ org_id, error: fileErr.message }, '[Execute] Fichiers disque absents, fallback BDD')
        clientFiles = await _getClientFilesFromDB(db, org_id)
      }

      if (!clientFiles) {
        await _markTaskError(db, task_id, 'Impossible de récupérer les fichiers de configuration client')
        return reply.code(404).send({ success: false, error: 'Configuration client introuvable.' })
      }

      // ---- 6. EXÉCUTION DES STEPS (SIMPLE OU PIPELINE) ----
      let finalResult = null
      let lastModel = null

      // En mode pipeline, le résultat d'une étape devient l'input de la suivante
      let pipelineContext = _buildUserInput(task.params)

      for (const step of steps) {
        const model = modelOverride || step.model || 'claude-3-5-sonnet-20241022'
        lastModel = model

        // Injection des fichiers .md dans le template (CÔTÉ SERVEUR — plus de .replaceAll fragile dans n8n)
        const resolvedPrompt = _injectMarkdownFiles(step.system_prompt_final, clientFiles)

        // Construire le message utilisateur pour cette étape
        // En pipeline : le résultat précédent est ajouté au contexte
        const userMessage = isPipeline && finalResult
          ? `${pipelineContext}\n\n--- RÉSULTAT ÉTAPE PRÉCÉDENTE ---\n${finalResult}`
          : pipelineContext

        request.log.info({
          task_id,
          step_order: step.step_order,
          model,
          is_pipeline: isPipeline,
        }, `[Execute] Appel Claude — étape ${step.step_order}/${steps.length}`)

        // Appel Claude via claudeBrain (gestion d'erreur interne)
        const claudeResponse = await claudeBrain.callClaudeAPI(resolvedPrompt, userMessage, model)
        finalResult = claudeResponse
      }

      // ---- 7. SAUVEGARDER LE RÉSULTAT ----
      const duration = Date.now() - startTime

      await db.query(
        `UPDATE tasks
         SET status = 'completed', result = $1, updated_at = NOW()
         WHERE id = $2`,
        [finalResult, task_id]
      )

      request.log.info({
        task_id,
        action_id: task.action_id,
        steps_executed: steps.length,
        duration_ms: duration,
      }, '[Execute] Tâche complétée avec succès')

      // ---- 8. RÉPONDRE À N8N ----
      return reply.code(200).send({
        success:        true,
        task_id,
        action_id:      task.action_id,
        status:         'completed',
        result:         finalResult,
        steps_executed: steps.length,
        model_used:     lastModel,
        duration_ms:    duration,
      })

    } catch (err) {
      // Gestion d'erreur globale — marque la tâche en erreur avant de répondre
      const errorMessage = err.message || 'Erreur inconnue'
      request.log.error({ task_id, error: errorMessage, stack: err.stack }, '[Execute] Erreur critique')

      try {
        await _markTaskError(db, task_id, errorMessage)
      } catch (dbErr) {
        request.log.error({ dbErr: dbErr.message }, '[Execute] Impossible de marquer la tâche en erreur')
      }

      return reply.code(500).send({
        success: false,
        task_id,
        error:   'Erreur interne lors de l\'exécution de la tâche.',
        detail:  process.env.NODE_ENV === 'development' ? errorMessage : undefined,
      })
    } finally {
      db.release()
    }
  })

  // ----------------------------------------------------------
  // GET /v1/actions/status/:task_id
  // Route légère pour que n8n poll le statut si besoin
  // ----------------------------------------------------------
  fastify.get('/v1/actions/status/:task_id', {
    schema: {
      tags:    ['actions'],
      summary: 'Récupère le statut d\'une tâche (polling léger pour n8n)',
    },
  }, async (request, reply) => {
    const { task_id } = request.params
    const db = await fastify.pg.connect()
    try {
      const result = await db.query(
        `SELECT id, action_id, status, result, error_message,
                created_at, updated_at
         FROM tasks WHERE id = $1`,
        [task_id]
      )
      if (result.rowCount === 0) {
        return reply.code(404).send({ success: false, error: 'Tâche introuvable.' })
      }
      const task = result.rows[0]
      return reply.send({
        success:       true,
        task_id:       task.id,
        action_id:     task.action_id,
        status:        task.status,
        result:        task.status === 'completed' ? task.result : null,
        error_message: task.status === 'error' ? task.error_message : null,
        created_at:    task.created_at,
        updated_at:    task.updated_at,
      })
    } finally {
      db.release()
    }
  })
}

// ============================================================
// HELPERS INTERNES
// ============================================================

/**
 * Injecte les fichiers .md dans un template de prompt.
 * Gère les deux formes : {{BRAND_MD}} et {{brand_md}} (insensible à la casse).
 *
 * CORRECTION DU BUG N8N : cette injection se fait serveur-side,
 * plus de .replaceAll() fragile dans un nœud Code n8n.
 *
 * @param {string} template - Le system_prompt_final avec des placeholders
 * @param {{ brand: string, s01: string, claude: string }} files - Contenu des fichiers .md
 * @returns {string} - Le prompt avec les fichiers injectés
 */
function _injectMarkdownFiles(template, files) {
  let result = template

  // Remplacement insensible à la casse pour {{BRAND_MD}}, {{brand_md}}, etc.
  result = result.replace(/\{\{BRAND_MD\}\}/gi,  files.brand  || '')
  result = result.replace(/\{\{S01_MD\}\}/gi,    files.s01    || '')
  result = result.replace(/\{\{CLAUDE_MD\}\}/gi, files.claude || '')

  // Formes alternatives parfois utilisées dans les templates GTM
  result = result.replace(/\{\{BRAND\}\}/gi,  files.brand  || '')
  result = result.replace(/\{\{ICP\}\}/gi,    files.s01    || '')
  result = result.replace(/\{\{CONFIG\}\}/gi, files.claude || '')

  return result
}

/**
 * Construit le message utilisateur depuis les params de la tâche.
 * Les params sont stockés en JSONB — on les sérialise de manière lisible.
 *
 * @param {Object} params - Paramètres JSONB de la tâche
 * @returns {string}
 */
function _buildUserInput(params) {
  if (!params) return ''
  if (typeof params === 'string') return params

  // Si params contient un champ "input" ou "user_input", l'utiliser directement
  if (params.input)      return params.input
  if (params.user_input) return params.user_input

  // Sinon, sérialiser tout le JSONB en texte lisible pour Claude
  return Object.entries(params)
    .map(([key, val]) => `${key}: ${typeof val === 'object' ? JSON.stringify(val) : val}`)
    .join('\n')
}

/**
 * Fallback : récupère les fichiers markdown depuis la BDD
 * si les fichiers disque (/app/clients/{org_id}/) sont absents.
 *
 * @param {import('pg').PoolClient} db
 * @param {string} orgId
 * @returns {Promise<{ brand: string, s01: string, claude: string } | null>}
 */
async function _getClientFilesFromDB(db, orgId) {
  const result = await db.query(
    `SELECT brand_md_raw, s01_md_raw, claude_md_raw
     FROM client_config
     WHERE org_id = $1`,
    [orgId]
  )
  if (result.rowCount === 0) return null
  const row = result.rows[0]
  return {
    brand:  row.brand_md_raw  || '',
    s01:    row.s01_md_raw    || '',
    claude: row.claude_md_raw || '',
  }
}

/**
 * Marque une tâche en état d'erreur dans PostgreSQL.
 *
 * @param {import('pg').PoolClient} db
 * @param {string} taskId
 * @param {string} errorMessage
 */
async function _markTaskError(db, taskId, errorMessage) {
  await db.query(
    `UPDATE tasks SET status = 'error', error_message = $1, updated_at = NOW() WHERE id = $2`,
    [errorMessage, taskId]
  )
}
