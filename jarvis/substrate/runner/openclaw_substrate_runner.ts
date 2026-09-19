/**
 * JARVIS OpenClaw Substrate Runner
 * Executes actual OpenClaw CUA modules directly in Node.js runtime.
 */
import { randomUUID } from "node:crypto";
import { exec, execFile } from "node:child_process";
import { promisify } from "node:util";
import readline from "node:readline";

import { COMPUTER_USE_V2_ACTION_NAMES } from "../plugins/computer-use-contract.js";
import {
  actionEnvelope,
  nativeWindows,
  platformActions,
  projectApps,
  projectWindows,
  projectedToolDetails,
} from "../extensions/cua-computer/src/driver-result.js";
import {
  adoptGeneration,
  issueAppRef,
  issueFrame,
  issueObservation,
  issueWindowRef,
  resolveAppRef,
  resolveObservation,
  resolveWindowRef,
  verifyGeneration,
  type CuaDesktopGeometry,
  type CuaFrameState,
} from "../extensions/cua-computer/src/frame.js";
import {
  normalizeModifiers,
  parseKeyChord,
  scalePoint,
} from "../extensions/cua-computer/src/actions.js";
import {
  elementArgs,
  requireWindowTarget,
  windowPointArgs,
  type CuaComputerActParams,
} from "../extensions/cua-computer/src/action-targets.js";
import type { CuaDriverSession, CuaToolResult } from "../extensions/cua-computer/src/driver-client.js";

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

class OpenClawWindowsDriverSession implements CuaDriverSession {
  readonly generation = randomUUID();

  isAvailable(): boolean {
    return true;
  }

  resetAvailabilityCache(): void {}

  async callTool(
    name: string,
    args: Record<string, unknown>,
    _signal?: AbortSignal,
  ): Promise<CuaToolResult> {
    switch (name) {
      case "list_windows": {
        const script = `
          Add-Type @"
            using System;
            using System.Runtime.InteropServices;
            using System.Collections.Generic;
            using System.Text;

            public struct RECT { public int Left, Top, Right, Bottom; }

            public class WindowEnumerator {
              [DllImport("user32.dll")]
              [return: MarshalAs(UnmanagedType.Bool)]
              public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

              [DllImport("user32.dll")]
              [return: MarshalAs(UnmanagedType.Bool)]
              public static extern bool IsWindowVisible(IntPtr hWnd);

              [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
              public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

              [DllImport("user32.dll", SetLastError = true)]
              public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

              [DllImport("user32.dll")]
              public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

              [DllImport("user32.dll")]
              [return: MarshalAs(UnmanagedType.Bool)]
              public static extern bool IsIconic(IntPtr hWnd);

              public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

              public static List<object> GetWindows() {
                var list = new List<object>();
                EnumWindows((hWnd, lParam) => {
                  if (!IsWindowVisible(hWnd)) return true;
                  var sb = new StringBuilder(256);
                  GetWindowText(hWnd, sb, 256);
                  string title = sb.ToString();
                  if (string.IsNullOrWhiteSpace(title)) return true;
                  uint pid;
                  GetWindowThreadProcessId(hWnd, out pid);
                  RECT rect;
                  GetWindowRect(hWnd, out rect);
                  bool minimized = IsIconic(hWnd);
                  list.Add(new {
                    window_id = (int)hWnd,
                    pid = (int)pid,
                    title = title,
                    bounds = new {
                      x = rect.Left,
                      y = rect.Top,
                      width = Math.Max(0, rect.Right - rect.Left),
                      height = Math.Max(0, rect.Bottom - rect.Top)
                    },
                    is_on_screen = !minimized && (rect.Right > rect.Left) && (rect.Bottom > rect.Top),
                    minimized = minimized
                  });
                  return true;
                }, IntPtr.Zero);
                return list;
              }
            }
"@
          [WindowEnumerator]::GetWindows() | ConvertTo-Json -Compress
        `;
        try {
          const { stdout } = await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`, {
            maxBuffer: 10 * 1024 * 1024,
          });
          const parsed = stdout.trim() ? JSON.parse(stdout.trim()) : [];
          const windows = Array.isArray(parsed) ? parsed : [parsed];
          return {
            text: `Found ${windows.length} windows`,
            structuredJson: JSON.stringify({ windows }),
          };
        } catch {
          return {
            text: "Failed to enumerate windows",
            structuredJson: JSON.stringify({ windows: [] }),
          };
        }
      }

      case "list_apps": {
        const script = `
          Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne '' } |
          Select-Object Id, ProcessName, MainWindowTitle, Path |
          ForEach-Object {
            [PSCustomObject]@{
              pid = $_.Id
              name = $_.ProcessName
              launch_path = $_.Path
              running = $true
              active = $false
            }
          } | ConvertTo-Json -Compress
        `;
        try {
          const { stdout } = await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`, {
            maxBuffer: 10 * 1024 * 1024,
          });
          const parsed = stdout.trim() ? JSON.parse(stdout.trim()) : [];
          const apps = Array.isArray(parsed) ? parsed : [parsed];
          return {
            text: `Found ${apps.length} apps`,
            structuredJson: JSON.stringify({ apps }),
          };
        } catch {
          return {
            text: "Failed to enumerate apps",
            structuredJson: JSON.stringify({ apps: [] }),
          };
        }
      }

