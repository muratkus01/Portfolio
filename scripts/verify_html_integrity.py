"""
Verify showcase.html integrity:
1. Check zero em/en dashes.
2. Check the simulator canvases and their functions exist.
3. Check every image the page can show.
4. Check all onclick handlers call functions the page defines.
5. Check every GRID_PARAMS.p01 combination resolves to a SCENARIO_CUBES.p01 key, built the
   way simP01 builds it, for both sweep years. The page must declare simP01 exactly once.
6. Check that every inline script parses the way a browser parses it: a classic script with
   node's vm.Script, a module with `node --check` (skipped, with a note, when node is not on
   PATH).
7. Check that Project 02's figures on the page, copied by hand, still equal the committed CSVs
   they cite: its ladder and sizing tables and its tiles, and the hero's market console, which
   shows window_stats.csv.

Usage: python scripts/verify_html_integrity.py [page.html]
The page defaults to showcase.html in the repository root. Image paths are resolved against
the repository root, where showcase.html lives. A failed check raises AssertionError.
"""
import csv
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

# Check 7 reads Project 02's committed reports. The ladder table has one column per window,
# and its rows run B0 to B3, then B3's share, then the premium.
P02_REPORTS = ROOT / 'projects' / '02-utility-hybrid-plant-dispatch' / 'reports'
P02_LADDER_WINDOWS = ('2025-01-01_30d', '2025-07-01_30d')
P02_RUNGS = ('B0 no battery', 'B1 curtailment avoidance', 'B2 perfect foresight', 'B3 rolling MPC')
P02_SIZING_TITLE = "Connection Size: The Battery's Own Value and Curtailing at Low Prices"
P02_LADDER_TITLE = 'Benchmark Ladder (Energy-Charts Data, Synthetic Forecasts)'


def check_canvases_and_functions(html):
    """Checks 2: the simulator canvases and their functions; returns the function names."""
    # Projects 02, 03 and 04 were archived on 2026-10-09; see archive/README.md.
    # The current P02 and P03 have no simulator canvas: their scenario grids were retired, P02's
    # for a sweep on hourly prices read as quarter-hours and extrapolated from 14 days, P03's
    # for sweeping synthetic forecast noise. Both publish CSVs in their reports/ folders.
    canvases = ['dispatchCanvas']
    for c in canvases:
        assert f'id="{c}"' in html, f"Missing canvas {c}"
        print(f"Canvas OK: {c}")

    funcs = [
        'drawDispatchCanvas', 'selectDayRegime', 'toggleChannel', 'updateHud',
        'setupCanvasListeners', 'updateRepresentativeDaysForYear', 'getRegimes'
    ]
    for fn in funcs:
        assert f'function {fn}(' in html, f"Missing function {fn}"
        print(f"Function OK: {fn}")
    return funcs


def check_gallery(html):
    """Check 3: every image the page can show exists on disk."""
    # No project shows per-year figures any more, so there are no year buttons in a gallery to
    # check. Project 02 has no figures: its results are the tables in its reports/ folder.
    # The images are the <img> tags and the gallery entries in PROJECT_VISUALS (src: '...'),
    # which switchVisual swaps in.
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


def p02_csv(name):
    """Rows of a P02 report as dicts of strings, e.g. p02_csv('sizing_2024.csv')[0]['conn_mw'] == '20.0'."""
    with open(P02_REPORTS / name, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def eur(value):
    """A CSV amount as the page prints it, e.g. eur('1094990.3') == '1,094,990 EUR'."""
    return f"{float(value):,.0f} EUR"


def table_cells(html, title):
    """Cell texts, tags stripped, of each body row of the first table after `title`.

    e.g. [['20 MW', '4.00', '1,094,990 EUR', ...], ...] for the sizing table.
    """
    start = html.index(title)
    body = html[html.index('<tbody>', start):html.index('</tbody>', start)]
    return [[re.sub(r'<[^>]+>', '', cell).strip() for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.S)]
            for row in re.findall(r'<tr>(.*?)</tr>', body, flags=re.S)]


def p02_ladder_columns():
    """The ladder table's columns as the page should print them, one list per window.

    Each holds B0 to B3's net revenue, B3's share of the B1-to-B2 gain, and the premium every
    rung is settled on / the one B3 planned on, e.g. [..., '94.1%', '1.48 / 0.00 EUR/MWh'].
    """
    columns = []
    for window in P02_LADDER_WINDOWS:
        rows = {r['rung']: r for r in p02_csv(f'ladder_{window}.csv')}
        b0, b1, b2, b3 = (float(rows[rung]['net_revenue_eur']) for rung in P02_RUNGS)
        mpc = rows['B3 rolling MPC']
        premium = (f"{float(mpc['premium_rate_mean_eur_mwh']):.2f} / "
                   f"{float(mpc['b3_premium_rate_mean_eur_mwh']):.2f} EUR/MWh")
        columns.append([eur(v) for v in (b0, b1, b2, b3)] + [f"{(b3 - b1) / (b2 - b1) * 100:.1f}%", premium])
    return columns


def p02_sizing_rows():
    """The sizing table's rows as the page should print them, keyed by the connection in MW.

    e.g. {20.0: ['4.00', '1,094,990 EUR', '124,552 EUR', '1,298,361 EUR', '138,351 EUR'], ...}:
    overbuild, then the battery's own value and the value of curtailing at low prices in 2024
    and in 2025. Every rung of a connection repeats both values, so any of its rows serves.
    """
    years = [{float(r['conn_mw']): r for r in p02_csv(f'sizing_{y}.csv')} for y in ('2024', '2025')]
    assert years[0].keys() == years[1].keys(), "sizing_2024.csv and sizing_2025.csv sweep different connections"
    return {conn: [f"{float(years[0][conn]['overbuild']):.2f}"]
            + [eur(y[conn][col]) for y in years for col in ('battery_value_eur', 'chosen_curtailment_value_eur')]
            for conn in sorted(years[0])}


