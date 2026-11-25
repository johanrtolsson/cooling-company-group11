"""Compare a no-storage baseline, TES and BESS using the original workbook.

Install: python -m pip install highspy openpyxl
Run:     python optimize_tes.py

TES uses a 600 kW chiller. Baseline and BESS use sufficient chiller
capacity to meet the same cooling demand after free cooling.
The 143-hour period repeats throughout a synthetic year. All demand is known.
This is a simplified energy model, not a hydraulic simulation.
"""

import csv
from pathlib import Path

import highspy
from openpyxl import load_workbook


# Files and CSV output.
DIRECTORY = Path(__file__).resolve().parent
EXCEL_FILE = DIRECTORY / "System sizing and dispatch (Electrical load).xlsx"
RESULTS_DIRECTORY = DIRECTORY / "results"
RESULTS_DIRECTORY.mkdir(exist_ok=True)


def write_csv(filename, headers, rows):
    """Save a result table in the results folder; replace the previous run."""
    with (RESULTS_DIRECTORY / filename).open("w", newline="", encoding="utf-8-sig") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(headers)
        writer.writerows(rows)


# Excel inputs: recalculate and save the workbook before running.
workbook = load_workbook(EXCEL_FILE, data_only=True)
sheet = workbook["Load demand and parameters"]
rows = range(2, 145)

# Hourly cooling demand [kW], prices [EUR/MWh] and temperatures [C].
timestamps = [sheet[f"A{r}"].value for r in rows]
demand = [float(sheet[f"B{r}"].value) for r in rows]
spot_prices = [float(sheet[f"E{r}"].value) for r in rows]  # EUR/MWh
return_temperatures = [float(sheet[f"G{r}"].value) for r in rows]
free_cooling = [min(q, float(sheet[f"C{r}"].value)) for r, q in zip(rows, demand)]

# TES limits and efficiencies.
chiller_max = float(sheet["L2"].value)       # 600 kW cooling
tes_power = float(sheet["L5"].value)         # 1000 kW charging/discharging
soc_start = float(sheet["L7"].value) / 100   # 50%
soc_min = float(sheet["L8"].value) / 100     # 3%
soc_max = float(sheet["L10"].value) / 100    # 100%
hourly_retention = float(sheet["L12"].value)
cop = float(sheet["L13"].value)
dt = float(sheet["L14"].value)              # Hours

# TES costs and water properties.
capex_per_m3 = float(sheet["L34"].value)     # EUR/m3
maintenance_per_m3_year = float(sheet["L35"].value)  # EUR/(m3 year)
lifetime = float(sheet["L36"].value)        # Years
# Excel stores the discount rate as a fraction (0.05 = 5%).
interest_rate = float(sheet["L59"].value)
cp = float(sheet["L60"].value)              # kJ/(kg K)
density = float(sheet["L61"].value)         # kg/m3

# Shared operating settings.
cold_temperature_c = float(sheet["L15"].value)
electricity_surcharge = float(sheet["L67"].value)  # EUR/MWh
objective = str(sheet["L58"].value).strip().lower()
if objective not in ("cost", "size"):
    raise ValueError('Excel cell L58 must contain "cost" or "size".')

# BESS limits and conversion efficiencies.
grid_limit = float(sheet["L20"].value)
battery_power_limit = float(sheet["L22"].value)  # Maximum allowed AC power [kW]
battery_soc_start = float(sheet["L24"].value) / 100
battery_soc_min = float(sheet["L25"].value) / 100
battery_soc_max = float(sheet["L27"].value) / 100
charge_efficiency = float(sheet["L29"].value)
discharge_efficiency = float(sheet["L30"].value)
battery_lifetime = float(sheet["L50"].value)
battery_maintenance_fraction = float(sheet["L48"].value) / 100

