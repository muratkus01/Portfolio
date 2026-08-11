# German Electricity Market & Regulatory Primer

**Purpose.** Every project in this portfolio treats the German regulatory framework as part
of the *environment specification*, not as a compliance footnote. This document is the
shared reference: it defines the market products, the legal constraints and the settlement
mechanics that the optimisation models and RL environments must reproduce.

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

- **Imbalance settlement** has been 15-minute in Germany for many years — a balancing group
  (*Bilanzkreis*) is settled against its schedule per quarter-hour.
- **Intraday** trading (SIDC continuous, plus the intraday auctions IDA1/IDA2/IDA3) has
  offered quarter-hourly products for years, and the quarter-hourly opening auction is a
  core price signal for renewables and storage.
- **Day-ahead** trading in the single day-ahead coupling (SDAC) moved from a 60-minute to a
  **15-minute MTU** in 2025 under the EU Electricity Balancing / CACM framework.

**Consequence for modelling:** an hourly model is no longer merely a simplification — it
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
and, worse, can create commitments it cannot honour later. Projects 02, 05 and 06 explicitly
model the **sequential, nested** nature of this decision problem.

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
- Offering capacity **removes headroom** from the energy markets — the opportunity cost
  coupling is the entire optimisation problem for storage (Projects 02, 05).
- **Prequalification** (*Präqualifikation*) per the TSO Prequalification Conditions is a hard
  gate: minimum bid size, measurement, remote control and proof of availability. A pooled
  bid requires an aggregator's prequalified pool.

### 1.4 Imbalance settlement and the balancing group

Every MWh is assigned to a **balancing group** (*Bilanzkreis*) under a *Bilanzkreisvertrag*
with the TSO. Deviation between schedule and metered reality is settled at the **reBAP**
(*regelzonenübergreifender einheitlicher Bilanzausgleichsenergiepreis*), a single ex-post
price per quarter-hour, published by the TSOs on `netztransparenz.de`.

reBAP is *signed and volatile* — it can exceed several hundred €/MWh and can also be
negative, meaning an imbalance in the system-helping direction is remunerated. This creates
a genuine sequential decision problem: closing a forecast error via intraday trading costs a
known spread now; leaving it open exposes the portfolio to an unknown reBAP later. Project 06
is built around exactly this trade-off.

---

## 2. Renewable generation: EEG and direct marketing

### 2.1 Direct marketing and the market premium

Plants above the small-scale thresholds must sell their electricity through **direct
marketing** (*Direktvermarktung*, EEG 2023 §§ 20 ff.) and receive a **market premium**
(*Marktprämie*) equal to the difference between the applicable value (*anzulegender Wert*)
and the monthly market value (*Monatsmarktwert*) of the technology. The plant is therefore
exposed to the *shape* of its own generation against prices — an incentive to shift, curtail
or store, not merely to maximise kWh.

### 2.2 The negative-price rule

Under EEG 2023 § 51, market-premium payments are suspended during extended periods of
negative day-ahead prices. The threshold was progressively tightened, and the 2025
*Solarspitzengesetz* (the act addressing PV feed-in peaks) removed remuneration for **new**
plants in **any** quarter-hour with a negative day-ahead price, compensating operators by
extending the remuneration period rather than by paying during those hours.

**Consequence for modelling:** the revenue function is discontinuous in price and depends on
plant vintage. Curtailment and storage charging become *rational* in negative-price
quarter-hours rather than merely tolerated. Projects 01 and 05 model this switch explicitly,
parameterised by commissioning date.

### 2.3 Remote controllability and feed-in management

EEG 2023 § 9 requires technical equipment allowing the network operator to reduce feed-in
remotely and to retrieve the actual feed-in power, above defined size thresholds. New small
PV systems without a smart-meter gateway face a **feed-in limitation** (the 60 %-of-installed-
capacity cap introduced by the 2025 Solarspitzengesetz) until intelligent metering is in
place — a direct, quantifiable incentive for the storage sizing questions in Project 01.

### 2.4 Redispatch 2.0

