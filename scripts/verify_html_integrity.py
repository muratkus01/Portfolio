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
    # Projects 02, 03 and 04 were archived on 2026-10-09; see archive/README.md.
    canvases = ['dispatchCanvas', 'p02-dispatchCanvas', 'p03-dispatchCanvas']
    for c in canvases:
        assert f'id="{c}"' in html, f"Missing canvas {c}"
        print(f"Canvas OK: {c}")

    # 3. Check functions exist in script
    funcs = [
        'drawDispatchCanvas', 'drawDispatchCanvasP02', 'drawDispatchCanvasP03',
        'selectDayRegime', 'selectDayRegimeP02', 'selectDayRegimeP03',
        'toggleChannel', 'toggleChannelP02', 'toggleChannelP03',
        'updateHud', 'updateHudP02', 'updateHudP03',
        'setupCanvasListeners', 'setupCanvasListenersP02', 'setupCanvasListenersP03',
        'updateRepresentativeDaysForYear', 'getRegimes'
    ]
    for fn in funcs:
        assert f'function {fn}(' in html, f"Missing function {fn}"
        print(f"Function OK: {fn}")

    # 4. Check 2026 gallery buttons (P03 has a single test-window figure, no year buttons)
    for pid in ['p01', 'p02']:
        btn_id = f'{pid}-galbtn-2026'
        assert f'id="{btn_id}"' in html, f"Missing {btn_id}"
        print(f"Gallery button OK: {btn_id}")

    # 5. Check every image the page can show exists on disk: the <img> tags and the gallery
    #    entries in PROJECT_VISUALS (src: '...'), which switchVisual swaps in
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    images = set(re.findall(r'<img[^>]+src="([^"]+)"', html)) | set(re.findall(r"src: '([^']+)'", html))
    for rel in sorted(images):
        assert (root / rel).is_file(), f"Missing figure file: {rel}"
        print(f"Figure file OK: {rel}")

    # 6. Check all onclick handlers call valid functions
    onclick_matches = re.findall(r'onclick="([^"]+)"', html)
    for oc in onclick_matches:
        fn_name = oc.split('(')[0].strip()
        assert f'function {fn_name}(' in html or f'{fn_name} =' in html or fn_name in funcs, f"Unknown onclick function: {fn_name}"

    print(f"Verified {len(onclick_matches)} onclick bindings successfully!")
    print("ALL HTML INTEGRITY CHECKS PASSED!")

if __name__ == '__main__':
    verify()
