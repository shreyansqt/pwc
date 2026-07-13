/**
 * PWC hub Worker — a small task-database API mirroring `scripts/taskdb.py` 1:1
 * (plus `export`/`import`, new to the hub; see docs/hub-design.md).
 *
 * Every workspace's rows share one D1 database, distinguished by a `workspace`
 * column (hub/schema.sql). Routes are `POST /w/:workspace/:op`, where `:op` is
 * a taskdb.py subcommand name and the JSON body carries that subcommand's
 * argparse dest fields. Responses mirror what `emit()` prints for that
 * subcommand; errors raised via `fail()` become `HTTP 400 {"error": "..."}`.
 *
 * No dependencies beyond @cloudflare/workers-types. Single file by design —
 * small and reviewable beats layered, per the brief.
 */

export interface Env {
	DB: D1Database;
	PWC_TOKEN: string;
}

// ── shared row shapes ─────────────────────────────────────────────────────

interface TaskRow {
	id: string;
	type: string;
	title: string;
	status: string;
	priority: number | null;
	notes: string | null;
	parked: number;
	parked_reason: string | null;
	archived_at: string | null;
	session_id: string | null;
	harness: string | null;
	model: string | null;
	runhost: string | null;
	workdir: string | null;
	inline: number;
	created_at: string;
	updated_at: string;
	last_event_at: string | null;
}

interface SummaryRow {
	id: string;
	type: string;
	title: string;
	status: string;
	priority: number | null;
	parked: number;
	parked_reason: string | null;
	archived_at: string | null;
	workdir: string | null;
	session_id: string | null;
	harness: string | null;
	model: string | null;
	runhost: string | null;
	last_event_at: string | null;
}

interface RefRow {
	id?: number;
	task_id?: string;
	kind: string;
	ref_type: string;
	value: string;
	label: string | null;
	created_at: string;
}

interface EventRow {
	id?: number;
	task_id: string | null;
	at: string;
	source: string;
	kind: string;
	detail: string | null;
}

// The always-loaded summary columns (the index tier) — mirrors taskdb.py's
// _SUMMARY_COLS.
const SUMMARY_COLS =
	'id, type, title, status, priority, parked, parked_reason, ' +
	'archived_at, workdir, session_id, harness, model, runhost, last_event_at';

// ── errors ─────────────────────────────────────────────────────────────────

/** Mirrors _common.fail(): a diagnostic that becomes an HTTP 400 {"error"}. */
class HubError extends Error {}

function fail(msg: string): never {
	throw new HubError(msg);
}

// ── time helpers (mirror _common.now_iso / days_ago_iso) ──────────────────

