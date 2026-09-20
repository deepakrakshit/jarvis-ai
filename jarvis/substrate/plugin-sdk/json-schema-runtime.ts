// Narrow JSON Schema validator surface for plugins that validate tool/model output.

export { normalizeJsonSchemaForTypeBox } from "@jarvis/normalization-core/json-schema";
export { validateJsonSchemaValue } from "../plugins/schema-validator.js";
export type { JsonSchemaObject } from "../shared/json-schema.types.js";
