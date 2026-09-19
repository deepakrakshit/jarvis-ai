# JARVIS Application Control: OpenClaw Source Reuse Map

This document establishes the authoritative source mapping between mature implementations in OpenClaw and the JARVIS Application Control subsystem.

---

## 1. Source Reuse Mapping Table

| Capability | Existing Implementation | Source Path | Reuse Strategy | JARVIS Integration |
| :--- | :--- | :--- | :--- | :--- |
| **Computer-Use Contract & Action Enums** | TypeBox action schemas, `COMPUTER_USE_V2_ACTION_NAMES`, error codes | `OpenClaw/src/plugins/computer-use-contract.ts` | **ADAPT** to Python dataclasses & enums | `src/jarvis/execution/computer/contract.py` |
| **Targeted Window Actions & Dispatch** | Window/element target routing, `requireWindowTarget`, `elementArgs` | `OpenClaw/extensions/cua-computer/src/window-actions.ts` | **ADAPT** to Python dispatch architecture | `src/jarvis/execution/computer/window_actions.py` |
| **Key Chords & Modifier Parsing** | `normalizeModifiers`, `parseKeyChord` | `OpenClaw/extensions/cua-computer/src/actions.ts` | **ADAPT** to Python string/modifier parser | `src/jarvis/execution/computer/actions.py` |
| **Driver Result Projections & Envelopes** | `actionEnvelope`, `projectApps`, `projectWindows`, `windowObservation` | `OpenClaw/extensions/cua-computer/src/driver-result.ts` | **ADAPT** to Python observation models | `src/jarvis/execution/computer/driver_result.py` |
| **Execution Resources & Lifecycles** | Resource management, ref registration, generation tracking | `OpenClaw/extensions/cua-computer/src/execution-resources.ts` | **ADAPT** to Python session resources | `src/jarvis/execution/computer/resources.py` |
| **Browser Subsystem** | Playwright session management, DOM interaction, page extraction | `OpenClaw/extensions/browser/` & `src/jarvis/execution/browser/` | **REUSE & ENHANCE** existing Playwright layer | `src/jarvis/execution/browser/session.py` |
| **Native Windows UI Automation** | Microsoft UI Automation COM (`IUIAutomation`) via `UIAutomationCore.dll` | Windows OS Native API / `comtypes.gen.UIAutomationClient` | **NATIVE INTEGRATION** via COM interop | `src/jarvis/execution/windows/uia.py` |
| **Window Management** | Win32 HWND APIs (`SetForegroundWindow`, `ShowWindow`, `GetWindowRect`) | Windows OS Native API via `win32gui`, `win32process` | **NATIVE INTEGRATION** via pywin32 | `src/jarvis/execution/windows/window_manager.py` |
| **Process Inspection & Management** | Process enumeration, executable path resolution, memory/CPU | `psutil` + `subprocess` in `src/jarvis/execution/windows/process.py` | **REUSE & EXTEND** with asynchronous readiness checks | `src/jarvis/execution/windows/app_launcher.py` |
| **Policy & Approvals** | High-risk action gating, approval interception, capability firewall | `src/jarvis/policy/engine.py` & `src/jarvis/policy/firewall.py` | **REUSE & EXTEND** with UI action permissions | `src/jarvis/policy/engine.py` |
| **Action Broker** | Capability routing, synchronous and asynchronous execution dispatch | `src/jarvis/actions/broker.py` & `src/jarvis/actions/registry.py` | **REGISTER** new application and UI capabilities | `src/jarvis/actions/registry.py` |

---

## 2. Component Dependency Graph

```
                                  [USER]
                                    │
                                    ▼
                           [Gemini 3.8 Live]
                                    │
                                    ▼
                          [JARVIS Control Plane]
                                    │
                                    ▼
                      [Application Control Engine]
                     (app_control_engine.py)
                                    │
                   ┌────────────────┴────────────────┐
                   │   Capability & Provider Resolver │
                   └────────────────┬────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼
[Provider: Native/UIA]     [Provider: Browser]     [Provider: Computer-Use]
 (uia.py, window_manager)     (browser/session)      (execution/computer)
         │                          │                          │
         ▼                          ▼                          ▼
 [Windows Applications]     [Web Pages / DOM]      [Visual Canvas / Fallback]
```

---

## 3. Implementation Blueprint

### A. Computer-Use Contract Adapter (`src/jarvis/execution/computer/contract.py`)
Directly reflects `OpenClaw/src/plugins/computer-use-contract.ts`:
- Actions: `screenshot`, `left_click`, `right_click`, `double_click`, `triple_click`, `mouse_move`, `left_click_drag`, `scroll`, `type`, `key`, `hold_key`, `wait`, `list_apps`, `list_windows`, `get_accessibility_tree`, `get_cursor_position`, `get_window_state`, `launch_app`, `kill_app`, `bring_to_front`, `set_value`, `zoom`, `invoke_menu`.
- Canonical status and error envelopes matching OpenClaw.

### B. Windows UI Automation Engine (`src/jarvis/execution/windows/uia.py`)
Fulfills the core requirement for structured in-app UI control:
- Initializes `IUIAutomation` COM interface via `comtypes.client.GetModule('UIAutomationCore.dll')`.
- Implements `get_root_element()`, `find_window_element(hwnd)`, `find_elements_by_condition()`, `inspect_ui_tree()`.
- Implements pattern invocation:
  - `InvokePattern.Invoke()` for buttons, menu items, hyperlinks.
  - `ValuePattern.SetValue()` for text boxes, editable combo boxes.
  - `TogglePattern.Toggle()` for checkboxes and switches.
  - `SelectionItemPattern.Select()` for list items, tabs, and radio buttons.
  - `ExpandCollapsePattern.Expand()` and `Collapse()` for dropdowns and tree nodes.
  - `ScrollPattern.Scroll()` for scrollable panels.

### C. HWND Window Manager (`src/jarvis/execution/windows/window_manager.py`)
- Provides deterministic window discovery and switching.
- Tracks `hwnd`, `title`, `process_id`, `process_name`, `class_name`, `is_visible`, `is_minimized`, `bounds`.
- Methods: `list_windows()`, `find_window(title_or_app)`, `focus_window(hwnd)`, `minimize_window(hwnd)`, `maximize_window(hwnd)`, `restore_window(hwnd)`, `close_window(hwnd)`, `move_window(hwnd, x, y, w, h)`.

### D. Dynamic Application Registry & Asynchronous Launcher
- `app_registry.py`: Dynamic enumeration of installed applications via Start Menu `.lnk` files, registered AppUserModelIds, registered URI schemes, and running processes.
- `app_launcher.py`: Detached process execution with non-blocking polling for process creation and window readiness, eliminating GUI launch timeouts.

### E. Universal Application Control Engine (`src/jarvis/core/app_control_engine.py`)
- Sits above all providers.
- Coordinates target resolution (fuzzy matching, confidence scoring).
- Executes the closed-loop **Observe -> Act -> Verify** cycle.
- Integrates with the JARVIS Policy Engine and Action Broker.