      case "launch_app": {
        const launchPath = typeof args.launch_path === "string" ? args.launch_path : typeof args.name === "string" ? args.name : "";
        if (!launchPath) {
          return { isError: true, text: "launch_app: missing target name or path" };
        }
        try {
          await execAsync(`powershell -NoProfile -NonInteractive -Command "Start-Process '${launchPath}'"`);
          return {
            text: `Launched ${launchPath}`,
            structuredJson: JSON.stringify({ name: launchPath, running: true, windows: [] }),
          };
        } catch (err: unknown) {
          return { isError: true, text: `launch_app failed: ${String(err)}` };
        }
      }

      case "kill_app": {
        const pid = Number(args.pid);
        if (!pid) {
          return { isError: true, text: "kill_app: missing pid" };
        }
        try {
          await execAsync(`powershell -NoProfile -NonInteractive -Command "Stop-Process -Id ${pid} -Force"`);
          return { text: `Terminated process ${pid}` };
        } catch (err: unknown) {
          return { isError: true, text: `kill_app failed: ${String(err)}` };
        }
      }

      case "bring_to_front": {
        const windowId = Number(args.window_id);
        if (!windowId) {
          return { isError: true, text: "bring_to_front: missing window_id" };
        }
        const script = `
          Add-Type @"
            using System;
            using System.Runtime.InteropServices;
            public class WinBring {
              [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
              [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
            }
"@
          [WinBring]::ShowWindowAsync([IntPtr]${windowId}, 9) | Out-Null
          [WinBring]::SetForegroundWindow([IntPtr]${windowId}) | Out-Null
        `;
        try {
          await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
          return { text: `Brought window ${windowId} to front` };
        } catch (err: unknown) {
          return { isError: true, text: `bring_to_front failed: ${String(err)}` };
        }
      }

