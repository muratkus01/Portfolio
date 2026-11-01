"""Wire scenario cubes from lookup_cubes.json into showcase.html."""
import json
import re
import sys
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
    # Project 02 publishes its results as CSVs under projects/02-*/reports/. Its old grid read
    # hourly prices as quarter-hours and scaled 14 days to a year, and is retired the same way.
    cubes_data.pop("p02", None)
    cubes_json = json.dumps(cubes_data, separators=(",", ":"))
    print(f"Loaded {len(cubes_data)} project cubes.")

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

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

    # 3. Add match status indicators and illustrative dispatch canvas labels
    for pid in ["p01", "p04"]:
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

    # 4. Inline SCENARIO_CUBES in JavaScript
    sc_code = f"""
    // =========================================================================
    // PRECOMPUTED PYTHON SCENARIO CUBES
    // =========================================================================
    const SCENARIO_CUBES = {cubes_json};

    const GRID_PARAMS = {{
      p01: {{
        pv: [4.0, 6.0, 8.0, 10.0, 12.0, 14.0],
        bess: [0.0, 2.0, 3.5, 5.0, 7.5, 9.37, 12.5, 15.0, 20.0],
        inv: [3.68, 5.63, 8.0, 10.0]
      }},
      p04: {{
        charge: [0.000, 0.010, 0.020, 0.030, 0.050, 0.070, 0.090, 0.100, 0.110, 0.120, 0.140],
        members: [6, 10, 15, 20, 25, 30],
        pv: [30.0, 50.0, 65.0, 90.0, 120.0]
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
            sys.exit("ERROR: Could not find DAY_REGIMES_MULTIYEAR anchor!")

    # 5. Replace the simulator functions (simP01 to simP04) with SCENARIO_CUBES lookups
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
"""
    # Replace the existing simulator functions, from simP01 to simP04. The block used to end at
    # simP03 and then at the hybrid-plant simP02; Projects 02 and 03 have no simulator any more
    # (their grids are retired), so simP04 is the last function of the block on the page.
    sim_regex = r'// Project 01: Residential PV\+BESS Sizing & 24h Dynamic Dispatch\s+function simP01\(\) \{[\s\S]+?recomputeDispatchP04\(charge, members, pv\);\s+drawDispatchCanvasP04\(\);\s+updateHudP04\(scrubberIdxP04\);\s+\}'
    if re.search(sim_regex, html):
        html = re.sub(sim_regex, new_sim_funcs.strip(), html)
        print("Replaced the simulator functions with SCENARIO_CUBES lookups.")
    else:
        # The P01 rework of the page removed this anchor comment. The simP01 template above is
        # older than that rework (it prints EUR/yr, reads a p01-slider-eeg the page lacks and
        # builds the key without invStr), so restoring the anchor would undo the rework.
        sys.exit("ERROR: Could not find the simulator functions block. Do not restore its anchor: "
                 "the simP01 template in this script predates the page's P01 rework and would undo "
                 "it. Bring the template up to date with the page first.")

    # Write output
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Successfully updated showcase.html ({len(html)} bytes).")

if __name__ == "__main__":
    main()
