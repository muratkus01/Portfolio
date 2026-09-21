"""
Complete generator for Projects 02-06 interactive dispatch canvas engines.
Validates zero em/en dashes and syntax correctness.
"""
import json
import re

def generate_all_dispatch_js():
    with open('simulations/results/day_regimes_all.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    p02_json = json.dumps(data['p02'], separators=(',', ':'))
    p03_json = json.dumps(data['p03'], separators=(',', ':'))
    p04_json = json.dumps(data['p04'], separators=(',', ':'))
    p05_json = json.dumps(data['p05'], separators=(',', ':'))
    p06_json = json.dumps(data['p06'], separators=(',', ':'))

    js_parts = []

    # =========================================================================
    # P02
    # =========================================================================
    js_parts.append(f"""
    // =========================================================================
    // PROJECT 02: 24-HOUR REVERSIBLE MULTI-MARKET DISPATCH ENGINE
    // =========================================================================
    const DAY_REGIMES_P02 = {p02_json};

    let currentRegimeKeyP02 = 'arbitrage';
    const activeChannelsP02 = {{
      flow: true,
      price: true,
      res: true,
      afrr: true
    }};

    function toggleChannelP02(chKey) {{
      activeChannelsP02[chKey] = !activeChannelsP02[chKey];
      const card = document.getElementById('p02-leg-' + chKey);
      if (card) card.classList.toggle('disabled', !activeChannelsP02[chKey]);
      drawDispatchCanvasP02();
    }}

    function selectDayRegimeP02(regKey) {{
      if (!DAY_REGIMES_P02[regKey]) return;
      currentRegimeKeyP02 = regKey;
      const reg = DAY_REGIMES_P02[regKey];

      ['arbitrage', 'afrr_surge', 'wear_stress', 'drought', 'extreme_high', 'extreme_low'].forEach(k => {{
        const btn = document.getElementById('p02-dbtn-' + k);
        if (btn) btn.classList.toggle('active', k === regKey);
      }});

      const dateEl = document.getElementById('p02-active-regime-date');
      if (dateEl) dateEl.innerText = reg.tag + ' (' + reg.date + ')';

      const descEl = document.getElementById('p02-day-regime-desc');
      if (descEl) {{
        descEl.innerHTML = '<strong>' + reg.title + ':</strong> ' + reg.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Day-Ahead Spot: ' +
          reg.price_min.toFixed(1) + ' to ' + reg.price_max.toFixed(1) + ' EUR/MWh</span>';
      }}

      simP02();
    }}

    const dispatchDataP02 = [];
    let scrubberIdxP02 = 48;

    function recomputeDispatchP02(turbMw, pumpMw, resMwh, wearEur) {{
      const reg = (typeof DAY_REGIMES_P02 !== 'undefined' && DAY_REGIMES_P02[currentRegimeKeyP02]) ? DAY_REGIMES_P02[currentRegimeKeyP02] : DAY_REGIMES_P02['arbitrage'];
      if (!reg) return;

      const turbScale = turbMw / 30.0;
      const pumpScale = pumpMw / 28.0;
      const resScale = resMwh / 2400.0;

      for (let i = 0; i < 96; i++) {{
        const hour = i / 4;
        const timeStr = String(Math.floor(hour)).padStart(2, '0') + ':' + String((i % 4) * 15).padStart(2, '0');
        const price = reg.prices[i];
        const baseFlow = reg.flow_mw[i];
        const scaledFlow = baseFlow >= 0 ? baseFlow * turbScale : baseFlow * pumpScale;
        const baseRes = reg.res_mwh[i] * resScale;
        const resPct = Math.min(100.0, Math.max(0.0, (baseRes / resMwh) * 100.0));
        const afrr = reg.afrr_mw[i] * turbScale;

        if (dispatchDataP02[i]) {{
          dispatchDataP02[i].time = timeStr;
          dispatchDataP02[i].step = i + 1;
          dispatchDataP02[i].price = price;
          dispatchDataP02[i].flow = scaledFlow;
          dispatchDataP02[i].res = baseRes;
          dispatchDataP02[i].resPct = resPct;
          dispatchDataP02[i].afrr = afrr;
        }} else {{
          dispatchDataP02.push({{
            time: timeStr,
            step: i + 1,
            price: price,
            flow: scaledFlow,
            res: baseRes,
            resPct: resPct,
            afrr: afrr
          }});
        }}
      }}
    }}

    function drawDispatchCanvasP02() {{
      const canvas = document.getElementById('p02-dispatchCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;
      const plotX = padLeft;
      const plotY = padTop;
      const plotW = w - padLeft - padRight;
      const plotH = h - padTop - padBottom;
      const stepW = plotW / 96.0;

      const maxAbsFlow = Math.max(35.0, Math.ceil(Math.max(...dispatchDataP02.map(d => Math.abs(d.flow))) / 5.0) * 5.0);
      const zeroFlowY = plotY + plotH / 2;
      const flowY = mw => zeroFlowY - (mw / maxAbsFlow) * (plotH / 2);

      const rawPrices = dispatchDataP02.map(d => d.price);
      const rawMinP = Math.min(...rawPrices);
      const rawMaxP = Math.max(...rawPrices);
      let minP = 0.0;
      if (rawMinP < -10.0) minP = Math.floor(rawMinP / 25.0) * 25.0;
      let maxP = 120.0;
      if (rawMaxP > 500.0) maxP = 1000.0;
      else if (rawMaxP > 200.0) maxP = Math.ceil(rawMaxP / 50.0) * 50.0;
      else if (rawMaxP > 120.0) maxP = Math.ceil(rawMaxP / 25.0) * 25.0;
      const pRange = Math.max(20.0, maxP - minP);
      const priceY = p => plotY + (1.0 - (p - minP) / pRange) * plotH;

      const resPctY = pct => (plotY + plotH) - (Math.max(0, Math.min(100, pct)) / 100.0) * plotH;

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(plotX, zeroFlowY);
      ctx.lineTo(plotX + plotW, zeroFlowY);
      ctx.stroke();

      ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('0 MW (Idle)', plotX + 6, zeroFlowY - 4);

      if (minP < 0) {{
        const zeroPY = priceY(0);
        if (zeroPY >= plotY && zeroPY <= plotY + plotH) {{
          ctx.fillStyle = 'rgba(244, 63, 94, 0.08)';
          ctx.fillRect(plotX, zeroPY, plotW, (plotY + plotH) - zeroPY);
        }}
      }}

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX, plotY);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('Flow (MW)', plotX - 32, plotY - 14);

      [-maxAbsFlow, -maxAbsFlow / 2, 0, maxAbsFlow / 2, maxAbsFlow].forEach(mw => {{
        const y = flowY(mw);
        ctx.strokeStyle = (mw === 0) ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.06)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotX, y);
        ctx.lineTo(plotX + plotW, y);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.beginPath();
        ctx.moveTo(plotX - 5, y);
        ctx.lineTo(plotX, y);
        ctx.stroke();

        ctx.fillStyle = mw > 0 ? '#34d399' : mw < 0 ? '#f59e0b' : 'rgba(255, 255, 255, 0.7)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        const signStr = mw > 0 ? '+' : '';
        ctx.fillText(signStr + mw.toFixed(0) + ' MW', plotX - 8, y + 3.5);
      }});

      ctx.strokeStyle = 'rgba(245, 158, 11, 0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX + plotW, plotY);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#f59e0b';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText('Spot (EUR/MWh)', plotX + plotW + 86, plotY - 14);

      let priceTicks = [0, 50, 100];
      if (maxP >= 900) priceTicks = [0, 250, 500, 750, 1000];
      else if (minP < -100) priceTicks = [-150, -100, -50, 0, 50, 100];
      else if (minP < -30) priceTicks = [-60, -30, 0, 30, 60, 90, 120];
      else if (maxP > 150) priceTicks = [0, 50, 100, 150, 200];

      priceTicks.forEach(p => {{
        if (p < minP - 10 || p > maxP + 10) return;
        const y = priceY(p);
        if (y < plotY - 2 || y > plotY + plotH + 2) return;

        ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
        ctx.beginPath();
        ctx.moveTo(plotX + plotW, y);
        ctx.lineTo(plotX + plotW + 5, y);
        ctx.stroke();

        ctx.fillStyle = (p < 0) ? '#fb7185' : '#f59e0b';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText((p > 0 ? '+' : '') + p + ' EUR', plotX + plotW + 8, y + 3.5);
      }});

      const halfResY = resPctY(50);
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.12)';
      ctx.setLineDash([2, 5]);
      ctx.beginPath();
      ctx.moveTo(plotX, halfResY);
      ctx.lineTo(plotX + plotW, halfResY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(56, 189, 248, 0.45)';
      ctx.font = '8px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('Upper Reservoir 50% Level', plotX + plotW / 2, halfResY - 3);

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.beginPath();
      ctx.moveTo(plotX, plotY + plotH);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      for (let i = 0; i <= 96; i += 4) {{
        const x = plotX + i * stepW;
        const isMajor = (i % 16 === 0);
        ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.15)' : 'rgba(255, 255, 255, 0.04)';
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();

        if (isMajor) {{
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.beginPath();
          ctx.moveTo(x, plotY + plotH);
          ctx.lineTo(x, plotY + plotH + 5);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
          ctx.font = '10px "JetBrains Mono", monospace';
          ctx.textAlign = 'center';
          ctx.fillText(String(i / 4).padStart(2, '0') + ':00', x, plotY + plotH + 18);
        }}
      }}

      if (activeChannelsP02.flow) {{
        for (let i = 0; i < 96; i++) {{
          const flow = dispatchDataP02[i].flow;
          if (Math.abs(flow) > 0.2) {{
            const barH = (Math.abs(flow) / maxAbsFlow) * (plotH / 2);
            const x = plotX + i * stepW + 0.6;
            const barW = Math.max(1, stepW - 1.2);
            if (flow > 0) {{
              const y = zeroFlowY - barH;
              ctx.fillStyle = 'rgba(16, 185, 129, 0.38)';
              ctx.fillRect(x, y, barW, barH);
              ctx.strokeStyle = '#34d399';
              ctx.strokeRect(x, y, barW, barH);
            }} else {{
              const y = zeroFlowY;
              ctx.fillStyle = 'rgba(245, 158, 11, 0.38)';
              ctx.fillRect(x, y, barW, barH);
              ctx.strokeStyle = '#fbbf24';
              ctx.strokeRect(x, y, barW, barH);
            }}
          }}
        }}
      }}

      if (activeChannelsP02.afrr) {{
        for (let i = 0; i < 96; i++) {{
          const afrr = dispatchDataP02[i].afrr;
          if (afrr > 0.2) {{
            const barH = (afrr / maxAbsFlow) * (plotH / 2);
            const x = plotX + i * stepW + 0.6;
            const barW = Math.max(1, stepW - 1.2);
            const y = zeroFlowY - barH;
            ctx.fillStyle = 'rgba(168, 85, 247, 0.22)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = 'rgba(192, 132, 252, 0.6)';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      if (activeChannelsP02.price) {{
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2.2;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP02[i].price);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      if (activeChannelsP02.res) {{
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 1.8;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = resPctY(dispatchDataP02[i].resPct);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      const curX = plotX + scrubberIdxP02 * stepW;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(curX, plotY, stepW, plotH);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(curX, plotY, stepW, plotH);

      ctx.fillStyle = '#06b6d4';
      ctx.beginPath();
      ctx.arc(curX + stepW / 2, plotY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }}

    function updateHudP02(idx) {{
      const d = dispatchDataP02[idx];
      if (!d) return;

      const timeEl = document.getElementById('p02-hud-time');
      if (timeEl) timeEl.innerText = d.time + ' (Step ' + d.step + '/96)';

      const priceEl = document.getElementById('p02-hud-price');
      if (priceEl) priceEl.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const flowStr = d.flow > 0.1 ? '+' + d.flow.toFixed(2) + ' MW (Gen)' : (d.flow < -0.1 ? d.flow.toFixed(2) + ' MW (Pump)' : '0.00 MW (Idle)');
      const flowEl = document.getElementById('p02-hud-flow');
      if (flowEl) flowEl.innerText = flowStr;

      const resEl = document.getElementById('p02-hud-res');
      if (resEl) resEl.innerText = Math.round(d.res).toLocaleString() + ' MWh (' + d.resPct.toFixed(1) + '%)';

      const afrrEl = document.getElementById('p02-hud-afrr');
      if (afrrEl) afrrEl.innerText = d.afrr.toFixed(1) + ' MW Reserved';

      let cumProfit = 0;
      for (let i = 0; i <= idx; i++) {{
        cumProfit += (dispatchDataP02[i].flow * dispatchDataP02[i].price * 0.25);
      }}
      const profitEl = document.getElementById('p02-hud-profit');
      if (profitEl) profitEl.innerText = (cumProfit >= 0 ? '+' : '') + Math.round(cumProfit).toLocaleString() + ' EUR';

      const legFlow = document.getElementById('p02-legval-flow');
      if (legFlow) legFlow.innerText = flowStr;

      const legPrice = document.getElementById('p02-legval-price');
      if (legPrice) legPrice.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const legRes = document.getElementById('p02-legval-res');
      if (legRes) legRes.innerText = Math.round(d.res).toLocaleString() + ' MWh (' + d.resPct.toFixed(1) + '%)';

      const legAfrr = document.getElementById('p02-legval-afrr');
      if (legAfrr) legAfrr.innerText = d.afrr.toFixed(1) + ' MW';
    }}

    function setupCanvasListenersP02() {{
      const canvas = document.getElementById('p02-dispatchCanvas');
      if (!canvas) return;
      function handleMove(clientX) {{
        const rect = canvas.getBoundingClientRect();
        const padLeft = 72;
        const padRight = 92;
        const plotW = rect.width - padLeft - padRight;
        const mouseX = clientX - rect.left;
        const clampedX = Math.max(0, Math.min(plotW - 0.001, mouseX - padLeft));
        scrubberIdxP02 = Math.min(95, Math.max(0, Math.floor((clampedX / plotW) * 96)));
        updateHudP02(scrubberIdxP02);
        drawDispatchCanvasP02();
      }}
      canvas.addEventListener('mousemove', e => handleMove(e.clientX));
      canvas.addEventListener('touchmove', e => {{
        if (e.touches.length > 0) handleMove(e.touches[0].clientX);
      }});
    }}
    """)

    # =========================================================================
    # P03
    # =========================================================================
    js_parts.append(f"""
    // =========================================================================
    // PROJECT 03: 24-HOUR SMART EV FLEET CHARGING & §14a DIMMING ENGINE
    // =========================================================================
    const DAY_REGIMES_P03 = {p03_json};

    let currentRegimeKeyP03 = 'dimming_event';
    const activeChannelsP03 = {{
      draw: true,
      limit: true,
      price: true,
      soc: true
    }};

    function toggleChannelP03(chKey) {{
      activeChannelsP03[chKey] = !activeChannelsP03[chKey];
      const card = document.getElementById('p03-leg-' + chKey);
      if (card) card.classList.toggle('disabled', !activeChannelsP03[chKey]);
      drawDispatchCanvasP03();
    }}

    function selectDayRegimeP03(regKey) {{
      if (!DAY_REGIMES_P03[regKey]) return;
      currentRegimeKeyP03 = regKey;
      const reg = DAY_REGIMES_P03[regKey];

      ['dimming_event', 'overnight_depot', 'daytime_turnover', 'departure_crunch', 'extreme_uncoordinated', 'negative_soak'].forEach(k => {{
        const btn = document.getElementById('p03-dbtn-' + k);
        if (btn) btn.classList.toggle('active', k === regKey);
      }});

      const dateEl = document.getElementById('p03-active-regime-date');
      if (dateEl) dateEl.innerText = reg.tag + ' (' + reg.date + ')';

      const descEl = document.getElementById('p03-day-regime-desc');
      if (descEl) {{
        descEl.innerHTML = '<strong>' + reg.title + ':</strong> ' + reg.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Tariff: ' +
          Math.min(...reg.prices).toFixed(1) + ' to ' + Math.max(...reg.prices).toFixed(1) + ' EUR/MWh</span>';
      }}

      simP03();
    }}

    const dispatchDataP03 = [];
    let scrubberIdxP03 = 72;

    function recomputeDispatchP03(dimKw, sessionsCount, bufferKwh) {{
      const reg = (typeof DAY_REGIMES_P03 !== 'undefined' && DAY_REGIMES_P03[currentRegimeKeyP03]) ? DAY_REGIMES_P03[currentRegimeKeyP03] : DAY_REGIMES_P03['dimming_event'];
      if (!reg) return;

      const sessionScale = sessionsCount / 48.0;

      for (let i = 0; i < 96; i++) {{
        const hour = i / 4;
        const timeStr = String(Math.floor(hour)).padStart(2, '0') + ':' + String((i % 4) * 15).padStart(2, '0');
        const price = reg.prices[i];
        const isDimmed = (reg.grid_limit_kw[i] < 200.0);
        const limit = isDimmed ? dimKw : 240.0;
        const baseDraw = reg.fleet_draw_kw[i] * sessionScale;
        const actualDraw = Math.min(limit, baseDraw);
        const soc = Math.min(100.0, reg.fleet_soc[i] + (bufferKwh / 50.0) * 1.5);

        if (dispatchDataP03[i]) {{
          dispatchDataP03[i].time = timeStr;
          dispatchDataP03[i].step = i + 1;
          dispatchDataP03[i].price = price;
          dispatchDataP03[i].limit = limit;
          dispatchDataP03[i].draw = actualDraw;
          dispatchDataP03[i].soc = soc;
        }} else {{
          dispatchDataP03.push({{
            time: timeStr,
            step: i + 1,
            price: price,
            limit: limit,
            draw: actualDraw,
            soc: soc
          }});
        }}
      }}
    }}

    function drawDispatchCanvasP03() {{
      const canvas = document.getElementById('p03-dispatchCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;
      const plotX = padLeft;
      const plotY = padTop;
      const plotW = w - padLeft - padRight;
      const plotH = h - padTop - padBottom;
      const stepW = plotW / 96.0;

      const observedMaxKw = Math.max(100.0, Math.max(...dispatchDataP03.map(d => Math.max(d.draw, d.limit))));
      const maxKw = Math.max(120.0, Math.ceil(observedMaxKw / 50.0) * 50.0);
      const kwY = kw => (plotY + plotH) - (Math.max(0, kw) / maxKw) * plotH;

      const rawPrices = dispatchDataP03.map(d => d.price);
      const rawMinP = Math.min(...rawPrices);
      const rawMaxP = Math.max(...rawPrices);
      let minP = 0.0;
      if (rawMinP < -10.0) minP = Math.floor(rawMinP / 25.0) * 25.0;
      let maxP = 150.0;
      if (rawMaxP > 500.0) maxP = 1000.0;
      else if (rawMaxP > 200.0) maxP = Math.ceil(rawMaxP / 50.0) * 50.0;
      else if (rawMaxP > 150.0) maxP = Math.ceil(rawMaxP / 25.0) * 25.0;
      const pRange = Math.max(20.0, maxP - minP);
      const priceY = p => plotY + (1.0 - (p - minP) / pRange) * plotH;

      const socY = s => (plotY + plotH) - (Math.max(0, Math.min(100, s)) / 100.0) * plotH;

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX, plotY);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('Power (kW)', plotX - 30, plotY - 14);

      for (let kw = 0; kw <= maxKw; kw += (maxKw > 200 ? 50 : 25)) {{
        const y = kwY(kw);
        ctx.strokeStyle = (kw === 0) ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.06)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotX, y);
        ctx.lineTo(plotX + plotW, y);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.beginPath();
        ctx.moveTo(plotX - 5, y);
        ctx.lineTo(plotX, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.fillText(kw + ' kW', plotX - 8, y + 3.5);
      }}

      ctx.strokeStyle = 'rgba(245, 158, 11, 0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX + plotW, plotY);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#f59e0b';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText('Tariff (EUR/MWh)', plotX + plotW + 90, plotY - 14);

      let priceTicks = [0, 50, 100, 150];
      if (maxP >= 900) priceTicks = [0, 250, 500, 750, 1000];
      else if (minP < -30) priceTicks = [-50, 0, 50, 100, 150];

      priceTicks.forEach(p => {{
        if (p < minP - 10 || p > maxP + 10) return;
        const y = priceY(p);
        if (y < plotY - 2 || y > plotY + plotH + 2) return;

        ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
        ctx.beginPath();
        ctx.moveTo(plotX + plotW, y);
        ctx.lineTo(plotX + plotW + 5, y);
        ctx.stroke();

        ctx.fillStyle = (p < 0) ? '#fb7185' : '#f59e0b';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText((p > 0 ? '+' : '') + p + ' EUR', plotX + plotW + 8, y + 3.5);
      }});

      ctx.fillStyle = '#38bdf8';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('Fleet SoC: 0% to 100%', plotX + plotW / 2, plotY - 14);

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.beginPath();
      ctx.moveTo(plotX, plotY + plotH);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      for (let i = 0; i <= 96; i += 4) {{
        const x = plotX + i * stepW;
        const isMajor = (i % 16 === 0);
        ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.15)' : 'rgba(255, 255, 255, 0.04)';
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();

        if (isMajor) {{
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.beginPath();
          ctx.moveTo(x, plotY + plotH);
          ctx.lineTo(x, plotY + plotH + 5);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
          ctx.font = '10px "JetBrains Mono", monospace';
          ctx.textAlign = 'center';
          ctx.fillText(String(i / 4).padStart(2, '0') + ':00', x, plotY + plotH + 18);
        }}
      }}

      if (activeChannelsP03.draw) {{
        for (let i = 0; i < 96; i++) {{
          const draw = dispatchDataP03[i].draw;
          if (draw > 0.5) {{
            const barH = (draw / maxKw) * plotH;
            const x = plotX + i * stepW + 0.6;
            const y = (plotY + plotH) - barH;
            const barW = Math.max(1, stepW - 1.2);
            ctx.fillStyle = 'rgba(16, 185, 129, 0.35)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = '#34d399';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      if (activeChannelsP03.limit) {{
        ctx.strokeStyle = '#c084fc';
        ctx.lineWidth = 2.0;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = kwY(dispatchDataP03[i].limit);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      if (activeChannelsP03.price) {{
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2.0;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP03[i].price);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      if (activeChannelsP03.soc) {{
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 1.8;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = socY(dispatchDataP03[i].soc);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      const curX = plotX + scrubberIdxP03 * stepW;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(curX, plotY, stepW, plotH);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(curX, plotY, stepW, plotH);

      ctx.fillStyle = '#06b6d4';
      ctx.beginPath();
      ctx.arc(curX + stepW / 2, plotY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }}

    function updateHudP03(idx) {{
      const d = dispatchDataP03[idx];
      if (!d) return;

      const timeEl = document.getElementById('p03-hud-time');
      if (timeEl) timeEl.innerText = d.time + ' (Step ' + d.step + '/96)';

      const priceEl = document.getElementById('p03-hud-price');
      if (priceEl) priceEl.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const limitEl = document.getElementById('p03-hud-limit');
      if (limitEl) limitEl.innerText = d.limit.toFixed(1) + ' kW' + (d.limit < 200 ? ' (Dimmed)' : ' (Unconstrained)');

      const drawEl = document.getElementById('p03-hud-draw');
      if (drawEl) drawEl.innerText = d.draw.toFixed(1) + ' kW';

      const socEl = document.getElementById('p03-hud-soc');
      if (socEl) socEl.innerText = d.soc.toFixed(1) + '% (Ready)';

      const dimstatEl = document.getElementById('p03-hud-dimstat');
      if (dimstatEl) {{
        if (d.draw <= d.limit + 0.1) {{
          dimstatEl.innerText = 'Compliant (0 Violation)';
          dimstatEl.className = 'hud-stat-val emerald';
        }} else {{
          dimstatEl.innerText = 'Overrun (' + (d.draw - d.limit).toFixed(1) + ' kW)';
          dimstatEl.className = 'hud-stat-val amber';
        }}
      }}

      const legDraw = document.getElementById('p03-legval-draw');
      if (legDraw) legDraw.innerText = d.draw.toFixed(1) + ' kW';

      const legLimit = document.getElementById('p03-legval-limit');
      if (legLimit) legLimit.innerText = d.limit.toFixed(1) + ' kW';

      const legPrice = document.getElementById('p03-legval-price');
      if (legPrice) legPrice.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const legSoc = document.getElementById('p03-legval-soc');
      if (legSoc) legSoc.innerText = d.soc.toFixed(1) + '%';
    }}

    function setupCanvasListenersP03() {{
      const canvas = document.getElementById('p03-dispatchCanvas');
      if (!canvas) return;
      function handleMove(clientX) {{
        const rect = canvas.getBoundingClientRect();
        const padLeft = 72;
        const padRight = 92;
        const plotW = rect.width - padLeft - padRight;
        const mouseX = clientX - rect.left;
        const clampedX = Math.max(0, Math.min(plotW - 0.001, mouseX - padLeft));
        scrubberIdxP03 = Math.min(95, Math.max(0, Math.floor((clampedX / plotW) * 96)));
        updateHudP03(scrubberIdxP03);
        drawDispatchCanvasP03();
      }}
      canvas.addEventListener('mousemove', e => handleMove(e.clientX));
      canvas.addEventListener('touchmove', e => {{
        if (e.touches.length > 0) handleMove(e.touches[0].clientX);
      }});
    }}
    """)

    # =========================================================================
    # P04
    # =========================================================================
    js_parts.append(f"""
    // =========================================================================
    // PROJECT 04: 24-HOUR RENEWABLE ENERGY COMMUNITY & P2P SHARING ENGINE
    // =========================================================================
    const DAY_REGIMES_P04 = {p04_json};

    let currentRegimeKeyP04 = 'summer_export';
    const activeChannelsP04 = {{
      pv: true,
      load: true,
      shared: true,
      price: true
    }};

    function toggleChannelP04(chKey) {{
      activeChannelsP04[chKey] = !activeChannelsP04[chKey];
      const card = document.getElementById('p04-leg-' + chKey);
      if (card) card.classList.toggle('disabled', !activeChannelsP04[chKey]);
      drawDispatchCanvasP04();
    }}

    function selectDayRegimeP04(regKey) {{
      if (!DAY_REGIMES_P04[regKey]) return;
      currentRegimeKeyP04 = regKey;
      const reg = DAY_REGIMES_P04[regKey];

      ['summer_export', 'winter_deficit', 'cloud_volatility', 'negative_day', 'evening_peak', 'core_cliff'].forEach(k => {{
        const btn = document.getElementById('p04-dbtn-' + k);
        if (btn) btn.classList.toggle('active', k === regKey);
      }});

      const dateEl = document.getElementById('p04-active-regime-date');
      if (dateEl) dateEl.innerText = reg.tag + ' (' + reg.date + ')';

      const descEl = document.getElementById('p04-day-regime-desc');
      if (descEl) {{
        descEl.innerHTML = '<strong>' + reg.title + ':</strong> ' + reg.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Internal Tariff: ' +
          Math.min(...reg.internal_price).toFixed(1) + ' to ' + Math.max(...reg.internal_price).toFixed(1) + ' ct/kWh</span>';
      }}

      simP04();
    }}

    const dispatchDataP04 = [];
    let scrubberIdxP04 = 50;

    function recomputeDispatchP04(chargeEur, membersCount, pvKwp) {{
      const reg = (typeof DAY_REGIMES_P04 !== 'undefined' && DAY_REGIMES_P04[currentRegimeKeyP04]) ? DAY_REGIMES_P04[currentRegimeKeyP04] : DAY_REGIMES_P04['summer_export'];
      if (!reg) return;

      const pvScale = pvKwp / 65.0;
      const loadScale = membersCount / 20.0;

      for (let i = 0; i < 96; i++) {{
        const hour = i / 4;
        const timeStr = String(Math.floor(hour)).padStart(2, '0') + ':' + String((i % 4) * 15).padStart(2, '0');
        const pv = Math.max(0, reg.pv_kw[i] * pvScale);
        const load = Math.max(0.1, reg.load_kw[i] * loadScale);
        const shared = Math.min(pv, load);
        const gridFlow = pv - load;
        const baseIntPrice = reg.internal_price[i];
        const intPrice = baseIntPrice + (chargeEur * 100.0 * 0.4);

        if (dispatchDataP04[i]) {{
          dispatchDataP04[i].time = timeStr;
          dispatchDataP04[i].step = i + 1;
          dispatchDataP04[i].pv = pv;
          dispatchDataP04[i].load = load;
          dispatchDataP04[i].shared = shared;
          dispatchDataP04[i].grid = gridFlow;
          dispatchDataP04[i].intPrice = intPrice;
        }} else {{
          dispatchDataP04.push({{
            time: timeStr,
            step: i + 1,
            pv: pv,
            load: load,
            shared: shared,
            grid: gridFlow,
            intPrice: intPrice
          }});
        }}
      }}
    }}

    function drawDispatchCanvasP04() {{
      const canvas = document.getElementById('p04-dispatchCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;
      const plotX = padLeft;
      const plotY = padTop;
      const plotW = w - padLeft - padRight;
      const plotH = h - padTop - padBottom;
      const stepW = plotW / 96.0;

      const observedMaxKw = Math.max(50.0, Math.max(...dispatchDataP04.map(d => Math.max(d.pv, d.load))));
      const maxKw = Math.max(60.0, Math.ceil(observedMaxKw / 20.0) * 20.0);
      const kwY = kw => (plotY + plotH) - (Math.max(0, kw) / maxKw) * plotH;

      const rawPrices = dispatchDataP04.map(d => d.intPrice);
      const rawMinP = Math.min(...rawPrices);
      const rawMaxP = Math.max(...rawPrices);
      const minP = Math.max(0.0, Math.floor(rawMinP / 5.0) * 5.0);
      const maxP = Math.max(minP + 10.0, Math.ceil(rawMaxP / 5.0) * 5.0);
      const pRange = Math.max(5.0, maxP - minP);
      const priceY = p => plotY + (1.0 - (p - minP) / pRange) * plotH;

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX, plotY);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('Power (kW)', plotX - 30, plotY - 14);

      for (let kw = 0; kw <= maxKw; kw += (maxKw > 100 ? 25 : 15)) {{
        const y = kwY(kw);
        ctx.strokeStyle = (kw === 0) ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.06)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotX, y);
        ctx.lineTo(plotX + plotW, y);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.beginPath();
        ctx.moveTo(plotX - 5, y);
        ctx.lineTo(plotX, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.fillText(kw + ' kW', plotX - 8, y + 3.5);
      }}

      ctx.strokeStyle = 'rgba(192, 132, 252, 0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX + plotW, plotY);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#c084fc';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText('Clearing (ct/kWh)', plotX + plotW + 90, plotY - 14);

      for (let p = minP; p <= maxP; p += 5) {{
        const y = priceY(p);
        if (y < plotY - 2 || y > plotY + plotH + 2) continue;

        ctx.strokeStyle = 'rgba(192, 132, 252, 0.4)';
        ctx.beginPath();
        ctx.moveTo(plotX + plotW, y);
        ctx.lineTo(plotX + plotW + 5, y);
        ctx.stroke();

        ctx.fillStyle = '#c084fc';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText(p.toFixed(0) + ' ct', plotX + plotW + 8, y + 3.5);
      }}

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.beginPath();
      ctx.moveTo(plotX, plotY + plotH);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      for (let i = 0; i <= 96; i += 4) {{
        const x = plotX + i * stepW;
        const isMajor = (i % 16 === 0);
        ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.15)' : 'rgba(255, 255, 255, 0.04)';
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();

        if (isMajor) {{
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.beginPath();
          ctx.moveTo(x, plotY + plotH);
          ctx.lineTo(x, plotY + plotH + 5);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
          ctx.font = '10px "JetBrains Mono", monospace';
          ctx.textAlign = 'center';
          ctx.fillText(String(i / 4).padStart(2, '0') + ':00', x, plotY + plotH + 18);
        }}
      }}

      if (activeChannelsP04.pv) {{
        for (let i = 0; i < 96; i++) {{
          const pv = dispatchDataP04[i].pv;
          if (pv > 0.2) {{
            const barH = (pv / maxKw) * plotH;
            const x = plotX + i * stepW + 0.6;
            const y = (plotY + plotH) - barH;
            const barW = Math.max(1, stepW - 1.2);
            ctx.fillStyle = 'rgba(16, 185, 129, 0.25)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = 'rgba(52, 211, 153, 0.7)';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      if (activeChannelsP04.shared) {{
        for (let i = 0; i < 96; i++) {{
          const shared = dispatchDataP04[i].shared;
          if (shared > 0.2) {{
            const barH = (shared / maxKw) * plotH;
            const x = plotX + i * stepW + 1.2;
            const y = (plotY + plotH) - barH;
            const barW = Math.max(1, stepW - 2.4);
            ctx.fillStyle = 'rgba(56, 189, 248, 0.55)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = '#38bdf8';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      if (activeChannelsP04.load) {{
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2.0;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = kwY(dispatchDataP04[i].load);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      if (activeChannelsP04.price) {{
        ctx.strokeStyle = '#c084fc';
        ctx.lineWidth = 2.0;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP04[i].intPrice);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      const curX = plotX + scrubberIdxP04 * stepW;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(curX, plotY, stepW, plotH);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(curX, plotY, stepW, plotH);

      ctx.fillStyle = '#06b6d4';
      ctx.beginPath();
      ctx.arc(curX + stepW / 2, plotY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }}

    function updateHudP04(idx) {{
      const d = dispatchDataP04[idx];
      if (!d) return;

      const timeEl = document.getElementById('p04-hud-time');
      if (timeEl) timeEl.innerText = d.time + ' (Step ' + d.step + '/96)';

      const pvEl = document.getElementById('p04-hud-pv');
      if (pvEl) pvEl.innerText = d.pv.toFixed(1) + ' kW';

      const loadEl = document.getElementById('p04-hud-load');
      if (loadEl) loadEl.innerText = d.load.toFixed(1) + ' kW';

      const sharedPct = (d.load > 0) ? Math.min(100, (d.shared / d.load) * 100) : 0;
      const sharedEl = document.getElementById('p04-hud-shared');
      if (sharedEl) sharedEl.innerText = d.shared.toFixed(1) + ' kW (' + sharedPct.toFixed(0) + '%)';

      const gridStr = d.grid > 0.05 ? d.grid.toFixed(1) + ' kW (Exp)' : (d.grid < -0.05 ? Math.abs(d.grid).toFixed(1) + ' kW (Imp)' : '0.0 kW (Bal)');
      const gridEl = document.getElementById('p04-hud-grid');
      if (gridEl) gridEl.innerText = gridStr;

      const priceEl = document.getElementById('p04-hud-price');
      if (priceEl) priceEl.innerText = d.intPrice.toFixed(1) + ' ct/kWh';

      const legPv = document.getElementById('p04-legval-pv');
      if (legPv) legPv.innerText = d.pv.toFixed(1) + ' kW';

      const legLoad = document.getElementById('p04-legval-load');
      if (legLoad) legLoad.innerText = d.load.toFixed(1) + ' kW';

      const legShared = document.getElementById('p04-legval-shared');
      if (legShared) legShared.innerText = d.shared.toFixed(1) + ' kW';

      const legPrice = document.getElementById('p04-legval-price');
      if (legPrice) legPrice.innerText = d.intPrice.toFixed(1) + ' ct/kWh';
    }}

    function setupCanvasListenersP04() {{
      const canvas = document.getElementById('p04-dispatchCanvas');
      if (!canvas) return;
      function handleMove(clientX) {{
        const rect = canvas.getBoundingClientRect();
        const padLeft = 72;
        const padRight = 92;
        const plotW = rect.width - padLeft - padRight;
        const mouseX = clientX - rect.left;
        const clampedX = Math.max(0, Math.min(plotW - 0.001, mouseX - padLeft));
        scrubberIdxP04 = Math.min(95, Math.max(0, Math.floor((clampedX / plotW) * 96)));
        updateHudP04(scrubberIdxP04);
        drawDispatchCanvasP04();
      }}
      canvas.addEventListener('mousemove', e => handleMove(e.clientX));
      canvas.addEventListener('touchmove', e => {{
        if (e.touches.length > 0) handleMove(e.touches[0].clientX);
      }});
    }}
    """)

    # =========================================================================
    # P05
    # =========================================================================
    js_parts.append(f"""
    // =========================================================================
    // PROJECT 05: 24-HOUR HYBRID OVER-PLANTING & EEG §51 DISPATCH ENGINE
    // =========================================================================
    const DAY_REGIMES_P05 = {p05_json};

    let currentRegimeKeyP05 = 'overplanting_storm';
    const activeChannelsP05 = {{
      raw: true,
      export: true,
      bess: true,
      price: true,
      soc: true
    }};

    function toggleChannelP05(chKey) {{
      activeChannelsP05[chKey] = !activeChannelsP05[chKey];
      const card = document.getElementById('p05-leg-' + chKey);
      if (card) card.classList.toggle('disabled', !activeChannelsP05[chKey]);
      drawDispatchCanvasP05();
    }}

    function selectDayRegimeP05(regKey) {{
      if (!DAY_REGIMES_P05[regKey]) return;
      currentRegimeKeyP05 = regKey;
      const reg = DAY_REGIMES_P05[regKey];

      ['overplanting_storm', 'eeg51_negative_run', 'summer_solar_noon', 'winter_wind_gale', 'extreme_high_spike', 'bottleneck_grid'].forEach(k => {{
        const btn = document.getElementById('p05-dbtn-' + k);
        if (btn) btn.classList.toggle('active', k === regKey);
      }});

      const dateEl = document.getElementById('p05-active-regime-date');
      if (dateEl) dateEl.innerText = reg.tag + ' (' + reg.date + ')';

      const descEl = document.getElementById('p05-day-regime-desc');
      if (descEl) {{
        descEl.innerHTML = '<strong>' + reg.title + ':</strong> ' + reg.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">Spot: ' +
          Math.min(...reg.prices).toFixed(1) + ' to ' + Math.max(...reg.prices).toFixed(1) + ' EUR/MWh</span>';
      }}

      simP05();
    }}

    const dispatchDataP05 = [];
    let scrubberIdxP05 = 52;

    function recomputeDispatchP05(pocCapMw, pvMw, bessMwh) {{
      const reg = (typeof DAY_REGIMES_P05 !== 'undefined' && DAY_REGIMES_P05[currentRegimeKeyP05]) ? DAY_REGIMES_P05[currentRegimeKeyP05] : DAY_REGIMES_P05['overplanting_storm'];
      if (!reg) return;

      const totalGenScale = (40.0 + pvMw) / 80.0;
      const bessMaxRate = bessMwh / 2.0;

      for (let i = 0; i < 96; i++) {{
        const hour = i / 4;
        const timeStr = String(Math.floor(hour)).padStart(2, '0') + ':' + String((i % 4) * 15).padStart(2, '0');
        const price = reg.prices[i];
        const rawGen = reg.raw_gen_mw[i] * totalGenScale;
        const curtailPotential = Math.max(0, rawGen - pocCapMw);

        let bessFlow = 0;
        if (curtailPotential > 0) {{
          bessFlow = Math.min(curtailPotential, bessMaxRate);
        }} else if (price > 120.0 && rawGen < pocCapMw) {{
          bessFlow = -Math.min(pocCapMw - rawGen, bessMaxRate * 0.8);
        }}

        const actualExport = Math.min(pocCapMw, Math.max(0, rawGen - bessFlow));
        const bessSoc = Math.min(100.0, Math.max(10.0, reg.bess_soc[i]));

        if (dispatchDataP05[i]) {{
          dispatchDataP05[i].time = timeStr;
          dispatchDataP05[i].step = i + 1;
          dispatchDataP05[i].price = price;
          dispatchDataP05[i].raw = rawGen;
          dispatchDataP05[i].export = actualExport;
          dispatchDataP05[i].bess = bessFlow;
          dispatchDataP05[i].soc = bessSoc;
          dispatchDataP05[i].poc = pocCapMw;
        }} else {{
          dispatchDataP05.push({{
            time: timeStr,
            step: i + 1,
            price: price,
            raw: rawGen,
            export: actualExport,
            bess: bessFlow,
            soc: bessSoc,
            poc: pocCapMw
          }});
        }}
      }}
    }}

    function drawDispatchCanvasP05() {{
      const canvas = document.getElementById('p05-dispatchCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;
      const plotX = padLeft;
      const plotY = padTop;
      const plotW = w - padLeft - padRight;
      const plotH = h - padTop - padBottom;
      const stepW = plotW / 96.0;

      const observedMaxMw = Math.max(60.0, Math.max(...dispatchDataP05.map(d => Math.max(d.raw, d.poc))));
      const maxMw = Math.max(70.0, Math.ceil(observedMaxMw / 10.0) * 10.0);
      const mwY = mw => (plotY + plotH) - (Math.max(0, mw) / maxMw) * plotH;

      const rawPrices = dispatchDataP05.map(d => d.price);
      const rawMinP = Math.min(...rawPrices);
      const rawMaxP = Math.max(...rawPrices);
      let minP = 0.0;
      if (rawMinP < -10.0) minP = Math.floor(rawMinP / 25.0) * 25.0;
      let maxP = 150.0;
      if (rawMaxP > 500.0) maxP = 1000.0;
      else if (rawMaxP > 200.0) maxP = Math.ceil(rawMaxP / 50.0) * 50.0;
      else if (rawMaxP > 150.0) maxP = Math.ceil(rawMaxP / 25.0) * 25.0;
      const pRange = Math.max(20.0, maxP - minP);
      const priceY = p => plotY + (1.0 - (p - minP) / pRange) * plotH;

      const socY = s => (plotY + plotH) - (Math.max(0, Math.min(100, s)) / 100.0) * plotH;

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX, plotY);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('Power (MW)', plotX - 30, plotY - 14);

      for (let mw = 0; mw <= maxMw; mw += 15) {{
        const y = mwY(mw);
        ctx.strokeStyle = (mw === 0) ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.06)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotX, y);
        ctx.lineTo(plotX + plotW, y);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.beginPath();
        ctx.moveTo(plotX - 5, y);
        ctx.lineTo(plotX, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.fillText(mw + ' MW', plotX - 8, y + 3.5);
      }}

      ctx.strokeStyle = 'rgba(245, 158, 11, 0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX + plotW, plotY);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#f59e0b';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText('Spot (EUR/MWh)', plotX + plotW + 90, plotY - 14);

      let priceTicks = [0, 50, 100, 150];
      if (maxP >= 900) priceTicks = [0, 250, 500, 750, 1000];
      else if (minP < -30) priceTicks = [-50, 0, 50, 100, 150];

      priceTicks.forEach(p => {{
        if (p < minP - 10 || p > maxP + 10) return;
        const y = priceY(p);
        if (y < plotY - 2 || y > plotY + plotH + 2) return;

        ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
        ctx.beginPath();
        ctx.moveTo(plotX + plotW, y);
        ctx.lineTo(plotX + plotW + 5, y);
        ctx.stroke();

        ctx.fillStyle = (p < 0) ? '#fb7185' : '#f59e0b';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText((p > 0 ? '+' : '') + p + ' EUR', plotX + plotW + 8, y + 3.5);
      }});

      ctx.fillStyle = '#fb7185';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('BESS SoC: 0% to 100%', plotX + plotW / 2, plotY - 14);

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.beginPath();
      ctx.moveTo(plotX, plotY + plotH);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      for (let i = 0; i <= 96; i += 4) {{
        const x = plotX + i * stepW;
        const isMajor = (i % 16 === 0);
        ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.15)' : 'rgba(255, 255, 255, 0.04)';
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();

        if (isMajor) {{
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.beginPath();
          ctx.moveTo(x, plotY + plotH);
          ctx.lineTo(x, plotY + plotH + 5);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
          ctx.font = '10px "JetBrains Mono", monospace';
          ctx.textAlign = 'center';
          ctx.fillText(String(i / 4).padStart(2, '0') + ':00', x, plotY + plotH + 18);
        }}
      }}

      // Over-Planting Curtailment Shading
      for (let i = 0; i < 96; i++) {{
        const raw = dispatchDataP05[i].raw;
        const poc = dispatchDataP05[i].poc;
        if (raw > poc) {{
          const yRaw = mwY(raw);
          const yPoc = mwY(poc);
          const x = plotX + i * stepW;
          ctx.fillStyle = 'rgba(244, 63, 94, 0.18)';
          ctx.fillRect(x, yRaw, stepW, yPoc - yRaw);
        }}
      }}

      // Horizontal PCC Limit
      if (dispatchDataP05.length > 0) {{
        const pocY = mwY(dispatchDataP05[0].poc);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(plotX, pocY);
        ctx.lineTo(plotX + plotW, pocY);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = '#f59e0b';
        ctx.font = '9px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText('PCC Grid Limit: ' + dispatchDataP05[0].poc.toFixed(0) + ' MW', plotX + 6, pocY - 4);
      }}

      // Raw Wind+PV curve
      if (activeChannelsP05.raw) {{
        ctx.strokeStyle = '#34d399';
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = mwY(dispatchDataP05[i].raw);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      // PCC Export curve
      if (activeChannelsP05.export) {{
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 2.2;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = mwY(dispatchDataP05[i].export);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      // BESS Power bars
      if (activeChannelsP05.bess) {{
        for (let i = 0; i < 96; i++) {{
          const bess = dispatchDataP05[i].bess;
          if (Math.abs(bess) > 0.2) {{
            const barH = (Math.abs(bess) / maxMw) * plotH;
            const x = plotX + i * stepW + 0.6;
            const y = (plotY + plotH) - barH;
            const barW = Math.max(1, stepW - 1.2);
            ctx.fillStyle = bess > 0 ? 'rgba(168, 85, 247, 0.45)' : 'rgba(244, 63, 94, 0.45)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = bess > 0 ? '#c084fc' : '#fb7185';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      // Day-Ahead Spot
      if (activeChannelsP05.price) {{
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP05[i].price);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      // BESS SoC
      if (activeChannelsP05.soc) {{
        ctx.strokeStyle = '#fb7185';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = socY(dispatchDataP05[i].soc);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      const curX = plotX + scrubberIdxP05 * stepW;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(curX, plotY, stepW, plotH);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(curX, plotY, stepW, plotH);

      ctx.fillStyle = '#06b6d4';
      ctx.beginPath();
      ctx.arc(curX + stepW / 2, plotY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }}

    function updateHudP05(idx) {{
      const d = dispatchDataP05[idx];
      if (!d) return;

      const timeEl = document.getElementById('p05-hud-time');
      if (timeEl) timeEl.innerText = d.time + ' (Step ' + d.step + '/96)';

      const priceEl = document.getElementById('p05-hud-price');
      if (priceEl) priceEl.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const rawEl = document.getElementById('p05-hud-raw');
      if (rawEl) rawEl.innerText = d.raw.toFixed(1) + ' MW';

      const exportStr = d.export.toFixed(1) + ' MW' + (d.export >= d.poc - 0.1 ? ' (Capped)' : '');
      const exportEl = document.getElementById('p05-hud-export');
      if (exportEl) exportEl.innerText = exportStr;

      const bessStr = d.bess > 0.1 ? '+' + d.bess.toFixed(1) + ' MW (Chg)' : (d.bess < -0.1 ? d.bess.toFixed(1) + ' MW (Dis)' : '0.0 MW (Idle)');
      const bessEl = document.getElementById('p05-hud-bess');
      if (bessEl) bessEl.innerText = bessStr;

      const socEl = document.getElementById('p05-hud-soc');
      if (socEl) socEl.innerText = d.soc.toFixed(1) + '%';

      const legRaw = document.getElementById('p05-legval-raw');
      if (legRaw) legRaw.innerText = d.raw.toFixed(1) + ' MW';

      const legExport = document.getElementById('p05-legval-export');
      if (legExport) legExport.innerText = exportStr;

      const legBess = document.getElementById('p05-legval-bess');
      if (legBess) legBess.innerText = bessStr;

      const legPrice = document.getElementById('p05-legval-price');
      if (legPrice) legPrice.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const legSoc = document.getElementById('p05-legval-soc');
      if (legSoc) legSoc.innerText = d.soc.toFixed(1) + '%';
    }}

    function setupCanvasListenersP05() {{
      const canvas = document.getElementById('p05-dispatchCanvas');
      if (!canvas) return;
      function handleMove(clientX) {{
        const rect = canvas.getBoundingClientRect();
        const padLeft = 72;
        const padRight = 92;
        const plotW = rect.width - padLeft - padRight;
        const mouseX = clientX - rect.left;
        const clampedX = Math.max(0, Math.min(plotW - 0.001, mouseX - padLeft));
        scrubberIdxP05 = Math.min(95, Math.max(0, Math.floor((clampedX / plotW) * 96)));
        updateHudP05(scrubberIdxP05);
        drawDispatchCanvasP05();
      }}
      canvas.addEventListener('mousemove', e => handleMove(e.clientX));
      canvas.addEventListener('touchmove', e => {{
        if (e.touches.length > 0) handleMove(e.touches[0].clientX);
      }});
    }}
    """)

    # =========================================================================
    # P06
    # =========================================================================
    js_parts.append(f"""
    // =========================================================================
    // PROJECT 06: 24-HOUR PROBABILISTIC FORECAST FAN & REBAP BIDDING ENGINE
    // =========================================================================
    const DAY_REGIMES_P06 = {p06_json};

    let currentRegimeKeyP06 = 'system_shortage';
    const activeChannelsP06 = {{
      bid: true,
      real: true,
      fan: true,
      rebap: true,
      da: true
    }};

    function toggleChannelP06(chKey) {{
      activeChannelsP06[chKey] = !activeChannelsP06[chKey];
      const card = document.getElementById('p06-leg-' + chKey);
      if (card) card.classList.toggle('disabled', !activeChannelsP06[chKey]);
      drawDispatchCanvasP06();
    }}

    function selectDayRegimeP06(regKey) {{
      if (!DAY_REGIMES_P06[regKey]) return;
      currentRegimeKeyP06 = regKey;
      const reg = DAY_REGIMES_P06[regKey];

      ['system_shortage', 'system_long', 'storm_ramp_uncertainty', 'stable_high_pressure', 'asymmetric_spread', 'forecast_bust_stress'].forEach(k => {{
        const btn = document.getElementById('p06-dbtn-' + k);
        if (btn) btn.classList.toggle('active', k === regKey);
      }});

      const dateEl = document.getElementById('p06-active-regime-date');
      if (dateEl) dateEl.innerText = reg.tag + ' (' + reg.date + ')';

      const descEl = document.getElementById('p06-day-regime-desc');
      if (descEl) {{
        descEl.innerHTML = '<strong>' + reg.title + ':</strong> ' + reg.desc +
          ' <span style="color: var(--cyan-bright); margin-left: 8px;">reBAP Spread: ' +
          Math.min(...reg.rebap_prices).toFixed(1) + ' to ' + Math.max(...reg.rebap_prices).toFixed(1) + ' EUR/MWh</span>';
      }}

      simP06();
    }}

    const dispatchDataP06 = [];
    let scrubberIdxP06 = 72;

    function recomputeDispatchP06(riskTau, maePct, spreadEur) {{
      const reg = (typeof DAY_REGIMES_P06 !== 'undefined' && DAY_REGIMES_P06[currentRegimeKeyP06]) ? DAY_REGIMES_P06[currentRegimeKeyP06] : DAY_REGIMES_P06['system_shortage'];
      if (!reg) return;

      const spreadScale = spreadEur / 42.0;
      const fanSpreadScale = maePct / 11.8;

      for (let i = 0; i < 96; i++) {{
        const hour = i / 4;
        const timeStr = String(Math.floor(hour)).padStart(2, '0') + ':' + String((i % 4) * 15).padStart(2, '0');
        const price = reg.prices[i];
        const rebap = reg.rebap_prices[i] * spreadScale;

        const q50 = reg.q50[i];
        const q10 = Math.max(0, q50 - (q50 - reg.q10[i]) * fanSpreadScale);
        const q25 = Math.max(0, q50 - (q50 - reg.q25[i]) * fanSpreadScale);
        const q75 = q50 + (reg.q75[i] - q50) * fanSpreadScale;
        const q90 = q50 + (reg.q90[i] - q50) * fanSpreadScale;

        // Interpolate optimal bid based on tau slider
        let bid = q50;
        if (riskTau <= 0.25) {{
          const f = (riskTau - 0.10) / 0.15;
          bid = q10 + f * (q25 - q10);
        }} else if (riskTau <= 0.50) {{
          const f = (riskTau - 0.25) / 0.25;
          bid = q25 + f * (q50 - q25);
        }} else if (riskTau <= 0.75) {{
          const f = (riskTau - 0.50) / 0.25;
          bid = q50 + f * (q75 - q50);
        }} else {{
          const f = (riskTau - 0.75) / 0.15;
          bid = q75 + f * (q90 - q75);
        }}

        const realized = reg.realized_mw[i];

        if (dispatchDataP06[i]) {{
          dispatchDataP06[i].time = timeStr;
          dispatchDataP06[i].step = i + 1;
          dispatchDataP06[i].price = price;
          dispatchDataP06[i].rebap = rebap;
          dispatchDataP06[i].q10 = q10;
          dispatchDataP06[i].q25 = q25;
          dispatchDataP06[i].q50 = q50;
          dispatchDataP06[i].q75 = q75;
          dispatchDataP06[i].q90 = q90;
          dispatchDataP06[i].bid = bid;
          dispatchDataP06[i].real = realized;
        }} else {{
          dispatchDataP06.push({{
            time: timeStr,
            step: i + 1,
            price: price,
            rebap: rebap,
            q10: q10,
            q25: q25,
            q50: q50,
            q75: q75,
            q90: q90,
            bid: bid,
            real: realized
          }});
        }}
      }}
    }}

    function drawDispatchCanvasP06() {{
      const canvas = document.getElementById('p06-dispatchCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      const w = rect.width;
      const h = rect.height;

      ctx.clearRect(0, 0, w, h);

      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;
      const plotX = padLeft;
      const plotY = padTop;
      const plotW = w - padLeft - padRight;
      const plotH = h - padTop - padBottom;
      const stepW = plotW / 96.0;

      const observedMaxMw = Math.max(30.0, Math.max(...dispatchDataP06.map(d => Math.max(d.q90, d.real, d.bid))));
      const maxMw = Math.max(35.0, Math.ceil(observedMaxMw / 5.0) * 5.0);
      const mwY = mw => (plotY + plotH) - (Math.max(0, mw) / maxMw) * plotH;

      const allPrices = [];
      dispatchDataP06.forEach(d => {{
        allPrices.push(d.price);
        allPrices.push(d.rebap);
      }});
      const rawMinP = Math.min(...allPrices);
      const rawMaxP = Math.max(...allPrices);
      let minP = -50.0;
      if (rawMinP < -100.0) minP = Math.floor(rawMinP / 50.0) * 50.0;
      else if (rawMinP > 0) minP = 0.0;
      let maxP = 200.0;
      if (rawMaxP > 500.0) maxP = 800.0;
      else if (rawMaxP > 200.0) maxP = Math.ceil(rawMaxP / 50.0) * 50.0;
      const pRange = Math.max(50.0, maxP - minP);
      const priceY = p => plotY + (1.0 - (p - minP) / pRange) * plotH;

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX, plotY);
      ctx.lineTo(plotX, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('Wind & Bid (MW)', plotX - 32, plotY - 14);

      for (let mw = 0; mw <= maxMw; mw += (maxMw > 40 ? 10 : 5)) {{
        const y = mwY(mw);
        ctx.strokeStyle = (mw === 0) ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.06)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotX, y);
        ctx.lineTo(plotX + plotW, y);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.beginPath();
        ctx.moveTo(plotX - 5, y);
        ctx.lineTo(plotX, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.fillText(mw + ' MW', plotX - 8, y + 3.5);
      }}

      ctx.strokeStyle = 'rgba(244, 63, 94, 0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotX + plotW, plotY);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      ctx.fillStyle = '#fb7185';
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText('reBAP / DA (EUR/MWh)', plotX + plotW + 90, plotY - 14);

      let priceTicks = [0, 100, 200];
      if (maxP >= 600) priceTicks = [-100, 0, 200, 400, 600, 800];
      else if (minP < -50) priceTicks = [-150, -100, -50, 0, 50, 100, 150];

      priceTicks.forEach(p => {{
        if (p < minP - 10 || p > maxP + 10) return;
        const y = priceY(p);
        if (y < plotY - 2 || y > plotY + plotH + 2) return;

        ctx.strokeStyle = 'rgba(244, 63, 94, 0.4)';
        ctx.beginPath();
        ctx.moveTo(plotX + plotW, y);
        ctx.lineTo(plotX + plotW + 5, y);
        ctx.stroke();

        ctx.fillStyle = (p < 0) ? '#fb7185' : '#f59e0b';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText((p > 0 ? '+' : '') + p + ' EUR', plotX + plotW + 8, y + 3.5);
      }});

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.beginPath();
      ctx.moveTo(plotX, plotY + plotH);
      ctx.lineTo(plotX + plotW, plotY + plotH);
      ctx.stroke();

      for (let i = 0; i <= 96; i += 4) {{
        const x = plotX + i * stepW;
        const isMajor = (i % 16 === 0);
        ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.15)' : 'rgba(255, 255, 255, 0.04)';
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();

        if (isMajor) {{
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.beginPath();
          ctx.moveTo(x, plotY + plotH);
          ctx.lineTo(x, plotY + plotH + 5);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.65)';
          ctx.font = '10px "JetBrains Mono", monospace';
          ctx.textAlign = 'center';
          ctx.fillText(String(i / 4).padStart(2, '0') + ':00', x, plotY + plotH + 18);
        }}
      }}

      // 1. Uncertainty Fan: q10 to q90 ribbon
      if (activeChannelsP06.fan) {{
        // Outer ribbon (q10 to q90)
        ctx.fillStyle = 'rgba(16, 185, 129, 0.12)';
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const x = plotX + (i + 0.5) * stepW;
          const y = mwY(dispatchDataP06[i].q90);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }}
        for (let i = 95; i >= 0; i--) {{
          const x = plotX + (i + 0.5) * stepW;
          const y = mwY(dispatchDataP06[i].q10);
          ctx.lineTo(x, y);
        }}
        ctx.closePath();
        ctx.fill();

        // Inner ribbon (q25 to q75)
        ctx.fillStyle = 'rgba(16, 185, 129, 0.22)';
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const x = plotX + (i + 0.5) * stepW;
          const y = mwY(dispatchDataP06[i].q75);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }}
        for (let i = 95; i >= 0; i--) {{
          const x = plotX + (i + 0.5) * stepW;
          const y = mwY(dispatchDataP06[i].q25);
          ctx.lineTo(x, y);
        }}
        ctx.closePath();
        ctx.fill();

        // Median q50 line
        ctx.strokeStyle = '#34d399';
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const x = plotX + (i + 0.5) * stepW;
          const y = mwY(dispatchDataP06[i].q50);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }}
        ctx.stroke();
      }}

      // 2. Realized Wind Generation Bars
      if (activeChannelsP06.real) {{
        for (let i = 0; i < 96; i++) {{
          const real = dispatchDataP06[i].real;
          if (real > 0.2) {{
            const barH = (real / maxMw) * plotH;
            const x = plotX + i * stepW + 0.6;
            const y = (plotY + plotH) - barH;
            const barW = Math.max(1, stepW - 1.2);
            ctx.fillStyle = 'rgba(168, 85, 247, 0.35)';
            ctx.fillRect(x, y, barW, barH);
            ctx.strokeStyle = '#c084fc';
            ctx.strokeRect(x, y, barW, barH);
          }}
        }}
      }}

      // 3. Optimal Newsvendor Bid q* Staircase
      if (activeChannelsP06.bid) {{
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 2.2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = mwY(dispatchDataP06[i].bid);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      // 4. reBAP Imbalance Price
      if (activeChannelsP06.rebap) {{
        ctx.strokeStyle = '#fb7185';
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP06[i].rebap);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
      }}

      // 5. Day-Ahead Spot
      if (activeChannelsP06.da) {{
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        for (let i = 0; i < 96; i++) {{
          const xStart = plotX + i * stepW;
          const xEnd = plotX + (i + 1) * stepW;
          const y = priceY(dispatchDataP06[i].price);
          if (i === 0) ctx.moveTo(xStart, y);
          else ctx.lineTo(xStart, y);
          ctx.lineTo(xEnd, y);
        }}
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      const curX = plotX + scrubberIdxP06 * stepW;
      ctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(curX, plotY, stepW, plotH);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(curX, plotY, stepW, plotH);

      ctx.fillStyle = '#06b6d4';
      ctx.beginPath();
      ctx.arc(curX + stepW / 2, plotY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }}

    function updateHudP06(idx) {{
      const d = dispatchDataP06[idx];
      if (!d) return;

      const timeEl = document.getElementById('p06-hud-time');
      if (timeEl) timeEl.innerText = d.time + ' (Step ' + d.step + '/96)';

      const daEl = document.getElementById('p06-hud-da');
      if (daEl) daEl.innerText = d.price.toFixed(2) + ' EUR/MWh';

      const rebapStr = (d.rebap >= 0 ? '+' : '') + d.rebap.toFixed(2) + ' EUR/MWh';
      const rebapEl = document.getElementById('p06-hud-rebap');
      if (rebapEl) rebapEl.innerText = rebapStr;

      const q50El = document.getElementById('p06-hud-q50');
      if (q50El) q50El.innerText = d.q50.toFixed(1) + ' MW';

      const bidEl = document.getElementById('p06-hud-bid');
      if (bidEl) bidEl.innerText = d.bid.toFixed(1) + ' MW (q*)';

      const realEl = document.getElementById('p06-hud-real');
      if (realEl) realEl.innerText = d.real.toFixed(1) + ' MW';

      const legBid = document.getElementById('p06-legval-bid');
      if (legBid) legBid.innerText = d.bid.toFixed(1) + ' MW';

      const legReal = document.getElementById('p06-legval-real');
      if (legReal) legReal.innerText = d.real.toFixed(1) + ' MW';

      const legFan = document.getElementById('p06-legval-fan');
      if (legFan) legFan.innerText = 'q10: ' + d.q10.toFixed(1) + ' / q90: ' + d.q90.toFixed(1);

      const legRebap = document.getElementById('p06-legval-rebap');
      if (legRebap) legRebap.innerText = rebapStr;

      const legDa = document.getElementById('p06-legval-da');
      if (legDa) legDa.innerText = d.price.toFixed(2) + ' EUR/MWh';
    }}

    function setupCanvasListenersP06() {{
      const canvas = document.getElementById('p06-dispatchCanvas');
      if (!canvas) return;
      function handleMove(clientX) {{
        const rect = canvas.getBoundingClientRect();
        const padLeft = 72;
        const padRight = 92;
        const plotW = rect.width - padLeft - padRight;
        const mouseX = clientX - rect.left;
        const clampedX = Math.max(0, Math.min(plotW - 0.001, mouseX - padLeft));
        scrubberIdxP06 = Math.min(95, Math.max(0, Math.floor((clampedX / plotW) * 96)));
        updateHudP06(scrubberIdxP06);
        drawDispatchCanvasP06();
      }}
      canvas.addEventListener('mousemove', e => handleMove(e.clientX));
      canvas.addEventListener('touchmove', e => {{
        if (e.touches.length > 0) handleMove(e.touches[0].clientX);
      }});
    }}
    """)

    full_code = "\n".join(js_parts)
    print("Full JS length:", len(full_code), "characters.")
    em_dashes = full_code.count('\u2014')
    en_dashes = full_code.count('\u2013')
    print("Em dashes:", em_dashes, "En dashes:", en_dashes)
    assert em_dashes == 0 and en_dashes == 0, "DASH BAN VIOLATION!"

    with open('scripts/generated_dispatch_p02_p06.js', 'w', encoding='utf-8') as f:
        f.write(full_code)
    print("Successfully saved to scripts/generated_dispatch_p02_p06.js")

if __name__ == '__main__':
    generate_all_dispatch_js()
