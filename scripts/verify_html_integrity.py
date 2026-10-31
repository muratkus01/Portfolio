"""
Verify showcase.html integrity:
1. Check zero em/en dashes.
2. Check the simulator canvases and their functions exist.
3. Check the per-year gallery buttons and every image the page can show.
4. Check all onclick handlers call functions the page defines.
5. Check every GRID_PARAMS.p01 combination resolves to a SCENARIO_CUBES.p01 key, built the
   way simP01 builds it, for both sweep years. The page must declare simP01 exactly once.
6. Check that every inline script parses the way a browser parses it: a classic script with
   node's vm.Script, a module with `node --check` (skipped, with a note, when node is not on
   PATH).

Usage: python scripts/verify_html_integrity.py [page.html]
The page defaults to showcase.html in the repository root. Image paths are resolved against
the repository root, where showcase.html lives. A failed check raises AssertionError.
"""
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The lines of simP01 that build a cube key. Check 5 mirrors them in Python, so it refuses a
# page whose simP01 builds the key differently instead of passing on a stale copy.
P01_KEY_LINES = (
    "const bessStr = (bess === 9.37) ? '9.37' : bess.toFixed(1);",
    "const invStr = Number.isInteger(inv) ? inv.toFixed(1) : String(inv);",
    "const cubeKey = `${pv.toFixed(1)}|${bessStr}|${invStr}|${yr}`;",
)
P01_YEAR_LINE = re.compile(r"const yr = \(currentGlobalYear === '(\d{4})'\) \? '(\d{4})' : '(\d{4})';")

# Check 6 parses a classic script as a browser does (the Script goal). `node --check` is laxer
# for a .js file: it wraps the file as a CommonJS module, so a top-level `return` passes, and
# recent node retries a failed parse as an ES module, so a top-level `await` passes too. A
# browser rejects both in a classic <script>.
CLASSIC_SCRIPT_PARSER = (
    "const vm = require('vm'), fs = require('fs'), file = process.argv[1];"
    "try { new vm.Script(fs.readFileSync(file, 'utf8'), { filename: file }); }"
    "catch (e) { console.error(String(e.stack).split('\\n    at ')[0]); process.exit(1); }"
)


def check_canvases_and_functions(html):
    """Checks 2: the simulator canvases and their functions; returns the function names."""
    # Projects 02, 03 and 04 were archived on 2026-10-09; see archive/README.md.
    # The current P03 has no simulator canvas: its scenario grid swept synthetic forecast noise
    # and was retired.
    canvases = ['dispatchCanvas', 'p02-dispatchCanvas']
    for c in canvases:
        assert f'id="{c}"' in html, f"Missing canvas {c}"
        print(f"Canvas OK: {c}")

    funcs = [
        'drawDispatchCanvas', 'drawDispatchCanvasP02',
        'selectDayRegime', 'selectDayRegimeP02',
        'toggleChannel', 'toggleChannelP02',
        'updateHud', 'updateHudP02',
        'setupCanvasListeners', 'setupCanvasListenersP02',
        'updateRepresentativeDaysForYear', 'getRegimes'
    ]
    for fn in funcs:
        assert f'function {fn}(' in html, f"Missing function {fn}"
        print(f"Function OK: {fn}")
    return funcs


def check_gallery(html):
    """Check 3: the 2026 gallery buttons, and every image the page can show exists on disk."""
    # P01 and P03 show the same figures for every year
    for pid in ['p02']:
        btn_id = f'{pid}-galbtn-2026'
        assert f'id="{btn_id}"' in html, f"Missing {btn_id}"
        print(f"Gallery button OK: {btn_id}")

    # the <img> tags and the gallery entries in PROJECT_VISUALS (src: '...'), which
    # switchVisual swaps in
    images = set(re.findall(r'<img[^>]+src="([^"]+)"', html)) | set(re.findall(r"src: '([^']+)'", html))
    for rel in sorted(images):
        assert (ROOT / rel).is_file(), f"Missing figure file: {rel}"
        print(f"Figure file OK: {rel}")


def check_onclick(html, funcs):
    """Check 4: every onclick handler calls a function the page defines."""
    onclick_matches = re.findall(r'onclick="([^"]+)"', html)
    for oc in onclick_matches:
        fn_name = oc.split('(')[0].strip()
        assert f'function {fn_name}(' in html or f'{fn_name} =' in html or fn_name in funcs, f"Unknown onclick function: {fn_name}"
    print(f"Verified {len(onclick_matches)} onclick bindings successfully!")


def js_function_body(html, name):
    """Source of `function name() { ... }` up to the closing brace at its own indentation.

    Refuses a page that declares the function more than once: the browser runs the last
    declaration, while the search below would read the first.
    """
    count = len(re.findall(rf'\bfunction\s+{name}\s*\(', html))
    assert count == 1, f"function {name} is declared {count} times; check 5 needs exactly one"
    m = re.search(rf'^( *)function {name}\(\) \{{\n(.*?)^\1\}}', html, flags=re.S | re.M)
    assert m, f"function {name} not found"
    return m.group(2)


def json_const(html, name):
    """Parse `const name = {...};` as JSON; like JavaScript, a repeated key keeps its last value."""
    start = html.index(f'const {name} = ') + len(f'const {name} = ')
    return json.loads(html[start:html.index(';\n', start)])


