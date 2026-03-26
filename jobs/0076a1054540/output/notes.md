# Resume Selection Notes - Terranova Robotics Controls Engineer

## JD Fitness Assessment

**Overall fit: MODERATE with significant gaps.**

The candidate has strong alignment on classical controls (PID, MPC), embedded systems (STM32, ESP32), ROS2, sensor integration, and PCB design. These map well to several JD requirements.

However, the JD heavily emphasizes ML/RL for adaptive control, perception, and planning. The candidate's database contains zero bullets involving machine learning, reinforcement learning, PyTorch/JAX, ONNX/TensorRT, EKF/UKF, or factor graphs. This is the single largest gap and affects the "robot controls and machine learning specialist" title directly.

## Project Selection Rationale

### 1. LIFT (4 bullets) - Highest priority
- Direct MPC + ROS2 experience (rank 1 JD match)
- OptiTrack sensor integration for real-time closed-loop control
- Pose control system with sub-centimeter tracking
- Team leadership on a complex multi-modal robot platform
- Placed first because it is the most recent and most directly relevant project

### 2. ROBOCON (4 bullets) - Strong controls depth
- Deep embedded firmware development (STM32, CAN, BLE)
- Multiple PID loop tuning on real hardware
- Real-time communication protocols (UART, DMA, 100 Hz)
- Navigation with inverse kinematics
- Demonstrates "large complex robotic systems" archetype

### 3. Crane (3 bullets) - Embedded + PCB bonus
- Fuzzy PID control on STM32 (control algorithm depth)
- 4-layer PCB consolidation (directly addresses "PCB and embedded systems design" bonus)
- ESP32 Wi-Fi remote control with low-latency communication

### 4. Xiaoxian (3 bullets) - Industrial robotics experience
- Six-axis robotic arm control (real industrial system)
- STM32 gateway + Modbus RTU (firmware-to-cloud integration analog)
- Multi-sensor integration for system monitoring

## Overlap Resolutions

- **[1] LIFT leadership bullets**: Kept "100% integration" version over "10 weeks" version -- systems integration emphasis better matches JD.
- **[7] LIFT mechanical design**: Neither version kept -- mechanical CAD focus not relevant to controls JD.
- **[2] ROBOCON leadership**: None selected from overlap group -- controls-specific bullets ranked higher for this JD.
- **[4] Crane prototype**: Kept ESP32/Wi-Fi version -- real-time comms and latency most relevant.
- **[5] Crane control/PCB**: Kept standalone fuzzy PID bullet -- clearest controls narrative.
- **[6] Vacuum magnetic drive**: Vacuum project not selected.
- **[3] Vacuum FMEA/integration**: Vacuum project not selected.

## Unmatched JD Gaps (Critical)

1. **ML/RL (PPO, SAC, supervised learning, adaptive control)** -- No source bullets. This is the largest gap.
2. **PyTorch/JAX** -- Not in database.
3. **ONNX/TensorRT** -- Not in database.
4. **EKF/UKF/factor graphs** -- Not in database.
5. **System identification** -- Not explicitly in database.
6. **CasADi/OSQP** -- Not in database (MPC is present but not these specific solvers).

## Transferable Skills Identified

- MPC controller development (LIFT) is transferable toward optimal control requirements, though specific solvers (OSQP, CasADi) are not demonstrated.
- Real-time sensor integration and latency-constrained firmware partially addresses "latency and jitter profiling" but is not a dedicated profiling discipline.
- Multi-robot coordination (ROBOCON) demonstrates complex systems experience relevant to "large complex robotic systems" archetype.

## Skills Section Adjustments

- Reordered skills to lead with "Controls & Robotics" to match JD title
- Added "Sensor Fusion" and "Inverse Kinematics" to highlight relevant competencies present in bullets
- Kept PCB Design prominent since JD marks it as "huge bonus"
- Did NOT add ML/PyTorch/ROS topics not supported by bullet evidence

## Edit Log

All edits were minor:
- Xiaoxian sensor bullet: combined two source sentences into one participial clause for conciseness
- Xiaoxian gateway bullet: changed period-separated result to participial clause for grammatical consistency
- ROBOCON UART bullet: "used" changed to "leveraged" (banned verb replacement)
- No factual claims were altered; no ownership levels were upgraded

## Recommendation to Candidate

The ML/RL gap is significant for this role. Before applying, consider:
1. Adding any coursework, personal projects, or capstone work involving PyTorch, RL, or state estimation
2. Highlighting any simulation work that involved model-based learning or system identification
3. Being prepared to discuss ML/controls bridge experience in the cover letter or interview

---

## Cover Letter Notes

### Theme Selection (3 dimensions)
1. **Classical controls & ROS2 depth** (TECHNICAL) — MPC + OptiTrack sensor integration on LIFT project
2. **Embedded hardware & PCB design** (SYSTEMS/HARDWARE) — STM32 firmware on ROBOCON + 4-layer PCB consolidation on Crane
3. **Maker/builder identity + company motivation** (CULTURE FIT) — vertical integration mindset, San Rafael cost comparison, intensity alignment

### JD Keyword Mapping
| JD Keyword | Where Used | Evidence |
|---|---|---|
| MPC | P1 (intro), P2 (LIFT detail) | LIFT-5: MPC controller with simulator |
| PID | P1 (intro), P3 (ROBOCON detail) | ROBOCON-9, ROBOCON-11: triple-PID chassis control |
| ROS2 | P1 (intro), P2 (LIFT detail) | LIFT-5: ROS2-based autonomy stack |
| Embedded C/C++ | P3 (ROBOCON detail) | ROBOCON-9: embedded C firmware for STM32F407 |
| PCB design | P3 (Crane detail) | Crane-9: 4-layer PCB consolidation in KiCad |

### ML/RL Gap Handling
- Addressed transparently in P4: "I do not yet have production ML/RL experience with PyTorch or JAX"
- Framed controls foundation as a bridge to learned-control methods (PPO/SAC mentioned)
- Did NOT claim or fabricate any ML experience

### Company-Specific References (all verified from company_research.md)
- Atlas, Prometheus, Vulcan (robot fleet names)
- GPS-degraded operating conditions
- San Rafael flooding context
- $900M seawall cost comparison
- "huge bonus" (direct JD quote for PCB/embedded)
- Seed-stage hardware company framing

### Self-Review Results
- [x] Word count: ~285 words (target 250-300) -- PASS
- [x] No banned phrases -- PASS
- [x] No repetition across paragraphs -- PASS
- [x] All claims trace to source bullets (LIFT-5, LIFT-4, ROBOCON-9, ROBOCON-11, Crane-9) -- PASS
- [x] No verbatim resume lines -- PASS
- [x] Block style (parindent=0pt) -- PASS
- [x] Date in Pacific Time (March 26, 2026) -- PASS
- [x] No role title in recipient block -- PASS
- [x] 4 paragraphs exactly -- PASS
- [x] Company name correct throughout -- PASS
- [x] No invented company facts -- PASS
