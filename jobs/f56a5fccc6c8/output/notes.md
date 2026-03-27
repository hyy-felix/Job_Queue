# Resume Selection Notes — Oklo Inc, Mechatronics Engineer

## JD Analysis

Oklo is building a Fuel Recycling Facility requiring remote-operated robotic material-handling in radiological hot cells. The role is heavily hardware-focused: designing automated systems, CAD modeling, prototyping, integration, and documentation. Key differentiators from a generic mechatronics role:

1. **Remote operation** in hazardous (high-radiation, high-temperature, high-corrosion) environments
2. **Nuclear/fuel handling** domain — regulatory documentation matters
3. **Full lifecycle ownership** — architecture through commissioning
4. **Startup pace** — iterative prototyping, not just design

## Project Selection Rationale (5 projects)

### Xiaoxian (Work Experience) — 4 bullets
Strongest direct relevance: robotic arm control for **fuel rod testing** maps directly to Oklo's nuclear fuel handling mission. Modbus RTU communication, sensor integration for safety, and encoder-based precision are all core JD matches. Positioned first in Work Experience to lead with nuclear-adjacent experience.

### Crane / Remote-Mining Excavator (Work Experience) — 4 bullets
Remote-controlled autonomous system with ESP32 Wi-Fi control maps to Oklo's remote-operated theme. PCB consolidation, FEA-driven structural design, and modular prototyping iterations demonstrate the full hardware lifecycle Oklo needs.

### LIFT (Professional Experience) — 3 bullets
Cross-functional team leadership, robotic arm design in Autodesk Fusion, and precision sensor integration (OptiTrack for closed-loop control) address the JD's leadership, CAD, and automation integration requirements.

### Vacuum (Professional Experience) — 3 bullets
FMEA analysis, sensor-driven diagnostics, and magnetically coupled drive for sealed environments are highly transferable to Oklo's harsh-environment engineering. The concept-to-prototype lifecycle demonstrates the startup-pace prototyping Oklo wants.

### ROBOCON (Professional Experience) — 3 bullets
End-to-end mechanical development lifecycle (CAD, BOM, supplier coordination), structural simulation in Ansys, and DFM for CNC machining round out the resume's coverage of fabrication, testing, and manufacturing skills.

## Overlap Resolution Summary

All 7 overlap tag groups resolved:
- [1]: Kept integration-focused variant over timeline variant
- [2]: Kept lifecycle variant (CAD/BOM/supplier) over team management or part sourcing variants
- [3]: Kept FMEA + diagnostics over DFA fabrication or subsystem integration
- [4]: Kept ESP32 remote control variant (remote operation = core JD theme)
- [5]: Kept SolidWorks FEA + PCB variant over standalone fuzzy PID or PLC migration
- [6]: Kept "Engineered magnetically coupled drive" over R&D experiment variant
- [7]: Kept robotic arm design over decision matrix variant

## Unmatched JD Gaps

1. **High-radiation environment experience**: No direct experience. Xiaoxian fuel rod testing and Vacuum sealed-environment engineering are the closest transferable matches.
2. **Regulatory/licensing documentation**: No direct experience generating NRC or similar regulatory filings. The Xiaoxian PLC review/validation work is weakly transferable.
3. **Nuclear domain knowledge**: Academic mechatronics + fuel rod testing internship provide a foundation but not deep nuclear engineering expertise.

## Edit Log

Only two minor edits were made:
1. **Xiaoxian sensor bullet**: Combined two sentences, removed "sensors like" for directness. No factual change.
2. **LIFT OptiTrack bullet**: Removed "in indoor environments" for brevity. No factual change.

## Page Fit Notes

17 bullets across 5 projects. If page overflow occurs, the trim_priority list in selection_log.json provides the removal order. The lowest-priority bullets (ROBOCON DFM, Crane modular prototype, ROBOCON collision analysis) can be removed first without dropping any project below the 2-bullet minimum.

---

## Cover Letter Notes

### Theme Selection (3 dimensions)
1. **Technical depth — Robotic automation for fuel handling**: Xiaoxian robotic arm programming, STM32 Modbus gateway, sensor integration for safety monitoring
2. **Hardware/deployment — Remote-operated systems & sealed-environment prototyping**: Crane ESP32 remote control with 95% repeatability, PCB consolidation, VacDry magnetically coupled drive for sealed chambers, rapid iterative prototyping
3. **Honest gap acknowledgment + transferable fit**: Acknowledged lack of direct high-radiation experience; positioned fuel-handling, remote-operated, and sealed-environment work as transferable

### JD Keyword Mapping
| JD Keyword | Paragraph | Source Bullet |
|---|---|---|
| robotic material-handling | P1 | Xiaoxian-2 (six-axis robotic arm) |
| automation integration | P2 | Xiaoxian-7 (sensor integration) |
| electromechanical systems | P2 | Xiaoxian-1 (STM32 gateway) |
| prototyping and iteration | P3 | Crane-14 (3 iterations in 2 months) |
| remote-operated | P4 | Crane-3 [4] (ESP32 remote control) |

### Self-Review Results
- **Word count**: ~286 words (within 250-300 target)
- **Paragraphs**: 4 exactly
- **Banned phrases**: None detected
- **Passive voice**: None detected
- **Repetition**: No accomplishment appears in more than one paragraph
- **Truthfulness**: All claims verified against Bullet_Point_Base.md
- **Format**: Block style (no indent), Pacific Time date, no role title in recipient block
- **Company facts used**: Fuel Recycling Facility (from JD), first-of-kind facility (from company research) — both verifiable