# BESS and chiller costs from Excel.
battery_energy_cost = float(sheet["L47"].value)  # 126 EUR/kWh
chiller_lifetime = float(sheet["L43"].value)
chiller_cost_per_kw = float(sheet["L39"].value)    # EUR/kW of thermal capacity
chiller_maintenance_per_kw = float(sheet["L41"].value)  # EUR/(kW year)
workbook.close()

electricity_prices = [p + electricity_surcharge for p in spot_prices]


# The chiller and storage cover demand remaining after free cooling.
residual_demand = [q - fc for q, fc in zip(demand, free_cooling)]

n = len(demand)
# Repeat the 143 consecutive hours throughout a synthetic year.
annual_factor = 8760 / (n * dt)             # Approximately 61.26
tes_delta_t = sum(return_temperatures) / n - cold_temperature_c  # About 14.81 K
# Ideal volume from E = density * volume * cp * temperature difference.
m3_per_kwh = 3600 / (density * cp * tes_delta_t)

def annuity(rate, years):
    """Convert a one-time investment into an equivalent annual capital cost."""
    if rate == 0:
        return 1 / years
    return rate * (1 + rate)**years / ((1 + rate)**years - 1)


def solve_storage(name, solver, storage_capacity, storage_cost):
    """Minimize cost, optionally finding and fixing minimum capacity first."""
    if objective == "size":
        solver.minimize(storage_capacity)
        if solver.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            raise RuntimeError(f"HiGHS did not find an optimal {name} size.")
        solver.addConstr(storage_capacity == solver.val(storage_capacity))
    solver.minimize(storage_cost)
    if solver.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        raise RuntimeError(f"HiGHS did not find an optimal {name} solution.")


annuity_factor = annuity(interest_rate, lifetime)
battery_annuity = annuity(interest_rate, battery_lifetime)
chiller_annuity = annuity(interest_rate, chiller_lifetime)

# Scale annual fixed costs to the period; electricity prices stay hourly.
fixed_period_cost_per_kwh = (
    (capex_per_m3 * annuity_factor + maintenance_per_m3_year)
    * m3_per_kwh / annual_factor
)


model = highspy.Highs()
bess_model = highspy.Highs()

model.setOptionValue("output_flag", False)
bess_model.setOptionValue("output_flag", False)

# TES variables: capacity [kWh], cooling output [kW], stored energy [kWh].
capacity = model.addVariable(lb=0, name="TES_kWh")
chiller = [model.addVariable(lb=0, ub=chiller_max, name=f"cooling_{t}")
           for t in range(n)]
energy = [model.addVariable(lb=0, name=f"energy_{t}") for t in range(n + 1)]

# Cyclic SoC: identical initial and final fractions of tank capacity.
model.addConstr(energy[0] == soc_start * capacity)
model.addConstr(energy[n] == soc_start * capacity)

for t in range(n + 1):
    model.addConstr(energy[t] >= soc_min * capacity)
    model.addConstr(energy[t] <= soc_max * capacity)

for t in range(n):
    model.addConstr(chiller[t] / cop <= grid_limit)
    # Positive surplus charges TES; negative surplus discharges TES.
    surplus = chiller[t] - residual_demand[t]
    model.addConstr(surplus <= tes_power)
    model.addConstr(surplus >= -tes_power)
    # Energy balance with standing losses.
    model.addConstr(
        energy[t + 1] == hourly_retention**dt * energy[t] + surplus * dt
    )

# Convert cooling output to electricity using COP and EUR/MWh to EUR/kWh.
electricity_cost = sum(electricity_prices[t] / 1000 * chiller[t] / cop * dt
                       for t in range(n))
period_cost = electricity_cost + fixed_period_cost_per_kwh * capacity

solve_storage("TES", model, capacity, period_cost)

# TES results.
optimal_capacity = model.val(capacity)
chiller_output = [model.val(q) for q in chiller]
stored_energy = [model.val(x) for x in energy]
electricity_kwh = sum(q / cop * dt for q in chiller_output)
electricity_eur = sum(p / 1000 * q / cop * dt
                      for p, q in zip(electricity_prices, chiller_output))
