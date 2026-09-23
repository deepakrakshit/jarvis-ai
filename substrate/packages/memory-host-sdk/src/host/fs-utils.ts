// Retain fs-safe's process configuration for host-side memory file operations.
export { root } from "@jarvis/fs-safe/root";
export { isPathInside, isPathInsideWithRealpath } from "@jarvis/fs-safe/path";
export {
  assertNoSymlinkParents,
  readRegularFile,
  statRegularFile,
} from "@jarvis/fs-safe/advanced";
export { walkDirectory, type WalkDirectoryEntry } from "@jarvis/fs-safe/walk";

/**
 * True for missing-file errors emitted by Node or fs-safe.
 * The narrowed union stays stable; extra-path authorization handles `not-file` separately.
 */
export function isFileMissingError(
  err: unknown,
): err is NodeJS.ErrnoException & { code: "ENOENT" | "ENOTDIR" | "not-file" | "not-found" } {
  if (!err || typeof err !== "object" || !("code" in err)) {
    return false;
  }
  return (
    err.code === "ENOENT" ||
    err.code === "ENOTDIR" ||
    err.code === "not-file" ||
    err.code === "not-found"
  );
}
