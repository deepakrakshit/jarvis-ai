/**
 * Authentication and credential lifecycle manager for the WhatsApp Rust engine.
 */

import { exec } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { useMultiFileAuthState } from "@oxidezap/baileyrs";

export interface AuthStateOptions {
  authDir: string;
  workspaceDir?: string;
  onQrCode?: (qr: string) => void;
  openBrowserOnQr?: boolean;
}

export class WhatsAppAuth {
  private readonly authDir: string;
  private readonly workspaceDir: string;
  private readonly onQrCode?: (qr: string) => void;
  private readonly openBrowserOnQr: boolean;

  constructor(options: AuthStateOptions) {
    this.authDir = resolve(options.authDir);
    this.workspaceDir = resolve(options.workspaceDir || process.cwd());
    this.onQrCode = options.onQrCode;
    this.openBrowserOnQr = options.openBrowserOnQr ?? true;

    mkdirSync(this.authDir, { recursive: true });
  }

  public get directory(): string {
    return this.authDir;
  }

  /**
   * Check whether durable authentication credentials are present on disk.
   */
  public hasPersistedCredentials(): boolean {
    if (!existsSync(this.authDir)) {
      return false;
    }
    try {
      const files = readdirSync(this.authDir);
      return files.includes("device-account.bin") || files.includes("creds.json");
    } catch {
      return false;
    }
  }

  /**
   * Load or initialize the multi-file authentication state.
   */
  public async loadState() {
    return await useMultiFileAuthState(this.authDir);
  }

  /**
   * Handle QR code generation: notify callback and write HTML viewer.
   */
  public handleQrCode(qr: string): string {
    if (this.onQrCode) {
      this.onQrCode(qr);
    }

    const htmlDir = resolve(this.workspaceDir, "data", "whatsapp");
    mkdirSync(htmlDir, { recursive: true });
    const htmlPath = resolve(htmlDir, "login_qr.html");

    const htmlContent = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>JARVIS WhatsApp Authentication</title>
  <style>
    body { background: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
    .card { background: #131b2e; border: 1px solid #1e293b; border-radius: 16px; padding: 32px; max-width: 480px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    h1 { color: #38bdf8; margin-top: 0; font-size: 24px; }
    p { color: #94a3b8; font-size: 15px; line-height: 1.6; }
    .qr-box { background: white; padding: 16px; border-radius: 12px; display: inline-block; margin: 20px 0; }
    .instructions { text-align: left; background: #0f172a; padding: 16px; border-radius: 8px; margin-top: 16px; }
    .instructions li { margin-bottom: 8px; font-size: 14px; color: #cbd5e1; }
    .status { color: #34d399; font-weight: 600; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>JARVIS - WhatsApp Authentication</h1>
    <p>Scan this QR code using WhatsApp on your mobile phone to authenticate.</p>
    <div class="qr-box">
      <img src="https://api.qrserver.com/v1/create-qr-code/?size=320x320&data=${encodeURIComponent(qr)}"
           onerror="this.onerror=null; this.src='https://chart.googleapis.com/chart?chs=320x320&cht=qr&chl=${encodeURIComponent(qr)}';"
           width="320" height="320" alt="WhatsApp QR Code" />
    </div>
    <div class="instructions">
      <ol>
        <li>Open <strong>WhatsApp</strong> on your phone.</li>
        <li>Tap <strong>Settings</strong> or <strong>Menu</strong> (three dots).</li>
        <li>Select <strong>Linked Devices</strong> &rarr; <strong>Link a Device</strong>.</li>
        <li>Point your phone camera at this QR code.</li>
      </ol>
    </div>
    <div class="status">Waiting for authentication scan...</div>
  </div>
</body>
</html>`;

    writeFileSync(htmlPath, htmlContent, "utf-8");

    if (this.openBrowserOnQr && process.platform === "win32") {
      try {
        exec(`cmd /c start "" "${htmlPath}"`);
      } catch {
        // Fallback silently if browser opener fails
      }
    }

    return htmlPath;
  }

  /**
   * Purge credentials and persistent session data for clean logout.
   */
  public clearSession(): void {
    if (existsSync(this.authDir)) {
      try {
        rmSync(this.authDir, { recursive: true, force: true });
        mkdirSync(this.authDir, { recursive: true });
      } catch (err) {
        console.error(`[WhatsAppAuth] Failed to purge credentials in ${this.authDir}:`, err);
      }
    }
  }
}