fixed_eur = fixed_period_cost_per_kwh * optimal_capacity
discharged_kwh = sum(max(0, demand_t - q) * dt
                     for demand_t, q in zip(residual_demand, chiller_output))

# Export hourly TES operation.
tes_rows = []
for t in range(n):
    tes_rows.append([t + 1, timestamps[t], demand[t], free_cooling[t],
                     residual_demand[t], spot_prices[t], electricity_prices[t],
                     chiller_output[t], max(0, chiller_output[t] - residual_demand[t]),
                     max(0, residual_demand[t] - chiller_output[t]),
                     stored_energy[t], stored_energy[t + 1],
                     100 * stored_energy[t + 1] / optimal_capacity,
                     chiller_output[t] / cop,
                     electricity_prices[t] / 1000 * chiller_output[t] / cop * dt])
write_csv("tes_results.csv",
          ["Hour", "Timestamp", "Cooling_demand_kW", "Free_cooling_kW",
           "Residual_demand_kW", "Spot_price_EUR_MWh",
           "Electricity_price_with_surcharge_EUR_MWh", "Chiller_kW",
           "TES_charge_kW", "TES_discharge_kW", "Initial_energy_kWh",
           "Final_energy_kWh", "Final_SoC_percent", "Grid_import_kW",
           "Electricity_cost_EUR"], tes_rows)


# Baseline without storage: chiller supplies the entire residual load.
baseline_power = [q / cop for q in residual_demand]
required_chiller = max(residual_demand)
baseline_chiller_capacity = max(chiller_max, required_chiller)
additional_chiller = max(0, required_chiller - chiller_max)

# Include the full chiller investment and maintenance in each case.
baseline_chiller_capex = baseline_chiller_capacity * chiller_cost_per_kw
tes_chiller_capex = chiller_max * chiller_cost_per_kw
baseline_chiller_annual = (
    baseline_chiller_capex * chiller_annuity
    + baseline_chiller_capacity * chiller_maintenance_per_kw
)
tes_chiller_annual = (
    tes_chiller_capex * chiller_annuity
    + chiller_max * chiller_maintenance_per_kw
)
baseline_electricity_eur = sum(
    price / 1000 * power * dt for price, power in zip(electricity_prices, baseline_power)
)
baseline_annual_cost = baseline_electricity_eur * annual_factor + baseline_chiller_annual

existing_chiller_shortfall = sum(max(0, q - chiller_max) * dt for q in residual_demand)

baseline_rows = []
for t in range(n):
    baseline_rows.append([t + 1, timestamps[t], demand[t], free_cooling[t],
                          residual_demand[t], baseline_power[t],
                          electricity_prices[t] / 1000 * baseline_power[t] * dt])
write_csv("baseline_results.csv",
          ["Hour", "Timestamp", "Cooling_demand_kW", "Free_cooling_kW",
           "Chiller_cooling_kW", "Grid_import_kW", "Electricity_cost_EUR"], baseline_rows)


# BESS variables: electrical power [kW] and stored energy [kWh].
battery_capacity = bess_model.addVariable(lb=0, name="battery_kWh")
grid = [bess_model.addVariable(lb=0, ub=grid_limit, name=f"grid_{t}") for t in range(n)]
charge = [bess_model.addVariable(lb=0, name=f"charge_{t}") for t in range(n)]
discharge = [bess_model.addVariable(lb=0, name=f"discharge_{t}") for t in range(n)]
battery_energy = [bess_model.addVariable(lb=0, name=f"battery_energy_{t}")
                  for t in range(n + 1)]

# Prevent simultaneous charging/discharging, including at negative prices.
charging_mode = [bess_model.addVariable(lb=0, ub=1, type=highspy.HighsVarType.kInteger,
                                       name=f"charging_mode_{t}") for t in range(n)]

