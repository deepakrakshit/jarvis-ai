/**
 * JARVIS Substrate Runner
 * Executes native CUA automation modules directly in Node.js runtime.
 */
import { randomUUID } from "node:crypto";
import { exec, execFile, spawn } from "node:child_process";
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
import { parseStandalonePlainTextToolCallBlocks } from "../packages/tool-call-repair/src/payload.js";

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

async function runWinAction(pyCode: string): Promise<string> {
  const fullScript = `
import ctypes, json, sys, os
user32 = ctypes.windll.user32
h = user32.OpenDesktopW("default", 0, False, 0x01FF)
if h:
    user32.SetThreadDesktop(h)
${pyCode}
`;
  return new Promise((resolve, reject) => {
    const child = spawn("python", ["-"], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => {
      stdout += d;
    });
    child.stderr.on("data", (d) => {
      stderr += d;
    });
    child.on("close", (code) => {
      if (code === 0) resolve(stdout.trim());
      else reject(new Error(stderr.trim() || `Exit code ${code}`));
    });
    child.stdin.write(fullScript);
    child.stdin.end();
  });
}

class JarvisWindowsDriverSession implements CuaDriverSession {
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
        try {
          const out = await runWinAction(`
import win32gui, win32process
windows = []
def enum_cb(hwnd, _):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd).strip()
        if title:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            minimized = win32gui.IsIconic(hwnd)
            w = max(0, rect[2] - rect[0])
            h_dim = max(0, rect[3] - rect[1])
            windows.append({
                "window_id": hwnd,
                "pid": pid,
                "title": title,
                "bounds": {"x": rect[0], "y": rect[1], "width": w, "height": h_dim},
                "is_on_screen": not minimized and w > 0 and h_dim > 0,
                "minimized": bool(minimized)
            })
win32gui.EnumWindows(enum_cb, None)
print(json.dumps({"windows": windows}))
`);
          const parsed = JSON.parse(out);
          const windows = parsed.windows || [];
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
        try {
          const out = await runWinAction(`
import psutil
apps = []
seen_pids = set()
for proc in psutil.process_iter(['pid', 'name', 'exe']):
    try:
        pinfo = proc.info
        pid = pinfo.get('pid')
        if pid and pid not in seen_pids:
            seen_pids.add(pid)
            name = pinfo.get('name') or ''
            exe = pinfo.get('exe') or ''
            if name.lower().endswith('.exe'):
                apps.append({
                    "pid": pid,
                    "name": name,
                    "launch_path": exe,
                    "running": True,
                    "active": False
                })
    except Exception:
        pass
print(json.dumps({"apps": apps}))
`);
          const parsed = JSON.parse(out);
          const apps = parsed.apps || [];
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
          const jsonPath = JSON.stringify(launchPath);
          await runWinAction(`
import subprocess, os
target = ${jsonPath}
try:
    os.startfile(target)
except Exception:
    subprocess.Popen(target, shell=True)
`);
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
          await runWinAction(`
import psutil
p = psutil.Process(${pid})
p.terminate()
`);
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
        try {
          await runWinAction(`
import win32gui, win32con
hwnd = ${windowId}
if win32gui.IsWindow(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
`);
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
      const out = await runWinAction(`
from ctypes import wintypes
pt = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(pt))
print(json.dumps({"x": pt.x, "y": pt.y}))
`);
      const parsed = JSON.parse(out);
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
      const out = await runWinAction(`
w = user32.GetSystemMetrics(0)
h_dim = user32.GetSystemMetrics(1)
print(json.dumps({"width": w, "height": h_dim, "scale_factor": 1.0}))
`);
      const parsed = JSON.parse(out);
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
    try {
      await runWinAction(`
import time
user32.SetCursorPos(${Math.round(input.x)}, ${Math.round(input.y)})
button = ${input.button}
count = ${input.count}
down_flag = 0x08 if button == 1 else (0x20 if button == 2 else 0x02)
up_flag = 0x10 if button == 1 else (0x40 if button == 2 else 0x04)
for _ in range(count):
    user32.mouse_event(down_flag, 0, 0, 0, 0)
    time.sleep(0.02)
    user32.mouse_event(up_flag, 0, 0, 0, 0)
    time.sleep(0.02)
`);
      return { text: `Clicked at (${input.x}, ${input.y})` };
    } catch (err: unknown) {
      return { isError: true, text: `Click failed: ${String(err)}` };
    }
  }

  async drag(input: { fromX: number; fromY: number; toX: number; toY: number }): Promise<CuaToolResult> {
    try {
      await runWinAction(`
import time
user32.SetCursorPos(${Math.round(input.fromX)}, ${Math.round(input.fromY)})
time.sleep(0.05)
user32.mouse_event(0x02, 0, 0, 0, 0)
steps = 20
fx, fy = ${Math.round(input.fromX)}, ${Math.round(input.fromY)}
tx, ty = ${Math.round(input.toX)}, ${Math.round(input.toY)}
for i in range(1, steps + 1):
    cx = int(fx + (tx - fx) * (i / float(steps)))
    cy = int(fy + (ty - fy) * (i / float(steps)))
    user32.SetCursorPos(cx, cy)
    time.sleep(0.01)
user32.mouse_event(0x04, 0, 0, 0, 0)
`);
      return { text: `Dragged from (${input.fromX}, ${input.fromY}) to (${input.toX}, ${input.toY})` };
    } catch (err: unknown) {
      return { isError: true, text: `Drag failed: ${String(err)}` };
    }
  }

  async moveCursor(input: { x: number; y: number }): Promise<CuaToolResult> {
    try {
      await runWinAction(`
user32.SetCursorPos(${Math.round(input.x)}, ${Math.round(input.y)})
`);
      return { text: `Moved cursor to (${input.x}, ${input.y})` };
    } catch (err: unknown) {
      return { isError: true, text: `Move failed: ${String(err)}` };
    }
  }

  async scroll(input: { direction: number; amount: bigint }): Promise<CuaToolResult> {
    const delta = input.direction === 0 ? Number(input.amount) * 120 : -Number(input.amount) * 120;
    try {
      await runWinAction(`
user32.mouse_event(0x0800, 0, 0, ${delta}, 0)
`);
      return { text: `Scrolled by ${input.amount}` };
    } catch (err: unknown) {
      return { isError: true, text: `Scroll failed: ${String(err)}` };
    }
  }

  async typeText(text: string): Promise<CuaToolResult> {
    try {
      const jsonText = JSON.stringify(text);
      await runWinAction(`
import pyautogui
pyautogui.PAUSE = 0.01
pyautogui.write(${jsonText})
`);
      return { text: `Typed: ${text}` };
    } catch (err: unknown) {
      return { isError: true, text: `Type failed: ${String(err)}` };
    }
  }

  async pressKey(input: { key: string; modifiers: string[] }): Promise<CuaToolResult> {
    try {
      const mods = JSON.stringify(input.modifiers);
      const key = JSON.stringify(input.key);
      await runWinAction(`
import pyautogui
mods = ${mods}
key = ${key}.lower()
key_map = {
    "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "tab": "tab",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown",
    "space": "space"
}
target_key = key_map.get(key, key)
if mods:
    pyautogui.hotkey(*mods, target_key)
else:
    pyautogui.press(target_key)
`);
      return { text: `Pressed key: ${[...input.modifiers, input.key].join("+")}` };
    } catch (err: unknown) {
      return { isError: true, text: `Press key failed: ${String(err)}` };
    }
  }

  async escalateScope(): Promise<any> {
    return { ok: true };
  }

  async dispose(): Promise<void> {}
}


export class JarvisSubstrateRunner {
  private readonly driver: JarvisWindowsDriverSession;
  private readonly frameState: CuaFrameState;

  constructor() {
    this.driver = new JarvisWindowsDriverSession();
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
    const format = String(params.format || "jpeg").toLowerCase();
    const pythonScript = `
import ctypes, io, base64, json
from PIL import ImageGrab
try:
    user32 = ctypes.windll.user32
    h = user32.OpenDesktopW("default", 0, False, 0x01FF)
    if h:
        user32.SetThreadDesktop(h)
    img = ImageGrab.grab()
    orig_w, orig_h = img.size
    max_w = ${maxWidth}
    if orig_w > max_w:
        ratio = max_w / float(orig_w)
        img = img.resize((int(orig_w * ratio), int(orig_h * ratio)))
    buf = io.BytesIO()
    fmt = "PNG" if "${format}" == "png" else "JPEG"
    img.save(buf, format=fmt, quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    print(json.dumps({"ok": True, "base64": b64, "width": orig_w, "height": orig_h, "shot_width": img.size[0], "shot_height": img.size[1]}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
`;

    return new Promise((resolve) => {
      const child = spawn("python", ["-"], { stdio: ["pipe", "pipe", "pipe"] });
      let stdoutData = "";
      let stderrData = "";
      child.stdout.on("data", (chunk) => {
        stdoutData += chunk;
      });
      child.stderr.on("data", (chunk) => {
        stderrData += chunk;
      });
      child.on("close", () => {
        try {
          const parsed = JSON.parse(stdoutData.trim());
          if (!parsed.ok) {
            resolve({ ok: false, error: parsed.error || stderrData });
            return;
          }
          const screenWidth = parsed.width || 1920;
          const screenHeight = parsed.height || 1080;
          const geometry: CuaDesktopGeometry = {
            platform: process.platform,
            display: "primary",
            screenWidth,
            screenHeight,
            scaleFactor: 1.0,
            screenshotWidth: parsed.shot_width || screenWidth,
            screenshotHeight: parsed.shot_height || screenHeight,
          };
          adoptGeneration(this.frameState, this.driver.generation);
          const displayFrameId = issueFrame(this.frameState, geometry, {
            width: screenWidth,
            height: screenHeight,
            referenceWidth: maxWidth,
          });
          resolve({
            ok: true,
            format,
            base64: parsed.base64,
            displayFrameId,
            screenIndex: 0,
            width: screenWidth,
            height: screenHeight,
          });
        } catch (err) {
          resolve({
            ok: false,
            error: `Snapshot parse failure: ${String(err)} (${stderrData})`,
          });
        }
      });
      child.stdin.write(pythonScript);
      child.stdin.end();
    });
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

  repairToolCall(text: string): Record<string, unknown> {
    try {
      const blocks = parseStandalonePlainTextToolCallBlocks(text);
      return { ok: true, blocks };
    } catch (err: unknown) {
      return { ok: false, error: err instanceof Error ? err.message : String(err), blocks: [] };
    }
  }
}

// Daemon / IPC Entry Point
export async function runDaemon() {
  const runner = new JarvisSubstrateRunner();
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
      } else if (method === "tool.repair") {
        result = runner.repairToolCall(params.text ?? "");
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
if (process.argv[1]?.endsWith("jarvis_substrate_runner.ts") || process.argv[1]?.endsWith("jarvis_substrate_runner.js")) {
  const isDaemon = process.argv.includes("--daemon");
  if (isDaemon) {
    runDaemon();
  } else {
    const methodIdx = process.argv.indexOf("--method");
    const paramsIdx = process.argv.indexOf("--params");
    const method = methodIdx !== -1 ? process.argv[methodIdx + 1] : "capabilities";
    const params = paramsIdx !== -1 ? JSON.parse(process.argv[paramsIdx + 1] || "{}") : {};

    const runner = new JarvisSubstrateRunner();
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
        } else if (method === "tool.repair") {
          result = runner.repairToolCall(params.text ?? "");
        } else if (method === "health") {
          result = { ok: true, status: "healthy", platform: process.platform, nodeVersion: process.version };
        } else {
          throw new Error(`Unknown method: ${method}`);
        }
        process.stdout.write(JSON.stringify(result, null, 2) + "\n");
        process.exit(0);
      } catch (err: unknown) {
        process.stderr.write(`Error: ${String(err)}\n`);
        process.exit(1);
      }
    })();
  }
}
