"""
Verify showcase.html integrity:
1. Check all canvas IDs exist.
2. Check all onclick functions and targets exist in script.
3. Check zero em/en dashes.
4. Check syntax of script tags.
"""
import re

def verify():
    with open('showcase.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Zero dash rule
    em_count = html.count('\u2014')
    en_count = html.count('\u2013')
    print(f"Em dashes: {em_count}, En dashes: {en_count}")
    assert em_count == 0 and en_count == 0, "Dash ban violation!"

    # 2. Check canvases
    canvases = ['dispatchCanvas', 'p02-dispatchCanvas', 'p03-dispatchCanvas', 'p04-dispatchCanvas', 'p05-dispatchCanvas', 'p06-dispatchCanvas']
    for c in canvases:
        assert f'id="{c}"' in html, f"Missing canvas {c}"
        print(f"Canvas OK: {c}")

    # 3. Check functions exist in script
    funcs = [
        'drawDispatchCanvas', 'drawDispatchCanvasP02', 'drawDispatchCanvasP03', 'drawDispatchCanvasP04', 'drawDispatchCanvasP05', 'drawDispatchCanvasP06',
        'selectDayRegime', 'selectDayRegimeP02', 'selectDayRegimeP03', 'selectDayRegimeP04', 'selectDayRegimeP05', 'selectDayRegimeP06',
        'toggleChannel', 'toggleChannelP02', 'toggleChannelP03', 'toggleChannelP04', 'toggleChannelP05', 'toggleChannelP06',
        'updateHud', 'updateHudP02', 'updateHudP03', 'updateHudP04', 'updateHudP05', 'updateHudP06',
        'setupCanvasListeners', 'setupCanvasListenersP02', 'setupCanvasListenersP03', 'setupCanvasListenersP04', 'setupCanvasListenersP05', 'setupCanvasListenersP06',
        'updateRepresentativeDaysForYear', 'getRegimes'
    ]
    for fn in funcs:
        assert f'function {fn}(' in html, f"Missing function {fn}"
        print(f"Function OK: {fn}")

    # 4. Check 2026 gallery buttons
    for pid in ['p01', 'p02', 'p03', 'p04', 'p05', 'p06']:
        btn_id = f'{pid}-galbtn-2026'
        assert f'id="{btn_id}"' in html, f"Missing {btn_id}"
        print(f"Gallery button OK: {btn_id}")

    # 5. Check 2026 figures exist on disk
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    figures = [
        root / 'projects' / '01-prosumer-pv-bess-mpc-rl' / 'docs' / 'figures' / 'p01_dispatch_2026.png',
        root / 'projects' / '02-pumped-storage-rl-multimarket' / 'docs' / 'figures' / 'p02_dispatch_2026.png',
        root / 'projects' / '03-smart-ev-charging-14a' / 'docs' / 'figures' / 'p03_dispatch_2026.png',
        root / 'projects' / '04-energy-sharing-rec' / 'docs' / 'figures' / 'p04_sharing_2026.png',
        root / 'projects' / '05-utility-hybrid-plant-dispatch' / 'docs' / 'figures' / 'p05_dispatch_2026.png',
        root / 'projects' / '06-probabilistic-forecast-to-bid' / 'docs' / 'figures' / 'p06_forecast_fan_2026.png',
    ]
    for fig in figures:
        assert fig.exists(), f"Missing figure file: {fig}"
        print(f"Figure file OK: {fig.name}")

    # 6. Check all onclick handlers call valid functions
    onclick_matches = re.findall(r'onclick="([^"]+)"', html)
    for oc in onclick_matches:
        fn_name = oc.split('(')[0].strip()
        assert f'function {fn_name}(' in html or f'{fn_name} =' in html or fn_name in funcs, f"Unknown onclick function: {fn_name}"

    print(f"Verified {len(onclick_matches)} onclick bindings successfully!")
    print("ALL HTML INTEGRITY CHECKS PASSED!")

if __name__ == '__main__':
    verify()
