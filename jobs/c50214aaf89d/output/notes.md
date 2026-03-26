# Resume Selection Notes -- Halobraid Embedded Systems Engineer

## JD Fitness Assessment
**Overall fit: Strong.** The candidate has deep embedded systems experience across multiple STM32 projects, extensive feedback control (PID, fuzzy PID, MPC), motor control for various actuator types, and hands-on experience with industry-standard communication protocols (CAN, UART, Modbus RTU, BLE, Wi-Fi). The main gap is direct computer vision algorithm development -- the candidate has transferable experience through OptiTrack-based real-time pose tracking and encoder-based imaging, but no traditional camera-based CV pipeline work.

## Project Selection Rationale

### 1. ROBOCON (4 bullets) -- Highest JD relevance
This project is the strongest match. It directly demonstrates:
- Embedded C firmware development on STM32F407
- Multiple communication protocols: CAN, UART (custom protocol with DMA), BLE
- PID-based feedback control (three nested loops)
- Motor control for omnidirectional mobile robot
- Real-time systems (100 Hz non-blocking updates)

All 4 selected bullets are embedded/controls-focused. The mechanical design and team management bullets (overlap tag [2]) were all rejected in favor of technical depth.

### 2. Crane (4 bullets) -- Strong embedded + electronics match
Covers key gaps that ROBOCON does not:
- Wireless communication (ESP32 Wi-Fi) -- directly matches "desired" JD skill
- Stepper motor control with fuzzy PID -- matches "variety of actuator types"
- PCB design and consolidation -- matches "electronics prototyping"
- Sensor-based perception (ultrasonic radar) -- partial CV/perception match

### 3. LIFT (3 bullets) -- Real-time feedback + vision + collaboration
Selected for:
- OptiTrack integration (<10 ms latency) maps to "computer vision" and "real-time perception" requirements
- ROS2 + MPC demonstrates advanced control system architecture
- Team leadership bullet addresses collaboration requirement

### 4. Xiaoxian / Work Experience (3 bullets) -- Industrial embedded systems
The only professional work experience. Selected bullets emphasize:
- Embedded C for robotic arm control (6-axis, multi-actuator)
- Modbus RTU protocol on STM32 -- direct protocol match
- Encoder-based imaging system -- transferable to computer vision requirement

## Overlap Resolutions
- **[1] LIFT team leadership**: Kept line 15 (prototype delivery) over line 13 (integration percentage). Delivery-focused framing is more tangible.
- **[2] ROBOCON leadership/management**: All 4 variants rejected. Embedded-focused bullets are far more relevant for this JD. No leadership bullet from ROBOCON was needed since LIFT covers collaboration.
- **[4] Crane overview**: Kept line 89 (ESP32 Wi-Fi detail) over lines 85, 87. Most specific embedded systems content.
- **[5] Crane STM32/PID**: Kept line 91 (fuzzy PID + stepper motor) over lines 95, 99. Direct match on feedback control + stepper motors.
- **[7] LIFT mechanical design**: Both variants rejected. Controls/vision bullets are higher priority.

## Skills Section Tailoring
Reorganized skills into four categories optimized for this JD:
1. **Embedded Systems** -- led with MCUs, embedded C/C++, control algorithms, PCB
2. **Communication Protocols** -- dedicated line for CAN, UART, SPI, I2C, Modbus, BLE, Wi-Fi
3. **Programming** -- Python listed first per JD emphasis
4. **Design & Prototyping** -- CAD/simulation tools and fabrication methods

## ROBOCON Title Change
Changed role title from "Integrated Mechanical Engineer" to "Embedded Systems & Controls Engineer" to better reflect the selected bullet content, which is entirely firmware/controls-focused rather than mechanical.

