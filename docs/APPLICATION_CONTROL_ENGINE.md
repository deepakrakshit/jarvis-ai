# JARVIS Application Control & Deep In-App Automation

**Status:** Technical Architecture & Driver Specification  
**Module:** `jarvis.core.app_control_engine` & `jarvis.execution.windows.uia`  
**Platform:** Microsoft Windows 10/11 x64  

---

## 1. Executive Summary

The Application Control Engine orchestrates desktop automation across applications running on Windows. Instead of blind pixel guessing or brittle shell scripts, JARVIS utilizes a four-tier provider hierarchy grounded in Microsoft UI Automation (COM UIA), Win32 API, Playwright DOM inspection, and coordinate CUA fallbacks.

Every desktop interaction strictly follows the closed-loop paradigm:
$$\text{Observe} \longrightarrow \text{Resolve} \longrightarrow \text{Act} \longrightarrow \text{Verify}$$

---

## 2. Multi-Provider Automation Hierarchy

When an action is requested against an application or control, JARVIS selects the most deterministic provider:

```
[Target Action Request]
          │
          ▼
┌─────────────────────────────────┐
│ Tier 1: Native Application URI  │  e.g., Spotify URI, VSCode protocol, deep links
└────────────────┬────────────────┘
                 │ (if unsupported or failed)
                 ▼
┌─────────────────────────────────┐
│ Tier 2: Microsoft COM UIA Tree  │  Direct COM interface: UIAutomationCore.dll
└────────────────┬────────────────┘  Control patterns (Invoke, Value, Toggle)
                 │ (if target is browser web content)
                 ▼
┌─────────────────────────────────┐
│ Tier 3: Browser DOM Subsystem   │  Playwright CDP connection, accessibility tree,
└────────────────┬────────────────┘  CSS/XPath selectors, semantic locator clicks
                 │ (if element unexposed by accessibility)
                 ▼
┌─────────────────────────────────┐
│ Tier 4: Coordinate CUA Fallback │  Bounding box center calculation, Substrate
└─────────────────────────────────┘  input simulation (mouse_click, type, drag)
```

---

## 3. Microsoft UI Automation (COM UIA) Integration

### 3.1 Out-of-Process COM Client
JARVIS interfaces with `UIAutomationCore.dll` through comtypes and Win32 interfaces, providing high-speed traversal of the Windows desktop automation tree:
- **Root Element:** `IUIAutomation::GetRootElement()` points to the desktop window.
- **Tree Walking:** Fast tree navigation via `IUIAutomationTreeWalker` (ControlViewWalker or RawViewWalker).
- **Element Caching:** Scans only top-level windows and active window subtrees to preserve CPU cycles.

### 3.2 Native Control Patterns
Rather than moving the mouse to coordinates, UIA allows invoking actions directly inside target applications:
- **`IUIAutomationInvokePattern`:** Dispatches `Invoke()` on buttons, hyperlinks, and menu items.
- **`IUIAutomationValuePattern`:** Sets text values on edit boxes and inputs via `SetValue(BSTR)` without stealing focus.
- **`IUIAutomationTogglePattern`:** Toggles checkboxes and radio controls.
- **`IUIAutomationSelectionItemPattern`:** Selects list and combo items.
- **`IUIAutomationExpandCollapsePattern`:** Expands accordion menus and dropdowns.
- **`IUIAutomationScrollPattern`:** Scrolls containers and documents programmatically.

### 3.3 Safe Fallbacks
When an element implementation rejects direct COM pattern calls (e.g. non-standard custom controls or permission isolation):
1. Element focus is requested (`SetFocus()`).
2. Element bounding coordinates are computed from `CurrentBoundingRectangle`.
3. Bounding box center $(x, y)$ is clicked via the JARVIS Native Substrate or Win32 `mouse_event`.
4. Text input is typed via `pyautogui` or substrate keystroke simulation.

---

## 4. Closed-Loop State Verification

JARVIS enforces deterministic verification for every interaction:
1. **Pre-Action State Snapshot:** Captures element presence, bounding box, value, and enabled state.
2. **Action Execution:** Invokes the resolved pattern or simulated hardware action.
3. **Settling Delay:** Allows target application message pumps to process events.
4. **Post-Action State Snapshot:** Re-queries the element or window state to verify:
   - For text entry: element value matches the expected string.
   - For button clicks: element state changed, or element was dismissed (e.g. dialog closed), or a new window appeared.
5. If verification fails, the engine transitions down the provider hierarchy to coordinate fallback.