Since October 2021, `§§ 13, 13a, 14, 14c EnWG` extend redispatch obligations to renewable,
storage and CHP plants from **100 kW** (and to any plant remotely controllable by the network
operator). Operators must supply planning data, forecasts and non-usability information, and
are compensated financially. For a hybrid plant or storage asset this means:

- generation schedules are **subject to override** by the network operator;
- forecast quality is a regulatory obligation, not only a commercial optimisation;
- the controller must distinguish *self-chosen* curtailment from *ordered* curtailment
  because the compensation regimes differ.

Projects 05 and 02 include a redispatch-order channel in the environment for this reason.

---

## 3. Behind-the-meter: controllable loads and tariffs

### 3.1 `§14a EnWG` — grid-orientated control of controllable consumption devices

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

**Consequence for modelling:** in Projects 01 and 03 the control problem is *not* the
textbook "minimise cost under a fixed tariff". The agent chooses a network-charge module
(a slow, annual decision), then operates under a stochastic dimming signal it does not
control, and must remain feasible and comfortable when it arrives. This is a constrained,
partially observable control problem with an exogenous safety-critical interrupt — a much
better-posed RL problem than the usual formulation.

### 3.2 `§41a EnWG` — dynamic tariffs

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
a defined command vocabulary. Projects 01 and 03 assume control at 15-minute granularity
through this channel rather than continuous-time actuation, and Project 03 models
OCPP 2.0.1 / EEBUS semantics on the device side.

### 3.4 Network charges and other levies

Retail price composition matters because it defines the *spread* a behind-the-meter asset
arbitrages: energy procurement, network charges (*Netzentgelte*, per DSO and per grid level),
concession fee (*Konzessionsabgabe*), electricity tax (*Stromsteuer*), offshore network levy,
`§19 StromNEV` levy, CHP levy (KWKG) and VAT. For industrial sites, `§19(2) StromNEV`
individual network charges (peak-load-based, *atypische Netznutzung*) create an explicit
peak-shaving incentive modelled in Project 03.

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
drafts; Project 04 is therefore specified to be *robust to the allocation rule*: it models
several candidate allocation mechanisms (static keys, dynamic proportional, optimisation-
based, market-based) and evaluates them on the same physical and economic substrate, so the
work remains valid regardless of which variant is finally enacted.

---

## 5. E-mobility specifics

- **Ladesäulenverordnung (LSV)** — technical, calibration-law and ad-hoc-payment
  requirements for *public* charging points. Non-public depot and workplace charging is
  outside LSV, which is why Project 03 focuses there.
- **AFIR** (EU 2023/1804) — deployment and payment/transparency requirements for public
  recharging infrastructure along TEN-T.
- **Eichrecht** (calibration law) — billing-relevant measurement must be verifiable; this
  constrains what an optimiser may bill against.
- **THG-Quote** — greenhouse-gas quota revenue attributable to charged electricity, a real
  revenue line for depot operators and therefore a term in Project 03's objective.
- **`§14a EnWG`** applies to non-public charging points — the dimming interrupt above is a
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
  ramp-rate limits protecting downstream ecology, and — for plants with natural inflow —
  hydrological boundary conditions. These are **hard constraints** and belong in the safety
  layer, never in the reward function (Project 02).

---

## 7. What this means for methodology

Four design rules follow from the above and are applied consistently across the portfolio:

1. **Δt = 15 min everywhere.** Hourly models are not accepted as the primary configuration.
2. **Hard constraints live in a safety layer, not in the reward.** Water levels, SoC limits,
   thermal comfort bands, grid connection capacity and `§14a` dimming are enforced by
   projection/action masking so that a learned policy cannot trade a penalty against a
   physical or legal violation.
3. **Regulatory switches are model parameters.** Plant vintage, `§14a` module choice,
   `§118(6)` applicability and negative-price rules are configuration, so sensitivity to
   regulatory change is a first-class result rather than an obsolescence risk.
4. **The baseline is a rolling-horizon MILP-MPC on the same forecasts.** See
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
