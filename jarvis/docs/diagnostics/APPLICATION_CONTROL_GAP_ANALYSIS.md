# Diagnostic Gap Analysis: JARVIS Application & UI Control

## Executive Summary

JARVIS has established reliable foundations for voice interaction, web search, background gateway operation, shell execution, and basic browser navigation. However, controlling desktop applications has hitherto relied almost exclusively on low-level shell commands (PowerShell) and blind coordinate-based automation (`pyautogui`).

This document provides a comprehensive source-level and runtime gap analysis comparing current JARVIS capabilities with OpenClaw's mature computer-use architecture and Windows-native APIs. It defines the blueprint for transitioning from shell automation to structured, capability-driven In-App and UI Automation.

---

## 1. What JARVIS Can Currently Control

The existing Windows execution subsystem (`src/jarvis/execution/windows/`) exposes the following capabilities:
1. **Shell Command Execution (`shell.py`):** Runs arbitrary PowerShell scripts via `subprocess.run` with a default 60-second timeout.
2. **Process Management (`process.py`):** Enumerates processes via `psutil`, inspects PID metadata, launches processes via `subprocess.Popen`, and terminates PIDs.
3. **Desktop Automation (`desktop.py`):**
   - Captures full-screen screenshots via `pyautogui.screenshot()`.
   - Sends mouse clicks at explicit pixel coordinates `(x, y)` via `pyautogui.click()`.
   - Types text into the currently focused window via `pyautogui.write()`.
   - Sends key presses via `pyautogui.press()`.
   - Enumerates top-level window titles and bounding boxes via `pygetwindow.getAllWindows()`.
4. **Filesystem Operations (`filesystem.py`):** Reads, writes, lists, and deletes files.
5. **System Hardware Telemetry (`system.py`):** Inspects CPU/memory, sets/gets audio volume, sets/gets display brightness.
6. **Browser Automation (`src/jarvis/execution/browser/`):** Playwright-based web navigation, DOM element clicks, text entry, and page content extraction.

---

## 2. How It Currently Controls Applications & The Root Flaws

### Flaw A: The Blocking Shell Application Launch Defect
When instructed to open an application (e.g. "Open Paint"), JARVIS previously executed `mspaint` directly as a shell command. Because `subprocess.run` or synchronous PowerShell invocations wait for the child process to terminate, and GUI applications remain open indefinitely, the command inevitably stalled until the 60-second timeout expired.
**Required Pattern:** GUI application launches must be completely detached and non-blocking, followed by a bounded polling loop verifying process and window readiness.

### Flaw B: Blind Coordinate Guessing vs. Structural UI Automation
To click a button or select an option, the existing system relied on guessing pixel coordinates or requesting coordinates from the model. Pixel coordinates are fragile across display scaling (DPI), window repositioning, monitor resolutions, and dynamic layout changes.
**Required Pattern:** Direct invocation of Microsoft UI Automation (UIA) control patterns (`InvokePattern`, `ValuePattern`, `TogglePattern`, `SelectionItemPattern`) addressing controls by `AutomationId`, `Name`, and `ControlType`.

### Flaw C: Ambiguous Multi-Instance Window Selection
When multiple windows of an application exist (e.g., three Chrome windows or two File Explorer windows), `pygetwindow` simply returns a list of titles without process correlation or deterministic disambiguation. The system often operated on the wrong window or failed silently.
**Required Pattern:** HWND-based Window Manager tracking window handles, process IDs, thread IDs, z-order, and window state (`minimized`, `maximized`, `normal`, `active`).

### Flaw D: Lack of Internal In-App UI Tree Inspection
JARVIS had no way to answer: "What buttons are visible in Paint?" or "What items are in the File menu?".
**Required Pattern:** Accessibility tree extraction and normalization via Windows UI Automation `IUIAutomationTreeWalker`.

---

## 3. What Is Missing in JARVIS

| Missing Component | Description | Impact |
| :--- | :--- | :--- |
| **Native Windows UI Automation Service** | Out-of-process COM integration with `UIAutomationCore.dll` | Inability to inspect or manipulate controls inside native applications |
| **Control Pattern Dispatcher** | Direct programmatic execution of `Invoke`, `Value`, `Toggle`, `Selection`, `Scroll` | Reliance on coordinate clicking instead of native UI events |
| **Window Manager** | HWND-based window management, activation, placement, and state queries | Fragile window switching and failure with multiple instances |
| **Dynamic Application Registry** | Discovery of installed applications via Start Menu, registry, AppUserModelId, URI | Inability to discover or launch unlisted applications generically |
| **Asynchronous App Launcher** | Non-blocking execution + process/window readiness verification | Hangs on GUI launches and false-positive reporting |
| **Semantic Target Resolver** | Natural language to UI element matching with confidence scoring | Fragility when user phrasing does not match exact UI text |
| **Observe -> Act -> Verify Loop** | Strict post-action observation and state verification | Hallucinating successful outcomes without proof |
| **UI Tree Normalizer** | Filtering massive raw UI trees down to interactive controls | Overwhelming model context window with raw accessibility trees |