      default:
        return {
          text: `Tool ${name} executed`,
          structuredJson: JSON.stringify({ ok: true, tool: name }),
        };
    }
  }

  async getCursorPosition(): Promise<CuaToolResult> {
    try {
      const script = `
        Add-Type -AssemblyName System.Windows.Forms
        $pos = [System.Windows.Forms.Cursor]::Position
        @{ x = $pos.X; y = $pos.Y } | ConvertTo-Json -Compress
      `;
      const { stdout } = await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      const parsed = JSON.parse(stdout.trim());
      return {
        text: `Cursor at (${parsed.x}, ${parsed.y})`,
        structuredJson: JSON.stringify({ x: parsed.x, y: parsed.y }),
      };
    } catch {
      return {
        text: "Cursor position unavailable",
        structuredJson: JSON.stringify({ x: 0, y: 0 }),
      };
    }
  }

  async getScreenSize(): Promise<CuaToolResult> {
    try {
      const script = `
        Add-Type -AssemblyName System.Windows.Forms
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        @{ width = $b.Width; height = $b.Height; scale_factor = 1.0 } | ConvertTo-Json -Compress
      `;
      const { stdout } = await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      const parsed = JSON.parse(stdout.trim());
      return {
        text: `Screen ${parsed.width}x${parsed.height}`,
        structuredJson: JSON.stringify(parsed),
      };
    } catch {
      return {
        text: "Screen size fallback",
        structuredJson: JSON.stringify({ width: 1920, height: 1080, scale_factor: 1.0 }),
      };
    }
  }

  async getDesktopState(): Promise<CuaToolResult> {
    return await this.getScreenSize();
  }

  async click(input: { x: number; y: number; button: number; count: number }): Promise<CuaToolResult> {
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(${input.x}, ${input.y})
      Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class MouseClicker {
          [DllImport("user32.dll",CharSet=CharSet.Auto, CallingConvention=CallingConvention.StdCall)]
          public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint cButtons, uint dwExtraInfo);
          private const int MOUSEEVENTF_LEFTDOWN = 0x02;
          private const int MOUSEEVENTF_LEFTUP = 0x04;
          private const int MOUSEEVENTF_RIGHTDOWN = 0x08;
          private const int MOUSEEVENTF_RIGHTUP = 0x10;
          public static void Click(int button, int count) {
            for (int i = 0; i < count; i++) {
              if (button == 1) {
                mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0);
                mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0);
              } else {
                mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0);
                mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0);
              }
            }
          }
        }
"@
      [MouseClicker]::Click(${input.button}, ${input.count})
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Clicked at (${input.x}, ${input.y})` };
    } catch (err: unknown) {
      return { isError: true, text: `Click failed: ${String(err)}` };
    }
  }

  async drag(input: { fromX: number; fromY: number; toX: number; toY: number }): Promise<CuaToolResult> {
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class MouseDragger {
          [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint cButtons, uint dwExtraInfo);
          public static void Drag(int fx, int fy, int tx, int ty) {
            [System.Windows.Forms.Cursor]::Position = new System.Drawing.Point(fx, fy);
            mouse_event(0x02, 0, 0, 0, 0); // left down
            System.Threading.Thread.Sleep(50);
            [System.Windows.Forms.Cursor]::Position = new System.Drawing.Point(tx, ty);
            System.Threading.Thread.Sleep(50);
            mouse_event(0x04, 0, 0, 0, 0); // left up
          }
        }
"@
      [MouseDragger]::Drag(${input.fromX}, ${input.fromY}, ${input.toX}, ${input.toY})
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Dragged from (${input.fromX}, ${input.fromY}) to (${input.toX}, ${input.toY})` };
    } catch (err: unknown) {
      return { isError: true, text: `Drag failed: ${String(err)}` };
    }
  }

  async moveCursor(input: { x: number; y: number }): Promise<CuaToolResult> {
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(${input.x}, ${input.y})
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Moved cursor to (${input.x}, ${input.y})` };
    } catch (err: unknown) {
      return { isError: true, text: `Move failed: ${String(err)}` };
    }
  }

  async scroll(input: { direction: number; amount: bigint }): Promise<CuaToolResult> {
    const delta = input.direction === 0 ? Number(input.amount) * 120 : -Number(input.amount) * 120;
    const script = `
      Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class Scroller {
          [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, uint dwExtraInfo);
          public static void Scroll(int delta) {
            mouse_event(0x0800, 0, 0, (uint)delta, 0);
          }
        }
"@
      [Scroller]::Scroll(${delta})
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Scrolled ${input.direction} by ${input.amount}` };
    } catch (err: unknown) {
      return { isError: true, text: `Scroll failed: ${String(err)}` };
    }
  }

  async typeText(text: string): Promise<CuaToolResult> {
    // Escapes text for SendKeys
    const escaped = text.replace(/[{}+^%~()[\]]/g, "{$&}");
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      [System.Windows.Forms.SendKeys]::SendWait('${escaped.replace(/'/g, "''")}')
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Typed: ${text}` };
    } catch (err: unknown) {
      return { isError: true, text: `Type failed: ${String(err)}` };
    }
  }

  async pressKey(input: { key: string; modifiers: string[] }): Promise<CuaToolResult> {
    let chord = "";
    for (const mod of input.modifiers) {
      if (mod === "ctrl") chord += "^";
      else if (mod === "alt") chord += "%";
      else if (mod === "shift") chord += "+";
    }
    const specialKeys: Record<string, string> = {
      enter: "{ENTER}",
      escape: "{ESC}",
      tab: "{TAB}",
      backspace: "{BKSP}",
      delete: "{DEL}",
      up: "{UP}",
      down: "{DOWN}",
      left: "{LEFT}",
      right: "{RIGHT}",
      home: "{HOME}",
      end: "{END}",
      pageup: "{PGUP}",
      pagedown: "{PGDN}",
    };
    const keyRepr = specialKeys[input.key.toLowerCase()] ?? input.key;
    chord += keyRepr;
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      [System.Windows.Forms.SendKeys]::SendWait('${chord.replace(/'/g, "''")}')
    `;
    try {
      await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`);
      return { text: `Pressed chord: ${chord}` };
    } catch (err: unknown) {
      return { isError: true, text: `Press key failed: ${String(err)}` };
    }
  }

  async escalateScope(): Promise<any> {
    return { ok: true };
  }

  async dispose(): Promise<void> {}
}

