/**
 * WhatsApp Linked-Device QR Authentication Setup.
 *
 * Launches the WhatsApp client in interactive terminal mode.
 * Displays a terminal QR code for the user to link their account via:
 *   WhatsApp -> Settings -> Linked Devices -> Link a Device
 *
 * Once scanned and authenticated, the session credentials are automatically
 * saved to ./auth for all subsequent calls.
 */

import { loadConfig } from "../config.js";
import { WhatsAppManager } from "../engine/client.js";

async function runAuthSetup(): Promise<void> {
  console.log("=== WhatsApp Linked-Device Authentication Setup ===");
  const config = loadConfig();

  const manager = new WhatsAppManager({ authDir: config.whatsappAuthDir });

  if (manager.hasPersistedAuth) {
    console.log(`\nExisting authenticated session detected in ${config.whatsappAuthDir}.`);
    console.log("Testing connection with existing credentials...");
  } else {
    console.log(`\nNo existing credentials in ${config.whatsappAuthDir}.`);
    console.log("Initializing connection. A QR code will appear below shortly.");
    console.log("Scan it on your phone: WhatsApp -> Linked Devices -> Link a Device.\n");
  }

  try {
    await manager.connect();
    console.log("\nPASS: WhatsApp connection established successfully!");
    console.log(`Credentials safely persisted in ${config.whatsappAuthDir}.`);
    console.log("You are ready to place calls.");
    manager.disconnect();
    process.exit(0);
  } catch (err: any) {
    console.error("\nFAIL: WhatsApp authentication failed:", err?.message ?? err);
    manager.disconnect();
    process.exit(1);
  }
}

runAuthSetup();
