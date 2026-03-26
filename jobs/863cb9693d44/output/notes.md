# Selection Notes: Energize Group - Senior Robotics and AI Research (Physical AI Architect)

## JD Fitness Assessment

**Overall Fit: WEAK-TO-MODERATE**

This role is fundamentally an AI research leadership position that requires deep expertise in diffusion models for robot learning, policy learning, and combining learned policies with classical control. The candidate's database has **zero bullets** covering diffusion models, ML-based policy learning, or any AI/ML research. The strongest alignment is in classical controls (MPC, PID, feedback control), ROS2, embedded systems, hardware/software co-design, and technical leadership.

The resume has been built to maximize the match on the non-AI portions of the JD (optimal control, ROS2, HW/SW co-design, real-time systems, leadership), but the AI/ML gap is a dealbreaker-level concern for a "Physical AI Architect" title.

## Project Selection Rationale

### 1. LIFT (5 bullets) - Strongest match
- ROS2-based system with MPC controller (direct JD match)
- Real-time feedback control with motion capture (trajectory optimization, feedback control)
- Cross-functional team leadership (JD asks for technical authority + mentoring)
- Hardware/software integration on physical robot platform

### 2. ROBOCON (4 bullets) - Strong embedded/control match
- Multi-robot system with embedded control architecture (real-time constraints)
- PID control loops with quantified performance (feedback control for physical systems)
- Team management across interdisciplinary groups (cross-functional leadership)
- Modular systems architecture (systems mindset, reliability, debuggability)

### 3. Vacuum Dryer (3 bullets) - Transferable systems engineering
- End-to-end system development from concept to prototype (deployment instincts)
- Sensor-control integration with real-time diagnostics (observability, HW/SW co-design)
- Reliability engineering through novel mechanical solutions

### 4. Xiaoxian (2 bullets) - Work experience with robotic arm
- Only work experience in database; shows industrial robotic arm control
- Sensor integration for monitoring and safety (systems mindset)

## Key Unmatched Gaps

1. **Diffusion-based policy learning** - Complete gap. No ML/AI research experience in database.
2. **Production deployment of AI systems** - All experience is academic/prototype/competition.
3. **Combining learned policies with classical control** - No ML policy experience at all.
4. **ML inference optimization** - No experience.
5. **PhD** - Candidate has M.Eng.

## Overlap Resolution Decisions

- [1] tags: Kept "delivering prototype within 10 weeks" over "100% integration" — timeline shows delivery capability.
- [2] tags: Kept budget/team management bullet over mechanical-only leadership bullets — stronger leadership signal for senior role.
- [6] tags: Kept "Engineered a magnetically coupled drive" over "Conducted R&D experiments" — clearer outcome.
- [7] tags: Both were low JD relevance (CAD/mechanical design); neither selected.

## Skills Section Adjustments

Reordered skills to lead with JD-relevant categories:
- Controls & Robotics first (MPC, PID, ROS2)
- Embedded & Real-Time Systems second (C/C++, real-time)
- Programming third (Python, C++)
- HW/SW Co-Design fourth

Added "Trajectory Optimization" and "Real-Time Control" as explicit skill keywords from JD. These are supported by MPC work in LIFT and embedded control in ROBOCON.

## Recommendation

Given the significant AI/ML gaps, the candidate should consider:
1. Whether this role is a realistic target given zero diffusion model / policy learning experience
2. Highlighting any coursework, reading, or side projects in ML-based robotics in a cover letter
3. Framing the application as bringing strong classical controls + systems integration expertise to complement an existing AI team

---

## Cover Letter Notes

### Theme Selection (3 distinct dimensions)
1. **Classical control + ROS2 systems integration** (technical depth) — LIFT: MPC controller, ROS2 autonomy stack, OptiTrack feedback loop, simulation-to-hardware pipeline
2. **Embedded real-time systems + HW/SW co-design** (hands-on hardware) — ROBOCON: STM32 firmware, CAN motor control, PID chassis architecture, team leadership; Vacuum: sensor-control module with diagnostics
3. **Honest gap acknowledgment** (closing) — No diffusion model or learned policy experience; positioned classical controls as the execution layer those policies depend on

### JD Keyword Mapping
| JD Keyword | Where Used | Evidence Source |
|------------|-----------|-----------------|
| optimal control / MPC | P1 (intro), P2 (LIFT MPC controller) | LIFT-bullet-11 |
| ROS2 | P1 (intro), P2 (ROS2 autonomy stack) | LIFT-bullet-11 |
| hardware/software co-design | P3 (embedded + sensor module) | ROBOCON-bullet-67, Vacuum-bullet-39 |
| real-time constraints | P1 (intro), P2 (10ms latency), P3 (real-time diagnostics) | LIFT-bullet-9, Vacuum-bullet-39 |
| diffusion-based policy learning | P4 (honest gap acknowledgment) | N/A — gap disclosed |

### Source Traceability
- P2 "MPC controller + OptiTrack + sub-centimeter tracking + 10ms latency" → LIFT-bullet-11, LIFT-bullet-9, LIFT-bullet-23
- P2 "simulation through physical deployment" → LIFT-bullet-11 (simulator + MPC), LIFT-bullet-23 (experimental validation)
- P3 "STM32 firmware, CAN-based motor control" → ROBOCON-bullet-67
- P3 "three-loop PID chassis, 1.2s convergence" → ROBOCON-bullet-73
- P3 "three interdisciplinary teams, six-month build" → ROBOCON-bullet-79
- P3 "sensor-control module with real-time diagnostics" → Vacuum-bullet-39

### Self-Review Results
- [x] Word count: ~298 words (target 250-300) — PASS
- [x] No banned phrases — PASS
- [x] No repetition across paragraphs — PASS
- [x] All claims traced to Bullet_Point_Base.md — PASS
- [x] No verbatim resume lines (narrativized, not copied) — PASS
- [x] Block style (parindent=0pt), date Pacific Time, no role title in recipient block — PASS
- [x] 4 paragraphs exactly — PASS
- [x] Diffusion model gap honestly disclosed, no AI/ML experience invented — PASS
- [x] Company name correct (Energize Group as recruiter, domain-focused language for undisclosed client) — PASS
