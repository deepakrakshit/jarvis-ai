import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadConfig, getSafeConfigSummary } from "../src/config.js";

describe("Configuration Loader", () => {
  test("loads configuration dynamically with valid GEMINI_API_KEY", () => {
    const config = loadConfig();
    assert.ok(config.geminiApiKey);
    assert.equal(typeof config.geminiModel, "string");
    assert.equal(typeof config.voiceDefaultName, "string");
    assert.equal(typeof config.userName, "string");
    assert.equal(typeof config.assistantName, "string");
    assert.equal(typeof config.defaultCountryCode, "string");
    assert.equal(typeof config.callHistoryFile, "string");
    assert.equal(typeof config.farewellGracePeriodMs, "number");
  });

  test("masks secrets in getSafeConfigSummary", () => {
    const config = loadConfig();
    const summary = getSafeConfigSummary(config);

    assert.equal("geminiApiKey" in summary, false);
    assert.equal(summary.geminiApiKeyConfigured, true);
    assert.equal(summary.geminiModel, config.geminiModel);
    assert.equal(summary.voiceDefaultName, config.voiceDefaultName);
  });
});