## Gaps and Flags
1. **Computer vision**: Core JD requirement. Candidate has transferable experience (OptiTrack real-time tracking, encoder-based imaging for point-cloud capture) but no traditional CV algorithm work (OpenCV, image segmentation, object detection). This is the largest gap.
2. **Machine learning**: Listed as desired. No source bullets available.
3. **4G/MQTT**: Listed as desired wireless protocols. Only Wi-Fi and BLE available from source.
4. **LIFT team leadership bullet**: Lacks explicit "how" -- no specific methodology described. Kept because it addresses the JD collaboration requirement and has strong impact (prototype in 10 weeks).

## Edit Log
- ROBOCON project title changed from "Integrated Mechanical Engineer" to "Embedded Systems & Controls Engineer" -- reflects the embedded-focused bullet selection for this application.
- No bullet text was modified from source. All bullets are verbatim from Bullet_Point_Base.md.

---

## Cover Letter Notes

### Theme Selection (3 themes, different dimensions)
1. **Embedded firmware + motor control** (technical depth) -- ROBOCON STM32 firmware, PID loops, UART protocol; Crane fuzzy PID + ESP32 Wi-Fi
2. **Hardware integration + prototype-to-production** (hardware/deployment) -- Crane PCB consolidation; Xiaoxian Modbus gateway + encoder imaging
3. **Cross-functional leadership** (collaboration) -- LIFT 5-person team, 10-week prototype delivery

### JD Keyword Mapping
| JD Keyword | Cover Letter Evidence | Source |
|---|---|---|
| embedded systems | STM32F407 firmware, embedded C | ROBOCON-line67 |
| motor control | CAN-based motor control, fuzzy PID stepper control | ROBOCON-line67, Crane-line91 |
| feedback control | three nested PID loops, fuzzy PID closed-loop | ROBOCON-line73, Crane-line91 |
| communication protocols | CAN, BLE, UART/DMA, Wi-Fi, Modbus RTU | ROBOCON-line67/71, Crane-line89, Xiaoxian-line117 |
| PCB design | 4-layer PCB consolidation in KiCad | Crane-line101 |

### Claim-to-Source Traceability
- "sub-2 mm tracking accuracy" -- ROBOCON-line67
- "three nested PID loops" -- ROBOCON-line73
- "custom UART protocol with DMA-driven interrupts, 100 Hz, 0.2% error" -- ROBOCON-line71
- "fuzzy PID closed-loop control for stepper motors" -- Crane-line91
- "ESP32-based Wi-Fi link" -- Crane-line89
- "five boards into single 4-layer PCB, 40% footprint reduction" -- Crane-line101
- "STM32-based Modbus RTU gateway" -- Xiaoxian-line117
- "encoder-triggered imaging, 30% data loss reduction" -- Xiaoxian-line127
- "five-person team, prototype in ten weeks" -- LIFT-line15
- "450+ prototype iterations" -- company_research.md (source: halobraid.com, teal-stone.com)

### Self-Review Results
- **6a. Word count:** ~300 words (at upper boundary, within limit)
- **6b. Weak language scan:** No banned phrases found. "I am writing to apply" is standard opener, not a banned phrase. Active voice throughout.
- **6c. Repetition check:** Para 2 = firmware/protocols, Para 3 = PCB/hardware/leadership -- no overlapping accomplishments.
- **6d. Truthfulness:** All claims verified against Bullet_Point_Base.md and company_research.md. No fabricated metrics or tools.
- **6e. Format check:** parindent=0pt (block style), date March 26 2026 Pacific Time, recipient block has no role title.
- **6f. Gap acknowledgment:** Para 4 honestly addresses CV gap ("indirect...rather than camera-based CV pipelines").

### Final Checklist
- [x] Every claim maps to source
- [x] No verbatim resume lines
- [x] 3 distinct themes (technical depth, hardware/deployment, leadership)
- [x] No invented company facts (450+ iterations sourced from company website)
- [x] Fits one page
- [x] ~300 words total body
- [x] 4 paragraphs exactly
- [x] No first-line indent (block style)
- [x] Date in Pacific Time
- [x] No role title in recipient block
- [x] Company name correct throughout (HaloBraid)
- [x] JD keywords appear naturally with evidence (5 keywords mapped)
- [x] No banned weak phrases
- [x] Self-review completed and logged