---

## 4. OpenClaw Implementations to Reuse

OpenClaw contains mature implementations of computer use and node capabilities that must be adapted into JARVIS rather than reinvented:

1. **`OpenClaw/src/plugins/computer-use-contract.ts`:**
   - Authoritative list of actions (`COMPUTER_USE_V2_ACTION_NAMES`): `screenshot`, `left_click`, `right_click`, `double_click`, `triple_click`, `mouse_move`, `left_click_drag`, `scroll`, `type`, `key`, `hold_key`, `wait`, `list_apps`, `list_windows`, `get_accessibility_tree`, `get_cursor_position`, `get_window_state`, `launch_app`, `kill_app`, `bring_to_front`, `set_value`, `zoom`, `invoke_menu`.
   - Contract verification and error taxonomies: `COMPUTER_CONTRACT_MISMATCH`, `COMPUTER_STALE_OBSERVATION`, `COMPUTER_UNSUPPORTED_ACTION`, `COMPUTER_INVALID_REQUEST`.
2. **`OpenClaw/extensions/cua-computer/src/window-actions.ts`:**
   - Window and element target extraction (`requireWindowTarget`, `elementArgs`, `windowPointArgs`).
   - App and window projection (`projectApps`, `projectWindows`, `windowObservation`).
   - Delivery mode handling (`background` accessibility delivery vs. `foreground` visual delivery).
3. **`OpenClaw/extensions/cua-computer/src/actions.ts`:**
   - Platform modifier normalization (Ctrl vs Cmd, Alt, Shift) and key chord parsing (`parseKeyChord`).
4. **`OpenClaw/extensions/cua-computer/src/driver-client.ts`:**
   - Uniform session and driver interface (`CuaDriverSession`, `ClickButton`, `ScrollDirection`, `EscalationReason`).
5. **`OpenClaw/src/node-host/runtime.ts`:**
   - Node-level computer use capability registration and lifecycle management.

---

## 5. Missing Windows-Native Capabilities & Technical Solution

On Windows, Microsoft provides the **UI Automation (UIA)** COM API via `UIAutomationCore.dll`.
Runtime inspection confirms that Python in the environment includes `comtypes`, `win32gui`, `win32process`, `win32con`, and `ctypes`:
- **`comtypes.client.GetModule('UIAutomationCore.dll')`** successfully generates typed COM interfaces (`IUIAutomation`, `IUIAutomationElement`, `IUIAutomationTreeWalker`, `IUIAutomationCondition`).
- **`IUIAutomation.GetRootElement()`** successfully accesses the desktop automation root.
- **`win32gui.SetForegroundWindow(hwnd)`**, `win32gui.ShowWindow(hwnd, sw_flag)`, `win32gui.GetWindowRect(hwnd)` provide direct, reliable window lifecycle controls.

---

## 6. Exact Source Files to Reuse & Adapt

| Source Location | Target in JARVIS | Strategy |
| :--- | :--- | :--- |
| `OpenClaw/src/plugins/computer-use-contract.ts` | `src/jarvis/execution/computer/contract.py` | Port TypeScript TypeBox contract to Python dataclasses & enums |
| `OpenClaw/extensions/cua-computer/src/actions.ts` | `src/jarvis/execution/computer/actions.py` | Adapt key chord parser and modifier normalization |
| `OpenClaw/extensions/cua-computer/src/window-actions.ts` | `src/jarvis/execution/computer/window_actions.py` | Adapt targeted window and element action dispatch logic |
| `OpenClaw/extensions/cua-computer/src/driver-result.ts` | `src/jarvis/execution/computer/driver_result.py` | Adapt observation projection and action envelope formatting |

---

## 7. Exact New JARVIS Components Required

