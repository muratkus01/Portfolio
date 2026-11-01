# German Electricity Market & Regulatory Primer

**Purpose.** Every project in this portfolio treats the German regulatory framework as part
of the *environment specification*, not as a compliance footnote. This document is the
shared reference: it defines the market products, the legal constraints and the settlement
mechanics the projects model. It also covers products and rules that no project implements
yet: each section names the projects that implement its subject, and marks the main items
that none implements as **not implemented**.

> **Project numbers.** 01 is the prosumer PV and battery project, 02 the utility hybrid plant
> and 03 the forecast-to-bid pipeline. A2, A3 and A4 are the archived pumped-storage,
> EV-charging and energy-sharing projects ([`archive/README.md`](../archive/README.md)): they
> still build and their tests still run, but they are no longer part of the portfolio. Older
> texts use the numbers of the six-project portfolio, in which the hybrid plant was 05 and
> forecast-to-bid 06.

> **Currency of information.** Energy law in Germany changes frequently. Each statement
> below carries the instrument it derives from so it can be re-verified against the current
> consolidated text on [gesetze-im-internet.de](https://www.gesetze-im-internet.de/) or the
> Bundesnetzagentur's decision database. Where a rule was in legislative motion at the time
> of writing, this is stated explicitly. **Nothing here is legal advice.**

---

## 1. Market architecture

### 1.1 Bidding zone and time resolution

Germany and Luxembourg form the **DE-LU bidding zone**. The structural fact that shapes
every project in this portfolio is the **15-minute market time unit (MTU)**:

- **Imbalance settlement** has been 15-minute in Germany for many years - a balancing group
  (*Bilanzkreis*) is settled against its schedule per quarter-hour.
- **Intraday** trading (SIDC continuous, plus the intraday auctions IDA1/IDA2/IDA3) has
  offered quarter-hourly products for years, and the quarter-hourly opening auction is a
  core price signal for renewables and storage.
- **Day-ahead** trading in the single day-ahead coupling (SDAC) moved from a 60-minute to a
  **15-minute MTU** in 2025 under the EU Electricity Balancing / CACM framework.

**Consequence for modelling:** an hourly model is no longer merely a simplification - it
misrepresents the product being traded and systematically hides the intra-hour ramping value
that batteries, pumped storage and controllable loads actually monetise. Every project in
this portfolio therefore uses **Δt = 15 min** as the native resolution.

### 1.2 The sequence of markets a flexible asset faces

```
        D-2 / D-1                D-1 12:00           D-1 15:00 → real time        real time        D+n
   ┌──────────────────┐    ┌────────────────────┐   ┌──────────────────────┐   ┌────────────┐  ┌──────────┐
   │ Balancing        │ →  │ Day-ahead auction  │ → │ Intraday             │ → │ Balancing  │→ │ Imbalance│
   │ capacity auctions│    │ (SDAC, 15-min MTU) │   │ auctions + continuous│   │ energy     │  │ settle-  │
   │ FCR / aFRR / mFRR│    │                    │   │ (SIDC, gate ~5 min)  │   │ activation │  │ ment     │
   └──────────────────┘    └────────────────────┘   └──────────────────────┘   └────────────┘  └──────────┘
        commitment              schedule               schedule correction         TSO call       reBAP
```

A dispatch policy that optimises one of these stages in isolation leaves money on the table
and, worse, can create commitments it cannot honour later. Project 03 models part of this
**sequential, nested** decision problem: a day-ahead position, intraday trades at the ID AEP
index and imbalance settled at reBAP. **Not implemented in any active project:** the
balancing capacity auctions, balancing energy activation and the intraday auctions. The
archived pumped-storage project (A2) co-optimised aFRR capacity with day-ahead energy.

### 1.3 Balancing (Regelleistung) products

Procured jointly by the four German TSOs via the platform at **regelleistung.net**, and
activated through the European platforms **PICASSO** (aFRR) and **MARI** (mFRR).

| Product | German | Full activation | Auction | Remuneration |
|---------|--------|-----------------|---------|--------------|
| FCR | Primärregelleistung (PRL) | 30 s | Daily, 4-hour blocks, symmetric | Capacity price only |
| aFRR | Sekundärregelleistung (SRL) | 5 min | Daily, 4-hour blocks, separate POS/NEG | Capacity **and** energy price |
| mFRR | Minutenreserve (MRL) | 12.5 min | Daily, 4-hour blocks, separate POS/NEG | Capacity **and** energy price |

Key modelling points:
- Capacity and energy are **separately priced**; aFRR/mFRR energy is called from a common
  European merit order, so activation is *stochastic from the bidder's perspective*.
- Offering capacity **removes headroom** from the energy markets - the opportunity cost
  coupling is the entire optimisation problem for storage. The archived pumped-storage
  project (A2) modelled it for aFRR; for the battery of Project 02 it is planned, not built.
- **Prequalification** (*Präqualifikation*) per the TSO Prequalification Conditions is a hard
  gate: minimum bid size, measurement, remote control and proof of availability. A pooled
  bid requires an aggregator's prequalified pool.

**Not implemented in any active project:** FCR, aFRR and mFRR. Project 02 declares aFRR
settings (`MarketConfig.afrr_*`) that no command reads; aFRR for its battery is planned,
not built.

### 1.4 Imbalance settlement and the balancing group

Every MWh is assigned to a **balancing group** (*Bilanzkreis*) under a *Bilanzkreisvertrag*
with the TSO. Deviation between schedule and metered reality is settled at the **reBAP**
(*regelzonenübergreifender einheitlicher Bilanzausgleichsenergiepreis*), a single ex-post
price per quarter-hour, published by the TSOs on `netztransparenz.de`.

reBAP is *signed and volatile* - it can exceed several hundred €/MWh and can also be
negative, meaning an imbalance in the system-helping direction is remunerated. This creates
a genuine sequential decision problem: closing a forecast error via intraday trading costs a
known spread now; leaving it open exposes the portfolio to an unknown reBAP later. Project 03
is built around exactly this trade-off, on real reBAP and ID AEP prices. Project 02 can
settle a deviation from a committed schedule at reBAP, on a synthetic reBAP series, but no
rung commits a schedule, so its imbalance is 0 in every table.

---

## 2. Renewable generation: EEG and direct marketing

### 2.1 Direct marketing and the market premium

Plants above the small-scale thresholds must sell their electricity through **direct
marketing** (*Direktvermarktung*, EEG 2023 §§ 20 ff.) and receive a **market premium**
(*Marktprämie*): the applicable value (*anzulegender Wert*) minus the technology's market
value, and nothing where the market value is higher. Plants commissioned or awarded before
2023 are paid against the monthly market value (*Monatsmarktwert*); plants commissioned and,
if tendered, awarded from 2023 on, against the annual market value (*Jahresmarktwert*)
(EEG 2023 Anlage 1 Nr. 2). The market value is the day-ahead price weighted by the
technology's national feed-in, a national figure that one plant cannot move. The plant is
therefore exposed to the *shape* of its own generation against prices - an incentive to
shift, curtail or store, not merely to maximise kWh.

Project 02 implements both periods, per technology. Its market values are proxies computed
from public generation data, not the values the TSOs publish, and it pays the plant one
blended premium rate per period ([its README](../projects/02-utility-hybrid-plant-dispatch/README.md),
section 0.3).

### 2.2 The negative-price rule

§ 51 EEG removes the market premium during negative day-ahead prices, by a rule that
depends on the plant's vintage: its commissioning date or, for a tendered plant, the bid
date (*Gebotstermin*) of the tender that set its applicable value, where § 100 says so
(§ 100 (1) EEG 2021; § 100 (1) and (46) EEG 2023). A plant tendered in 2020 and
commissioned in 2022 keeps the six-hour rule; one tendered in 2024 and commissioned in 2026
keeps the rule of the vintage from 2023 to 24 February 2025.

Project 02 implements the rule of every vintage, as below. The statutes are those
`hybrid/market.py` cites; the rule names are `MarketConfig.negative_price_rule`.

| Vintage | The premium is removed | Statute | Rule in Project 02 |
|---|---|---|---|
| Before 2016 | never | § 24 (3) Nr. 1 EEG 2014; § 100 (1) EEG 2017 | `none` |
| 2016 to 2020 | for the whole run of negative prices, once it lasts six hours | § 24 (1) EEG 2014 and § 51 (1) EEG 2017, kept by § 100 (2) Nr. 13 EEG 2021 | `six_hour` |
| 2021 to 2022 | for the whole run, once it lasts four hours | § 51 (1) as in force on 31 December 2022, kept by § 100 (1) EEG 2023 | `four_hour` |
| 1 January 2023 to 24 February 2025 | for the whole run, once it lasts a threshold that depends on the calendar year of the prices: four hours in 2023, three in 2024 and 2025, two in 2026, one from 2027 | § 51 (1) as in force on 24 February 2025, kept by § 100 (46) EEG 2023 | `four_hour` to `one_hour`, by year (`market.vintage_rules`) |
| From 25 February 2025 | in every period with a negative price, whatever the run length | § 51 (1) as amended by the *Solarspitzengesetz* (BGBl. 2025 I Nr. 51) | `new_2025` |

**The whole run.** Every rule with a threshold zeroes the applicable value "für den gesamten
Zeitraum" of uninterrupted negative prices: once a run reaches the threshold, every hour of
it loses the premium, not only the hours after the threshold. Runs are counted in calendar
hours. From 1 October 2025, when the day-ahead products are quarter-hours, an hour counts as
negative when the mean of its quarter-hour prices is negative (§ 100 (45) sentence 2
EEG 2023).

**Not implemented in any project:** the § 51a extension of the support period by the
periods without a premium, and the small-plant exemptions of § 51 (2).

**Consequence for modelling:** the revenue function is discontinuous in price and depends on
plant vintage. Curtailment and storage charging become *rational* in negative-price
quarter-hours rather than merely tolerated. Project 02 models the rule of every vintage, and
runs each rule on the same prices as a sensitivity; a rule run on prices from before its
vintage existed is a counterfactual. Project 01 has a two-way switch, no rule or the rule
from 25 February 2025, off by default.

### 2.3 Remote controllability and feed-in management

EEG 2023 § 9 requires technical equipment allowing the network operator to reduce feed-in
remotely and to retrieve the actual feed-in power, above defined size thresholds. New small
PV systems without a smart-meter gateway face a **feed-in limitation** (the 60 %-of-installed-
capacity cap introduced by the 2025 Solarspitzengesetz) until intelligent metering is in
place - a direct incentive for storage sizing.

**Not implemented in any project:** remote control under § 9 and the 60 % feed-in cap.
Project 01 applies no export cap in any result.

### 2.4 Redispatch 2.0

Since October 2021, `§§ 13, 13a, 14, 14c EnWG` extend redispatch obligations to renewable,
storage and CHP plants from **100 kW** (and to any plant remotely controllable by the network
operator). Operators must supply planning data, forecasts and non-usability information, and
are compensated financially. For a hybrid plant or storage asset this means:

- generation schedules are **subject to override** by the network operator;
- forecast quality is a regulatory obligation, not only a commercial optimisation;
- the controller must distinguish *self-chosen* curtailment from *ordered* curtailment
  because the compensation regimes differ.

**Not implemented in any project:** redispatch orders and their compensation. Project 02
carries the channel in code, an export cap below the connection (`market.redispatch_series`,
`plant.dispatch_step`), but no command applies an order, so no result contains redispatch.
Its curtailment split is chosen against forced by the connection, not against ordered.

---

## 3. Behind-the-meter: controllable loads and tariffs

### 3.1 `§14a EnWG` - grid-orientated control of controllable consumption devices

The Bundesnetzagentur determinations effective **1 January 2024** made grid-orientated
control the standard framework for **steuerbare Verbrauchseinrichtungen** (*controllable
consumption devices*) connected in the low-voltage grid: heat pumps, non-public charging
points, air conditioning/cooling, and electricity storage, each above roughly 4.2 kW.

The bargain: the network operator may **dim** these devices in a grid stress situation, but
never below a guaranteed minimum power (**4.2 kW** per device, with the network operator
required to use grid-orientated control only where a concrete constraint exists), and the
customer receives a **reduced network charge** in return, choosing between:

| Module | Mechanism |
|--------|-----------|
| **Module 1** | Flat annual reduction of the network charge (*pauschale Netzentgeltreduzierung*) |
| **Module 2** | Percentage reduction on the energy component (*Arbeitspreis*) of the network charge |
| **Module 3** | Time-variable network charges (*zeitvariable Netzentgelte*), selectable in addition to Module 1, phased in from 2025 |

**Consequence for modelling:** the control problem is *not* the textbook "minimise cost
under a fixed tariff". The site chooses a network-charge module (a slow, annual decision),
then operates under a stochastic dimming signal it does not control, and must remain
feasible when it arrives. This is a constrained, partially observable control problem with
an exogenous safety-critical interrupt - a much better-posed RL problem than the usual
formulation. Project 01 has the module as a setting and the dimming signal as a synthetic
process, because no public data on realised dimming events exists; its safety layer caps
battery charging during an event. The archived EV-charging project (A3) built its depot
problem around both.

### 3.2 `§41a EnWG` - dynamic tariffs

Since **1 January 2025**, all electricity suppliers must offer a **dynamic tariff**
(*dynamischer Stromtarif*) linked to the spot market price. Combined with the mandatory
metering rollout, this makes the household or small commercial site a price-responsive
agent with quarter-hourly granularity. Commercial implementations (aWATTar, Tibber and
others) publish forward-looking hourly/quarter-hourly prices via public APIs, which is what
makes Project 01 tractable on open data.

### 3.3 Metering: MsbG, iMSys and the smart meter gateway

The *Messstellenbetriebsgesetz* governs the rollout of intelligent metering systems
(**iMSys** = modern meter + **Smart-Meter-Gateway**). The 2023 act restarting the
digitalisation of the energy transition (GNDEW) accelerated the rollout and the agenda for
the **CLS channel** (controllable local system) used to steer devices under `§14a EnWG`.

**Consequence for modelling:** the *actuation path* has real latency, limited bandwidth and
a defined command vocabulary. Project 01 assumes control at 15-minute granularity through
this channel rather than continuous-time actuation, as did the archived EV-charging project
(A3). **Not implemented in any project:** OCPP 2.0.1 and EEBUS semantics on the device side;
A3 names them as its target interface, but its code does not implement them.

### 3.4 Network charges and other levies

Retail price composition matters because it defines the *spread* a behind-the-meter asset
arbitrages: energy procurement, network charges (*Netzentgelte*, per DSO and per grid level),
concession fee (*Konzessionsabgabe*), electricity tax (*Stromsteuer*), offshore network levy,
`§19 StromNEV` levy, CHP levy (KWKG) and VAT. For industrial sites, `§19(2) StromNEV`
individual network charges (peak-load-based, *atypische Netznutzung*) create an explicit
peak-shaving incentive, which the archived EV-charging project (A3) modelled as a charge on
the billing period's highest quarter-hour.

Project 01 builds the household's import price from the spot price, a supplier margin, the
network charge's energy component, the levies as one figure and electricity tax, with VAT on
the total (`prosumer.config.TariffConfig`). **Not implemented in any project:** the concession fee, and
network charges per DSO and grid level.

---

## 4. Collective self-supply, Mieterstrom and energy sharing

| Instrument | Legal basis | What it enables |
|-----------|-------------|-----------------|
| **Mieterstrom** | `§21(3) EEG` | Landlord supplies tenants in the same building with on-site PV electricity, with a *Mieterstromzuschlag* surcharge; supplier obligations apply |
| **Gemeinschaftliche Gebäudeversorgung** | `§42b EnWG`, introduced by *Solarpaket I* (2024) | A lighter-weight route than Mieterstrom: on-site generation shared among units in a building without the full supplier obligations |
| **Bürgerenergiegesellschaft** | `§3 Nr. 15 EEG` | Privileged status for citizen energy companies, including tendering exemptions under conditions |
| **Energy sharing** | RED II Art. 22, IEMD Art. 16 (EU); German transposition via EnWG amendments | Members of a renewable energy community share generation across grid connection points |

**Status note.** Building-level collective supply (`§42b EnWG`) is in force. Broader
**energy sharing across the public grid** has been the subject of successive EnWG amendment
drafts; the archived energy-sharing project (A4) was therefore built to be *robust to the
allocation rule*: it models four allocation mechanisms (static key, dynamic proportional,
optimisation-based, market-based) under four supply regimes (individual supply, Mieterstrom,
`§42b EnWG` and energy sharing) and evaluates them on the same physical and economic
substrate, so the work remains valid regardless of which variant is finally enacted. No
active project models collective supply or energy sharing, and no project models the
Bürgerenergiegesellschaft status.

---

## 5. E-mobility specifics

No active project models e-mobility; the archived EV-charging project (A3) did.

- **Ladesäulenverordnung (LSV)** - technical, calibration-law and ad-hoc-payment
  requirements for *public* charging points. Non-public depot and workplace charging is
  outside LSV, which is why A3 focused there.
- **AFIR** (EU 2023/1804) - deployment and payment/transparency requirements for public
  recharging infrastructure along TEN-T.
- **Eichrecht** (calibration law) - billing-relevant measurement must be verifiable; this
  constrains what an optimiser may bill against.
- **THG-Quote** - greenhouse-gas quota revenue attributable to charged electricity, a real
  revenue line for depot operators and therefore a term in A3's objective.
- **`§14a EnWG`** applies to non-public charging points - the dimming interrupt above is a
  first-class part of the depot scheduling problem.

---

## 6. Hydro and pumped storage specifics

- **Double-charging exemption:** pumped storage is exempt from network charges on the
  electricity drawn for pumping under the conditions of `§118(6) EnWG`, subject to defined
  time limits and criteria for new and modernised plants. Whether the exemption applies
  materially changes the arbitrage spread and must be a model parameter, not a constant.
- **System services:** hydro units are natural providers of FCR/aFRR, black-start capability
  and instantaneous reserve; `§13 EnWG` system-stability obligations and the TSOs' contracted
  ancillary services form a revenue stream that competes with energy arbitrage for the same
  MW of headroom.
- **Water-law constraints:** *Wasserrechtliche Genehmigung*, minimum/maximum reservoir levels,
  ramp-rate limits protecting downstream ecology, and - for plants with natural inflow -
  hydrological boundary conditions. These are **hard constraints** and belong in the safety
  layer, never in the reward function (the archived pumped-storage project, A2).

No active project models hydro or pumped storage; A2 modelled the `§118(6)` exemption as a
switch and the reservoir limits in its safety layer.

---

## 7. What this means for methodology

Four design rules follow from the above and are applied consistently across the portfolio:

1. **Δt = 15 min everywhere.** Hourly models are not accepted as the primary configuration.
2. **Hard constraints live in a safety layer, not in the reward.** Water levels (A2), SoC
   limits, grid connection capacity and `§14a` dimming are enforced by projection/action
   masking so that a learned policy cannot trade a penalty against a physical or legal
   violation. No project models thermal comfort bands.
3. **Regulatory switches are model parameters.** Plant vintage, `§14a` module choice,
   `§118(6)` applicability and negative-price rules are configuration, so sensitivity to
   regulatory change is a first-class result rather than an obsolescence risk.
4. **The baseline is a rolling-horizon MPC on the same forecasts.** It is an LP in
   Projects 01 and 02; the archived pumped-storage project (A2) needed a MILP, with a binary
   direction variable. Project 03 climbs a forecast ladder instead. See
   [`03-methodology-benchmark-ladder.md`](03-methodology-benchmark-ladder.md).

---

## 8. Primary sources

| Source | What it gives you |
|--------|-------------------|
| [gesetze-im-internet.de](https://www.gesetze-im-internet.de/) | Consolidated EnWG, EEG, MsbG, StromNEV, StromNZV texts |
| [Bundesnetzagentur](https://www.bundesnetzagentur.de/) | `§14a` determinations, network charge rules, monitoring reports |
| [SMARD.de](https://www.smard.de/) | BNetzA's official market data portal (generation, load, prices, balancing) |
| [netztransparenz.de](https://www.netztransparenz.de/) | reBAP, balancing volumes, EEG plant master data, redispatch data |
| [regelleistung.net](https://www.regelleistung.net/) | Balancing capacity/energy auction results and prequalification rules |
| [ENTSO-E Transparency](https://transparency.entsoe.eu/) | Pan-European generation, load, cross-border, balancing data |
| [Marktstammdatenregister](https://www.marktstammdatenregister.de/) | Every registered generation and storage unit in Germany |
| [Energy-Charts (Fraunhofer ISE)](https://energy-charts.info/) | Curated, well-documented public electricity data + API |

Detailed access notes, licences and known pitfalls: [`02-data-sources.md`](02-data-sources.md).

---

## 9. Regulatory constants in the code

The constants below are the ones the code holds: the symbol it uses, its default value, and
the source its comment cites. Projects are named as at the top. The concession fee, and the
KWKG and offshore levies as separate rates, which an earlier version of this table listed,
are in no project.

| Parameter | Code symbol | Value | Source, as the code cites it | Where |
|---|---|---|---|---|
| VAT | `vat`; `grid_vat` and `shared_vat` in A4 | 19 %; 0 % at A3's commercial depot | UStG § 12 (1); the depot reclaims it (UStG § 15) | 01, A3, A4 |
| Electricity tax | `electricity_tax`; `grid_electricity_tax` in A4 | 2.05 ct/kWh | § 3 StromStG, standard rate, 2024/2025 | 01, A3, A4 |
| Levies (KWKG, offshore network levy, § 19 StromNEV) as one figure | `levies`; `grid_levies` in A4 | 1.5 ct/kWh | 2024/2025 | 01, A3, A4 |
| Network charge, energy component | `network_charge` in 01; `grid_network_charge` in A4; `network_energy` in A3 | 8.5 ct/kWh; 6.0 ct/kWh at A3's depot | DSO and BNetzA averages, 2024/2025 | 01, A3, A4 |
| Feed-in tariff for small PV | `feed_in_tariff` | 7.86 ct/kWh | EEG 2023 § 48 (1) Nr. 1, partial feed-in, February 2025 (A4); a legacy option in 01 | 01, A4 |
| Applicable value, onshore wind | `MarketConfig.anzulegender_wert_wind` | 73.5 EUR/MWh | the BNetzA ceiling of the 2025 onshore tenders (EEG 2023 § 36), as in 2024; not an award | 02 |
| Applicable value, PV | `MarketConfig.anzulegender_wert_pv` | 68.0 EUR/MWh | the BNetzA ceiling of the 2025 ground-mounted solar tenders (§ 37), 73.7 EUR/MWh in 2024; not an award | 02 |
| Direct marketing fee | `MarketConfig.direct_marketing_fee` | 3.0 EUR/MWh | a trader's management fee, 2024/2025 | 02 |
| Negative-price rule | `negative_price_rule` | in 02, by vintage: `none`, `six_hour`, `four_hour`, `three_hour`, `two_hour`, `one_hour`, `new_2025` (section 2.2); in 01, `none` or `new_2025` | § 51 EEG, kept per vintage by § 100; BGBl. 2025 I Nr. 51 | 02, 01 |
| § 14a guaranteed minimum power | `Para14aConfig.p_min_kw` in 01; `Para14aConfig.p_min_total_kw` in A3 | 4.2 kW | BNetzA BK6-22-300 / BK8-22/010-A, in force 1 January 2024 | 01, A3 |
| § 14a Module 1, flat reduction | `module_1_annual_reduction` | 190 EUR/a | the same determination | 01, A3 |
| § 14a Module 2, reduction | `module_2_reduction` | 60 % of the network charge's energy component | the same determination | 01, A3 |
| § 14a Module 3, time-variable factors | `module_3_factors` | 0.4 / 1.0 / 1.9 (low / standard / high) | the same determination | 01, A3 |
| Peak charge | `peak_charge_eur_kw_year` | 120 EUR/kW/a, on the billing period's highest quarter-hour | § 19 (2) StromNEV, 2024/2025 | A3 |
| THG-Quote revenue | `thg_eur_kwh` | 6 ct per kWh charged | 38. BImSchV, 2024 | A3 |
| Pumped-storage network-charge exemption | `para_118_6_exempt` | on | § 118 (6) EnWG | A2 |
| Pump network charge, if the exemption lapses | `network_charge_pump` | 12 EUR/MWh | BNetzA monitoring report, 2024/2025 | A2 |
| Mieterstrom surcharge | `mieterstrom_surcharge` | 2.61 ct/kWh, in the Mieterstrom regime | § 21 (3) / § 48a EEG, 2024/2025 | A4 |
| Internal price of shared energy | `internal_price` | 20 ct/kWh under § 42b EnWG; 24 under Mieterstrom; 18 under energy sharing | an assumption per regime, not a statutory value | A4 |
| Step length | `dt` | 0.25 h | the 15-minute market time unit (section 1.1) | every project |
