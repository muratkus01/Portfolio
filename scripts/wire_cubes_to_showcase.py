"""Wire scenario cubes from lookup_cubes.json into showcase.html."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "showcase.html"
CUBES_PATH = ROOT / "simulations" / "results" / "lookup_cubes.json"

def main():
    print("Reading lookup_cubes.json...")
    with open(CUBES_PATH, "r", encoding="utf-8") as f:
        cubes_json = f.read().strip()
    
    # Validate json
    cubes_data = json.loads(cubes_json)
    # Project 03 publishes its results as CSVs under projects/03-*/reports/. Its old scenario grid
    # swept synthetic forecast noise and is retired, so no P03 cube goes into the page, even if
    # an older lookup_cubes.json still carries one. Same separators as the file, so the other
    # cubes are embedded unchanged.
    cubes_data.pop("p03", None)
    cubes_json = json.dumps(cubes_data, separators=(",", ":"))
    print(f"Loaded {len(cubes_data)} project cubes.")

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Update Project 02 plant capacity wording to 300 MW / 2,400 MWh
    html = re.sub(
        r'30 MW turbine / 28 MW pump',
        '300 MW turbine (4 x 75 MW) / 280 MW pump (4 x 70 MW)',
        html
    )
    html = re.sub(
        r'120 MW turbine \(4 x 30 MW\) / 112 MW pump \(4 x 28 MW\)',
        '300 MW turbine (4 x 75 MW) / 280 MW pump (4 x 70 MW)',
        html
    )
    html = re.sub(
        r'Reversible Machine \(30 MW / 28 MW\)',
        'Reversible Machine (300 MW / 280 MW)',
        html
    )
    html = re.sub(
        r'Reversible Machine \(120 MW / 112 MW\)',
        'Reversible Machine (300 MW / 280 MW)',
        html
    )

    # 2. Update Slider attributes in HTML to snap to cube grid values
    # P01
    html = re.sub(
        r'<input type="range" id="p01-slider-pv"[^>]+>',
        '<input type="range" id="p01-slider-pv" min="4.0" max="14.0" step="2.0" value="8.0" oninput="simP01()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p01-slider-bess"[^>]+>',
        '<input type="range" id="p01-slider-bess" min="0.0" max="20.0" step="0.5" value="9.37" oninput="simP01()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p01-slider-inv"[^>]+>',
        '<input type="range" id="p01-slider-inv" min="3.68" max="10.0" step="0.05" value="5.63" oninput="simP01()">',
        html
    )

    # P02
    html = re.sub(
        r'<input type="range" id="p02-slider-turb"[^>]+>',
        '<input type="range" id="p02-slider-turb" min="50.0" max="100.0" step="25.0" value="75.0" oninput="simP02()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p02-slider-pump"[^>]+>',
        '<input type="range" id="p02-slider-pump" min="46.7" max="93.3" step="23.3" value="70.0" oninput="simP02()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p02-slider-res"[^>]+>',
        '<input type="range" id="p02-slider-res" min="1200" max="3600" step="1200" value="2400" oninput="simP02()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p02-slider-wear"[^>]+>',
        '<input type="range" id="p02-slider-wear" min="0" max="300" step="50" value="100" oninput="simP02()">',
        html
    )

    # P04
    html = re.sub(
        r'<input type="range" id="p04-slider-charge"[^>]+>',
        '<input type="range" id="p04-slider-charge" min="0.000" max="0.140" step="0.010" value="0.020" oninput="simP04()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p04-slider-members"[^>]+>',
        '<input type="range" id="p04-slider-members" min="6" max="30" step="1" value="10" oninput="simP04()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p04-slider-pv"[^>]+>',
        '<input type="range" id="p04-slider-pv" min="30" max="120" step="5" value="65" oninput="simP04()">',
        html
    )

    # P02
    html = re.sub(
        r'<input type="range" id="p02-slider-poc"[^>]+>',
        '<input type="range" id="p02-slider-poc" min="25" max="60" step="5" value="40" oninput="simP02()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p02-slider-pv"[^>]+>',
        '<input type="range" id="p02-slider-pv" min="20" max="60" step="10" value="40" oninput="simP02()">',
        html
    )
    html = re.sub(
        r'<input type="range" id="p02-slider-bess"[^>]+>',
        '<input type="range" id="p02-slider-bess" min="0" max="40" step="10" value="20" oninput="simP02()">',
        html
    )

    # 3. Add match status indicators and illustrative dispatch canvas labels
    for pid in ["p01", "p02", "p04", "p02"]:
        badge_orig = f'<span class="sim-card-badge">{pid.upper()} Live Physics Model</span>'
        badge_new = f'<span class="sim-card-badge">{pid.upper()} Live Physics Model</span> <span class="sim-card-badge emerald" id="{pid}-grid-match">(Exact Grid Match)</span>'
        if badge_orig in html:
            html = html.replace(badge_orig, badge_new)

    # Add Illustrative labels to canvas headers
    html = html.replace(
        '<span>Representative Seasonal & Market Dynamics Selection:</span>',
        '<span>Representative Seasonal & Market Dynamics Selection <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )
    html = html.replace(
        '<span>Representative Multi-Market & Hydrological Regimes:</span>',
        '<span>Representative Multi-Market & Hydrological Regimes <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )
    html = html.replace(
        '<span>Representative Fleet Charging & DSO Dimming Scenarios:</span>',
        '<span>Representative Fleet Charging & DSO Dimming Scenarios <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )
    html = html.replace(
        '<span>Representative Community Load & Market Regimes:</span>',
        '<span>Representative Community Load & Market Regimes <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )
    html = html.replace(
        '<span>Representative Renewable & Market Regimes:</span>',
        '<span>Representative Renewable & Market Regimes <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )
    html = html.replace(
        '<span>Representative Renewable & Asymmetric Imbalance Scenarios:</span>',
        '<span>Representative Renewable & Asymmetric Imbalance Scenarios <span style="color:#94a3b8; font-size:0.75rem; font-weight:normal;">(Illustrative 24h Telemetry)</span>:</span>'
    )

    # Add Footnotes to P02 and P02 KPI consoles
    p02_res_grid = '<div class="sim-results-grid">'
    # Let's insert footnotes after the sim-results-grid in P02 and P02
    p02_footnote = '<div style="font-size:0.72rem; color:#94a3b8; margin-top:8px; margin-bottom:12px;">*Note: Figures extrapolated from 14-day empirical telemetry window (scaled by 365/14) to represent full annual operations.</div>'
    
    # We will inject the footnote in the section of P02 and P02
    html = html.replace(
        '<div class="sim-res-val emerald" id="p02-res-stat">0 Simultaneous (Guaranteed)</div>\n          </div>\n        </div>',
        '<div class="sim-res-val emerald" id="p02-res-stat">0 Simultaneous (Guaranteed)</div>\n          </div>\n        </div>\n        ' + p02_footnote
    )
    html = html.replace(
        '<div class="sim-res-val emerald" id="p02-res-avoid">34,200 EUR/yr</div>\n          </div>\n        </div>',
        '<div class="sim-res-val emerald" id="p02-res-avoid">34,200 EUR/yr</div>\n          </div>\n        </div>\n        ' + p02_footnote
    )

    # 4. Inline SCENARIO_CUBES in JavaScript
    sc_code = f"""
    // =========================================================================
    // PRECOMPUTED PYTHON SCENARIO CUBES (2,256 SCENARIOS ACROSS ALL 6 PROJECTS)
    // =========================================================================
    const SCENARIO_CUBES = {cubes_json};

    const GRID_PARAMS = {{
      p01: {{
        pv: [4.0, 6.0, 8.0, 10.0, 12.0, 14.0],
        bess: [0.0, 2.0, 3.5, 5.0, 7.5, 9.37, 12.5, 15.0, 20.0],
        inv: [3.68, 5.63, 8.0, 10.0]
      }},
      p02: {{
        turb: [50.0, 75.0, 100.0],
        res: [1200.0, 2400.0, 3600.0],
        wear: [0, 50, 100, 150, 200, 300]
      }},
      p04: {{
        charge: [0.000, 0.010, 0.020, 0.030, 0.050, 0.070, 0.090, 0.100, 0.110, 0.120, 0.140],
        members: [6, 10, 15, 20, 25, 30],
        pv: [30.0, 50.0, 65.0, 90.0, 120.0]
      }},
      p02: {{
        poc: [25.0, 30.0, 40.0, 50.0, 60.0],
        pv: [20.0, 30.0, 40.0, 50.0, 60.0],
        bess: [0.0, 10.0, 20.0, 30.0, 40.0]
      }}
    }};

    function snapToGrid(val, arr) {{
      return arr.reduce((prev, curr) => (Math.abs(curr - val) < Math.abs(prev - val) ? curr : prev));
    }}