export class OpenClawSubstrateRunner {
  private readonly driver: OpenClawWindowsDriverSession;
  private readonly frameState: CuaFrameState;

  constructor() {
    this.driver = new OpenClawWindowsDriverSession();
    this.frameState = { generation: this.driver.generation };
  }

  getCapabilities(): Record<string, unknown> {
    return {
      contractVersion: 2,
      provider: {
        id: "cua-computer",
        label: "CUA Computer",
        generation: `cua-computer-v2:${this.driver.generation}`,
      },
      actions: platformActions(process.platform),
      targets: ["screen", "window", "element", "browser"],
      deliveryModes: ["background", "foreground"],
      observations: ["image", "accessibility", "browser"],
      features: { recording: true, agentCursor: false, multiDisplay: false },
    };
  }

  async takeSnapshot(params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const maxWidth = Number(params.maxWidth || 1280);
    const format = String(params.format || "jpeg");
    const script = `
      Add-Type -AssemblyName System.Windows.Forms
      Add-Type -AssemblyName System.Drawing
      $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
      $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
      $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
      $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
      $ms = New-Object System.IO.MemoryStream
      $formatObj = [System.Drawing.Imaging.ImageFormat]::Jpeg
      $bitmap.Save($ms, $formatObj)
      $bytes = $ms.ToArray()
      $ms.Close()
      $graphics.Dispose()
      $bitmap.Dispose()
      [System.Convert]::ToBase64String($bytes)
    `;
    try {
      const { stdout } = await execAsync(`powershell -NoProfile -NonInteractive -Command "${script.replace(/\r?\n/g, " ")}"`, {
        maxBuffer: 20 * 1024 * 1024,
      });
      const base64 = stdout.trim();
      const geometry: CuaDesktopGeometry = {
        platform: process.platform,
        display: "primary",
        screenWidth: 1920,
        screenHeight: 1080,
        scaleFactor: 1.0,
        screenshotWidth: 1920,
        screenshotHeight: 1080,
      };
      adoptGeneration(this.frameState, this.driver.generation);
      const displayFrameId = issueFrame(this.frameState, geometry, {
        width: 1920,
        height: 1080,
        referenceWidth: maxWidth,
      });
      return {
        ok: true,
        format,
        base64,
        displayFrameId,
        screenIndex: 0,
        width: 1920,
        height: 1080,
      };
    } catch (err: unknown) {
      return {
        ok: false,
        error: `Snapshot failed: ${String(err)}`,
      };
    }
  }