# Cyclic battery SoC.
bess_model.addConstr(battery_energy[0] == battery_soc_start * battery_capacity)
bess_model.addConstr(battery_energy[n] == battery_soc_start * battery_capacity)
for t in range(n + 1):
    bess_model.addConstr(battery_energy[t] >= battery_soc_min * battery_capacity)
    bess_model.addConstr(battery_energy[t] <= battery_soc_max * battery_capacity)

for t in range(n):
    # AC power balance; grid import cannot be negative.
    bess_model.addConstr(grid[t] + discharge[t] == baseline_power[t] + charge[t])
    bess_model.addConstr(charge[t] <= min(grid_limit, battery_power_limit) * charging_mode[t])
    bess_model.addConstr(discharge[t] <= min(baseline_power[t], battery_power_limit)
                         * (1 - charging_mode[t]))
    # Battery energy balance with charging and discharging losses.
    bess_model.addConstr(
        battery_energy[t + 1] == battery_energy[t]
        + charge_efficiency * charge[t] * dt
        - discharge[t] * dt / discharge_efficiency
    )

# CAPEX per nominal kWh; annual maintenance is a fraction of CAPEX.
battery_capex = battery_energy_cost * battery_capacity
bess_electricity_cost = sum(electricity_prices[t] / 1000 * grid[t] * dt for t in range(n))
bess_period_cost = (
    bess_electricity_cost
    + (battery_annuity + battery_maintenance_fraction) * battery_capex / annual_factor
    + baseline_chiller_annual / annual_factor
)

solve_storage("BESS", bess_model, battery_capacity, bess_period_cost)

# BESS results.
bess_capacity_kwh = bess_model.val(battery_capacity)
grid_output = [bess_model.val(x) for x in grid]
charge_output = [bess_model.val(x) for x in charge]
discharge_output = [bess_model.val(x) for x in discharge]
bess_stored_energy = [bess_model.val(x) for x in battery_energy]
# Required AC power for the optimized dispatch; no separate power cost.
bess_power_kw = max(max(charge_output), max(discharge_output))
bess_capex_eur = battery_energy_cost * bess_capacity_kwh
bess_electricity_eur = sum(p / 1000 * g * dt for p, g in zip(electricity_prices, grid_output))
bess_annual_cost = (
    bess_electricity_eur * annual_factor
    + bess_capex_eur * (battery_annuity + battery_maintenance_fraction)
    + baseline_chiller_annual
)

bess_rows = []
for t in range(n):
    bess_rows.append([t + 1, timestamps[t], baseline_power[t], grid_output[t],
                      charge_output[t], discharge_output[t], bess_stored_energy[t],
                      bess_stored_energy[t + 1], 100 * bess_stored_energy[t + 1] / bess_capacity_kwh,
                      electricity_prices[t] / 1000 * grid_output[t] * dt])
write_csv("bess_results.csv",
          ["Hour", "Timestamp", "Chiller_electricity_kW", "Grid_import_kW",
           "Battery_charge_kW", "Battery_discharge_kW", "Initial_energy_kWh",
           "Final_energy_kWh", "Final_SoC_percent", "Electricity_cost_EUR"], bess_rows)


# Compare full chiller, storage, maintenance and electricity costs.
# Common free-cooling CAPEX is excluded in all cases.
delivered_cooling_kwh = sum(demand) * dt
annual_cooling_kwh = delivered_cooling_kwh * annual_factor
tes_annual_cost = (electricity_eur + fixed_eur) * annual_factor + tes_chiller_annual

