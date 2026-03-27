# Resume Selection Notes — Wilder Systems Sr. Robotics Software Engineer

## JD Summary
Wilder Systems builds modular autonomous robotic systems for aerospace manufacturing (Robots-as-a-Service). The Sr. Robotics Software Engineer role demands:
- Real-time robotic software stack: motion control, kinematics, trajectory planning, MoveIt, C++/Python/ROS 2
- Autonomy: perception, localization, mapping, path planning, sensor fusion
- Full-stack HW/SW integration: sensors, actuators, PLCs, industrial controllers
- Testing/validation: simulation, HIL, telemetry, root-cause analysis
- Leadership: mentoring, code reviews, CI/CD standards
- Production deployment and lifecycle support

## Project Selection Rationale (4 projects, 17 bullets)

### 1. Xiaoxian (Work Experience) — 4 bullets
Strongest direct match for industrial robotics: six-axis robotic arm programming, Modbus RTU communication, Siemens PLC debugging, sensor integration. Directly maps to JD requirements for PLCs, industrial controllers, reusable communication interfaces, and system-level debugging.

### 2. LIFT (Professional Experience) — 4 bullets
Strongest match for ROS 2, motion control, trajectory planning, and closed-loop control. The ROS2+MPC bullet is the single highest-relevance bullet in the database. OptiTrack integration shows real-time low-latency sensor feedback. Cross-functional team leadership matches mentoring/leadership JD requirement.

### 3. ROBOCON (Professional Experience) — 5 bullets
Deep embedded systems and controls experience: CAN-based motor control, PID navigation with inverse kinematics, custom UART protocol, modular system architecture. Team management (3 teams, $20K budget) demonstrates leadership. Strong match for motion control algorithms, kinematics, communication interfaces, and modular software architecture.

### 4. Crane (Professional Experience) — 4 bullets
Covers control systems (fuzzy PID), PCB/hardware integration, PLC platform migration, and multi-device coordination. PLC migration bullet directly addresses JD's industrial controller requirement. PCB consolidation shows HW/SW integration depth.

## Overlap Resolution Summary
- [1]: Kept LIFT-6 (integration focus) over LIFT-7 (prototype timeline)
- [2]: Kept ROBOCON-15 (leadership with budget/milestones) over ROBOCON-1, -5, -8
- [4]: Kept Crane-3 (ESP32 + communication) over Crane-1, Crane-2
- [5]: Kept Crane-4 (fuzzy PID, accuracy) and Crane-8 (PLC migration) over Crane-6
- [7]: Neither LIFT overlap-7 bullet selected; other LIFT bullets are stronger

## Unmatched JD Gaps (Critical)
1. **MoveIt**: No bullet in the database mentions MoveIt. The ROS2+MPC bullet is the closest transferable match (same ecosystem, different tool).
2. **AI-powered perception / computer vision / deep learning**: No direct experience. The sensor fusion and OptiTrack bullets are partial transferable matches at best.
3. **CI/CD automation**: No bullet covers CI/CD, testing frameworks, or automation pipelines.
4. **SLAM / mapping**: No direct experience. Localization via OptiTrack is a structured-environment solution, not SLAM.
5. **Simulation environments / HIL testing**: The ROS2 simulator bullet is a partial match but not a dedicated simulation/HIL workflow.

## Fit Assessment
This is a moderate-to-good fit. The candidate has strong alignment with:
- ROS 2 and real-time control (LIFT)
- Embedded firmware, kinematics, PID control (ROBOCON)
- Industrial robotics, PLCs, Modbus (Xiaoxian, Crane)
- Cross-functional team leadership (LIFT, ROBOCON)

The main gaps are MoveIt, AI-based perception, CI/CD, and SLAM — these are significant for a Sr. role. The cover letter should emphasize the strong control systems and integration foundation while being honest about growth areas.

## Edit Log
- Minor sentence combining edits on Xiaoxian-1, Xiaoxian-7, Xiaoxian-8 (two-sentence source combined into one; no factual changes)
- Removed "in indoor environments" from LIFT-4 for brevity
- All edits preserve original factual claims and ownership levels

---

## Cover Letter Notes

### Theme Selection (3 dimensions)
1. **Technical depth — ROS 2, motion control, trajectory planning**: LIFT project (LIFT-5, LIFT-4, LIFT-11). Narrative expands on the simulate-deploy-measure-refine workflow without repeating resume bullet text.
2. **Industrial HW/SW integration — PLCs, embedded firmware, Modbus**: Xiaoxian project (Xiaoxian-2, Xiaoxian-1, Xiaoxian-8). Narrative groups three distinct industrial tasks into a cohesive paragraph.
3. **Cross-functional leadership**: ROBOCON-15 (3 teams, $20K budget) and LIFT-6 (5-member team). Used as supporting evidence in Body 2, not a standalone paragraph.

### JD Keyword Mapping
| JD Keyword | Where in CL | Source Bullet |
|---|---|---|
| ROS 2 | Opening, Body 1 | LIFT-5 |
| motion control / trajectory planning | Body 1 | LIFT-5, LIFT-4, LIFT-11 |
| PLC / industrial controllers | Opening, Body 2 | Xiaoxian-8 |
| hardware-software integration | Opening, Body 2 | Xiaoxian-1, Xiaoxian-2 |
| C++ (embedded C) | Body 2 | Xiaoxian-2 |

### Company-Specific References
- "Wilder's drilling and defastening platforms" (Body 1) — sourced from company_research.md, confirmed via wsrobots.com
- "WS5000 fleet" (Body 2) — sourced from company_research.md ($30M STRATFI contract)
- "Wilder's mission" (Closing) — references "lights-out manufacturing" from company website

### Gap Acknowledgment
- MoveIt and AI-based perception acknowledged honestly in closing paragraph
- Framed ROS 2 and sensor integration as transferable foundation

### Self-Review Results (6-point check)
- [x] **6a. Word count**: ~279 words total body. Opening ~48, Body 1 ~88, Body 2 ~96, Closing ~47. All within limits.
- [x] **6b. Weak language**: No banned phrases found. Active voice throughout.
- [x] **6c. Repetition**: No accomplishment appears in more than one paragraph. LIFT referenced in both Body 1 (technical) and Body 2 (leadership) but for different aspects.
- [x] **6d. Truthfulness**: Every claim traces to Bullet_Point_Base.md. No invented metrics or tools. Company facts sourced from company_research.md.
- [x] **6e. Format**: Block-style (no indent), date March 26, 2026 (Pacific Time), no role title in recipient block.
- [x] **6f. Logged**: This section.

### Final Checklist
- [x] Every claim maps to source
- [x] No verbatim resume lines
- [x] 3 distinct themes (technical, industrial integration, leadership)
- [x] No invented company facts
- [x] Fits one page
- [x] ~279 words total body
- [x] 4 paragraphs exactly
- [x] No first-line indent (block style)
- [x] Date in Pacific Time
- [x] No role title in recipient block
- [x] Company name correct throughout
- [x] JD keywords appear naturally with evidence
- [x] No banned weak phrases
- [x] Self-review completed and logged