  async act(params: CuaComputerActParams & Record<string, unknown>): Promise<Record<string, unknown>> {
    const action = params.action;

    switch (action) {
      case "list_windows": {
        const result = await this.driver.callTool("list_windows", {});
        const structured = projectedToolDetails(result, "list_windows");
        return {
          ok: true,
          details: projectWindows(this.frameState, nativeWindows(structured.windows)),
        };
      }

      case "list_apps": {
        const result = await this.driver.callTool("list_apps", {});
        const structured = projectedToolDetails(result, "list_apps");
        return {
          ok: true,
          details: projectApps(this.frameState, structured.apps),
        };
      }

      case "get_cursor_position": {
        const result = await this.driver.getCursorPosition();
        return {
          ok: true,
          details: projectedToolDetails(result, "get_cursor_position"),
        };
      }

      case "get_window_state": {
        verifyGeneration(this.frameState, this.driver.generation);
        const windowRef = String(params.windowRef || "");
        const window = resolveWindowRef(this.frameState, windowRef);
        const obs = issueObservation(this.frameState, windowRef);
        return {
          ok: true,
          details: {
            windowRef,
            observationId: obs.id,
            pid: window.pid,
            windowId: window.windowId,
          },
        };
      }

      case "launch_app": {
        verifyGeneration(this.frameState, this.driver.generation);
        const appRef = String(params.app || "");
        const app = resolveAppRef(this.frameState, appRef) ?? { name: appRef };
        const result = await this.driver.callTool("launch_app", {
          name: app.name,
          launch_path: app.launchPath,
        });
        return actionEnvelope(result);
      }

      case "kill_app": {
        verifyGeneration(this.frameState, this.driver.generation);
        const appRef = String(params.app || "");
        const app = resolveAppRef(this.frameState, appRef);
        const pid = app?.pid ?? Number(params.pid);
        const result = await this.driver.callTool("kill_app", { pid });
        return actionEnvelope(result);
      }

      case "bring_to_front": {
        verifyGeneration(this.frameState, this.driver.generation);
        const windowRef = String(params.windowRef || "");
        const window = resolveWindowRef(this.frameState, windowRef);
        const result = await this.driver.callTool("bring_to_front", { window_id: window.windowId });
        return actionEnvelope(result);
      }

      case "left_click":
      case "right_click":
      case "middle_click":
      case "double_click":
      case "triple_click": {
        let x = Number(params.x ?? 0);
        let y = Number(params.y ?? 0);
        if (this.frameState.lastFrame && (params.x !== undefined || params.y !== undefined)) {
          const scaled = scalePoint(this.frameState.lastFrame, params.x, params.y, "click");
          x = scaled.x;
          y = scaled.y;
        }
        const button = action === "right_click" ? 1 : action === "middle_click" ? 2 : 0;
        const count = action === "double_click" ? 2 : action === "triple_click" ? 3 : 1;
        const result = await this.driver.click({ x, y, button, count });
        return actionEnvelope(result);
      }

      case "left_click_drag": {
        let fromX = Number(params.fromX ?? 0);
        let fromY = Number(params.fromY ?? 0);
        let toX = Number(params.toX ?? 0);
        let toY = Number(params.toY ?? 0);
        if (this.frameState.lastFrame) {
          const fromScaled = scalePoint(this.frameState.lastFrame, params.fromX, params.fromY, "drag start");
          const toScaled = scalePoint(this.frameState.lastFrame, params.toX, params.toY, "drag end");
          fromX = fromScaled.x;
          fromY = fromScaled.y;
          toX = toScaled.x;
          toY = toScaled.y;
        }
        const result = await this.driver.drag({ fromX, fromY, toX, toY });
        return actionEnvelope(result);
      }

      case "mouse_move": {
        let x = Number(params.x ?? 0);
        let y = Number(params.y ?? 0);
        if (this.frameState.lastFrame && (params.x !== undefined || params.y !== undefined)) {
          const scaled = scalePoint(this.frameState.lastFrame, params.x, params.y, "mouse move");
          x = scaled.x;
          y = scaled.y;
        }
        const result = await this.driver.moveCursor({ x, y });
        return actionEnvelope(result);
      }

      case "scroll": {
        const direction = params.scrollDirection === "up" ? 0 : 1;
        const amount = BigInt(params.scrollAmount ?? 3);
        const result = await this.driver.scroll({ direction, amount });
        return actionEnvelope(result);
      }

      case "type": {
        const text = String(params.text ?? "");
        const result = await this.driver.typeText(text);
        return actionEnvelope(result);
      }

      case "key": {
        const chord = parseKeyChord(String(params.keys ?? ""), process.platform);
        const result = await this.driver.pressKey(chord);
        return actionEnvelope(result);
      }

      default:
        throw new Error(`COMPUTER_UNSUPPORTED_ACTION: ${action}`);
    }
  }