# Identical delivered cooling makes minimum cost equivalent to minimum LCOC.
summary = [
    ["Baseline", 0, "no storage", 0, baseline_chiller_capacity,
     max(baseline_power), sum(baseline_power) * dt, baseline_electricity_eur,
     baseline_annual_cost / annual_factor, baseline_annual_cost,
     baseline_annual_cost / annual_cooling_kwh, 0],
    ["TES", optimal_capacity, "thermal", tes_power, chiller_max,
     max(chiller_output) / cop, electricity_kwh, electricity_eur,
     tes_annual_cost / annual_factor, tes_annual_cost,
     tes_annual_cost / annual_cooling_kwh,
     100 * (1 - (max(chiller_output) / cop) / max(baseline_power))],
    ["BESS", bess_capacity_kwh, "electrical", bess_power_kw, baseline_chiller_capacity,
     max(grid_output), sum(grid_output) * dt, bess_electricity_eur,
     bess_annual_cost / annual_factor, bess_annual_cost,
     bess_annual_cost / annual_cooling_kwh,
     100 * (1 - max(grid_output) / max(baseline_power))]
]

# Summary details; empty baseline SoC cells mean no storage.
tes_capex_eur = capex_per_m3 * optimal_capacity * m3_per_kwh
tes_soc = [100 * e / optimal_capacity for e in stored_energy]
bess_soc = [100 * e / bess_capacity_kwh for e in bess_stored_energy]
bess_discharged_kwh = sum(discharge_output) * dt

summary[0] += [
    0, "", "", "", "", 0, additional_chiller, existing_chiller_shortfall,
    0, 0, 0, baseline_chiller_capex, baseline_chiller_capex * chiller_annuity,
    baseline_chiller_capacity * chiller_maintenance_per_kw,
    baseline_electricity_eur * annual_factor
]
summary[1] += [
    optimal_capacity * m3_per_kwh, tes_soc[0], tes_soc[-1], min(tes_soc), max(tes_soc),
    discharged_kwh, 0, "", tes_capex_eur, tes_capex_eur * annuity_factor,
    optimal_capacity * m3_per_kwh * maintenance_per_m3_year,
    tes_chiller_capex, tes_chiller_capex * chiller_annuity,
    chiller_max * chiller_maintenance_per_kw, electricity_eur * annual_factor
]
summary[2] += [
    0, bess_soc[0], bess_soc[-1], min(bess_soc), max(bess_soc), bess_discharged_kwh,
    additional_chiller, "", bess_capex_eur, bess_capex_eur * battery_annuity,
    bess_capex_eur * battery_maintenance_fraction, baseline_chiller_capex,
    baseline_chiller_capex * chiller_annuity,
    baseline_chiller_capacity * chiller_maintenance_per_kw,
    bess_electricity_eur * annual_factor
]

for row in summary:
    row += [objective, n * dt, annual_factor, delivered_cooling_kwh,
            annual_cooling_kwh, electricity_surcharge]

write_csv("comparison_results.csv",
          ["Case", "Storage_capacity_kWh", "Storage_energy_type", "Storage_power_kW",
                     "Chiller_capacity_kW", "Peak_grid_import_kW", "Period_grid_energy_kWh",
                     "Period_electricity_cost_EUR", "Total_period_cost_EUR", "Annual_cost_EUR",
                     "LCOC_EUR_per_kWh_cooling", "Peak_shaving_percent",
                     "Tank_volume_m3", "Initial_SoC_percent", "Final_SoC_percent",
                     "Minimum_SoC_percent", "Maximum_SoC_percent", "Discharged_energy_kWh",
                     "Additional_chiller_capacity_kW", "Existing_chiller_only_shortfall_kWh",
                     "Storage_CAPEX_EUR", "Annual_storage_capital_cost_EUR",
                     "Annual_storage_maintenance_EUR", "Chiller_CAPEX_EUR",
                     "Annual_chiller_capital_cost_EUR", "Annual_chiller_maintenance_EUR",
                     "Annual_electricity_cost_EUR", "Objective", "Period_hours", "Annual_factor",
                     "Delivered_cooling_period_kWh", "Delivered_cooling_annual_kWh",
                     "Electricity_surcharge_EUR_MWh"], summary)

