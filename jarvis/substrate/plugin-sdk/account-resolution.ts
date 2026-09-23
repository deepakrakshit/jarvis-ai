/**
 * Public SDK subpath for account id normalization and account matching helpers.
 */
export {
  createAccountListHelpers,
  hasConfiguredAccountValue,
  listCombinedAccountIds,
  normalizeAccountId,
  normalizeE164,
  normalizeOptionalAccountId,
  resolveListedDefaultAccountId,
  resolveMergedAccountConfig,
  resolveNormalizedAccountEntry,
  resolveUserPath,
  DEFAULT_ACCOUNT_ID,
} from "./account-core.js";

export type { JarvisConfig } from "../config/types.jarvis.js";
export { resolveAccountEntry, resolveAccountKey } from "../routing/account-lookup.js";
export type { ChannelAccountKeyPolicy } from "../routing/account-lookup.js";
