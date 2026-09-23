// Narrow SQLite schema, path, and transaction helpers for first-party runtime.

export type { Generated, Selectable } from "kysely";
export { runQueuedStoreWrite, type StoreWriterQueue } from "../shared/store-writer-queue.js";
export {
  openSqliteWorkerStore,
  runSqliteWorkerStoreWrite,
  SqliteWorkerError,
  type SqliteWorkerBackend,
  type SqliteWorkerCommand,
  type SqliteWorkerOperations,
  type SqliteWorkerStore,
} from "../infra/sqlite-worker-store.js";
export { requestSqliteWorkerOperationAdmission } from "../infra/sqlite-worker-operation-admission.js";
export {
  openJARVISAgentSqliteWorkerStore,
  type JARVISAgentSqliteWorkerStore,
} from "../state/jarvis-agent-worker-store.js";

export {
  borrowJARVISAgentDatabase,
  ensureJARVISAgentDatabaseSchema,
  openJARVISAgentDatabase,
  resolveJARVISAgentSqlitePath,
  withJARVISAgentDatabaseAsync,
} from "../state/jarvis-agent-db.js";
export { withJARVISAgentDatabaseReadOnly } from "../state/jarvis-agent-db-readonly.js";
export { withJARVISAgentDatabaseWrite } from "../state/jarvis-agent-db-write.js";
export { assertJARVISAgentDatabaseForMaintenance } from "../state/jarvis-agent-db-maintenance.js";
export { ensureJARVISAgentStandingIntentsSchema } from "../state/jarvis-agent-standing-intents-schema.js";
export {
  compileSqliteQueryBindings,
  enableNodeSqliteKyselyStatementCache,
  executeSqliteQuerySync,
  executeSqliteQueryTakeFirstSync,
  getNodeSqliteKysely,
  iterateSqliteQuerySync,
  prepareSqliteQuerySync,
  sqliteStringSet,
} from "../infra/kysely-sync.js";
export { openNodeSqliteDatabase, resolveExistingSqliteFileUri } from "../infra/node-sqlite.js";
export {
  prepareSqliteReadOnlyLocation,
  prepareSqliteReadOnlyLocationSync,
} from "../infra/sqlite-snapshot-source.js";
export {
  assertTransactionUsable,
  runSqliteImmediateTransaction,
  runSqliteImmediateTransactionSync,
} from "../infra/sqlite-transaction.js";
export { tableExists } from "../state/jarvis-state-db-schema-helpers.js";