function nowIso(): string {
	return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function daysAgoIso(days: number): string {
	const t = new Date(Date.now() - days * 86400 * 1000);
	return t.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

// ── ids: slugify, dedup, resolve-through-aliases ───────────────────────────

/** Lowercase, alphanumeric-and-hyphen slug from free text (first few words). */
function slugify(text: string | undefined | null, maxwords = 4): string {
	const words = (text || '').toLowerCase().match(/[a-z0-9]+/g) || [];
	const slug = words.slice(0, maxwords).join('-');
	return slug || 'task';
}

/** Return `base`, or base-2/-3/... if it (or an alias) is already taken in this workspace. */
async function dedupId(db: D1Database, workspace: string, base: string): Promise<string> {
	let candidate = base;
	let n = 1;
	// eslint-disable-next-line no-constant-condition
	while (true) {
		const hit = await db
			.prepare(
				'SELECT 1 FROM tasks WHERE workspace = ? AND id = ? ' +
					'UNION SELECT 1 FROM task_aliases WHERE workspace = ? AND alias = ?'
			)
			.bind(workspace, candidate, workspace, candidate)
			.first();
		if (!hit) return candidate;
		n += 1;
		candidate = `${base}-${n}`;
	}
}

/** Map any id or former alias to the current canonical tasks.id (or null). */
async function resolveId(db: D1Database, workspace: string, taskId: string): Promise<string | null> {
	const direct = await db
		.prepare('SELECT 1 FROM tasks WHERE workspace = ? AND id = ?')
		.bind(workspace, taskId)
		.first();
	if (direct) return taskId;
	const row = await db
		.prepare('SELECT task_id FROM task_aliases WHERE workspace = ? AND alias = ?')
		.bind(workspace, taskId)
		.first<{ task_id: string }>();
	return row ? row.task_id : null;
}

/** Fetch a task by id OR a former alias. Throws (like _require_task/fail) if not found. */
async function requireTask(db: D1Database, workspace: string, taskId: string): Promise<TaskRow> {
	const canonical = await resolveId(db, workspace, taskId);
	if (canonical === null) fail(`no task ${JSON.stringify(taskId)}`);
	const row = await db
		.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?')
		.bind(workspace, canonical)
		.first<TaskRow>();
	// canonical came from resolveId against the same table, so this is always found.
	return row as TaskRow;
}

// ── request body types (argparse dest fields, per op) ──────────────────────

interface SummaryBody {
	all?: boolean;
	archived?: boolean;
	done_within_days?: number;
}
interface DetailBody {
	task: string;
}
interface ThresholdBody {
	threshold_days?: number;
}
interface EventsBody {
	since?: string;
	task?: string;
}
interface FindRefsBody {
	ref_type?: string;
	kind?: 'identity' | 'working';
	value: string;
}
interface FindSessionBody {
	session_id: string;
}
interface AddTaskBody {
	task?: string;
	type: string;
	title: string;
	status?: string;
	priority?: number | null;
	notes?: string | null;
	workdir?: string | null;
	harness?: string | null;
	model?: string | null;
	runhost?: string | null;
	parked?: boolean;
	parked_reason?: string | null;
	inline?: boolean;
}
interface UpdateTaskBody {
	task: string;
	title?: string | null;
	status?: string | null;
	priority?: number | null;
	notes?: string | null;
	workdir?: string | null;
	harness?: string | null;
	model?: string | null;
	runhost?: string | null;
	parked_reason?: string | null;
	parked?: 0 | 1 | null;
}
interface AddRefBody {
	task: string;
	kind: 'identity' | 'working';
	ref_type: string;
	value: string;
	label?: string | null;
}
interface LogEventBody {
	task?: string | null;
	source?: 'coordinator' | 'worker' | 'brief' | 'system';
	kind: string;
	detail?: string | null;
	set_status?: string | null;
}
interface SetSessionBody {
	task: string;
	session_id: string;
	workdir?: string | null;
	harness?: string | null;
	model?: string | null;
}
interface ClearSessionBody {
	task: string;
}
interface ArchiveBody {
	task: string;
	reason?: string | null;
	unarchive?: boolean;
}
interface PromoteBody {
	task: string;
	new_id: string;
}
interface MergeBody {
	from: string;
	into: string;
}
interface ExportShape {
	tasks: Record<string, unknown>[];
	task_aliases: Record<string, unknown>[];
	task_refs: Record<string, unknown>[];
	events: Record<string, unknown>[];
	task_sessions: Record<string, unknown>[];
}

// ── row helpers ─────────────────────────────────────────────────────────────

/** Strip the workspace column before returning a row over the API. */
function omitWorkspace<T extends Record<string, unknown>>(row: T): Omit<T, 'workspace'> {
	const { workspace: _workspace, ...rest } = row;
	return rest;
}

// ── ops ──────────────────────────────────────────────────────────────────--

async function opSummary(db: D1Database, workspace: string, body: Partial<SummaryBody>): Promise<SummaryRow[]> {
	let where: string;
	const params: unknown[] = [workspace];
	if (body.archived) {
		where = 'WHERE workspace = ? AND archived_at IS NOT NULL';
	} else if (body.all) {
		where = 'WHERE workspace = ? AND archived_at IS NULL';
	} else {
		const doneWithinDays = body.done_within_days ?? 2.0;
		const cutoff = daysAgoIso(doneWithinDays);
		where =
			'WHERE workspace = ? AND archived_at IS NULL ' +
			"AND (status != 'done' OR COALESCE(updated_at, created_at) >= ?)";
		params.push(cutoff);
	}
	const { results } = await db
		.prepare(
			`SELECT ${SUMMARY_COLS} FROM tasks ${where} ` + 'ORDER BY (priority IS NULL), priority, last_event_at DESC'
		)
		.bind(...params)
		.all<SummaryRow>();
	return results;
}

async function opDetail(db: D1Database, workspace: string, body: Partial<DetailBody>) {
	if (!body.task) fail('detail: task is required');
	const row = await requireTask(db, workspace, body.task);
	const tid = row.id; // canonical, in case body.task was an alias
	const refs = await db
		.prepare(
			'SELECT kind, ref_type, value, label, created_at FROM task_refs ' +
				'WHERE workspace = ? AND task_id = ? ORDER BY id'
		)
		.bind(workspace, tid)
		.all<RefRow>();
	const events = await db
		.prepare(
			'SELECT at, source, kind, detail FROM events ' + 'WHERE workspace = ? AND task_id = ? ORDER BY at, id'
		)
		.bind(workspace, tid)
		.all<EventRow>();
	const aliases = await db
		.prepare('SELECT alias FROM task_aliases WHERE workspace = ? AND task_id = ? ORDER BY created_at')
		.bind(workspace, tid)
		.all<{ alias: string }>();
	return {
		task: omitWorkspace(row as unknown as Record<string, unknown>),
		refs: refs.results,
		events: events.results,
		aliases: aliases.results.map((a) => a.alias),
	};
}

/** Active, not-parked tasks untouched beyond the threshold. Surfaced, not acted on. */
async function opStale(db: D1Database, workspace: string, body: Partial<ThresholdBody>): Promise<SummaryRow[]> {
	const cutoff = daysAgoIso(body.threshold_days ?? 7.0);
	const { results } = await db
		.prepare(
			`SELECT ${SUMMARY_COLS} FROM tasks ` +
				"WHERE workspace = ? AND status != 'done' AND parked = 0 AND archived_at IS NULL " +
				'  AND COALESCE(last_event_at, created_at) < ? ' +
				'ORDER BY COALESCE(last_event_at, created_at)'
		)
		.bind(workspace, cutoff)
		.all<SummaryRow>();
	return results;
}

/** Parked tasks aged beyond the threshold — the gentler "still waiting?" nudge. */
async function opParkedAging(db: D1Database, workspace: string, body: Partial<ThresholdBody>): Promise<SummaryRow[]> {
	const cutoff = daysAgoIso(body.threshold_days ?? 14.0);
	const { results } = await db
		.prepare(
			`SELECT ${SUMMARY_COLS} FROM tasks ` +
				"WHERE workspace = ? AND status != 'done' AND parked = 1 AND archived_at IS NULL " +
				'  AND COALESCE(last_event_at, created_at) < ? ' +
				'ORDER BY COALESCE(last_event_at, created_at)'
		)
		.bind(workspace, cutoff)
		.all<SummaryRow>();
	return results;
}

async function opEvents(db: D1Database, workspace: string, body: Partial<EventsBody>) {
	const clauses = ['workspace = ?'];
	const params: unknown[] = [workspace];
	if (body.task) {
		clauses.push('task_id = ?');
		params.push(body.task);
	}
	if (body.since) {
		clauses.push('at >= ?');
		params.push(body.since);
	}
	const { results } = await db
		.prepare(`SELECT id, task_id, at, source, kind, detail FROM events WHERE ${clauses.join(' AND ')} ORDER BY at, id`)
		.bind(...params)
		.all<EventRow & { id: number }>();
	return results;
}

/**
 * The task currently holding this worker session id (or null). Reverse of
 * set-session: maps a `claude --session-id <uuid>` back to its task.
 */
async function opFindSession(db: D1Database, workspace: string, body: Partial<FindSessionBody>) {
	if (!body.session_id) fail('find-session: session_id is required');
	const row = await db
		.prepare(`SELECT ${SUMMARY_COLS} FROM tasks WHERE workspace = ? AND session_id = ? ORDER BY updated_at DESC LIMIT 1`)
		.bind(workspace, body.session_id)
		.first<SummaryRow>();
	return row ?? null;
}

/** Tasks carrying a ref matching (ref_type, value). The inbound-matcher query path. */
async function opFindRefs(db: D1Database, workspace: string, body: Partial<FindRefsBody>) {
	if (!body.value) fail('find-refs: value is required');
	const clauses = ['r.workspace = ?', 'value = ?'];
	const params: unknown[] = [workspace, body.value];
	if (body.ref_type) {
		clauses.push('ref_type = ?');
		params.push(body.ref_type);
	}
	if (body.kind) {
		clauses.push('kind = ?');
		params.push(body.kind);
	}
	const { results } = await db
		.prepare(
			'SELECT DISTINCT t.id, t.type, t.title, t.status ' +
				'FROM task_refs r JOIN tasks t ON t.workspace = r.workspace AND t.id = r.task_id ' +
				`WHERE ${clauses.join(' AND ')} ORDER BY t.id`
		)
		.bind(...params)
		.all();
	return results;
}

async function opAddTask(db: D1Database, workspace: string, body: Partial<AddTaskBody>) {
	if (!body.type) fail('add-task: type is required');
	if (!body.title) fail('add-task: title is required');
	const base = body.task || slugify(body.title);
	const tid = await dedupId(db, workspace, base);
	const ts = nowIso();
	const status = body.status ?? 'pending';
	await db.batch([
		db
			.prepare(
				'INSERT INTO tasks (workspace, id, type, title, status, priority, notes, ' +
					'  parked, parked_reason, workdir, harness, model, runhost, inline, ' +
					'  created_at, updated_at, last_event_at) ' +
					'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
			)
			.bind(
				workspace,
				tid,
				body.type,
				body.title,
				status,
				body.priority ?? null,
				body.notes ?? null,
				body.parked ? 1 : 0,
				body.parked_reason ?? null,
				body.workdir ?? null,
				body.harness ?? null,
				body.model ?? null,
				body.runhost ?? null,
				body.inline ? 1 : 0,
				ts,
				ts,
				ts
			),
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, tid, ts, 'coordinator', 'created', body.title),
	]);
	const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

const UPDATE_TASK_FIELDS = ['title', 'status', 'priority', 'notes', 'parked_reason', 'workdir', 'harness', 'model', 'runhost'] as const;

async function opUpdateTask(db: D1Database, workspace: string, body: Partial<UpdateTaskBody>) {
	if (!body.task) fail('update-task: task is required');
	const old = await requireTask(db, workspace, body.task);
	const tid = old.id;
	const sets: string[] = [];
	const params: unknown[] = [];
	const changes: string[] = [];
	for (const field of UPDATE_TASK_FIELDS) {
		const val = body[field as keyof UpdateTaskBody];
		if (val !== undefined && val !== null) {
			sets.push(`${field} = ?`);
			params.push(val);
			const oldVal = (old as unknown as Record<string, unknown>)[field];
			if (oldVal !== val) {
				changes.push(`${field}: ${JSON.stringify(oldVal)} -> ${JSON.stringify(val)}`);
			}
		}
	}
	if (body.parked !== undefined && body.parked !== null) {
		sets.push('parked = ?');
		params.push(body.parked ? 1 : 0);
	}
	if (sets.length === 0) fail('update-task: nothing to change');
	sets.push('updated_at = ?');
	const ts = nowIso();
	params.push(ts);
	params.push(workspace, tid);

	const statements: D1PreparedStatement[] = [
		db.prepare(`UPDATE tasks SET ${sets.join(', ')} WHERE workspace = ? AND id = ?`).bind(...params),
	];
	// Log a status event when status changed, else a generic note of the change.
	if (body.status !== undefined && body.status !== null && old.status !== body.status) {
		statements.push(
			db
				.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
				.bind(workspace, tid, nowIso(), 'coordinator', 'status', `status -> ${body.status}`)
		);
		statements.push(db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(nowIso(), workspace, tid));
	} else if (changes.length > 0) {
		statements.push(
			db
				.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
				.bind(workspace, tid, nowIso(), 'coordinator', 'note', changes.join('; '))
		);
		statements.push(db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(nowIso(), workspace, tid));
	}
	await db.batch(statements);
	const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

async function opAddRef(db: D1Database, workspace: string, body: Partial<AddRefBody>) {
	if (!body.task) fail('add-ref: task is required');
	if (!body.kind) fail('add-ref: kind is required');
	if (!body.ref_type) fail('add-ref: ref_type is required');
	if (!body.value) fail('add-ref: value is required');
	const task = await requireTask(db, workspace, body.task);
	const tid = task.id;
	const ts = nowIso();
	await db
		.prepare('INSERT INTO task_refs (workspace, task_id, kind, ref_type, value, label, created_at) VALUES (?,?,?,?,?,?,?)')
		.bind(workspace, tid, body.kind, body.ref_type, body.value, body.label ?? null, ts)
		.run();
	const row = await db
		.prepare('SELECT * FROM task_refs WHERE workspace = ? AND task_id = ? ORDER BY id DESC LIMIT 1')
		.bind(workspace, tid)
		.first<RefRow & { id: number; workspace: string }>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

/**
 * The single write path workers use. Append-only into events (+ last_event_at).
 * With set_status, the same batch also updates the task's status — emits the
 * task row when status was set, else the event row. Requires task when
 * set_status is given.
 */
async function opLogEvent(db: D1Database, workspace: string, body: Partial<LogEventBody>) {
	if (!body.kind) fail('log-event: kind is required');
	const source = body.source ?? 'coordinator';
	let tid: string | null = null;
	if (body.task) {
		tid = (await requireTask(db, workspace, body.task)).id;
	}
	if (body.set_status && tid === null) fail('log-event --set-status requires --task');

	const ts = nowIso();
	const statements: D1PreparedStatement[] = [
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, tid, ts, source, body.kind, body.detail ?? null),
	];
	if (tid !== null) {
		statements.push(db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid));
	}
	if (body.set_status && tid !== null) {
		statements.push(
			db.prepare('UPDATE tasks SET status = ?, updated_at = ? WHERE workspace = ? AND id = ?').bind(body.set_status, nowIso(), workspace, tid)
		);
	}
	const results = await db.batch(statements);
	if (body.set_status && tid !== null) {
		const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
		return omitWorkspace(row as unknown as Record<string, unknown>);
	}
	// The event's rowid comes back on the first batched INSERT's meta.
	const eventId = results[0].meta.last_row_id;
	const row = await db.prepare('SELECT * FROM events WHERE workspace = ? AND id = ?').bind(workspace, eventId).first();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

/** Record the pre-allocated worker session id at spawn, atomic with a dispatched event. */
/**
 * Record the worker session at spawn. Appends to task_sessions (the durable
 * provenance — every session that ever ran this task) AND points tasks.session_id at
 * it (just "the one to resume next"). A RESUME of an existing session hits the same
 * task_sessions row (PK is workspace+task+session) and refreshes started_at — it is
 * the same session reopened, not a new one.
 */
async function opSetSession(db: D1Database, workspace: string, body: Partial<SetSessionBody>) {
	if (!body.task) fail('set-session: task is required');
	if (!body.session_id) fail('set-session: session_id is required');
	const task = await requireTask(db, workspace, body.task);
	const tid = task.id;
	const ts = nowIso();
	const sets = ['session_id = ?', 'updated_at = ?'];
	const params: unknown[] = [body.session_id, ts];
	if (body.workdir !== undefined && body.workdir !== null) {
		sets.push('workdir = ?');
		params.push(body.workdir);
	}
	params.push(workspace, tid);
	const harness = body.harness ?? task.harness ?? null;
	const model = body.model ?? task.model ?? null;
	await db.batch([
		db.prepare(`UPDATE tasks SET ${sets.join(', ')} WHERE workspace = ? AND id = ?`).bind(...params),
		db
			.prepare(
				'INSERT INTO task_sessions (workspace, task_id, session_id, harness, model, started_at) VALUES (?,?,?,?,?,?) ' +
					'ON CONFLICT(workspace, task_id, session_id) DO UPDATE SET started_at = excluded.started_at',
			)
			.bind(workspace, tid, body.session_id, harness, model, ts),
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, tid, ts, 'coordinator', 'dispatched', `session ${body.session_id}`),
		db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
	]);
	const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

/** Every session that has ever run this task, oldest first (the provenance record). */
async function opSessions(db: D1Database, workspace: string, body: Partial<{ task: string }>) {
	if (!body.task) fail('sessions: task is required');
	const task = await requireTask(db, workspace, body.task);
	const { results } = await db
		.prepare(
			'SELECT session_id, harness, model, started_at FROM task_sessions ' +
				'WHERE workspace = ? AND task_id = ? ORDER BY started_at, session_id',
		)
		.bind(workspace, task.id)
		.all();
	return results ?? [];
}

/**
 * Rebuild task_sessions from the append-only `dispatched` event log.
 *
 * Every spawn wrote a `dispatched` event carrying its session id, and events are never
 * mutated — so the full history survived even where tasks.session_id did not (the old
 * sweep NULLed it on worker death; a re-dispatch overwrote it). The event log is thus
 * the only complete record of what ran what, and this is the one-time repair.
 * Idempotent.
 */
async function opBackfillSessions(db: D1Database, workspace: string) {
	const { results } = await db
		.prepare("SELECT task_id, at, detail FROM events WHERE workspace = ? AND kind = 'dispatched' AND task_id IS NOT NULL ORDER BY at, id")
		.bind(workspace)
		.all<{ task_id: string; at: string; detail: string | null }>();
	const re = /\b(?:session\s+)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|ses_[A-Za-z0-9]+)\b/;
	const stmts = [];
	const seen = new Set<string>();
	for (const ev of results ?? []) {
		const m = re.exec(ev.detail ?? '');
		if (!m) continue;
		const sid = m[1];
		const key = `${ev.task_id} ${sid}`;
		if (seen.has(key)) continue;
		seen.add(key);
		stmts.push(
			db
				.prepare(
					'INSERT INTO task_sessions (workspace, task_id, session_id, harness, model, started_at) ' +
						'SELECT ?, ?, ?, harness, model, ? FROM tasks WHERE workspace = ? AND id = ? ' +
						'ON CONFLICT(workspace, task_id, session_id) DO UPDATE SET ' +
						'  started_at = MIN(task_sessions.started_at, excluded.started_at)',
				)
				.bind(workspace, ev.task_id, sid, ev.at, workspace, ev.task_id),
		);
	}
	if (stmts.length) await db.batch(stmts);
	return {
		dispatch_events_scanned: (results ?? []).length,
		sessions_restored: stmts.length,
		note: 'rebuilt from the append-only `dispatched` events — the only record that survived the old clear-session sweep and re-dispatch overwrites',
	};
}

/**
 * Detach a task's worker session: set session_id back to NULL. Logs a neutral
 * note (not a `dispatched` event) and leaves status untouched.
 */
async function opClearSession(db: D1Database, workspace: string, body: Partial<ClearSessionBody>) {
	if (!body.task) fail('clear-session: task is required');
	const task = await requireTask(db, workspace, body.task);
	const tid = task.id;
	const ts = nowIso();
	await db.batch([
		db.prepare('UPDATE tasks SET session_id = NULL, updated_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, tid, ts, 'coordinator', 'note', 'session cleared (detached worker session)'),
		db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
	]);
	const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

/**
 * Remove a task from the board WITHOUT marking it done (or reverse). See
 * taskdb.py cmd_archive's docstring for the full semantics; ported verbatim:
 * archiving preserves the task's real status and stamps archived_at with WHEN
 * it left the board. --reason is required unless --unarchive.
 */
async function opArchive(db: D1Database, workspace: string, body: Partial<ArchiveBody>) {
	if (!body.task) fail('archive: task is required');
	const task = await requireTask(db, workspace, body.task);
	const tid = task.id;
	if (body.unarchive) {
		const ts = nowIso();
		await db.batch([
			db.prepare('UPDATE tasks SET archived_at = NULL, updated_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
			db
				.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
				.bind(workspace, tid, ts, 'coordinator', 'unarchive', 'unarchived (back on the board)'),
			db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
		]);
	} else {
		if (!body.reason) fail('archive: reason is required (why is this leaving the board?)');
		const ts = nowIso();
		await db.batch([
			db.prepare('UPDATE tasks SET archived_at = ?, updated_at = ? WHERE workspace = ? AND id = ?').bind(ts, ts, workspace, tid),
			db
				.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
				.bind(workspace, tid, ts, 'coordinator', 'archive', `archived (off board, not done): ${body.reason}`),
			db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, tid),
		]);
	}
	const row = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, tid).first<TaskRow>();
	return omitWorkspace(row as unknown as Record<string, unknown>);
}

/**
 * Give a task a new canonical id, keeping its old id as an alias. Re-points
 * the task row, its refs, events, and any existing aliases to the new id.
 * D1 does not support PRAGMA defer_foreign_keys mid-transaction the way
 * sqlite3 does; batch() statements execute in order within one transaction,
 * so re-pointing children before the row rename would violate the FK. Instead
 * we INSERT the new row first, repoint every child to it, then delete the old
 * row — never leaving a child pointing at a nonexistent task in between.
 */
async function opPromote(db: D1Database, workspace: string, body: Partial<PromoteBody>) {
	if (!body.task) fail('promote: task is required');
	if (!body.new_id) fail('promote: new_id is required');
	const row = await requireTask(db, workspace, body.task);
	const oldId = row.id;
	const newId = body.new_id;
	if (newId === oldId) fail(`task is already ${JSON.stringify(newId)}`);
	if ((await resolveId(db, workspace, newId)) !== null) fail(`id ${JSON.stringify(newId)} is already taken`);
	const ts = nowIso();

	const { workspace: _w, ...rest } = row as unknown as Record<string, unknown>;
	const cols = Object.keys(rest);
	const newRow = { ...rest, id: newId, updated_at: ts };
	const insertCols = ['workspace', ...cols];
	const placeholders = insertCols.map(() => '?').join(',');
	const insertValues = [workspace, ...cols.map((c) => (c === 'id' ? newId : c === 'updated_at' ? ts : (newRow as Record<string, unknown>)[c]))];

	await db.batch([
		db.prepare(`INSERT INTO tasks (${insertCols.join(',')}) VALUES (${placeholders})`).bind(...insertValues),
		db.prepare('UPDATE task_refs SET task_id = ? WHERE workspace = ? AND task_id = ?').bind(newId, workspace, oldId),
		db.prepare('UPDATE events SET task_id = ? WHERE workspace = ? AND task_id = ?').bind(newId, workspace, oldId),
		db.prepare('UPDATE task_aliases SET task_id = ? WHERE workspace = ? AND task_id = ?').bind(newId, workspace, oldId),
		db
			.prepare('INSERT INTO task_aliases (workspace, alias, task_id, created_at) VALUES (?,?,?,?)')
			.bind(workspace, oldId, newId, ts),
		db.prepare('DELETE FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, oldId),
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, newId, ts, 'coordinator', 'note', `promoted: id ${oldId} -> ${newId} (old id kept as alias)`),
		db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, newId),
	]);
	const out = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, newId).first<TaskRow>();
	return omitWorkspace(out as unknown as Record<string, unknown>);
}

/**
 * Merge one task INTO another: `from` is absorbed into `into`, which
 * survives. Ported from cmd_merge — see that docstring for the full
 * semantics (refs de-duplicated by (kind, ref_type, value), absorbed notes
 * appended, absorbed id + its aliases become aliases of the survivor, then
 * the absorbed row is deleted).
 */
async function opMerge(db: D1Database, workspace: string, body: Partial<MergeBody>) {
	if (!body.into) fail('merge: into is required');
	if (!body.from) fail('merge: from is required');
	const survivor = await requireTask(db, workspace, body.into);
	const absorbed = await requireTask(db, workspace, body.from);
	const intoId = survivor.id;
	const fromId = absorbed.id;
	if (intoId === fromId) fail('cannot merge a task into itself');
	const ts = nowIso();

	// 1. Move refs, skipping ones the survivor already has (by identity tuple).
	const existingRefs = await db
		.prepare('SELECT kind, ref_type, value FROM task_refs WHERE workspace = ? AND task_id = ?')
		.bind(workspace, intoId)
		.all<{ kind: string; ref_type: string; value: string }>();
	const existingKeys = new Set(existingRefs.results.map((r) => `${r.kind} ${r.ref_type} ${r.value}`));
	const fromRefs = await db
		.prepare('SELECT id, kind, ref_type, value FROM task_refs WHERE workspace = ? AND task_id = ?')
		.bind(workspace, fromId)
		.all<{ id: number; kind: string; ref_type: string; value: string }>();

	const statements: D1PreparedStatement[] = [];
	for (const r of fromRefs.results) {
		const key = `${r.kind} ${r.ref_type} ${r.value}`;
		if (existingKeys.has(key)) {
			statements.push(db.prepare('DELETE FROM task_refs WHERE workspace = ? AND id = ?').bind(workspace, r.id));
		} else {
			statements.push(db.prepare('UPDATE task_refs SET task_id = ? WHERE workspace = ? AND id = ?').bind(intoId, workspace, r.id));
		}
	}

	// 2. Move event history onto the survivor.
	statements.push(db.prepare('UPDATE events SET task_id = ? WHERE workspace = ? AND task_id = ?').bind(intoId, workspace, fromId));

	// 3. Re-point the absorbed task's aliases, then make its id an alias too.
	statements.push(db.prepare('UPDATE task_aliases SET task_id = ? WHERE workspace = ? AND task_id = ?').bind(intoId, workspace, fromId));
	statements.push(
		db
			.prepare('INSERT OR IGNORE INTO task_aliases (workspace, alias, task_id, created_at) VALUES (?,?,?,?)')
			.bind(workspace, fromId, intoId, ts)
	);

	// 4. Fold the absorbed notes into the survivor's notes.
	if (absorbed.notes) {
		const mergedNotes = survivor.notes
			? `${survivor.notes}\n\n[merged from ${fromId}] ${absorbed.notes}`
			: `[merged from ${fromId}] ${absorbed.notes}`;
		statements.push(
			db.prepare('UPDATE tasks SET notes = ?, updated_at = ? WHERE workspace = ? AND id = ?').bind(mergedNotes, ts, workspace, intoId)
		);
	}

	// 5. Delete the absorbed row (its children are already re-pointed).
	statements.push(db.prepare('DELETE FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, fromId));

	statements.push(
		db
			.prepare('INSERT INTO events (workspace, task_id, at, source, kind, detail) VALUES (?,?,?,?,?,?)')
			.bind(workspace, intoId, ts, 'coordinator', 'note', `merged ${fromId} into ${intoId} (refs, history, aliases absorbed)`)
	);
	statements.push(db.prepare('UPDATE tasks SET last_event_at = ? WHERE workspace = ? AND id = ?').bind(ts, workspace, intoId));

	await db.batch(statements);
	const out = await db.prepare('SELECT * FROM tasks WHERE workspace = ? AND id = ?').bind(workspace, intoId).first<TaskRow>();
	return omitWorkspace(out as unknown as Record<string, unknown>);
}

/** New op: dump every row for this workspace, workspace column stripped. */
async function opExport(db: D1Database, workspace: string): Promise<ExportShape> {
	const [tasks, aliases, refs, events, sessions] = await Promise.all([
		db.prepare('SELECT * FROM tasks WHERE workspace = ? ORDER BY created_at, id').bind(workspace).all(),
		db.prepare('SELECT * FROM task_aliases WHERE workspace = ? ORDER BY created_at, alias').bind(workspace).all(),
		db.prepare('SELECT * FROM task_refs WHERE workspace = ? ORDER BY id').bind(workspace).all(),
		db.prepare('SELECT * FROM events WHERE workspace = ? ORDER BY id').bind(workspace).all(),
		db.prepare('SELECT * FROM task_sessions WHERE workspace = ? ORDER BY started_at, session_id').bind(workspace).all(),
	]);
	return {
		tasks: tasks.results.map((r) => omitWorkspace(r as Record<string, unknown>)),
		task_aliases: aliases.results.map((r) => omitWorkspace(r as Record<string, unknown>)),
		task_refs: refs.results.map((r) => omitWorkspace(r as Record<string, unknown>)),
		events: events.results.map((r) => omitWorkspace(r as Record<string, unknown>)),
		task_sessions: sessions.results.map((r) => omitWorkspace(r as Record<string, unknown>)),
	};
}

/**
 * New op: load an export back into a workspace. Fails 409 if the workspace
 * already has any tasks, so import can't silently merge with existing data.
 * task_refs/events are re-inserted without their original integer ids (D1
 * assigns fresh AUTOINCREMENT ids), but insertion follows the original id
 * order so timelines stay sorted; tasks/task_aliases keep their real ids
 * since those are the meaningful primary keys.
 */
async function opImport(db: D1Database, workspace: string, body: Partial<ExportShape>) {
	const existing = await db.prepare('SELECT 1 FROM tasks WHERE workspace = ? LIMIT 1').bind(workspace).first();
	if (existing) fail(`import: workspace ${JSON.stringify(workspace)} already has tasks`);

	const statements: D1PreparedStatement[] = [];
	for (const t of body.tasks ?? []) {
		const cols = Object.keys(t);
		statements.push(
			db
				.prepare(`INSERT INTO tasks (workspace, ${cols.join(',')}) VALUES (?, ${cols.map(() => '?').join(',')})`)
				.bind(workspace, ...cols.map((c) => t[c]))
		);
	}
	for (const a of body.task_aliases ?? []) {
		const cols = Object.keys(a);
		statements.push(
			db
				.prepare(`INSERT INTO task_aliases (workspace, ${cols.join(',')}) VALUES (?, ${cols.map(() => '?').join(',')})`)
				.bind(workspace, ...cols.map((c) => a[c]))
		);
	}
	for (const sess of body.task_sessions ?? []) {
		const cols = Object.keys(sess);
		statements.push(
			db
				.prepare(`INSERT INTO task_sessions (workspace, ${cols.join(',')}) VALUES (?, ${cols.map(() => '?').join(',')})`)
				.bind(workspace, ...cols.map((c) => sess[c]))
		);
	}
	// task_refs and events carry an `id` field in the export shape (useful for
	// ordering/debugging) but we re-insert without it, in original-id order,
	// so D1 assigns fresh AUTOINCREMENT ids while preserving timeline order.
	const sortedRefs = [...(body.task_refs ?? [])].sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0));
	for (const r of sortedRefs) {
		const { id: _id, ...rest } = r;
		const cols = Object.keys(rest);
		statements.push(
			db
				.prepare(`INSERT INTO task_refs (workspace, ${cols.join(',')}) VALUES (?, ${cols.map(() => '?').join(',')})`)
				.bind(workspace, ...cols.map((c) => rest[c]))
		);
	}
	const sortedEvents = [...(body.events ?? [])].sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0));
	for (const e of sortedEvents) {
		const { id: _id, ...rest } = e;
		const cols = Object.keys(rest);
		statements.push(
			db
				.prepare(`INSERT INTO events (workspace, ${cols.join(',')}) VALUES (?, ${cols.map(() => '?').join(',')})`)
				.bind(workspace, ...cols.map((c) => rest[c]))
		);
	}
	if (statements.length > 0) await db.batch(statements);
	return { workspace, imported: { tasks: (body.tasks ?? []).length, task_aliases: (body.task_aliases ?? []).length, task_refs: (body.task_refs ?? []).length, events: (body.events ?? []).length } };
}