def grid_params_p01(html):
    """GRID_PARAMS.p01 as lists of floats, e.g. {'pv': [4.0, ...], 'bess': [...], 'inv': [...]}."""
    start = html.index('const GRID_PARAMS = {')
    blocks = re.findall(r'\bp01:\s*\{([^}]*)\}', html[start:html.index('};', start)])
    assert len(blocks) == 1, f"GRID_PARAMS has {len(blocks)} p01 entries, expected 1"
    grid = {name: [float(v) for v in values.split(',')]
            for name, values in re.findall(r'(\w+):\s*\[([^\]]*)\]', blocks[0])}
    assert set(grid) == {'pv', 'bess', 'inv'}, f"Unexpected GRID_PARAMS.p01 fields: {sorted(grid)}"
    return grid


def p01_cube_key(pv, bess, inv, year):
    """The key simP01 builds, e.g. p01_cube_key(8.0, 9.37, 5.63, '2025') == '8.0|9.37|5.63|2025'."""
    bess_str = '9.37' if bess == 9.37 else f'{bess:.1f}'
    # Number.isInteger(inv) ? inv.toFixed(1) : String(inv); String(5.63) is '5.63', like repr
    inv_str = f'{inv:.1f}' if float(inv).is_integer() else repr(inv)
    return f'{pv:.1f}|{bess_str}|{inv_str}|{year}'


def check_p01_cube_keys(html):
    """Check 5: every GRID_PARAMS.p01 combination names a SCENARIO_CUBES.p01 cell."""
    body = js_function_body(html, 'simP01')
    for line in P01_KEY_LINES:
        assert line in body, f"simP01 no longer builds its cube key with: {line} (update check 5)"
    year_line = P01_YEAR_LINE.search(body)
    assert year_line, "simP01 no longer picks the sweep year as check 5 expects (update check 5)"
    years = sorted({year_line.group(2), year_line.group(3)})

    grid = grid_params_p01(html)
    cells = json_const(html, 'SCENARIO_CUBES')['p01']['data']
    keys = [p01_cube_key(*c) for c in itertools.product(grid['pv'], grid['bess'], grid['inv'], years)]
    missing = [k for k in keys if k not in cells]
    assert not missing, (f"{len(missing)} of {len(keys)} GRID_PARAMS.p01 combinations have no "
                         f"SCENARIO_CUBES.p01 key, e.g. {missing[:3]}")
    print(f"P01 cube keys OK: all {len(keys)} GRID_PARAMS.p01 combinations "
          f"({len(grid['pv'])} PV x {len(grid['bess'])} BESS x {len(grid['inv'])} inverter x "
          f"{len(years)} years) resolve to SCENARIO_CUBES.p01 keys")


def script_kind(attrs):
    """'classic' or 'module' for a JavaScript <script>; None for JSON, templates and other data."""
    m = re.search(r'type\s*=\s*["\']([^"\']+)', attrs, flags=re.I)
    if m is None or m.group(1).lower() in ('text/javascript', 'application/javascript'):
        return 'classic'
    return 'module' if m.group(1).lower() == 'module' else None


def check_js_syntax(html):
    """Check 6: every inline script parses as the browser parses it; a skip note when node is absent."""
    node = shutil.which('node')
    if node is None:
        print("JS syntax check SKIPPED: node is not on PATH, so the inline scripts were not parsed.")
        return
    scripts = [(script_kind(m.group(1)), m)
               for m in re.finditer(r'<script\b([^>]*)>(.*?)</script>', html, flags=re.S | re.I)
               if 'src=' not in m.group(1) and m.group(2).strip()]
    scripts = [(kind, m) for kind, m in scripts if kind]
    with tempfile.TemporaryDirectory() as tmp:
        for n, (kind, m) in enumerate(scripts):
            path = Path(tmp) / f"inline_{n}{'.mjs' if kind == 'module' else '.js'}"
            # Pad with the page's earlier lines, so node reports page line numbers.
            path.write_text('\n' * html.count('\n', 0, m.start(2)) + m.group(2), encoding='utf-8')
            # node --check parses an .mjs file as a module, which is right for a module script
            cmd = ([node, '--check', str(path)] if kind == 'module'
                   else [node, '-e', CLASSIC_SCRIPT_PARSER, str(path)])
            run = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            assert run.returncode == 0, f"JS syntax error in inline script {n + 1}:\n{run.stderr.strip()}"
    print(f"JS syntax OK: {len(scripts)} inline script(s) parse as the browser parses them")


def verify(page=ROOT / 'showcase.html'):
    """Run checks 1 to 6 on `page`; raises AssertionError at the first failure."""
    with open(page, 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Zero dash rule
    em_count = html.count('\u2014')
    en_count = html.count('\u2013')
    print(f"Em dashes: {em_count}, En dashes: {en_count}")
    assert em_count == 0 and en_count == 0, "Dash ban violation!"

    funcs = check_canvases_and_functions(html)
    check_gallery(html)
    check_onclick(html, funcs)
    check_p01_cube_keys(html)
    check_js_syntax(html)
    print("ALL HTML INTEGRITY CHECKS PASSED!")


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'showcase.html')