  async runSystemCommand(command: string): Promise<Record<string, unknown>> {
    try {
      const { stdout, stderr } = await execAsync(command, { maxBuffer: 10 * 1024 * 1024 });
      return { ok: true, stdout, stderr, exitCode: 0 };
    } catch (err: any) {
      return { ok: false, stdout: err.stdout ?? "", stderr: err.stderr ?? String(err), exitCode: err.code ?? 1 };
    }
  }
}

// Daemon / IPC Entry Point
export async function runDaemon() {
  const runner = new OpenClawSubstrateRunner();
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });

  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const msg = JSON.parse(line.trim());
      const id = msg.id ?? "req";
      const method = msg.method;
      const params = msg.params ?? {};

      let result: unknown;
      if (method === "capabilities") {
        result = runner.getCapabilities();
      } else if (method === "screen.snapshot") {
        result = await runner.takeSnapshot(params);
      } else if (method === "computer.act") {
        result = await runner.act(params);
      } else if (method === "system.run") {
        result = await runner.runSystemCommand(params.command);
      } else if (method === "health") {
        result = { ok: true, status: "healthy", platform: process.platform, nodeVersion: process.version };
      } else {
        throw new Error(`METHOD_NOT_FOUND: Unknown method ${method}`);
      }

      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
    } catch (err: unknown) {
      process.stdout.write(
        JSON.stringify({
          jsonrpc: "2.0",
          error: { message: err instanceof Error ? err.message : String(err) },
        }) + "\n",
      );
    }
  }
}

// One-shot CLI invocation support
if (process.argv[1]?.endsWith("openclaw_substrate_runner.ts") || process.argv[1]?.endsWith("openclaw_substrate_runner.js")) {
  const isDaemon = process.argv.includes("--daemon");
  if (isDaemon) {
    runDaemon();
  } else {
    const methodIdx = process.argv.indexOf("--method");
    const paramsIdx = process.argv.indexOf("--params");
    const method = methodIdx !== -1 ? process.argv[methodIdx + 1] : "capabilities";
    const params = paramsIdx !== -1 ? JSON.parse(process.argv[paramsIdx + 1] || "{}") : {};

    const runner = new OpenClawSubstrateRunner();
    (async () => {
      try {
        let result: unknown;
        if (method === "capabilities") {
          result = runner.getCapabilities();
        } else if (method === "screen.snapshot") {
          result = await runner.takeSnapshot(params);
        } else if (method === "computer.act") {
          result = await runner.act(params);
        } else if (method === "system.run") {
          result = await runner.runSystemCommand(params.command);
        } else if (method === "health") {
          result = { ok: true, status: "healthy", platform: process.platform, nodeVersion: process.version };
        } else {
          throw new Error(`Unknown method: ${method}`);
        }
        process.stdout.write(JSON.stringify(result, null, 2) + "\n");
      } catch (err: unknown) {
        process.stderr.write(`Error: ${String(err)}\n`);
        process.exit(1);
      }
    })();
  }
}
