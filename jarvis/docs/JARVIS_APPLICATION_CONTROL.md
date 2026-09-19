# JARVIS Application Control Architecture

## Executive Summary

The JARVIS Application Control Architecture transitions the system from basic shell automation to deep, structured in-app and UI interaction. Rather than guessing pixel coordinates or relying on fragile PowerShell command sequences, JARVIS employs a multi-provider hierarchy anchored by Microsoft UI Automation (UIA) and closed-loop state verification.

---

## 1. System Architecture Diagram

```
                              [USER]
                                │
                                ▼
                       [GEMINI 3.8 LIVE]
                                │
                                ▼
                      [JARVIS CONTROL PLANE]
                                │
                                ▼
                   [APPLICATION CONTROL ENGINE]
                                │
                                ▼
                       [TARGET RESOLUTION]
                                │
           ┌────────────────────┴────────────────────┐
           │                                         │
           ▼                                         ▼
┌──────────────────────────────┐          ┌─────────────────────┐
│ Provider Hierarchy:          │          │ Policy Engine &     │
│ 1. Native API / URI Scheme   │◄─────────┤ Capability Firewall │
│ 2. Windows UI Automation     │          │ (Risk & Approvals)  │
│ 3. Browser DOM Subsystem     │          └─────────────────────┘
│ 4. Computer-Use Fallback     │
└──────────────┬───────────────┘
               │
               ▼
         [APPLICATION]
               │
               ▼
         [OBSERVATION]
               │
               ▼
        [VERIFICATION]
               │
               ▼
        [ACTION RESULT]
               │
               ▼
       [CONTROL PLANE]
               │
               ▼
       [GEMINI 3.8 LIVE]
```

---

## 2. Automation Provider Hierarchy

When executing an in-app action, JARVIS determines the most deterministic and reliable provider using a strict hierarchy:

### Provider 1: Native Application / Programmatic API
- If an application exposes a documented programmatic interface, URI scheme, or verified command-line protocol (e.g. `code --goto <file>:<line>`, Spotify URI navigation), this path is preferred.
- **Verification Requirement:** The result must be independently verified via window or process state before reporting success.

### Provider 2: Windows UI Automation (Primary Desktop Mechanism)
- Out-of-process COM integration with Microsoft UI Automation (`UIAutomationCore.dll`).
- Discovers controls within the application automation tree (`AutomationElement`).
- Invokes native control patterns (`InvokePattern`, `ValuePattern`, `TogglePattern`, `SelectionItemPattern`, `ExpandCollapsePattern`, `ScrollPattern`) directly in the target process.

### Provider 3: Browser Subsystem (DOM & Web Pages)
- For web pages and browser-based applications (Chrome, Edge), JARVIS utilizes its specialized Playwright browser node.
- Interacts through DOM element handles, selectors, accessibility roles, and page evaluation rather than desktop mouse clicks.

### Provider 4: Computer-Use Fallback (Visual & Coordinate Actions)
- Used when UI Automation does not expose elements (e.g., custom rendering canvas, game engines, remote desktop sessions).
- Follows the OpenClaw CUA contract: captures desktop screenshots, calculates target positions, and sends hardware mouse/keyboard events through `pyautogui` or virtual input drivers, followed by screenshot comparison verification.

---

## 3. Core Architectural Subsystems

### A. Dynamic Application Registry (`AppRegistry`)
- Eliminates hardcoded application lists and dictionaries.
- Dynamically discovers applications installed on the host machine by:
  1. Inspecting Start Menu shortcuts (`.lnk` files across User and Common Start Menu directories).
  2. Enumerating registered `AppUserModelId` packages via Windows shell.
  3. Reading registered URI protocol handlers in `HKEY_CLASSES_ROOT`.
  4. Correlating running processes with their executable paths and window handles.
- Maps display names (e.g. "Notepad", "Paint", "Spotify") to verified executable targets.

### B. Asynchronous Application Launcher (`AppLauncher`)
- Solves the blocking GUI process hang defect.
- Spawns GUI processes using detached flags (`subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS`).
- Runs a bounded polling loop (e.g. 5 seconds) checking:
  1. Has the process ID been spawned?
  2. Does a visible top-level window exist for that process?
  3. Is the main window responsive and ready for input?
- Returns immediately upon verification without waiting for the GUI application to terminate.