// ── auth ─────────────────────────────────────────────────────────────────--

/** Timing-safe compare of the bearer token against env.PWC_TOKEN. */
async function isAuthorized(request: Request, env: Env): Promise<boolean> {
	const header = request.headers.get('Authorization') ?? '';
	const match = /^Bearer (.+)$/.exec(header);
	if (!match) return false;
	const provided = match[1];
	const encoder = new TextEncoder();
	const a = encoder.encode(provided);
	const b = encoder.encode(env.PWC_TOKEN);
	if (a.byteLength !== b.byteLength) {
		// Still run a comparison of equal length to avoid a length-based timing
		// signal distinguishing "wrong length" from "wrong content".
		await crypto.subtle.timingSafeEqual(a, a);
		return false;
	}
	return crypto.subtle.timingSafeEqual(a, b);
}

// ── dispatch ─────────────────────────────────────────────────────────────--

type Op =
	| 'summary'
	| 'detail'
	| 'stale'
	| 'parked-aging'
	| 'events'
	| 'find-refs'
	| 'find-session'
	| 'add-task'
	| 'update-task'
	| 'add-ref'
	| 'log-event'
	| 'set-session'
	| 'sessions'
	| 'backfill-sessions'
	| 'clear-session'
	| 'archive'
	| 'promote'
	| 'merge'
	| 'export'
	| 'import';

