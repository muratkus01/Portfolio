# Glossary — German ⇄ English Energy & Market Terminology

For readers coming from either side: German energy-sector terms with their English
equivalents and the meaning as used in this portfolio.

## Market and trading

| German | English | Meaning in this portfolio |
|--------|---------|---------------------------|
| Day-Ahead-Auktion | Day-ahead auction | SDAC-coupled auction closing at D-1 12:00 CET; 15-minute MTU |
| Intraday (kontinuierlich) | Continuous intraday | SIDC order book, tradable until shortly before delivery |
| Intraday-Auktion (IDA1/2/3) | Intraday auctions | Discrete pan-European intraday auctions |
| Marktzeiteinheit (MZE) | Market time unit (MTU) | The settlement/product interval — 15 min throughout this work |
| Bilanzkreis | Balancing group | The commercial portfolio in which schedules and metered energy net out |
| Bilanzkreisverantwortlicher (BKV) | Balance responsible party (BRP) | The entity liable for imbalance in a balancing group |
| Ausgleichsenergie | Imbalance energy | Deviation between schedule and reality, settled ex post |
| reBAP | Uniform imbalance price | Single signed €/MWh price per quarter-hour across the German control areas |
| Fahrplan | Schedule | Committed quarter-hourly position |
| Direktvermarktung | Direct marketing | Selling renewable output on the market rather than at a fixed feed-in tariff |
| Marktprämie | Market premium | EEG top-up to the market value |
| Anzulegender Wert | Applicable value | The reference value underpinning the market premium |
| Monatsmarktwert | Monthly market value | Technology-specific monthly average market value |
| Spotmarkt | Spot market | Day-ahead + intraday |

## Balancing and system services

| German | English | Note |
|--------|---------|------|
| Regelleistung | Balancing capacity | Reserved capability, paid as capacity price |
| Regelarbeit | Balancing energy | Activated energy, paid as energy price |
| Primärregelleistung (PRL) | FCR — frequency containment reserve | 30 s, symmetric, capacity price only |
| Sekundärregelleistung (SRL) | aFRR — automatic frequency restoration reserve | 5 min, separate POS/NEG, capacity + energy |
| Minutenreserve (MRL) | mFRR — manual frequency restoration reserve | 12.5 min, capacity + energy |
| Präqualifikation | Prequalification | TSO technical/commercial approval to offer balancing products |
| Systemdienstleistungen | Ancillary services | Frequency, voltage, restoration, operational management |
| Schwarzstartfähigkeit | Black-start capability | Restart without external supply — a hydro strength |
| Momentanreserve | Inertia / instantaneous reserve | Rotating-mass frequency support |
| Redispatch | Redispatch | Network-operator-ordered change of generation/consumption to relieve congestion |
| Einspeisemanagement | Feed-in management | Predecessor regime, now absorbed into Redispatch 2.0 |
| Engpassmanagement | Congestion management | Umbrella term |

## Grid, metering and charges

| German | English | Note |
|--------|---------|------|
| Netzbetreiber (VNB / ÜNB) | DSO / TSO | Distribution / transmission system operator |
| Netzentgelt | Network charge | Per grid level and operator; the main BTM saving lever |
| Netzanschlusspunkt | Grid connection point | The physical/commercial boundary of an asset |
| Steuerbare Verbrauchseinrichtung | Controllable consumption device | `§14a EnWG` — heat pump, wallbox, storage, A/C |
| Netzorientierte Steuerung | Grid-orientated control | The DSO's dimming right under `§14a EnWG` |
| Zeitvariable Netzentgelte | Time-variable network charges | `§14a` Module 3 |
| Messstellenbetreiber (MSB) | Metering point operator | Responsible for meter and gateway |
| Intelligentes Messsystem (iMSys) | Intelligent metering system | Modern meter + smart meter gateway |
| Smart-Meter-Gateway (SMGW) | Smart meter gateway | Secure communication unit; hosts the CLS control channel |
| Atypische Netznutzung | Atypical network use | `§19(2) StromNEV` reduced charges for off-peak-shifted load |
| Baukostenzuschuss | Connection cost contribution | One-off charge for firm connection capacity |
| Eichrecht | Calibration law | Requirement for verifiable billing measurement |

## Generation, storage and community

| German | English | Note |
|--------|---------|------|
| Erneuerbare-Energien-Gesetz (EEG) | Renewable Energy Sources Act | Support framework for renewables |
| Energiewirtschaftsgesetz (EnWG) | Energy Industry Act | Market and grid regulation |
| Pumpspeicherkraftwerk (PSW) | Pumped-storage power plant | Project 02's asset |
| Oberbecken / Unterbecken | Upper / lower reservoir | Storage state variable |
| Fallhöhe | Head | Determines specific energy per m³ |
| Turbinenbetrieb / Pumpbetrieb | Turbine / pump mode | Mutually exclusive operating modes |
| Wirkungsgrad | Efficiency | Round-trip efficiency for storage |
| Eigenverbrauch | Self-consumption | Share of own generation used on site |
| Autarkiegrad | Self-sufficiency | Share of demand covered by own generation |
| Mieterstrom | Tenant electricity | `§21(3) EEG` on-site supply to tenants |
| Gemeinschaftliche Gebäudeversorgung | Collective building supply | `§42b EnWG`, lighter-weight route |
| Bürgerenergiegesellschaft | Citizen energy company | `§3 Nr. 15 EEG` privileged status |
| Erneuerbare-Energie-Gemeinschaft | Renewable energy community (REC) | RED II Art. 22 |
| Ladepunkt / Ladesäule | Charging point / charging station | Public vs. non-public distinction matters legally |
| Lastmanagement | Load management | Site-level power limiting and sharing |
| Wärmepumpe | Heat pump | `§14a` controllable device; thermal storage flexibility |

## Methods

| Term | Meaning here |
|------|--------------|
| MPC | Model predictive control — rolling-horizon optimisation, re-solved each step |
| MILP | Mixed-integer linear programme |
| RL / DRL | (Deep) reinforcement learning |
| PPO / SAC | On-policy / off-policy deep RL algorithms used as the learned controllers |
| MDP / POMDP | (Partially observable) Markov decision process — the formal environment model |
| CRPS | Continuous ranked probability score — probabilistic forecast quality |
| Pinball loss | Quantile loss, per-quantile forecast quality |
| CVaR | Conditional value at risk — tail-risk metric on the result distribution |
| Perfect foresight | Optimisation with exact future knowledge; a ceiling, never a controller |
| Safety layer | Projection/masking that makes hard-constraint violation impossible |