def p02_tiles():
    """The four P02 tiles as the page should print them, e.g. ['94.1 / 95.4%', '1.24M / 1.46M EUR', '0 / 0', '0']."""
    stats = {r['window']: r for r in p02_csv('window_stats.csv')}
    as_built = float(stats['2024']['connection_mw'])
    value = [next(float(r['battery_value_eur']) for r in p02_csv(f'sizing_{y}.csv') if float(r['conn_mw']) == as_built)
             for y in ('2024', '2025')]
    shares = [column[4] for column in p02_ladder_columns()]
    violations = sum(int(r['violations']) for f in sorted(P02_REPORTS.glob('*.csv'))
                     for r in p02_csv(f.name) if 'violations' in r)
    return [f"{shares[0][:-1]} / {shares[1]}", f"{value[0] / 1e6:.2f}M / {value[1] / 1e6:.2f}M EUR",
            f"{int(stats['2024']['steps_over_connection'])} / {int(stats['2025']['steps_over_connection'])}",
            str(violations)]


def check_p02_figures(html):
    """Check 7, part 1: P02's ladder and sizing tables and its tiles equal the CSVs."""
    ladder = table_cells(html, P02_LADDER_TITLE)
    for j, want in enumerate(p02_ladder_columns()):
        got = [row[1 + j] for row in ladder]
        assert got == want, f"P02 ladder, {P02_LADDER_WINDOWS[j]}: the page shows {got}, the CSV gives {want}"
    sizing = p02_sizing_rows()
    page = {float(row[0].split()[0]): row[1:] for row in table_cells(html, P02_SIZING_TITLE)}
    assert page == sizing, f"P02 sizing table: the page shows {page}, sizing_2024/2025.csv give {sizing}"
    section = html[html.index('<section id="p02"'):]
    strip = section[section.index('class="metrics-strip"'):]
    tiles = re.findall(r'<div class="mini-val[^"]*">([^<]*)</div>', strip)[:4]
    assert tiles == p02_tiles(), f"P02 tiles: the page shows {tiles}, the CSVs give {p02_tiles()}"
    print(f"P02 figures OK: the ladder table ({len(P02_LADDER_WINDOWS)} windows), the sizing table "
          f"({len(sizing)} connections) and the 4 tiles equal the CSVs in its reports/")


def market_console_figures(year):
    """The hero console's figures for `year`, from window_stats.csv, keyed as in YEAR_CONFIG.

    e.g. {'label': '2025: 365 Days, 35,040 Quarter-Hours', 'spotMean': '89.32 EUR/MWh', ...}.
    """
    row = next(r for r in p02_csv('window_stats.csv') if r['window'] == year)
    # The peak cell's fixed text says 80 MW installed and 0 quarter-hours above a 60 MW connection.
    fixed = (float(row['installed_mw']), float(row['connection_mw']), int(row['steps_over_connection']))
    assert fixed == (80.0, 60.0, 0), f"window_stats.csv, {year}: update the console's peak cell, it says {fixed}"
    return {'label': f"{year}: {float(row['days']):.0f} Days, {float(row['steps']):,.0f} Quarter-Hours",
            'spotMean': f"{float(row['mean_price_eur_mwh']):.2f} EUR/MWh",
            'negQuarterHours': f"{float(row['negative_price_quarter_hours']):,.0f}",
            'peakGen': f"{float(row['peak_generation_mw']):.1f} MW"}


def check_market_console(html):
    """Check 7, part 2: YEAR_CONFIG's market figures, and the console's markup, equal window_stats.csv."""
    config = html[html.index('const YEAR_CONFIG = {'):]
    for year in ('2024', '2025'):
        m = re.search(rf"'{year}': \{{\s*market: \{{(.*?)\}}", config, flags=re.S)
        assert m, f"YEAR_CONFIG['{year}'] has no market entry"
        for key, value in market_console_figures(year).items():
            assert f"{key}: '{value}'" in m.group(1), f"YEAR_CONFIG['{year}'].market.{key} should be '{value}'"
    # 2024's prices were hourly, so its count of negative quarter-hours is four times the hours.
    hours = float(market_console_figures('2024')['negQuarterHours'].replace(',', '')) / 4
    assert f"so {hours:,.0f} hours'" in config, f"YEAR_CONFIG['2024'].market.negSub should give {hours:,.0f} hours"
    # The page opens on 2025, and the markup shows its figures before the script runs.
    shown = market_console_figures('2025')
    for el_id, key in (('market-year-tag', 'label'), ('mkt-spot-mean', 'spotMean'),
                       ('mkt-neg-qh', 'negQuarterHours'), ('mkt-peak-gen', 'peakGen')):
        assert f'id="{el_id}">{shown[key]}<' in html, f"#{el_id} should show 2025's '{shown[key]}'"
    print("Market console OK: its 2024 and 2025 figures equal window_stats.csv")


def verify(page=ROOT / 'showcase.html'):
    """Run checks 1 to 7 on `page`; raises AssertionError at the first failure."""
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
    check_p02_figures(html)
    check_market_console(html)
    print("ALL HTML INTEGRITY CHECKS PASSED!")


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'showcase.html')