const KNOWN_OPS: ReadonlySet<string> = new Set<Op>([
	'summary',
	'detail',
	'stale',
	'parked-aging',
	'events',
	'find-refs',
	'find-session',
	'add-task',
	'update-task',
	'add-ref',
	'log-event',
	'set-session',
	'sessions',
	'backfill-sessions',
	'clear-session',
	'archive',
	'promote',
	'merge',
	'export',
	'import',
]);

async function handleOp(db: D1Database, workspace: string, op: Op, body: Record<string, unknown>): Promise<unknown> {
	// Each op's `body` parameter is a Partial<...> of its documented shape: the
	// wire body is untrusted JSON, and every op enforces its own required
	// fields at runtime via fail() (see e.g. opDetail's `if (!body.task)`).
	// Casting to Partial<X> here is therefore honest — it doesn't claim fields
	// exist that runtime checks haven't verified yet.
	switch (op) {
		case 'summary':
			return opSummary(db, workspace, body as Partial<SummaryBody>);
		case 'detail':
			return opDetail(db, workspace, body as Partial<DetailBody>);
		case 'stale':
			return opStale(db, workspace, body as Partial<ThresholdBody>);
		case 'parked-aging':
			return opParkedAging(db, workspace, body as Partial<ThresholdBody>);
		case 'events':
			return opEvents(db, workspace, body as Partial<EventsBody>);
		case 'find-refs':
			return opFindRefs(db, workspace, body as Partial<FindRefsBody>);
		case 'find-session':
			return opFindSession(db, workspace, body as Partial<FindSessionBody>);
		case 'add-task':
			return opAddTask(db, workspace, body as Partial<AddTaskBody>);
		case 'update-task':
			return opUpdateTask(db, workspace, body as Partial<UpdateTaskBody>);
		case 'add-ref':
			return opAddRef(db, workspace, body as Partial<AddRefBody>);
		case 'log-event':
			return opLogEvent(db, workspace, body as Partial<LogEventBody>);
		case 'sessions':
			return opSessions(db, workspace, body);
		case 'backfill-sessions':
			return opBackfillSessions(db, workspace);
		case 'set-session':
			return opSetSession(db, workspace, body as Partial<SetSessionBody>);
		case 'clear-session':
			return opClearSession(db, workspace, body as Partial<ClearSessionBody>);
		case 'archive':
			return opArchive(db, workspace, body as Partial<ArchiveBody>);
		case 'promote':
			return opPromote(db, workspace, body as Partial<PromoteBody>);
		case 'merge':
			return opMerge(db, workspace, body as Partial<MergeBody>);
		case 'export':
			return opExport(db, workspace);
		case 'import':
			return opImport(db, workspace, body as Partial<ExportShape>);
	}
}

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' },
	});
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const url = new URL(request.url);

		if (request.method === 'GET' && url.pathname === '/health') {
			return json({ ok: true });
		}

		if (!(await isAuthorized(request, env))) {
			return json({ error: 'unauthorized' }, 401);
		}

		const match = /^\/w\/([^/]+)\/([^/]+)$/.exec(url.pathname);
		if (!match || request.method !== 'POST') {
			return json({ error: 'not found' }, 404);
		}
		const [, workspace, opRaw] = match;
		if (!KNOWN_OPS.has(opRaw)) {
			return json({ error: `unknown op ${JSON.stringify(opRaw)}` }, 404);
		}
		const op = opRaw as Op;

		let body: Record<string, unknown>;
		try {
			body = request.headers.get('content-length') === '0' ? {} : await request.json();
		} catch {
			return json({ error: 'invalid JSON body' }, 400);
		}

		try {
			const result = await handleOp(env.DB, workspace, op, body);
			return json(result);
		} catch (err) {
			if (err instanceof HubError) {
				return json({ error: err.message }, 400);
			}
			// Anything else (D1 error, programming bug) is unexpected — surface it
			// as a 500 rather than pretending it's a validation error like the
			// Python's fail() path would.
			console.error('pwc-hub error', err);
			return json({ error: 'internal error' }, 500);
		}
	},
};