### C. Window Manager (`WindowManager`)
- Tracks and controls desktop application windows using Win32 HWND handles.
- Capabilities:
  - `window.list()`: Enumerates all open windows with title, HWND, process ID, bounds, and visibility.
  - `window.find()`: Resolves windows using fuzzy title matching, process names, or HWND.
  - `window.focus()`: Brings target window to foreground cleanly using `SetForegroundWindow` with thread-input attachment.
  - `window.minimize()`, `window.maximize()`, `window.restore()`, `window.close()`.
  - `window.move()` and `window.resize()`.
- **Multi-Instance Disambiguation:** When multiple windows share an application name, the Window Manager selects the window matching task context (e.g. active document, URL in title, or most recently active).

### D. Windows UI Automation Engine (`WindowsUIAutomation`)
- Connects directly to Microsoft UI Automation COM engine (`IUIAutomation`).
- Inspects the live control hierarchy: buttons, text boxes, menus, tabs, checkboxes, comboboxes, tree items.
- Control Patterns supported:
  - `InvokePattern`: Clicks buttons and menu items programmatically without moving the physical mouse cursor.
  - `ValuePattern`: Sets text in input fields cleanly without typing character-by-character.
  - `TogglePattern`: Switches checkboxes and toggle buttons between checked and unchecked states.
  - `SelectionItemPattern`: Selects tabs, list items, and radio buttons.
  - `ExpandCollapsePattern`: Expands or collapses dropdown menus and tree views.
  - `ScrollPattern`: Scrolls panes up, down, left, or right.

### E. Semantic Target Resolution & Confidence Scoring
- Resolves user natural language instructions into concrete UI elements.
- Resolution cascade:
  `AutomationId -> Name -> ControlType -> Accessibility properties -> ClassName -> Content text -> Bounding box`
- Scoring:
  - Exact `AutomationId` match: 1.0 confidence.
  - Exact `Name` match: 0.95 confidence.
  - Case-insensitive substring match on `Name`: 0.85 confidence.
  - Fuzzy text similarity: 0.60 - 0.80 confidence.
- Ambiguity Policy: If multiple candidates have high confidence, the system does not guess; it reports ambiguity or prompts the operator for clarification.

### F. Normalized UI Tree Snapshots
- Raw UI trees can contain tens of thousands of layout containers, borders, and empty panes.
- The tree normalizer walks the accessibility tree and prunes non-interactive elements, producing a compact summary containing only actionable controls:
  ```
  Window: "Paint" [HWND: 0x001204A2]
  ├── Tab: "Home" (Selected)
  ├── Tab: "View"
  ├── Button: "Paste" (Enabled)
  ├── Button: "Cut" (Disabled)
  ├── Group: "Tools"
  │   ├── Button: "Pencil"
  │   ├── Button: "Fill with color"
  │   └── Button: "Text"
  └── Group: "Shapes"
      ├── Button: "Line"
      ├── Button: "Rectangle"
      └── Button: "Ellipse"
  ```
- This normalized representation is supplied to Gemini 3.8 Live, preserving token budgets while giving the model complete situational awareness.

---

## 4. The Closed Execution Loop: Observe -> Act -> Verify

Every in-app interaction strictly follows the four-step loop:

1. **OBSERVE:**
   - Query the active window and inspect the relevant branch of the UI automation tree.
2. **RESOLVE & ACT:**
   - Locate the target element and execute the appropriate control pattern or action via the selected provider.
3. **OBSERVE AGAIN:**
   - Query the application state immediately after the action has completed (e.g. check if the new tab opened, check if the button state changed, check if the dialog closed).
4. **VERIFY & REPORT:**
   - Compare post-action observation with expected state change.
   - Return structured result with authoritative status:
     - `SUCCESS`: Verified expected change.
     - `FAILED`: Action completed but expected change did not occur.
     - `AMBIGUOUS`: Multiple targets or conflicting state.
     - `NEEDS_APPROVAL`: Action touches privileged or sensitive controls.

---

## 5. Security & Policy Enforcement

All application and UI automation actions pass through the JARVIS Policy Engine before execution:
- **Capability Firewall:** UI actions require capability authorization (`CAPABILITY_COMPUTER_CLICK`, `CAPABILITY_COMPUTER_TYPE`, `CAPABILITY_UI_INVOKE`, `CAPABILITY_PROCESS_LAUNCH`).
- **Privileged Controls:** Interacting with credential fields, UAC prompts, system security settings, or destructive file operations triggers high-risk classification requiring explicit operator approval.
- **Auditing:** Every action logs target application, window handle, element AutomationId, provider type, and verification result without logging private input contents.
