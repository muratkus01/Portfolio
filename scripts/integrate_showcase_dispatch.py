"""
Integrate P02-P06 interactive 24-hour canvas dispatch engines into showcase.html.
Ensures zero em/en dashes and strict synchronization.
"""
import re

def integrate():
    with open('showcase.html', 'r', encoding='utf-8') as f:
        html = f.read()

    with open('scripts/generated_dispatch_p02_p06.js', 'r', encoding='utf-8') as f:
        generated_js = f.read()

    # 1. Update padLeft/padRight/padTop/padBottom in P01 drawDispatchCanvas
    p01_old_pads = """      const padLeft = 65;
      const padRight = 85;
      const padTop = 32;
      const padBottom = 32;"""
    p01_new_pads = """      const padLeft = 72;
      const padRight = 92;
      const padTop = 36;
      const padBottom = 34;"""
    assert p01_old_pads in html, "Could not find P01 pad definitions"
    html = html.replace(p01_old_pads, p01_new_pads, 1)

    # 2. Update padLeft/padRight in P01 setupCanvasListeners
    old_listener_pads = """        const padLeft = 65;
        const padRight = 85;"""
    new_listener_pads = """        const padLeft = 72;
        const padRight = 92;"""
    assert old_listener_pads in html, "Could not find P01 listener pads"
    html = html.replace(old_listener_pads, new_listener_pads, 1)

    # 3. Update selectProject to redraw active canvas on tab switch
    old_select_proj = """          if (smoothScroll) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        }
      }
    }"""
    new_select_proj = """          if (smoothScroll) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        }
      }

      // Redraw canvas for active project now that display is block
      setTimeout(() => {
        if (projId === 'p01' || projId === 'all') drawDispatchCanvas();
        if (projId === 'p02' || projId === 'all') drawDispatchCanvasP02();
        if (projId === 'p03' || projId === 'all') drawDispatchCanvasP03();
        if (projId === 'p04' || projId === 'all') drawDispatchCanvasP04();
        if (projId === 'p05' || projId === 'all') drawDispatchCanvasP05();
        if (projId === 'p06' || projId === 'all') drawDispatchCanvasP06();
      }, 50);
    }"""
    assert old_select_proj in html, "Could not find selectProject ending"
    html = html.replace(old_select_proj, new_select_proj, 1)

    # 4. Insert generated JS right after setupCanvasListeners()
    anchor = "      window.addEventListener('resize', drawDispatchCanvas);\n    }"
    assert anchor in html, "Could not find setupCanvasListeners anchor"
    replacement = anchor + "\n\n" + generated_js
    html = html.replace(anchor, replacement, 1)

    # 5. Update simP02 to trigger dispatch recomputation and redraw
    old_simp02_end = """      // B2 capture %
      const b2Cap = Math.min(96.5, Math.max(75.0, 88.0 + 4.0 * (res / 2400.0) - 2.0 * (wear / 150.0)));
      document.getElementById('p02-res-b2cap').innerText = b2Cap.toFixed(1) + '% Capture';
    }"""
    new_simp02_end = """      // B2 capture %
      const b2Cap = Math.min(96.5, Math.max(75.0, 88.0 + 4.0 * (res / 2400.0) - 2.0 * (wear / 150.0)));
      document.getElementById('p02-res-b2cap').innerText = b2Cap.toFixed(1) + '% Capture';

      // Update 24h Reversible Multi-Market Dispatch Canvas
      recomputeDispatchP02(turb, pump, res, wear);
      drawDispatchCanvasP02();
      updateHudP02(scrubberIdxP02);
    }"""
    assert old_simp02_end in html, "Could not find simP02 ending"
    html = html.replace(old_simp02_end, new_simp02_end, 1)

    # 6. Update simP03 to trigger dispatch recomputation and redraw
    old_simp03_end = """      // §14a net rebate
      const rebate = 1480 * (sessions / 48.0);
      document.getElementById('p03-res-rebate').innerText = Math.round(rebate).toLocaleString() + ' EUR/yr';
    }"""
    new_simp03_end = """      // §14a net rebate
      const rebate = 1480 * (sessions / 48.0);
      document.getElementById('p03-res-rebate').innerText = Math.round(rebate).toLocaleString() + ' EUR/yr';

      // Update 24h Fleet Charging and §14a Dimming Canvas
      recomputeDispatchP03(dim, sessions, buffer);
      drawDispatchCanvasP03();
      updateHudP03(scrubberIdxP03);
    }"""
    assert old_simp03_end in html, "Could not find simP03 ending"
    html = html.replace(old_simp03_end, new_simp03_end, 1)

    # 7. Update simP04 to trigger dispatch recomputation and redraw
    old_simp04_end = """      const savings = Math.max(0, 18.4 * (1.0 - charge / breakeven) * Math.pow(pv / 65.0, 0.4));
      document.getElementById('p04-res-savings').innerText = savings.toFixed(1) + '% Tariff Cut';
    }"""
    new_simp04_end = """      const savings = Math.max(0, 18.4 * (1.0 - charge / breakeven) * Math.pow(pv / 65.0, 0.4));
      document.getElementById('p04-res-savings').innerText = savings.toFixed(1) + '% Tariff Cut';

      // Update 24h REC P2P Sharing Canvas
      recomputeDispatchP04(charge, members, pv);
      drawDispatchCanvasP04();
      updateHudP04(scrubberIdxP04);
    }"""
    assert old_simp04_end in html, "Could not find simP04 ending"
    html = html.replace(old_simp04_end, new_simp04_end, 1)

    # 8. Update simP05 to trigger dispatch recomputation and redraw
    old_simp05_end = """      // EEG §51 avoidance
      const avoid = 34200 * (totalGen / 80.0) * (1.0 + (bess / 20.0) * 0.3);
      document.getElementById('p05-res-avoid').innerText = Math.round(avoid).toLocaleString() + ' EUR/yr';
    }"""
    new_simp05_end = """      // EEG §51 avoidance
      const avoid = 34200 * (totalGen / 80.0) * (1.0 + (bess / 20.0) * 0.3);
      document.getElementById('p05-res-avoid').innerText = Math.round(avoid).toLocaleString() + ' EUR/yr';

      // Update 24h Hybrid Over-Planting and EEG §51 Canvas
      recomputeDispatchP05(poc, pv, bess);
      drawDispatchCanvasP05();
      updateHudP05(scrubberIdxP05);
    }"""
    assert old_simp05_end in html, "Could not find simP05 ending"
    html = html.replace(old_simp05_end, new_simp05_end, 1)

    # 9. Update simP06 to trigger dispatch recomputation and redraw
    old_simp06_end = """      // Risk reduction
      const riskRed = -(44.0 + 15.0 * (0.50 - risk));
      document.getElementById('p06-res-riskred').innerText = riskRed.toFixed(1) + '% Vol';
    }"""
    new_simp06_end = """      // Risk reduction
      const riskRed = -(44.0 + 15.0 * (0.50 - risk));
      document.getElementById('p06-res-riskred').innerText = riskRed.toFixed(1) + '% Vol';

      // Update 24h Forecast Fan and reBAP Imbalance Canvas
      recomputeDispatchP06(risk, mae, spread);
      drawDispatchCanvasP06();
      updateHudP06(scrubberIdxP06);
    }"""
    assert old_simp06_end in html, "Could not find simP06 ending"
    html = html.replace(old_simp06_end, new_simp06_end, 1)

    # 10. Update DOMContentLoaded and resize event listener
    old_dom_loaded = """    window.addEventListener('DOMContentLoaded', () => {
      selectProject('p01', false);
      setupCanvasListeners();
      updateHud(scrubberIdx);
      // Initialize all 6 project simulators with defaults
      simP01();
      simP02();
      simP03();
      simP04();
      simP05();
      simP06();
    });"""

    new_dom_loaded = """    function redrawAllCanvases() {
      drawDispatchCanvas();
      drawDispatchCanvasP02();
      drawDispatchCanvasP03();
      drawDispatchCanvasP04();
      drawDispatchCanvasP05();
      drawDispatchCanvasP06();
    }
    window.addEventListener('resize', redrawAllCanvases);

    window.addEventListener('DOMContentLoaded', () => {
      selectProject('p01', false);
      setupCanvasListeners();
      setupCanvasListenersP02();
      setupCanvasListenersP03();
      setupCanvasListenersP04();
      setupCanvasListenersP05();
      setupCanvasListenersP06();

      // Initialize all 6 project simulators with defaults
      simP01();
      simP02();
      simP03();
      simP04();
      simP05();
      simP06();

      updateHud(scrubberIdx);
      updateHudP02(scrubberIdxP02);
      updateHudP03(scrubberIdxP03);
      updateHudP04(scrubberIdxP04);
      updateHudP05(scrubberIdxP05);
      updateHudP06(scrubberIdxP06);
    });"""

    assert old_dom_loaded in html, "Could not find DOMContentLoaded block"
    html = html.replace(old_dom_loaded, new_dom_loaded, 1)

    # Verify Dash Ban
    em_dashes = html.count('\u2014')
    en_dashes = html.count('\u2013')
    print("Em dashes in modified html:", em_dashes, "En dashes:", en_dashes)
    assert em_dashes == 0 and en_dashes == 0, f"Dash ban violated: {em_dashes} em, {en_dashes} en"

    with open('showcase.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Successfully integrated P02-P06 canvas engines into showcase.html!")

if __name__ == '__main__':
    integrate()
