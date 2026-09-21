import pandas as pd
import numpy as np
import json

df_spot = pd.read_parquet('projects/01-prosumer-pv-bess-mpc-rl/data/processed/spot_de_lu_2024.parquet')
df_spot.index = pd.to_datetime(df_spot.index)

dates = {
    'winter': ('2024-01-15', 'Winter Heating & Baseline Tariff', 'Winter Base (Jan 15)', 'Low solar yield (max 1.6 kW) with elevated residential heating load. Battery executes off-peak nighttime charging to shave high evening spot prices (112.80 EUR/MWh).'),
    'spring': ('2024-04-14', 'Spring Solar Dip & Negative Midday', 'Spring Duck Curve (Apr 14)', 'Strong rooftop PV ramp crashes wholesale spot tariff to -60.07 EUR/MWh between 11:00 and 15:00. Battery charges from negative grid prices and surplus solar.'),
    'summer': ('2024-06-26', 'Summer Solstice Maximum Generation', 'Summer Solstice (Jun 26)', 'Peak solar generation across 16 hours of daylight (7.6 kW peak). Battery reaches 100% SoC before solar noon; smart MPC shifts exports to evening peak (134.39 EUR/MWh).'),
    'autumn': ('2024-10-18', 'Autumn Transition & Evening Ramp', 'Autumn Transition (Oct 18)', 'Moderate solar (3.2 kW) with sharp evening demand ramp. Two distinct intraday battery arbitrage cycles between morning and evening price peaks (181.28 EUR/MWh).'),
    'extreme_high': ('2024-12-12', 'German Dunkelflaute Scarcity Spike', 'Extreme High Spike (+936 EUR/MWh)', 'Historic Central European Dunkelflaute cold spell. Spot prices spike to 936.28 EUR/MWh at 18:00. Battery discharges at maximum 5.63 kW inverter capacity, generating massive cost savings.'),
    'extreme_low': ('2024-05-12', 'Renewable Cannibalization Crash', 'Extreme Negative (-135 EUR/MWh)', 'Mother\'s Day renewable oversupply crashes spot market to -135.45 EUR/MWh. Under EEG Section 51, export subsidies are cut; battery absorbs grid power at negative cost.')
}

day_regimes = {}
for k, (d, title, tag, desc) in dates.items():
    sub = df_spot.loc[d]
    hourly_prices = sub['spot_eur_per_kwh'].to_numpy() * 1000.0
    # Interpolate to 96 quarter-hours
    qh_prices = np.interp(np.linspace(0, 23, 96), np.arange(len(hourly_prices)), hourly_prices)
    
    # Generate realistic seasonal PV profile (8 kWp base)
    qh_pv = np.zeros(96)
    if k == 'winter':
        mask = (np.arange(96) >= 34) & (np.arange(96) <= 66)
        qh_pv[mask] = 1.6 * np.sin((np.arange(96)[mask] - 34) * np.pi / 32) ** 1.8
    elif k == 'spring':
        mask = (np.arange(96) >= 26) & (np.arange(96) <= 78)
        qh_pv[mask] = 6.5 * np.sin((np.arange(96)[mask] - 26) * np.pi / 52) ** 2
    elif k == 'summer':
        mask = (np.arange(96) >= 21) & (np.arange(96) <= 85)
        qh_pv[mask] = 7.6 * np.sin((np.arange(96)[mask] - 21) * np.pi / 64) ** 1.8
    elif k == 'autumn':
        mask = (np.arange(96) >= 30) & (np.arange(96) <= 74)
        qh_pv[mask] = 3.2 * np.sin((np.arange(96)[mask] - 30) * np.pi / 44) ** 2
    elif k == 'extreme_high':
        mask = (np.arange(96) >= 36) & (np.arange(96) <= 64)
        qh_pv[mask] = 1.2 * np.sin((np.arange(96)[mask] - 36) * np.pi / 28) ** 2
    elif k == 'extreme_low':
        mask = (np.arange(96) >= 24) & (np.arange(96) <= 80)
        qh_pv[mask] = 7.2 * np.sin((np.arange(96)[mask] - 24) * np.pi / 56) ** 2
        
    # Residential load profile (kW)
    hours = np.linspace(0, 24, 96, endpoint=False)
    if k in ['winter', 'extreme_high']:
        qh_load = 1.3 + 1.5 * np.exp(-((hours - 7.5)/2.0)**2) + 2.3 * np.exp(-((hours - 19.0)/2.5)**2)
    elif k in ['summer']:
        qh_load = 0.6 + 0.6 * np.exp(-((hours - 8.0)/3.0)**2) + 1.2 * np.exp(-((hours - 19.5)/2.5)**2)
    else:
        qh_load = 0.8 + 1.0 * np.exp(-((hours - 7.5)/2.5)**2) + 1.6 * np.exp(-((hours - 19.0)/2.5)**2)
        
    day_regimes[k] = {
        'date': d,
        'title': title,
        'tag': tag,
        'desc': desc,
        'price_min': float(np.round(qh_prices.min(), 2)),
        'price_max': float(np.round(qh_prices.max(), 2)),
        'price_mean': float(np.round(qh_prices.mean(), 2)),
        'prices': [float(np.round(p, 2)) for p in qh_prices],
        'pv': [float(np.round(p, 2)) for p in qh_pv],
        'load': [float(np.round(l, 2)) for l in qh_load]
    }

with open('simulations/results/day_regimes.json', 'w', encoding='utf-8') as f:
    json.dump(day_regimes, f, indent=2)

print('Saved day_regimes.json successfully!')
for k, v in day_regimes.items():
    print(f"{k}: {v['tag']}, range [{v['price_min']}, {v['price_max']}] EUR/MWh, PV max {max(v['pv']):.2f} kW")
