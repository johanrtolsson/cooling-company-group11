# Cooling Company - Group 11

Compare baseline cooling, thermal energy storage (TES), and battery energy storage (BESS).
The TES case optimizes chiller and storage capacity using HiGHS.

## Setup

Use Python 3.13 and run these commands from the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

Edit parameters in `Data inputs/System sizing and dispatch (Electrical load).xlsx`.
Recalculate and save the workbook in Excel before running:

```bash
python optimize_tes.py
python plot_results.py
```

CSV results are saved in `results/`; figures are saved as PNG and PDF in `graphs/`.

## Model assumptions

- The 143 selected hours are treated as a sequential profile repeated over a synthetic year.
- Storage starts and ends at the same state of charge, as specified in Excel.
- Capital costs are annualized using each component's lifetime and the discount rate.
- Free cooling is a fixed input profile. The hydraulic layout and TES temperature range still need to be reconciled.
- Costs exclude the common free-cooling investment, distribution network, and pump electricity.

Archive folders preserve earlier results. Git history preserves earlier code versions.