```
src/jarvis/
├── core/
│   └── app_control_engine.py      # Universal Application Control Engine & Resolver
├── execution/
│   ├── computer/
│   │   ├── contract.py            # Adapted OpenClaw computer-use action contract
│   │   ├── actions.py             # Key chord & modifier parser
│   │   └── provider.py            # Computer use execution provider
│   └── windows/
│       ├── uia.py                 # Native Windows UI Automation (COM IUIAutomation)
│       ├── window_manager.py      # HWND-based Window Manager
│       ├── app_launcher.py        # Asynchronous non-blocking application launcher
│       └── app_registry.py        # Dynamic Application Registry & Discovery
```

---

## 8. Implementation Plan by Capability Domains

### Domain 1: Application Discovery & Robust Asynchronous Launch
- Implement `AppRegistry`: dynamic discovery of installed applications via Start Menu shortcuts, registered AppUserModelIds, Windows URI schemes, and running executables.
- Implement `AppLauncher`: non-blocking detached process spawn with readiness polling (process creation + main window visibility detection).

### Domain 2: Window Manager
- Implement `WindowManager`: HWND enumeration, active window resolution, multi-instance disambiguation, foreground switching, window placement (`minimize`, `maximize`, `restore`, `move`, `resize`, `close`), and geometry inspection.

### Domain 3: Windows UI Automation Service
- Implement `WindowsUIAutomation`: native COM `IUIAutomation` client, element lookup by `AutomationId`, `Name`, `ControlType`, and `ClassName`.
- Control Pattern invocation: `InvokePattern` (buttons), `ValuePattern` (text inputs), `TogglePattern` (checkboxes), `SelectionItemPattern` (lists/tabs), `ExpandCollapsePattern` (comboboxes/menus), and `ScrollPattern`.

### Domain 4: UI Tree Inspection & Semantic Normalization
- Extract hierarchical UI trees using `IUIAutomationTreeWalker`.
- Filter and normalize raw trees into concise, semantic summaries containing only interactive controls to preserve LLM token budgets.

### Domain 5: Semantic Target Resolution & Confidence Scoring
- Implement fuzzy matching between natural language queries and UI element properties (`AutomationId`, `Name`, `HelpText`, `ClassName`).
- Assign confidence scores (0.0 to 1.0); report ambiguity if multiple high-confidence targets match.

### Domain 6: Observe -> Act -> Verify Closed-Loop Execution
- Universal `AppControlEngine`:
  1. **Observe:** Inspect current window and UI tree.
  2. **Resolve:** Identify candidate element and automation provider (Native API vs. UI Automation vs. Browser vs. Computer-Use).
  3. **Act:** Execute control pattern or action.
  4. **Verify:** Re-observe state and confirm expected change.
  5. **Report:** Return status (`SUCCESS`, `FAILED`, `AMBIGUOUS`, `NEEDS_APPROVAL`).

### Domain 7: Gemini Live High-Level Tool Integration
- Expose high-level intent tools to Gemini 3.8 Live: `app_launch`, `app_focus`, `app_close`, `window_list`, `ui_inspect`, `ui_interact`, `computer_action`.
- Gemini specifies the intent; JARVIS resolves the provider, locates elements, executes, and verifies.

---

## 9. Current Limitations & Mitigation Strategy

1. **Custom Canvas & Inaccessible Frameworks:**
   - Some applications (e.g. games, raw Direct3D surfaces) do not populate accessibility trees.
   - *Mitigation:* Fall back to visual computer-use (`pyautogui` / vision screenshot) when UIA yields no interactive elements.
2. **Elevated Privileges (UAC):**
   - Non-elevated automation cannot inspect or interact with elevated (Administrator) windows due to UIPI (User Interface Privilege Isolation).
   - *Mitigation:* Detect AccessDenied and report `NEEDS_APPROVAL` / administrative elevation requirement.
3. **Transient UI Popups & Menus:**
   - Context menus can close upon focus shift.
   - *Mitigation:* Support in-process UIA caching and direct pattern invocation without forcing window deactivation.

---

## 10. Verification & Test Plan

1. **Automated Unit Tests:**
   - `test_app_registry.py`: Dynamic discovery of common system applications without hardcoded dictionaries.
   - `test_app_launcher.py`: Non-blocking launch and readiness detection.
   - `test_window_manager.py`: Multi-instance disambiguation, window listing, and state mutations.
   - `test_uia.py`: Element lookup, pattern invocation, tree normalization.
   - `test_app_control_engine.py`: Provider hierarchy selection, semantic target resolution, and closed-loop verification.
2. **Interactive Live Verification:**
   - Launching and verifying Paint, Notepad, Calculator, and File Explorer.
   - Interacting with buttons and input fields via native UIA control patterns.
   - Safe status reporting without false claims of success.
