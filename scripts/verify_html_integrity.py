import os
import re
from pathlib import Path

with open('showcase.html', 'r', encoding='utf-8') as f:
    html = f.read()

print("1. Auditing em/en dashes...")
dashes = [(i+1, l) for i, l in enumerate(html.splitlines()) if '—' in l or '–' in l]
if dashes:
    print(f"FAILED: Found {len(dashes)} dash violations:")
    for ln, text in dashes[:5]:
        print(f"  L{ln}: {text}")
else:
    print("PASSED: 0 em/en dashes found.")

print("\n2. Checking image references in PROJECT_VISUALS...")
img_srcs = re.findall(r"src:\s*'([^']+)'", html)
missing_imgs = []
for src in img_srcs:
    p = Path(src)
    if not p.exists():
        missing_imgs.append(src)
if missing_imgs:
    print(f"FAILED: {len(missing_imgs)} images missing on disk:")
    for m in missing_imgs:
        print("  Missing:", m)
else:
    print(f"PASSED: All {len(img_srcs)} visual image assets exist on disk.")

print("\n3. Checking DOM element IDs referenced in JavaScript...")
# Find all document.getElementById('...')
js_ids = set(re.findall(r"document\.getElementById\(['\"]([^'\"]+)['\"]\)", html))
# Also setEl('...')
setel_ids = set(re.findall(r"setEl\(['\"]([^'\"]+)['\"]", html))
all_js_ids = js_ids.union(setel_ids)

# Find all id="..." in HTML
dom_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', html))

missing_dom = []
for jid in sorted(all_js_ids):
    # Skip dynamic template constructions like projId + '-active-img'
    if '+' in jid or '${' in jid:
        continue
    if jid not in dom_ids:
        missing_dom.append(jid)

if missing_dom:
    print(f"FAILED: {len(missing_dom)} DOM elements referenced in JS missing from HTML:")
    for m in missing_dom:
        print("  Missing element ID:", m)
else:
    print(f"PASSED: All {len(all_js_ids)} statically referenced element IDs exist in DOM.")

print("\n4. Checking Day Regime Selector Buttons...")
day_keys = ['winter', 'spring', 'summer', 'autumn', 'extreme_high', 'extreme_low']
missing_day_btns = [f"dbtn-{k}" for k in day_keys if f"dbtn-{k}" not in dom_ids]
if missing_day_btns:
    print("FAILED: Missing day buttons:", missing_day_btns)
else:
    print("PASSED: All 6 seasonal day buttons (dbtn-*) exist in DOM.")

print("\n5. Checking Discrete Legend Cards...")
channels = ['price', 'pv', 'bat', 'soc', 'grid']
missing_leg_cards = [f"leg-{c}" for c in channels if f"leg-{c}" not in dom_ids]
missing_leg_vals = [f"legval-{c}" for c in channels if f"legval-{c}" not in dom_ids]
if missing_leg_cards or missing_leg_vals:
    print("FAILED: Missing legend items:", missing_leg_cards, missing_leg_vals)
else:
    print("PASSED: All 5 discrete legend cards and live value readouts exist in DOM.")

print("\n6. Checking 6 Project Visual Galleries...")
for pid in ['p01', 'p02', 'p03', 'p04', 'p05', 'p06']:
    active_img = f"{pid}-active-img"
    caption = f"{pid}-caption"
    if active_img not in dom_ids or caption not in dom_ids:
        print(f"FAILED: Missing visual elements for {pid}")
    else:
        print(f"PASSED: {pid} has active image ({active_img}) and caption ({caption}).")

print("\nAll HTML and JavaScript structural checks completed cleanly!")
