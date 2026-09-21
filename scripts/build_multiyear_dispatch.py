"""
Multi-Year Dispatch Engine and 2026 Visual Benchmark Integrator for showcase.html.
Ensures zero em/en dashes and strict synchronization.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def build():
    with open(ROOT / "simulations" / "results" / "day_regimes_multiyear.json", "r", encoding="utf-8") as f:
        multiyear_data = json.load(f)

    with open(ROOT / "showcase.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Add 2026 gallery buttons to all 6 projects if not already present
    # P01
    p01_old_btn = '<button class="gallery-btn active" id="p01-galbtn-2025" onclick="switchVisual(\'p01\', \'2025\')">2025 Dispatch</button>'
    p01_new_btns = '<button class="gallery-btn" id="p01-galbtn-2026" onclick="switchVisual(\'p01\', \'2026\')">2026 (YTD)</button>\n                ' + p01_old_btn
    if 'id="p01-galbtn-2026"' not in html:
        assert p01_old_btn in html, "Could not find p01-galbtn-2025"
        html = html.replace(p01_old_btn, p01_new_btns, 1)

    # P02
    p02_old_btn = '<button class="gallery-btn active" id="p02-galbtn-2025" onclick="switchVisual(\'p02\', \'2025\')">2025 Dispatch</button>'
    p02_new_btns = '<button class="gallery-btn" id="p02-galbtn-2026" onclick="switchVisual(\'p02\', \'2026\')">2026 (YTD)</button>\n                ' + p02_old_btn
    if 'id="p02-galbtn-2026"' not in html:
        assert p02_old_btn in html, "Could not find p02-galbtn-2025"
        html = html.replace(p02_old_btn, p02_new_btns, 1)

    # P03
    p03_old_btn = '<button class="gallery-btn active" id="p03-galbtn-2025" onclick="switchVisual(\'p03\', \'2025\')">2025 Dispatch</button>'
    p03_new_btns = '<button class="gallery-btn" id="p03-galbtn-2026" onclick="switchVisual(\'p03\', \'2026\')">2026 (YTD)</button>\n                ' + p03_old_btn
    if 'id="p03-galbtn-2026"' not in html:
        assert p03_old_btn in html, "Could not find p03-galbtn-2025"
        html = html.replace(p03_old_btn, p03_new_btns, 1)

    # P04
    p04_old_btn = '<button class="gallery-btn active" id="p04-galbtn-2025" onclick="switchVisual(\'p04\', \'2025\')">2025 P2P Flow</button>'
    p04_new_btns = '<button class="gallery-btn" id="p04-galbtn-2026" onclick="switchVisual(\'p04\', \'2026\')">2026 (YTD)</button>\n                ' + p04_old_btn
    if 'id="p04-galbtn-2026"' not in html:
        assert p04_old_btn in html, "Could not find p04-galbtn-2025"
        html = html.replace(p04_old_btn, p04_new_btns, 1)

    # P05
    p05_old_btn = '<button class="gallery-btn active" id="p05-galbtn-2025" onclick="switchVisual(\'p05\', \'2025\')">2025 Dispatch</button>'
    p05_new_btns = '<button class="gallery-btn" id="p05-galbtn-2026" onclick="switchVisual(\'p05\', \'2026\')">2026 (YTD)</button>\n                ' + p05_old_btn
    if 'id="p05-galbtn-2026"' not in html:
        assert p05_old_btn in html, "Could not find p05-galbtn-2025"
        html = html.replace(p05_old_btn, p05_new_btns, 1)

    # P06
    p06_old_btn = '<button class="gallery-btn active" id="p06-galbtn-2025" onclick="switchVisual(\'p06\', \'2025\')">2025 Fan Chart</button>'
    p06_new_btns = '<button class="gallery-btn" id="p06-galbtn-2026" onclick="switchVisual(\'p06\', \'2026\')">2026 (YTD)</button>\n                ' + p06_old_btn
    if 'id="p06-galbtn-2026"' not in html:
        assert p06_old_btn in html, "Could not find p06-galbtn-2025"
        html = html.replace(p06_old_btn, p06_new_btns, 1)

    # 2. Update PROJECT_VISUALS to include 2026
    # Let's inspect PROJECT_VISUALS replacement
    old_p01_vis = """      p01: {
        '2025': {
          src: 'projects/01-prosumer-pv-bess-mpc-rl/docs/figures/p01_dispatch_2025.png',
          caption: '<strong>Figure 1 (2025):</strong> 3-day 15-minute residential dispatch under 2025 DE-LU dynamic tariffs showing spot price volatility, solar pre-charging, and state-of-charge trajectory.'
        },"""
    new_p01_vis = """      p01: {
        '2026': {
          src: 'projects/01-prosumer-pv-bess-mpc-rl/docs/figures/p01_dispatch_2026.png',
          caption: '<strong>Figure 1 (2026 YTD):</strong> 3-day 15-minute residential dispatch under 2026 DE-LU dynamic tariffs showing elevated spot price volatility, solar pre-charging, and state-of-charge trajectory.'
        },
        '2025': {
          src: 'projects/01-prosumer-pv-bess-mpc-rl/docs/figures/p01_dispatch_2025.png',
          caption: '<strong>Figure 1 (2025):</strong> 3-day 15-minute residential dispatch under 2025 DE-LU dynamic tariffs showing spot price volatility, solar pre-charging, and state-of-charge trajectory.'
        },"""
    if "'2026': {\n          src: 'projects/01-prosumer-pv-bess-mpc-rl/docs/figures/p01_dispatch_2026.png'" not in html:
        assert old_p01_vis in html, "Could not find old p01 visual"
        html = html.replace(old_p01_vis, new_p01_vis, 1)

    old_p02_vis = """      p02: {
        '2025': {
          src: 'projects/02-pumped-storage-rl-multimarket/docs/figures/p02_dispatch_2025.png',
          caption: '<strong>Figure 2 (2025):</strong> Multi-market dispatch on 2025 DE-LU spot prices: Day-Ahead spot arbitrage, aFRR reserve capacity, and upper reservoir head trajectory.'
        },"""
    new_p02_vis = """      p02: {
        '2026': {
          src: 'projects/02-pumped-storage-rl-multimarket/docs/figures/p02_dispatch_2026.png',
          caption: '<strong>Figure 2 (2026 YTD):</strong> Multi-market dispatch on 2026 DE-LU spot prices: Day-Ahead spot arbitrage, aFRR reserve capacity, and upper reservoir head trajectory under record market spreads.'
        },
        '2025': {
          src: 'projects/02-pumped-storage-rl-multimarket/docs/figures/p02_dispatch_2025.png',
          caption: '<strong>Figure 2 (2025):</strong> Multi-market dispatch on 2025 DE-LU spot prices: Day-Ahead spot arbitrage, aFRR reserve capacity, and upper reservoir head trajectory.'
        },"""
    if "'2026': {\n          src: 'projects/02-pumped-storage-rl-multimarket/docs/figures/p02_dispatch_2026.png'" not in html:
        assert old_p02_vis in html, "Could not find old p02 visual"
        html = html.replace(old_p02_vis, new_p02_vis, 1)

    old_p03_vis = """      p03: {
        '2025': {
          src: 'projects/03-smart-ev-charging-14a/docs/figures/p03_dispatch_2025.png',
          caption: '<strong>Figure 3 (2025):</strong> Depot 48-session charging dispatch respecting the 100 kW dynamic grid dimming constraint with EDF departure guarantee.'
        },"""
    new_p03_vis = """      p03: {
        '2026': {
          src: 'projects/03-smart-ev-charging-14a/docs/figures/p03_dispatch_2026.png',
          caption: '<strong>Figure 3 (2026 YTD):</strong> Depot 48-session charging dispatch respecting the 100 kW dynamic grid dimming constraint under 2026 dynamic retail tariffs.'
        },
        '2025': {
          src: 'projects/03-smart-ev-charging-14a/docs/figures/p03_dispatch_2025.png',
          caption: '<strong>Figure 3 (2025):</strong> Depot 48-session charging dispatch respecting the 100 kW dynamic grid dimming constraint with EDF departure guarantee.'
        },"""
    if "'2026': {\n          src: 'projects/03-smart-ev-charging-14a/docs/figures/p03_dispatch_2026.png'" not in html:
        assert old_p03_vis in html, "Could not find old p03 visual"
        html = html.replace(old_p03_vis, new_p03_vis, 1)

    old_p04_vis = """      p04: {
        '2025': {
          src: 'projects/04-energy-sharing-rec/docs/figures/p04_sharing_2025.png',
          caption: '<strong>Figure 4 (2025):</strong> 20-member community P2P matching under 2025 conditions (PV self-consumption, local sharing, and cooperative surplus accumulation).'
        },"""
    new_p04_vis = """      p04: {
        '2026': {
          src: 'projects/04-energy-sharing-rec/docs/figures/p04_sharing_2026.png',
          caption: '<strong>Figure 4 (2026 YTD):</strong> 20-member community P2P matching under 2026 conditions (PV self-consumption, local sharing, and cooperative surplus accumulation).'
        },
        '2025': {
          src: 'projects/04-energy-sharing-rec/docs/figures/p04_sharing_2025.png',
          caption: '<strong>Figure 4 (2025):</strong> 20-member community P2P matching under 2025 conditions (PV self-consumption, local sharing, and cooperative surplus accumulation).'
        },"""
    if "'2026': {\n          src: 'projects/04-energy-sharing-rec/docs/figures/p04_sharing_2026.png'" not in html:
        assert old_p04_vis in html, "Could not find old p04 visual"
        html = html.replace(old_p04_vis, new_p04_vis, 1)

    old_p05_vis = """      p05: {
        '2025': {
          src: 'projects/05-utility-hybrid-plant-dispatch/docs/figures/p05_dispatch_2025.png',
          caption: '<strong>Figure 5 (2025):</strong> 80 MW hybrid plant dispatch holding strict 40 MW POC interconnection limit under 2025 spot prices with battery buffer cycling and EEG §51 curtailment.'
        },"""
    new_p05_vis = """      p05: {
        '2026': {
          src: 'projects/05-utility-hybrid-plant-dispatch/docs/figures/p05_dispatch_2026.png',
          caption: '<strong>Figure 5 (2026 YTD):</strong> 80 MW hybrid plant dispatch holding strict 40 MW POC interconnection limit under 2026 spot prices with battery buffer cycling and EEG §51 curtailment.'
        },
        '2025': {
          src: 'projects/05-utility-hybrid-plant-dispatch/docs/figures/p05_dispatch_2025.png',
          caption: '<strong>Figure 5 (2025):</strong> 80 MW hybrid plant dispatch holding strict 40 MW POC interconnection limit under 2025 spot prices with battery buffer cycling and EEG §51 curtailment.'
        },"""
    if "'2026': {\n          src: 'projects/05-utility-hybrid-plant-dispatch/docs/figures/p05_dispatch_2026.png'" not in html:
        assert old_p05_vis in html, "Could not find old p05 visual"
        html = html.replace(old_p05_vis, new_p05_vis, 1)

    old_p06_vis = """      p06: {
        '2025': {
          src: 'projects/06-probabilistic-forecast-to-bid/docs/figures/p06_forecast_fan_2025.png',
          caption: '<strong>Figure 6 (2025):</strong> 24-hour fan chart with 9 quantile ribbons (q10 to q90) and real-time reBAP imbalance cashout distribution under 2025 weather patterns.'
        },"""
    new_p06_vis = """      p06: {
        '2026': {
          src: 'projects/06-probabilistic-forecast-to-bid/docs/figures/p06_forecast_fan_2026.png',
          caption: '<strong>Figure 6 (2026 YTD):</strong> 24-hour fan chart with 9 quantile ribbons (q10 to q90) and real-time reBAP imbalance cashout distribution under 2026 weather patterns.'
        },
        '2025': {
          src: 'projects/06-probabilistic-forecast-to-bid/docs/figures/p06_forecast_fan_2025.png',
          caption: '<strong>Figure 6 (2025):</strong> 24-hour fan chart with 9 quantile ribbons (q10 to q90) and real-time reBAP imbalance cashout distribution under 2025 weather patterns.'
        },"""
    if "'2026': {\n          src: 'projects/06-probabilistic-forecast-to-bid/docs/figures/p06_forecast_fan_2026.png'" not in html:
        assert old_p06_vis in html, "Could not find old p06 visual"
        html = html.replace(old_p06_vis, new_p06_vis, 1)

    # 3. Update switchVisual to support 2026
    old_switch_vis = """      ['2025', '2024', 'bench'].forEach(vk => {
        const btn = document.getElementById(projId + '-galbtn-' + vk);
        if (btn) btn.classList.toggle('active', vk === visualKey);
      });"""
    new_switch_vis = """      ['2026', '2025', '2024', 'bench'].forEach(vk => {
        const btn = document.getElementById(projId + '-galbtn-' + vk);
        if (btn) btn.classList.toggle('active', vk === visualKey);
      });"""
    assert old_switch_vis in html, "Could not find switchVisual button loop"
    html = html.replace(old_switch_vis, new_switch_vis, 1)

    # 4. Update setGlobalYear gallery switching
    old_set_gal = """      // Switch active figure in galleries for all projects
      const projList = ['p01', 'p02', 'p03', 'p04', 'p05', 'p06'];
      const vYearKey = yKey === '2024' ? '2024' : '2025';
      projList.forEach(pid => {
        const benchBtn = document.getElementById(pid + '-galbtn-bench');
        const isBench = benchBtn && benchBtn.classList.contains('active');
        if (!isBench) {
          switchVisual(pid, vYearKey);
        }
      });"""
    new_set_gal = """      // Switch active figure in galleries for all projects to currently selected year
      currentGlobalYear = yKey;
      const projList = ['p01', 'p02', 'p03', 'p04', 'p05', 'p06'];
      projList.forEach(pid => {
        const benchBtn = document.getElementById(pid + '-galbtn-bench');
        const isBench = benchBtn && benchBtn.classList.contains('active');
        if (!isBench) {
          switchVisual(pid, yKey);
        }
      });

      // Update multi-year representative day regimes and dispatch for all projects
      updateRepresentativeDaysForYear(yKey);"""
    assert old_set_gal in html, "Could not find setGlobalYear gallery switching block"
    html = html.replace(old_set_gal, new_set_gal, 1)

    # 5. Replace DAY_REGIMES constant with DAY_REGIMES_MULTIYEAR and helper
    multiyear_compact = json.dumps(multiyear_data, separators=(',', ':'))

    # Replace old DAY_REGIMES declaration
    old_regimes_decl = """    let currentRegimeKey = 'summer';"""
    new_regimes_decl = f"""    // Multi-Year Representative Seasonal Regimes (2024, 2025, 2026)
    const DAY_REGIMES_MULTIYEAR = {multiyear_compact};
    let currentGlobalYear = '2025';
    let currentRegimeKey = 'summer';

    function getRegimes(pid) {{
      const yr = DAY_REGIMES_MULTIYEAR[currentGlobalYear] || DAY_REGIMES_MULTIYEAR['2025'];
      return yr[pid] || DAY_REGIMES_MULTIYEAR['2024'][pid];
    }}

    function updateRepresentativeDaysForYear(yearKey) {{
      const yr = DAY_REGIMES_MULTIYEAR[yearKey] || DAY_REGIMES_MULTIYEAR['2025'];
      
      // Update Project 01 button labels and active tag
      const r01 = yr.p01[currentRegimeKey] || yr.p01['summer'];
      const date01 = document.getElementById('active-regime-date');
      if (date01) date01.innerText = r01.tag + ' (' + r01.date + ')';
      const desc01 = document.getElementById('day-regime-desc');
      if (desc01) {{
        desc01.innerHTML = '<strong>' + r01.title + ':</strong> ' + r01.desc + 
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Wholesale Spot: ' + 
          r01.price_min.toFixed(1) + ' to ' + r01.price_max.toFixed(1) + ' EUR/MWh (Mean: ' + r01.price_mean.toFixed(1) + ' EUR/MWh)</span>';
      }}
      ['winter', 'spring', 'summer', 'autumn', 'extreme_high', 'extreme_low'].forEach(k => {{
        const btn = document.getElementById('dbtn-' + k);
        if (btn && yr.p01[k]) {{
          btn.innerText = yr.p01[k].tag;
        }}
      }});

      // Update Project 02
      const r02 = yr.p02[currentRegimeKeyP02] || yr.p02['arbitrage'];
      const date02 = document.getElementById('p02-active-regime-date');
      if (date02) date02.innerText = r02.tag + ' (' + r02.date + ')';
      const desc02 = document.getElementById('p02-day-regime-desc');
      if (desc02) {{
        desc02.innerHTML = '<strong>' + r02.title + ':</strong> ' + r02.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Day-Ahead Spot: ' +
          r02.price_min.toFixed(1) + ' to ' + r02.price_max.toFixed(1) + ' EUR/MWh</span>';
      }}
      ['arbitrage', 'afrr_surge', 'wear_stress', 'drought', 'extreme_high', 'extreme_low'].forEach(k => {{
        const btn = document.getElementById('p02-dbtn-' + k);
        if (btn && yr.p02[k]) {{
          btn.innerText = yr.p02[k].tag;
        }}
      }});

      // Update Project 03
      const r03 = yr.p03[currentRegimeKeyP03] || yr.p03['dimming_event'];
      const date03 = document.getElementById('p03-active-regime-date');
      if (date03) date03.innerText = r03.tag + ' (' + r03.date + ')';
      const desc03 = document.getElementById('p03-day-regime-desc');
      if (desc03) {{
        desc03.innerHTML = '<strong>' + r03.title + ':</strong> ' + r03.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Tariff: ' +
          r03.price_min.toFixed(1) + ' to ' + r03.price_max.toFixed(1) + ' EUR/MWh</span>';
      }}
      ['dimming_event', 'overnight_depot', 'daytime_turnover', 'departure_crunch', 'extreme_uncoordinated', 'negative_soak'].forEach(k => {{
        const btn = document.getElementById('p03-dbtn-' + k);
        if (btn && yr.p03[k]) {{
          btn.innerText = yr.p03[k].tag;
        }}
      }});

      // Update Project 04
      const r04 = yr.p04[currentRegimeKeyP04] || yr.p04['summer_export'];
      const date04 = document.getElementById('p04-active-regime-date');
      if (date04) date04.innerText = r04.tag + ' (' + r04.date + ')';
      const desc04 = document.getElementById('p04-day-regime-desc');
      if (desc04) {{
        desc04.innerHTML = '<strong>' + r04.title + ':</strong> ' + r04.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Internal Tariff: ' +
          r04.price_min.toFixed(1) + ' to ' + r04.price_max.toFixed(1) + ' ct/kWh</span>';
      }}
      ['summer_export', 'winter_deficit', 'cloud_volatility', 'negative_day', 'evening_peak', 'core_cliff'].forEach(k => {{
        const btn = document.getElementById('p04-dbtn-' + k);
        if (btn && yr.p04[k]) {{
          btn.innerText = yr.p04[k].tag;
        }}
      }});

      // Update Project 05
      const r05 = yr.p05[currentRegimeKeyP05] || yr.p05['overplanting_storm'];
      const date05 = document.getElementById('p05-active-regime-date');
      if (date05) date05.innerText = r05.tag + ' (' + r05.date + ')';
      const desc05 = document.getElementById('p05-day-regime-desc');
      if (desc05) {{
        desc05.innerHTML = '<strong>' + r05.title + ':</strong> ' + r05.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Spot: ' +
          r05.price_min.toFixed(1) + ' to ' + r05.price_max.toFixed(1) + ' EUR/MWh</span>';
      }}
      ['overplanting_storm', 'eeg51_negative_run', 'summer_solar_noon', 'winter_wind_gale', 'extreme_high_spike', 'bottleneck_grid'].forEach(k => {{
        const btn = document.getElementById('p05-dbtn-' + k);
        if (btn && yr.p05[k]) {{
          btn.innerText = yr.p05[k].tag;
        }}
      }});

      // Update Project 06
      const r06 = yr.p06[currentRegimeKeyP06] || yr.p06['system_shortage'];
      const date06 = document.getElementById('p06-active-regime-date');
      if (date06) date06.innerText = r06.tag + ' (' + r06.date + ')';
      const desc06 = document.getElementById('p06-day-regime-desc');
      if (desc06) {{
        desc06.innerHTML = '<strong>' + r06.title + ':</strong> ' + r06.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">reBAP Spread: ' +
          r06.price_min.toFixed(1) + ' to ' + r06.price_max.toFixed(1) + ' EUR/MWh</span>';
      }}
      ['system_shortage', 'system_long', 'storm_ramp_uncertainty', 'stable_high_pressure', 'asymmetric_spread', 'forecast_bust_stress'].forEach(k => {{
        const btn = document.getElementById('p06-dbtn-' + k);
        if (btn && yr.p06[k]) {{
          btn.innerText = yr.p06[k].tag;
        }}
      }});

      // Re-run all simulators with current year data
      simP01();
      simP02();
      simP03();
      simP04();
      simP05();
      simP06();
    }}
"""
    assert old_regimes_decl in html, "Could not find currentRegimeKey declaration"
    html = html.replace(old_regimes_decl, new_regimes_decl, 1)

    # 6. Update selectDayRegime functions to look up from getRegimes(pid)
    # P01 selectDayRegime
    old_sel_p01 = """    function selectDayRegime(regKey) {
      if (!DAY_REGIMES[regKey]) return;
      currentRegimeKey = regKey;
      const reg = DAY_REGIMES[regKey];"""
    new_sel_p01 = """    function selectDayRegime(regKey) {
      const regMap = getRegimes('p01');
      if (!regMap[regKey]) return;
      currentRegimeKey = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p01 in html, "Could not find selectDayRegime"
    html = html.replace(old_sel_p01, new_sel_p01, 1)

    # P01 recomputeDispatchForSizing
    old_recomp_p01 = """    function recomputeDispatchForSizing(pvKw, bessKwh, invKw) {
      const reg = (typeof DAY_REGIMES !== 'undefined' && DAY_REGIMES[currentRegimeKey]) ? DAY_REGIMES[currentRegimeKey] : DAY_REGIMES['summer'];"""
    new_recomp_p01 = """    function recomputeDispatchForSizing(pvKw, bessKwh, invKw) {
      const regMap = getRegimes('p01');
      const reg = (regMap && regMap[currentRegimeKey]) ? regMap[currentRegimeKey] : regMap['summer'];"""
    assert old_recomp_p01 in html, "Could not find recomputeDispatchForSizing"
    html = html.replace(old_recomp_p01, new_recomp_p01, 1)

    # P02 selectDayRegimeP02
    old_sel_p02 = """    function selectDayRegimeP02(regKey) {
      if (!DAY_REGIMES_P02[regKey]) return;
      currentRegimeKeyP02 = regKey;
      const reg = DAY_REGIMES_P02[regKey];"""
    new_sel_p02 = """    function selectDayRegimeP02(regKey) {
      const regMap = getRegimes('p02');
      if (!regMap[regKey]) return;
      currentRegimeKeyP02 = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p02 in html, "Could not find selectDayRegimeP02"
    html = html.replace(old_sel_p02, new_sel_p02, 1)

    # P02 recomputeDispatchP02
    old_recomp_p02 = """    function recomputeDispatchP02(turbMw, pumpMw, resMwh, wearEur) {
      const reg = (typeof DAY_REGIMES_P02 !== 'undefined' && DAY_REGIMES_P02[currentRegimeKeyP02]) ? DAY_REGIMES_P02[currentRegimeKeyP02] : DAY_REGIMES_P02['arbitrage'];"""
    new_recomp_p02 = """    function recomputeDispatchP02(turbMw, pumpMw, resMwh, wearEur) {
      const regMap = getRegimes('p02');
      const reg = (regMap && regMap[currentRegimeKeyP02]) ? regMap[currentRegimeKeyP02] : regMap['arbitrage'];"""
    assert old_recomp_p02 in html, "Could not find recomputeDispatchP02"
    html = html.replace(old_recomp_p02, new_recomp_p02, 1)

    # P03 selectDayRegimeP03
    old_sel_p03 = """    function selectDayRegimeP03(regKey) {
      if (!DAY_REGIMES_P03[regKey]) return;
      currentRegimeKeyP03 = regKey;
      const reg = DAY_REGIMES_P03[regKey];"""
    new_sel_p03 = """    function selectDayRegimeP03(regKey) {
      const regMap = getRegimes('p03');
      if (!regMap[regKey]) return;
      currentRegimeKeyP03 = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p03 in html, "Could not find selectDayRegimeP03"
    html = html.replace(old_sel_p03, new_sel_p03, 1)

    # P03 recomputeDispatchP03
    old_recomp_p03 = """    function recomputeDispatchP03(dimKw, sessionsCount, bufferKwh) {
      const reg = (typeof DAY_REGIMES_P03 !== 'undefined' && DAY_REGIMES_P03[currentRegimeKeyP03]) ? DAY_REGIMES_P03[currentRegimeKeyP03] : DAY_REGIMES_P03['dimming_event'];"""
    new_recomp_p03 = """    function recomputeDispatchP03(dimKw, sessionsCount, bufferKwh) {
      const regMap = getRegimes('p03');
      const reg = (regMap && regMap[currentRegimeKeyP03]) ? regMap[currentRegimeKeyP03] : regMap['dimming_event'];"""
    assert old_recomp_p03 in html, "Could not find recomputeDispatchP03"
    html = html.replace(old_recomp_p03, new_recomp_p03, 1)

    # P04 selectDayRegimeP04
    old_sel_p04 = """    function selectDayRegimeP04(regKey) {
      if (!DAY_REGIMES_P04[regKey]) return;
      currentRegimeKeyP04 = regKey;
      const reg = DAY_REGIMES_P04[regKey];"""
    new_sel_p04 = """    function selectDayRegimeP04(regKey) {
      const regMap = getRegimes('p04');
      if (!regMap[regKey]) return;
      currentRegimeKeyP04 = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p04 in html, "Could not find selectDayRegimeP04"
    html = html.replace(old_sel_p04, new_sel_p04, 1)

    # P04 recomputeDispatchP04
    old_recomp_p04 = """    function recomputeDispatchP04(chargeEur, membersCount, pvKwp) {
      const reg = (typeof DAY_REGIMES_P04 !== 'undefined' && DAY_REGIMES_P04[currentRegimeKeyP04]) ? DAY_REGIMES_P04[currentRegimeKeyP04] : DAY_REGIMES_P04['summer_export'];"""
    new_recomp_p04 = """    function recomputeDispatchP04(chargeEur, membersCount, pvKwp) {
      const regMap = getRegimes('p04');
      const reg = (regMap && regMap[currentRegimeKeyP04]) ? regMap[currentRegimeKeyP04] : regMap['summer_export'];"""
    assert old_recomp_p04 in html, "Could not find recomputeDispatchP04"
    html = html.replace(old_recomp_p04, new_recomp_p04, 1)

    # P05 selectDayRegimeP05
    old_sel_p05 = """    function selectDayRegimeP05(regKey) {
      if (!DAY_REGIMES_P05[regKey]) return;
      currentRegimeKeyP05 = regKey;
      const reg = DAY_REGIMES_P05[regKey];"""
    new_sel_p05 = """    function selectDayRegimeP05(regKey) {
      const regMap = getRegimes('p05');
      if (!regMap[regKey]) return;
      currentRegimeKeyP05 = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p05 in html, "Could not find selectDayRegimeP05"
    html = html.replace(old_sel_p05, new_sel_p05, 1)

    # P05 recomputeDispatchP05
    old_recomp_p05 = """    function recomputeDispatchP05(pocCapMw, pvMw, bessMwh) {
      const reg = (typeof DAY_REGIMES_P05 !== 'undefined' && DAY_REGIMES_P05[currentRegimeKeyP05]) ? DAY_REGIMES_P05[currentRegimeKeyP05] : DAY_REGIMES_P05['overplanting_storm'];"""
    new_recomp_p05 = """    function recomputeDispatchP05(pocCapMw, pvMw, bessMwh) {
      const regMap = getRegimes('p05');
      const reg = (regMap && regMap[currentRegimeKeyP05]) ? regMap[currentRegimeKeyP05] : regMap['overplanting_storm'];"""
    assert old_recomp_p05 in html, "Could not find recomputeDispatchP05"
    html = html.replace(old_recomp_p05, new_recomp_p05, 1)

    # P06 selectDayRegimeP06
    old_sel_p06 = """    function selectDayRegimeP06(regKey) {
      if (!DAY_REGIMES_P06[regKey]) return;
      currentRegimeKeyP06 = regKey;
      const reg = DAY_REGIMES_P06[regKey];"""
    new_sel_p06 = """    function selectDayRegimeP06(regKey) {
      const regMap = getRegimes('p06');
      if (!regMap[regKey]) return;
      currentRegimeKeyP06 = regKey;
      const reg = regMap[regKey];"""
    assert old_sel_p06 in html, "Could not find selectDayRegimeP06"
    html = html.replace(old_sel_p06, new_sel_p06, 1)

    # P06 recomputeDispatchP06
    old_recomp_p06 = """    function recomputeDispatchP06(riskTau, maePct, spreadEur) {
      const reg = (typeof DAY_REGIMES_P06 !== 'undefined' && DAY_REGIMES_P06[currentRegimeKeyP06]) ? DAY_REGIMES_P06[currentRegimeKeyP06] : DAY_REGIMES_P06['system_shortage'];"""
    new_recomp_p06 = """    function recomputeDispatchP06(riskTau, maePct, spreadEur) {
      const regMap = getRegimes('p06');
      const reg = (regMap && regMap[currentRegimeKeyP06]) ? regMap[currentRegimeKeyP06] : regMap['system_shortage'];"""
    assert old_recomp_p06 in html, "Could not find recomputeDispatchP06"
    html = html.replace(old_recomp_p06, new_recomp_p06, 1)

    # Update simP01 to use getRegimes('p01')
    old_simp01_reg = """      const reg = (typeof DAY_REGIMES !== 'undefined' && DAY_REGIMES[currentRegimeKey]) ? DAY_REGIMES[currentRegimeKey] : null;"""
    new_simp01_reg = """      const regMap = getRegimes('p01');
      const reg = (regMap && regMap[currentRegimeKey]) ? regMap[currentRegimeKey] : null;"""
    assert old_simp01_reg in html, "Could not find simP01 reg lookup"
    html = html.replace(old_simp01_reg, new_simp01_reg, 1)

    # Check zero dashes
    em_count = html.count('\u2014')
    en_count = html.count('\u2013')
    print(f"Em dashes: {em_count}, En dashes: {en_count}")
    assert em_count == 0 and en_count == 0, f"Dash ban violation: {em_count} em, {en_count} en"

    with open(ROOT / "showcase.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Successfully integrated multiyear dispatch and 2026 visuals into showcase.html!")

if __name__ == "__main__":
    build()