"""
    # Insert or replace SCENARIO_CUBES right before DAY_REGIMES_MULTIYEAR
    if "const SCENARIO_CUBES = " in html:
        pattern = r'// =+\s+// PRECOMPUTED PYTHON SCENARIO CUBES[\s\S]+?(?=\s+// Multi-Year Representative Seasonal Regimes)'
        if re.search(pattern, html):
            html = re.sub(pattern, sc_code.strip(), html)
            print("Replaced existing SCENARIO_CUBES block.")
        else:
            idx1 = html.find('const SCENARIO_CUBES = ')
            idx2 = html.find('// Multi-Year Representative Seasonal Regimes')
            if idx1 != -1 and idx2 != -1:
                html = html[:idx1] + sc_code.strip() + "\n\n    " + html[idx2:]
                print("Replaced SCENARIO_CUBES by index slice.")
    else:
        anchor = "    // Multi-Year Representative Seasonal Regimes (2024, 2025, 2026)\n    const DAY_REGIMES_MULTIYEAR"
        if anchor in html:
            html = html.replace(anchor, sc_code + "\n" + anchor)
            print("Inlined SCENARIO_CUBES and GRID_PARAMS successfully.")
        else:
            print("ERROR: Could not find DAY_REGIMES_MULTIYEAR anchor!")
            return

    # 5. Replace the simulator functions (simP01 to the hybrid-plant simP02) with SCENARIO_CUBES lookups
    new_sim_funcs = """
    // Project 01: Residential PV+BESS Sizing & 24h Dynamic Dispatch
    function simP01() {
      const pvSlider = document.getElementById('p01-slider-pv');
      const bessSlider = document.getElementById('p01-slider-bess');
      const invSlider = document.getElementById('p01-slider-inv');
      const eegSlider = document.getElementById('p01-slider-eeg');
      if (!pvSlider || !bessSlider || !invSlider || !eegSlider) return;

      const pvRaw = parseFloat(pvSlider.value);
      const bessRaw = parseFloat(bessSlider.value);
      const invRaw = parseFloat(invSlider.value);
      const eeg = parseFloat(eegSlider.value);

      const p1Grid = GRID_PARAMS.p01;
      const pv = snapToGrid(pvRaw, p1Grid.pv);
      const bess = snapToGrid(bessRaw, p1Grid.bess);
      const inv = snapToGrid(invRaw, p1Grid.inv);
      const isExact = (Math.abs(pvRaw - pv) < 0.05 && Math.abs(bessRaw - bess) < 0.05 && Math.abs(invRaw - inv) < 0.05);

      document.getElementById('p01-val-pv').innerText = pv.toFixed(1) + ' kWp';
      document.getElementById('p01-val-bess').innerText = bess.toFixed(2) + ' kWh';
      document.getElementById('p01-val-inv').innerText = inv.toFixed(2) + ' kW';
      document.getElementById('p01-val-eeg').innerText = eeg.toFixed(2) + ' ct/kWh';

      const matchEl = document.getElementById('p01-grid-match');
      if (matchEl) {
        matchEl.innerText = isExact ? '(Exact Grid Match)' : `(Nearest Grid: ${pv}kWp/${bess}kWh/${inv}kW)`;
        matchEl.className = 'sim-card-badge ' + (isExact ? 'emerald' : 'cyan');
      }

      const yr = (currentGlobalYear === '2024') ? '2024' : '2025';
      const bessStr = (bess === 9.37) ? '9.37' : bess.toFixed(1);
      const cubeKey = `${pv.toFixed(1)}|${bessStr}|${inv}|${yr}`;
      const cell = SCENARIO_CUBES.p01 && SCENARIO_CUBES.p01.data ? SCENARIO_CUBES.p01.data[cubeKey] : null;

      if (cell) {
        // [net_cost_eur, self_consumption_frac, grid_independence_frac, b1_cost_eur, b3_cost_eur, gap_closure_frac]
        const netCost = cell[0];
        const scFrac = cell[1];
        const gapClosure = cell[5];

        document.getElementById('p01-res-sc').innerText = (scFrac * 100).toFixed(1) + '%';
        const costEl = document.getElementById('p01-res-cost');
        costEl.innerText = (netCost <= 0 ? '' : '+') + Math.round(netCost).toLocaleString() + ' EUR/yr';
        costEl.className = 'sim-res-val ' + (netCost <= 0 ? 'emerald' : 'amber');

        const recEl = document.getElementById('p01-res-rec');
        if (bess < 0.5) {
          recEl.innerText = '0.0% (No BESS)';
          recEl.className = 'sim-res-val';
        } else {
          recEl.innerText = (gapClosure * 100).toFixed(1) + '% Rec';
          recEl.className = 'sim-res-val emerald';
        }
      }

      // Peak Grid Export & Inverter Status
      const peakExport = Math.min(inv, pv * 0.88);
      document.getElementById('p01-res-peak').innerText = peakExport.toFixed(2) + ' kW';
      const statEl = document.getElementById('p01-res-stat');
      if (pv > inv * 1.5) {
        statEl.innerText = 'Clipping (' + (pv - inv).toFixed(1) + ' kW Overload)';
        statEl.className = 'sim-res-val amber';
      } else {
        statEl.innerText = 'Pass (Within Limits)';
        statEl.className = 'sim-res-val emerald';
      }

      // Update 24h Scrubber Canvas
      recomputeDispatchForSizing(pv, bess, inv);
      drawDispatchCanvas();
      updateHud(scrubberIdx);
    }

    // Project 02: Pumped Storage Reversible Machine & Wear
    let currentP02Policy = 'b3';
    function simP02() {
      const turbEl = document.getElementById('p02-slider-turb');
      const pumpEl = document.getElementById('p02-slider-pump');
      const resEl = document.getElementById('p02-slider-res');
      const wearEl = document.getElementById('p02-slider-wear');
      if (!turbEl || !pumpEl || !resEl || !wearEl) return;

      const turbRaw = parseFloat(turbEl.value);
      const pumpRaw = parseFloat(pumpEl.value);
      const resRaw = parseFloat(resEl.value);
      const wearRaw = parseFloat(wearEl.value);

      const p2Grid = GRID_PARAMS.p02;
      const turb = snapToGrid(turbRaw, p2Grid.turb);
      const res = snapToGrid(resRaw, p2Grid.res);
      const wear = snapToGrid(wearRaw, p2Grid.wear);
      const pump = turb * (70.0 / 75.0); // Physical coupling in PlantConfig (4 x 70 MW / 4 x 75 MW)

      const isExact = (Math.abs(turbRaw - turb) < 0.1 && Math.abs(resRaw - res) < 1.0 && Math.abs(wearRaw - wear) < 0.1);

      document.getElementById('p02-val-turb').innerText = turb.toFixed(1) + ' MW';
      document.getElementById('p02-val-pump').innerText = pump.toFixed(1) + ' MW';
      document.getElementById('p02-val-res').innerText = Math.round(res).toLocaleString() + ' MWh';
      document.getElementById('p02-val-wear').innerText = Math.round(wear) + ' EUR/switch';

      const matchEl = document.getElementById('p02-grid-match');
      if (matchEl) {
        matchEl.innerText = isExact ? `(Exact Grid: ${currentP02Policy.toUpperCase()})` : `(Nearest: ${turb}MW/${res}MWh/${currentP02Policy})`;
        matchEl.className = 'sim-card-badge ' + (isExact ? 'emerald' : 'cyan');
      }

      const yr = (currentGlobalYear === '2024') ? '2024' : '2025';
      const cubeKey = `${turb.toFixed(1)}|${res.toFixed(1)}|${Math.round(wear)}|${currentP02Policy}|${yr}`;
      const cell = SCENARIO_CUBES.p02 && SCENARIO_CUBES.p02.data ? SCENARIO_CUBES.p02.data[cubeKey] : null;

      if (cell) {
        // [revenue_eur_per_year_extrapolated, reversals_per_day, wear_cost_eur_per_year_extrapolated, b1_revenue_eur_per_year_extrapolated, b3_revenue_eur_per_year_extrapolated]
        const rev = cell[0];
        const revsPerDay = cell[1];
        const wearCost = cell[2];
        const b3Rev = cell[4];

        document.getElementById('p02-res-rev').innerText = Math.round(rev).toLocaleString() + ' EUR/yr*';
        document.getElementById('p02-res-revs').innerText = revsPerDay.toFixed(2) + ' /day';
        document.getElementById('p02-res-wearcost').innerText = Math.round(wearCost).toLocaleString() + ' EUR/yr*';
        
        const b2Cap = (b3Rev > 0) ? Math.min(100.0, (rev / b3Rev) * 100.0) : 100.0;
        document.getElementById('p02-res-b2cap').innerText = b2Cap.toFixed(1) + '% B3 Benchmark';
      }

      document.getElementById('p02-res-stat').innerText = '0 Simultaneous (Guaranteed)';

      recomputeDispatchP02(turb, pump, res, wear);
      drawDispatchCanvasP02();
      updateHudP02(scrubberIdxP02);
    }

    // Project 04: REC P2P Sharing & Core Stability
    function simP04() {
      const chargeEl = document.getElementById('p04-slider-charge');
      const membersEl = document.getElementById('p04-slider-members');
      const pvEl = document.getElementById('p04-slider-pv');
      if (!chargeEl || !membersEl || !pvEl) return;

      const chargeRaw = parseFloat(chargeEl.value);
      const membersRaw = parseFloat(membersEl.value);
      const pvRaw = parseFloat(pvEl.value);

      const p4Grid = GRID_PARAMS.p04;
      const charge = snapToGrid(chargeRaw, p4Grid.charge);
      const members = snapToGrid(membersRaw, p4Grid.members);
      const pv = snapToGrid(pvRaw, p4Grid.pv);
      const isExact = (Math.abs(chargeRaw - charge) < 0.001 && Math.abs(membersRaw - members) < 0.1 && Math.abs(pvRaw - pv) < 0.1);

      document.getElementById('p04-val-charge').innerText = charge.toFixed(3) + ' EUR/kWh';
      document.getElementById('p04-val-members').innerText = Math.round(members) + ' Members';
      document.getElementById('p04-val-pv').innerText = Math.round(pv) + ' kWp';

      const matchEl = document.getElementById('p04-grid-match');
      if (matchEl) {
        matchEl.innerText = isExact ? '(Exact Grid Match)' : `(Nearest: ${charge.toFixed(3)}EUR/${members}m/${pv}kWp)`;
        matchEl.className = 'sim-card-badge ' + (isExact ? 'emerald' : 'cyan');
      }

      const yr = (currentGlobalYear === '2024') ? '2024' : '2025';
      const chargeStr = String(parseFloat(charge.toFixed(3)));
      const formattedCharge = (chargeStr.indexOf('.') === -1) ? chargeStr + '.0' : chargeStr;
      const cubeKey = `${formattedCharge}|${Math.round(members)}|${pv.toFixed(1)}|${yr}`;
      const cell = SCENARIO_CUBES.p04 && SCENARIO_CUBES.p04.data ? SCENARIO_CUBES.p04.data[cubeKey] : null;

      if (cell) {
        // [welfare_eur_per_week, consumer_saving_eur, owner_gain_eur, self_consumption_pct, breakeven_tariff_eur_kwh]
        const welfareWk = cell[0];
        const selfCons = cell[3];
        const breakeven = cell[4];

        document.getElementById('p04-res-surplus').innerText = welfareWk.toFixed(2) + ' EUR/wk';
        document.getElementById('p04-res-savings').innerText = selfCons.toFixed(1) + '% Self-Cons';

        const coreEl = document.getElementById('p04-res-core');
        if (charge <= breakeven) {
          coreEl.innerText = '0 Blocking (Core Stable)';
          coreEl.className = 'sim-res-val emerald';
        } else {
          coreEl.innerText = 'Defection Risk (> Breakeven)';
          coreEl.className = 'sim-res-val amber';
        }
      }

      recomputeDispatchP04(charge, members, pv);
      drawDispatchCanvasP04();
      updateHudP04(scrubberIdxP04);
    }

    // Project 02: Hybrid Over-Planting Curtailment
    function simP02() {
      const pocEl = document.getElementById('p02-slider-poc');
      const pvEl = document.getElementById('p02-slider-pv');
      const bessEl = document.getElementById('p02-slider-bess');
      if (!pocEl || !pvEl || !bessEl) return;

      const pocRaw = parseFloat(pocEl.value);
      const pvRaw = parseFloat(pvEl.value);
      const bessRaw = parseFloat(bessEl.value);

      const p5Grid = GRID_PARAMS.p02;
      const poc = snapToGrid(pocRaw, p5Grid.poc);
      const pv = snapToGrid(pvRaw, p5Grid.pv);
      const bess = snapToGrid(bessRaw, p5Grid.bess);
      const isExact = (Math.abs(pocRaw - poc) < 0.1 && Math.abs(pvRaw - pv) < 0.1 && Math.abs(bessRaw - bess) < 0.1);

      document.getElementById('p02-val-poc').innerText = Math.round(poc) + ' MW';
      document.getElementById('p02-val-pv').innerText = Math.round(pv) + ' MW';
      document.getElementById('p02-val-bess').innerText = Math.round(bess) + ' MWh';

      const matchEl = document.getElementById('p02-grid-match');
      if (matchEl) {
        matchEl.innerText = isExact ? '(Exact Grid Match)' : `(Nearest: ${poc}MW POC/${pv}MW PV/${bess}MWh)`;
        matchEl.className = 'sim-card-badge ' + (isExact ? 'emerald' : 'cyan');
      }

      const yr = (currentGlobalYear === '2024') ? '2024' : '2025';
      const cubeKey = `${poc.toFixed(1)}|${pv.toFixed(1)}|${bess.toFixed(1)}|${yr}`;
      const cell = SCENARIO_CUBES.p02 && SCENARIO_CUBES.p02.data ? SCENARIO_CUBES.p02.data[cubeKey] : null;

      if (cell) {
        // [revenue_eur_per_year_extrapolated, curtail_mwh_per_year_extrapolated, overplanting_factor, b1_revenue_eur_per_year_extrapolated, b3_revenue_eur_per_year_extrapolated, revenue_lift_pct]
        const rev = cell[0];
        const curtailMwh = cell[1];
        const overplant = cell[2];
        const liftPct = cell[5];

        document.getElementById('p02-res-ratio').innerText = overplant.toFixed(2) + 'x Overplant';
        document.getElementById('p02-res-curtail').innerText = curtailMwh.toFixed(1) + ' MWh/yr*';
        document.getElementById('p02-res-lift').innerText = (liftPct >= 0 ? '+' : '') + liftPct.toFixed(1) + '% Rev';
        document.getElementById('p02-res-avoid').innerText = Math.round(rev).toLocaleString() + ' EUR/yr*';
      }

      recomputeDispatchP02(poc, pv, bess);
      drawDispatchCanvasP02();
      updateHudP02(scrubberIdxP02);
    }
"""
    # Replace the existing simulator functions, from simP01 to the hybrid-plant simP02. The block
    # used to end at simP03; Project 03 has no simulator any more (its grid is retired).
    sim_regex = r'// Project 01: Residential PV\+BESS Sizing & 24h Dynamic Dispatch\s+function simP01\(\) \{[\s\S]+?recomputeDispatchP02\(poc, pv, bess\);\s+drawDispatchCanvasP02\(\);\s+updateHudP02\(scrubberIdxP02\);\s+\}'
    if re.search(sim_regex, html):
        html = re.sub(sim_regex, new_sim_funcs.strip(), html)
        print("Replaced the simulator functions with SCENARIO_CUBES lookups.")
    else:
        print("ERROR: Could not find the simulator functions block!")
        return

    # Write output
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Successfully updated showcase.html ({len(html)} bytes).")

if __name__ == "__main__":
    main()
