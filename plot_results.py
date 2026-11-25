"""Run after optimize_tes.py to save result graphs as PNG and PDF."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DIRECTORY = Path(__file__).resolve().parent
GRAPHS = DIRECTORY / "graphs"
GRAPHS.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.18,
    "axes.axisbelow": True,
    "lines.linewidth": 1.8,
})
BLUE, ORANGE = "#2878B5", "#DA8128"


def read_results(filename):
    with (DIRECTORY / "results" / filename).open(encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def values(rows, column):
    return [float(row[column]) for row in rows]


def save(fig, filename):
    fig.savefig(GRAPHS / f"{filename}.png", dpi=200)
    fig.savefig(GRAPHS / f"{filename}.pdf")
    plt.close(fig)


tes = read_results("tes_results.csv")
baseline = read_results("baseline_results.csv")
bess = read_results("bess_results.csv")
comparison = read_results("comparison_results.csv")
hours = values(tes, "Hour")
period_hours = float(comparison[0]["Period_hours"])
dt = period_hours / len(hours)
boundaries = [i * dt for i in range(len(hours) + 1)]

prices = values(tes, "Electricity_price_with_surcharge_EUR_MWh")

# Cooling balance above; storage state and electricity price below.
fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
for column, label, color in [
    ("Cooling_demand_kW", "Total cooling demand", "#333333"),
    ("Free_cooling_kW", "Free cooling", "#279579"),
    ("Residual_demand_kW", "Demand after free cooling (baseline / BESS chiller)", ORANGE),
    ("Chiller_kW", "TES-case chiller production", BLUE),
]:
    axes[0].stairs(values(tes, column), boundaries, baseline=None, color=color, label=label)
axes[0].set(title="Cooling demand, free cooling and chiller production", ylabel="Thermal power [kW]", ylim=(0, None))
axes[0].legend(ncols=2, loc="lower left", bbox_to_anchor=(0, 1.01), fontsize=8, borderaxespad=0)
axes[0].set_title(axes[0].get_title(), pad=36)

for rows, label, color in [(tes, "TES", BLUE), (bess, "BESS", ORANGE)]:
    case = next(row for row in comparison if row["Case"] == label)
    soc = [float(case["Initial_SoC_percent"])] + values(rows, "Final_SoC_percent")
    axes[1].plot(boundaries, soc, color=color, label=f"{label} SoC")
axes[1].set(title="Storage state of charge and electricity price", ylabel="SoC [%]",
            ylim=(0, 105), xlabel="Elapsed time [h]", xlim=(0, period_hours))
price_ax = axes[1].twinx()
price_ax.stairs(prices, boundaries, baseline=None, color="#984A8B", linestyle="--",
                linewidth=1.3, alpha=0.7, label="Electricity price incl. surcharge")
price_ax.set_ylabel("Electricity price [EUR/MWh]", color="#984A8B")
price_ax.tick_params(axis="y", colors="#984A8B")
price_ax.grid(False)
handles, labels = axes[1].get_legend_handles_labels()
price_handles, price_labels = price_ax.get_legend_handles_labels()
axes[1].legend(handles + price_handles, labels + price_labels, ncols=3, loc="lower left",
               bbox_to_anchor=(0, 1.01), fontsize=8, borderaxespad=0)
axes[1].set_title(axes[1].get_title(), pad=25)
save(fig, "01_operation_overview")

# Compare peak grid demand against the baseline.
fig, ax = plt.subplots(figsize=(12, 4.5), layout="constrained")
baseline_peak = max(values(baseline, "Grid_import_kW"))
for rows, name, color in [(baseline, "Baseline", "#777777"), (tes, "TES", BLUE), (bess, "BESS", ORANGE)]:
    power = values(rows, "Grid_import_kW")
    peak = max(power)
    label = f"{name}: {peak:.1f} kW peak"
    if name != "Baseline":
        label += f" ({100 * (1 - peak / baseline_peak):.1f}% reduction)"
    ax.stairs(power, boundaries, baseline=None, color=color,
              linestyle="--" if name == "BESS" else "-", label=label)
ax.set(title="Peak shaving: grid import compared with baseline", xlabel="Elapsed time [h]",
       ylabel="Electrical power [kW]", xlim=(0, period_hours), ylim=(0, baseline_peak * 1.08))
ax.legend(ncols=3, loc="lower left", bbox_to_anchor=(0, 1.01), fontsize=9, borderaxespad=0)
ax.set_title(ax.get_title(), pad=25)
save(fig, "03_grid_import_comparison")

# Annualized costs use the repeated 143-hour profile; capital costs include CRF.
fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), layout="constrained")
cases = [row["Case"] for row in comparison]
bottom = [0.0] * len(cases)
components = [
    ("Annual_electricity_cost_EUR", "Electricity", BLUE),
    ("Annual_chiller_capital_cost_EUR", "Chiller capital", "#8BB5D5"),
    ("Annual_chiller_maintenance_EUR", "Chiller maintenance", "#727272"),
    ("Annual_storage_capital_cost_EUR", "Storage capital", ORANGE),
    ("Annual_storage_maintenance_EUR", "Storage maintenance", "#EABF89"),
]
for column, label, color in components:
    costs = [v / 1000 for v in values(comparison, column)]
    axes[0].bar(cases, costs, bottom=bottom, color=color, label=label, width=0.6)
    bottom = [a + b for a, b in zip(bottom, costs)]
for i, total in enumerate(bottom):
    axes[0].annotate(f"{total:.1f}", (i, total), xytext=(0, 5), textcoords="offset points", ha="center")
axes[0].set(title="Equivalent annual cost breakdown", ylabel="Annual cost [thousand EUR/year]", ylim=(0, max(bottom) * 1.18))
axes[0].legend(loc="upper left", fontsize=8)
lcoc = [v * 1000 for v in values(comparison, "LCOC_EUR_per_kWh_cooling")]
bars = axes[1].bar(cases, lcoc, color=["#727272", BLUE, ORANGE], width=0.6)
axes[1].bar_label(bars, fmt="%.2f", padding=5)
axes[1].set(title="Levelized cost of cooling", ylabel="LCOC [EUR/MWh cooling]", ylim=(0, max(lcoc) * 1.18))
fig.supxlabel("Repeated-period annual estimate; common free-cooling investment excluded", fontsize=9)
save(fig, "02_cost_comparison")
