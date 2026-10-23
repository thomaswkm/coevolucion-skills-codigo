#!/usr/bin/env python3
"""Plot the Stage 3 descriptive and inferential RQ1/RQ2 results.

IMPORTANT — assignment rule
---------------------------
The Stage 2 guidelines require that the tables and figures of the article be
produced by the students themselves and explicitly forbid generative AI from
producing them. This script is provided as a reusable utility of the replication
package so that *you* can inspect, adapt and own the figures. If you include any
of its output in the article, you are responsible for having authored the figure
design and for complying with the rule. Do not paste a figure you have not
reviewed and adapted.

Usage:
    uv run python scripts/plot_results.py \
        --results results \
        --inference results-inference \
        --output figures-etapa3 \
        --forest --sign --sensitivity

Default outputs (two article candidates, boxplots as in Stage 2):
    fig_rq1_boxplot             RQ1 commits per repository by category.
    fig_rq2_boxplot             RQ2 commits per repository before and after
                                adoption, by category.

Optional outputs:
    fig_effects                 Forest plot of the five primary effects.
    fig_sign_tests              Sign test (D6): positive proportion, exact
                                interval and agreement with Wilcoxon.
    fig_rq1_sensitivity         RQ1 direction under every sensitivity marked as
                                ``computed`` in sensitivity_status.csv.

The sensitivity panels are discovered from sensitivity_status.csv, so new
normalised metrics or sample restrictions (commit share, content clusters, bot
exclusion, ...) are plotted without editing this script. The panel list and
sample sizes are recorded in manifest.json.

The script writes PNG (300 dpi) and PDF versions of each figure, a README.md and
a manifest.json with input checksums and tool versions. It refuses to write into
a non-empty output directory unless --force is passed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter


RQ1_CATEGORIES = ("skill", "production", "test")
RQ2_CATEGORIES = ("production", "test")
RQ1_COMPARISONS = ("skill-production", "skill-test", "production-test")
RQ2_COMPARISONS = ("production_post_minus_pre", "test_post_minus_pre")
CATEGORY_LABELS = {
    "skill": "skills",
    "production": "producción",
    "test": "pruebas",
}
CATEGORY_COLORS = {
    "skill": "#4C72B0",
    "production": "#DD8452",
    "test": "#55A868",
}
COMPARISON_LABELS = {
    ("RQ1", "skill-production"): "producción − skills",
    ("RQ1", "skill-test"): "pruebas − skills",
    ("RQ1", "production-test"): "pruebas − producción",
    ("RQ2", "production_post_minus_pre"): "producción (post − pre)",
    ("RQ2", "test_post_minus_pre"): "pruebas (post − pre)",
}
SENSITIVITY_ORDER = (
    "churn_commits_per_file",
    "churn_churn_per_file",
    "churn_commit_share",
    "skill_content_clusters",
    "bots_excluded",
    "ambiguous_excluded",
    "window_30_days",
    "window_90_days",
)
SENSITIVITY_LABELS = {
    "churn_commits_per_file": "Commits por archivo",
    "churn_churn_per_file": "Churn por archivo",
    "churn_commit_share": "Proporción de commits",
    "skill_content_clusters": "Contenido de skill (D9)",
    "bots_excluded": "Sin bots",
    "ambiguous_excluded": "Sin adopciones ambiguas",
    "window_30_days": "Ventana de 30 días",
    "window_90_days": "Ventana de 90 días",
}
PERIOD_COLORS = {"pre": "#4C72B0", "post": "#DD8452"}
RQ_COLORS = {"RQ1": "#4C72B0", "RQ2": "#DD8452"}
WARNING = (
    "Utilidad de graficado del paquete de réplica. La pauta de la Etapa 2 exige "
    "que las tablas y figuras del artículo sean elaboradas por los estudiantes y "
    "prohíbe que la IA generativa las produzca. Revisá y adaptá cada figura antes "
    "de usarla."
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def optional_csv(path: Path) -> list[dict[str, str]]:
    return read_csv(path) if path.is_file() else []


def require_columns(
    rows: list[dict[str, str]], path: Path, columns: Iterable[str]
) -> None:
    if not rows:
        raise SystemExit(f"{path} está vacío o no tiene filas de datos.")
    missing = set(columns) - set(rows[0])
    if missing:
        raise SystemExit(
            f"{path} no contiene las columnas requeridas: {', '.join(sorted(missing))}."
        )


def numeric(rows: list[dict[str, str]], column: str) -> np.ndarray:
    try:
        return np.array([float(row[column]) for row in rows], dtype=float)
    except (KeyError, ValueError, TypeError) as exc:
        raise SystemExit(f"No se pudo leer la columna numérica {column!r}: {exc}") from exc


def fmt_number(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value))}"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def fmt_p(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 0.001:
        return "p < 0,001"
    return f"p = {value:.3f}".replace(".", ",")


def as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes", "si", "sí"}


def comparison_label(rq: str, comparison: str) -> str:
    return COMPARISON_LABELS.get((rq, comparison), comparison.replace("_", " "))


def sensitivity_label(name: str) -> str:
    return SENSITIVITY_LABELS.get(name, name.replace("_", " "))


def comma_formatter(value: float, _position: float | None = None) -> str:
    return fmt_number(value)


def apply_comma_axis(axis: plt.Axes, orientation: str = "x") -> None:
    target = axis.xaxis if orientation == "x" else axis.yaxis
    target.set_major_formatter(FuncFormatter(comma_formatter))


def status_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        name = (row.get("sensitivity") or "").strip()
        if name:
            rows[name] = row
    return rows


def computed_sensitivities(
    statuses: dict[str, dict[str, str]], rows: list[dict[str, str]]
) -> list[str]:
    """Sensitivities that were actually computed, in a stable readable order."""
    if statuses:
        names = [
            name
            for name, row in statuses.items()
            if row.get("status", "").strip().lower() == "computed"
        ]
    else:
        names = sorted(
            {
                row.get("sensitivity", "")
                for row in rows
                if row.get("sensitivity") not in {"", "primary", "sensitivity"}
            }
        )
    names = [name for name in names if name]
    known = [name for name in SENSITIVITY_ORDER if name in names]
    extra = sorted(name for name in names if name not in SENSITIVITY_ORDER)
    return known + extra


def sensitivity_title(name: str, statuses: dict[str, dict[str, str]]) -> str:
    row = statuses.get(name, {})
    repositories = (row.get("repositories") or "").strip()
    label = sensitivity_label(name)
    return f"{label}\n(n={repositories})" if repositories else label


def sign_map(
    rows: list[dict[str, str]], sensitivity: str = "primary"
) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        if row.get("sensitivity") != sensitivity:
            continue
        result[(row.get("rq", ""), row.get("comparison", ""))] = row
    return result


def effect_map(
    rows: list[dict[str, str]], sensitivity: str = "primary"
) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        if row.get("sensitivity") != sensitivity:
            continue
        result[(row["rq"], row["comparison"])] = row
    return result


def holm_p_map(
    rows: list[dict[str, str]], sensitivity: str = "primary"
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for row in rows:
        if row.get("sensitivity") != sensitivity:
            continue
        if row.get("test") != "wilcoxon":
            continue
        result[(row["rq"], row["comparison"])] = float(row["p_adjusted"])
    return result


def hl_ci_map(
    rows: list[dict[str, str]], sensitivity: str = "primary"
) -> dict[tuple[str, str], tuple[float, float]]:
    result: dict[tuple[str, str], tuple[float, float]] = {}
    for row in rows:
        if row.get("sensitivity") != sensitivity:
            continue
        if row.get("quantity") != "hodges_lehmann":
            continue
        result[(row["rq"], row["comparison"])] = (
            float(row["ci_low"]),
            float(row["ci_high"]),
        )
    return result


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.5,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def save(figure: plt.Figure, output: Path, name: str) -> None:
    for suffix in ("png", "pdf"):
        figure.savefig(
            output / f"{name}.{suffix}",
            dpi=300,
            bbox_inches="tight",
            metadata={"Software": None, "CreationDate": None, "ModDate": None},
        )
    plt.close(figure)


def draw_boxes(
    axis: plt.Axes,
    data: list[np.ndarray],
    positions: np.ndarray,
    colors: list[str],
) -> None:
    box = axis.boxplot(
        data,
        positions=positions,
        widths=0.55,
        patch_artist=True,
        medianprops={"color": "black", "linewidth": 0.9},
        whiskerprops={"linewidth": 0.8},
        capprops={"linewidth": 0.8},
        boxprops={"linewidth": 0.8},
        flierprops={"markersize": 3},
    )
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.5)


def plot_rq1_boxplot(rows: list[dict[str, str]], output: Path) -> str:
    data = [numeric(rows, f"{category}_commits") for category in RQ1_CATEGORIES]
    positions = np.arange(1, len(RQ1_CATEGORIES) + 1)
    figure, axis = plt.subplots(figsize=(3.5, 2.8))
    draw_boxes(
        axis,
        data,
        positions,
        [CATEGORY_COLORS[category] for category in RQ1_CATEGORIES],
    )
    axis.set_xticks(positions)
    axis.set_xticklabels([CATEGORY_LABELS[category] for category in RQ1_CATEGORIES])
    axis.set_ylim(bottom=0)
    axis.set_ylabel("commits por repositorio (60 días)")
    axis.set_title("RQ1: commits posteriores por categoría")
    save(figure, output, "fig_rq1_boxplot")
    return "fig_rq1_boxplot"


def plot_rq2_boxplot(rows: list[dict[str, str]], output: Path) -> str:
    data: list[np.ndarray] = []
    labels: list[str] = []
    colors: list[str] = []
    for category in RQ2_CATEGORIES:
        for period in ("pre", "post"):
            data.append(numeric(rows, f"{category}_{period}"))
            labels.append(
                f"{CATEGORY_LABELS[category]}\n"
                f"{'previo' if period == 'pre' else 'posterior'}"
            )
            colors.append(PERIOD_COLORS[period])
    positions = np.arange(1, len(data) + 1)
    figure, axis = plt.subplots(figsize=(3.5, 2.8))
    draw_boxes(axis, data, positions, colors)
    axis.set_xticks(positions)
    axis.set_xticklabels(labels)
    axis.set_ylim(bottom=0)
    axis.set_ylabel("commits por repositorio (60 días)")
    axis.set_title("RQ2: commits previos y posteriores por categoría")
    save(figure, output, "fig_rq2_boxplot")
    return "fig_rq2_boxplot"


def rq1_effect_entries(
    effects: dict[tuple[str, str], dict[str, str]],
    intervals: dict[tuple[str, str], tuple[float, float]],
    pvalues: dict[tuple[str, str], float],
) -> list[tuple[str, str, float, float, float, float | None]]:
    entries: list[tuple[str, str, float, float, float, float | None]] = []
    for comparison in RQ1_COMPARISONS:
        row = effects.get(("RQ1", comparison))
        if row is None:
            continue
        hl = float(row["hodges_lehmann"])
        low, high = intervals.get(("RQ1", comparison), (hl, hl))
        entries.append(
            (
                comparison,
                COMPARISON_LABELS[("RQ1", comparison)],
                hl,
                low,
                high,
                pvalues.get(("RQ1", comparison)),
            )
        )
    return entries


def plot_effects(
    effects: dict[tuple[str, str], dict[str, str]],
    intervals: dict[tuple[str, str], tuple[float, float]],
    pvalues: dict[tuple[str, str], float],
    output: Path,
    n: int,
) -> str:
    entries: list[tuple[str, str, float, float, float, float | None]] = []
    for rq, comparison in (
        ("RQ1", "skill-production"),
        ("RQ1", "skill-test"),
        ("RQ1", "production-test"),
        ("RQ2", "production_post_minus_pre"),
        ("RQ2", "test_post_minus_pre"),
    ):
        row = effects.get((rq, comparison))
        if row is None:
            continue
        hl = float(row["hodges_lehmann"])
        ci_key = (
            ("RQ2", comparison.removesuffix("_post_minus_pre"))
            if rq == "RQ2"
            else (rq, comparison)
        )
        low, high = intervals.get(ci_key, (hl, hl))
        entries.append(
            (
                COMPARISON_LABELS[(rq, comparison)],
                rq,
                hl,
                low,
                high,
                pvalues.get((rq, comparison)),
            )
        )

    if len(entries) < 2:
        raise SystemExit(
            "No hay suficientes efectos primarios para el gráfico de bosque. "
            "Revisá results-inference/effect_sizes.csv y bootstrap_ci.csv."
        )

    figure, axis = plt.subplots(figsize=(3.6, 3.2))
    y_positions = np.arange(len(entries))[::-1]
    x_low = min(entry[3] for entry in entries)
    x_high = max(entry[4] for entry in entries)
    span = max(x_high - x_low, 1e-9)
    text_x = x_high + span * 0.04

    for y, (label, rq, hl, low, high, pvalue) in zip(y_positions, entries):
        color = RQ_COLORS[rq]
        significant = pvalue is not None and pvalue < 0.05
        axis.errorbar(
            hl,
            y,
            xerr=[[max(0.0, hl - low)], [max(0.0, high - hl)]],
            fmt="o",
            markersize=4.5,
            markerfacecolor=color if significant else "white",
            markeredgecolor=color,
            ecolor=color,
            elinewidth=1.0,
            capsize=2.5,
            zorder=3,
        )
        axis.text(
            text_x,
            y,
            fmt_p(pvalue),
            va="center",
            ha="left",
            fontsize=6.5,
            color="black",
        )

    axis.axvline(0, color="black", linewidth=0.9, linestyle="--", zorder=1)
    axis.set_yticks(y_positions)
    axis.set_yticklabels([entry[0] for entry in entries])
    axis.tick_params(axis="y", length=0)
    axis.set_xlim(x_low - span * 0.06, x_high + span * 0.28)
    axis.set_xlabel("Hodges–Lehmann (IC 95 % bootstrap)")
    axis.set_title(f"Tamaños de efecto (n={n})")
    apply_comma_axis(axis, "x")
    figure.text(
        0.5,
        0.01,
        "Relleno: p de Holm < 0,05. Azul: RQ1; naranja: RQ2.",
        ha="center",
        va="bottom",
        fontsize=6.5,
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    save(figure, output, "fig_effects")
    return "fig_effects"


def plot_rq1_sensitivity(
    panels: list[tuple[str, list[tuple[str, str, float, float, float, float | None]]]],
    output: Path,
    n: int,
) -> str:
    panels = [(title, entries) for title, entries in panels if entries]
    if not panels:
        raise SystemExit(
            "No hay sensibilidades calculadas para RQ1. Revisá "
            "results-inference/sensitivity_effects.csv y sensitivity_status.csv."
        )

    columns = min(3, len(panels))
    rows = math.ceil(len(panels) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(7.0, 2.55 * rows + 0.35),
        sharey=True,
        squeeze=False,
    )
    flat = axes.ravel()
    color = RQ_COLORS["RQ1"]
    y_positions = np.arange(len(RQ1_COMPARISONS))[::-1]
    y_by_comparison = dict(zip(RQ1_COMPARISONS, y_positions))

    for index, axis in enumerate(flat):
        if index >= len(panels):
            axis.set_visible(False)
            continue
        title, entries = panels[index]
        lows = [entry[3] for entry in entries] + [0.0]
        highs = [entry[4] for entry in entries] + [0.0]
        span = max(max(highs) - min(lows), 1e-9)
        for comparison, _, hl, low, high, pvalue in entries:
            y = y_by_comparison[comparison]
            significant = pvalue is not None and pvalue < 0.05
            axis.errorbar(
                hl,
                y,
                xerr=[[max(0.0, hl - low)], [max(0.0, high - hl)]],
                fmt="o",
                markersize=4.5,
                markerfacecolor=color if significant else "white",
                markeredgecolor=color,
                ecolor=color,
                elinewidth=1.0,
                capsize=2.5,
                zorder=3,
            )
        axis.axvline(0, color="black", linewidth=0.9, linestyle="--", zorder=1)
        axis.set_yticks(y_positions)
        if index % columns == 0:
            axis.set_yticklabels(
                [COMPARISON_LABELS[("RQ1", comparison)] for comparison in RQ1_COMPARISONS]
            )
        axis.tick_params(axis="y", length=0)
        axis.set_xlim(min(lows) - span * 0.08, max(highs) + span * 0.08)
        apply_comma_axis(axis, "x")
        axis.set_title(title)
        axis.set_xlabel("HL (IC 95 %)")

    figure.suptitle(f"RQ1: robustez de la dirección (n={n})", fontsize=9.5)
    figure.text(
        0.5,
        0.01,
        "Relleno: p de Holm < 0,05. La escala del eje horizontal difiere entre paneles.",
        ha="center",
        va="bottom",
        fontsize=6.5,
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.93))
    save(figure, output, "fig_rq1_sensitivity")
    return "fig_rq1_sensitivity"


def plot_sign_tests(
    rows: list[dict[str, str]],
    output: Path,
    n: int,
) -> str:
    entries: list[tuple[str, str, dict[str, str]]] = []
    primary_signs = sign_map(rows)
    for rq, comparisons in (("RQ1", RQ1_COMPARISONS), ("RQ2", RQ2_COMPARISONS)):
        for comparison in comparisons:
            row = primary_signs.get((rq, comparison))
            if row is not None:
                entries.append((comparison_label(rq, comparison), rq, row))
    if not entries:
        raise SystemExit(
            "No hay filas primarias en results-inference/sign_tests.csv."
        )

    figure, axis = plt.subplots(figsize=(3.6, 3.0))
    y_positions = np.arange(len(entries))[::-1]
    for y, (label, rq, row) in zip(y_positions, entries):
        proportion = float(row["positive_proportion"])
        low = float(row["ci_low"])
        high = float(row["ci_high"])
        significant = as_bool(row.get("significant"))
        robust = as_bool(row.get("robust"))
        color = RQ_COLORS[rq]
        axis.errorbar(
            proportion,
            y,
            xerr=[[max(0.0, proportion - low)], [max(0.0, high - proportion)]],
            fmt="D" if robust else "o",
            markersize=4.5,
            markerfacecolor=color if significant else "white",
            markeredgecolor=color,
            ecolor=color,
            elinewidth=1.0,
            capsize=2.5,
            zorder=3,
        )

    axis.axvline(0.5, color="black", linewidth=0.9, linestyle="--", zorder=1)
    axis.set_yticks(y_positions)
    axis.set_yticklabels(
        [
            f"{label}\n{fmt_p(float(row['p_adjusted'])) if row.get('p_adjusted') else 'p = —'}"
            f" · {row.get('positive', '?')}/{row.get('n_nonzero', '?')}"
            for label, _, row in entries
        ]
    )
    axis.tick_params(axis="y", length=0)
    axis.set_xlim(-0.02, 1.02)
    axis.set_xlabel("proporción positiva (IC 95 % exacto)")
    axis.set_title(f"Test del signo (D6, n={n})")
    apply_comma_axis(axis, "x")
    figure.text(
        0.5,
        0.01,
        "Relleno: p de Holm < 0,05. Rombo: coincide con Wilcoxon (robusto). "
        "Línea: 0,5.",
        ha="center",
        va="bottom",
        fontsize=6.5,
    )
    figure.tight_layout(rect=(0, 0.07, 1, 1))
    save(figure, output, "fig_sign_tests")
    return "fig_sign_tests"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_output(output: Path, force: bool) -> None:
    if output.exists() and any(output.iterdir()) and not force:
        raise SystemExit(
            f"El directorio {output} no está vacío. Usá --force para sobrescribir "
            "las figuras generadas por esta utilidad."
        )
    output.mkdir(parents=True, exist_ok=True)


def write_readme(
    output: Path,
    n: int,
    figures: list[str],
    has_inference: bool,
    sensitivity_names: list[str] | None = None,
    statuses: dict[str, dict[str, str]] | None = None,
) -> None:
    sensitivity_names = sensitivity_names or []
    statuses = statuses or {}
    rows = [
        "| `fig_rq1_boxplot` | Figura 1 (candidata) | Caja por categoría con los commits posteriores por repositorio: mediana, cuartiles, bigotes y valores atípicos. |",
        "| `fig_rq2_boxplot` | Figura 2 (candidata) | Caja por categoría y periodo (previo y posterior) con los commits por repositorio. |",
    ]
    if "fig_effects" in figures:
        rows.append(
            "| `fig_effects` | Opcional | Efectos de Hodges–Lehmann con IC bootstrap para los tres pares de RQ1 y las dos diferencias de RQ2. El relleno indica significación tras Holm. |"
        )
    if "fig_sign_tests" in figures:
        rows.append(
            "| `fig_sign_tests` | Opcional | Test del signo (D6): proporción de diferencias positivas con IC exacto de Clopper–Pearson. El relleno indica significación tras Holm y el rombo, concordancia con Wilcoxon. |"
        )
    if "fig_rq1_sensitivity" in figures:
        rows.append(
            "| `fig_rq1_sensitivity` | Opcional | Dirección de los tres pares de RQ1 con el conteo absoluto y cada sensibilidad marcada como `computed`. La escala horizontal difiere entre paneles. |"
        )

    inference_note = (
        "Los IC, efectos, valores `p`, tests del signo y sensibilidades provienen de "
        "`results-inference/`."
        if has_inference
        else "No se encontró `results-inference/`; las figuras se generaron sin IC ni efectos."
    )
    sensitivity_lines = []
    if sensitivity_names:
        for name in sensitivity_names:
            row = statuses.get(name, {})
            repositories = (row.get("repositories") or "").strip()
            detail = (row.get("detail") or "").strip()
            suffix = f" (n={repositories})" if repositories else ""
            note = f" — {detail}" if detail else ""
            sensitivity_lines.append(
                f"- `{name}`: {sensitivity_label(name)}{suffix}{note}"
            )
    elif "fig_rq1_sensitivity" in figures:
        sensitivity_lines.append("- Sin sensibilidades calculadas en `results-inference/`.")
    else:
        sensitivity_lines.append(
            "- No se solicitó `--sensitivity`; ejecútalo para graficar las "
            "sensibilidades de `results-inference/`."
        )

    excluded_lines = [
        f"- `{name}`: {row.get('status', '')}"
        + (f" ({row['detail']})" if row.get("detail") else "")
        for name, row in statuses.items()
        if row.get("status", "").strip().lower() != "computed"
    ]
    if not excluded_lines:
        excluded_lines.append("- Ninguna sensibilidad quedó fuera de cálculo.")

    sign_note = (
        "El test del signo es la sensibilidad D6: solo se considera robusto cuando "
        "coincide en dirección y significación con Wilcoxon."
        if "fig_sign_tests" in figures
        else "Ejecutá también `--sign` para generar la figura del test del signo (D6)."
    )

    content = f"""# Figuras de Etapa 3

> {WARNING}

Generadas por `scripts/plot_results.py` a partir de `results/` y
`results-inference/` para la muestra de **n={n}** repositorios. Cada figura se
entrega en PNG (300 dpi) y PDF. {inference_note}

| Archivo | Uso | Contenido |
|---|---|---|
{chr(10).join(rows)}

## Cómo regenerarlas

```bash
uv run python scripts/plot_results.py \\
  --results results \\
  --inference results-inference \\
  --output figures-etapa3 \\
  --forest --sign --sensitivity
```

Sin `--forest`, `--sign` y `--sensitivity` se generan solo las dos figuras
candidatas. El script se niega a escribir en un directorio no vacío; usá
`--force` para sobrescribir.

## Sensibilidades

{chr(10).join(sensitivity_lines)}

Sensibilidades no graficadas:

{chr(10).join(excluded_lines)}

{sign_note}

## Lectura

- Las figuras de RQ1 y RQ2 siguen el estilo de la Etapa 2: solo cajas, escala
  lineal y valores atípicos visibles. Los IC bootstrap y las diferencias de
  mediana no se dibujan; están en `results-inference/` y en la tabla del
  artículo.
- En RQ2 la unidad de inferencia es la diferencia por repositorio. La figura
  muestra los marginales previo y posterior, por lo que no sustituye al
  análisis pareado.
- `fig_rq1_sensitivity` está pensada para ancho completo (`figure*`); las demás
  caben en una columna.
- `sample_representativeness.csv` alimenta la tabla muestra-marco (Tabla I); no
  se convierte en figura porque el marco solo aporta el número de skills y las
  estrellas, la antigüedad y la copia verbatim del marco están faltantes.
- `manifest.json` registra insumos, checksums, configuración, sensibilidades
  incluidas y versiones.

Recordá numerar cada figura, agregar un pie descriptivo, mencionarla en el texto
y elaborar el diseño final vos mismo según la pauta.
"""
    (output / "README.md").write_text(content, encoding="utf-8")


def write_manifest(
    output: Path,
    results: Path,
    inference: Path,
    n: int,
    figures: list[str],
    inputs: list[Path],
    forest: bool,
    sign: bool,
    sensitivity: bool,
    sensitivity_names: list[str] | None = None,
    statuses: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    sensitivity_names = sensitivity_names or []
    statuses = statuses or {}
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": file_sha256(Path(__file__).resolve()),
        },
        "configuration": {
            "results_directory": str(results),
            "inference_directory": str(inference),
            "output_directory": str(output),
            "forest": forest,
            "sign": sign,
            "sensitivity": sensitivity,
            "sensitivity_panels": sensitivity_names,
            "sample_size": n,
        },
        "sensitivity_status": statuses,
        "inputs": {
            str(path): {"sha256": file_sha256(path)} for path in inputs if path.is_file()
        },
        "figures": figures,
        "tool_versions": {
            "python": platform.python_version(),
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
        },
        "warning": WARNING,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def run(
    results: Path,
    inference: Path,
    output: Path,
    *,
    forest: bool = False,
    sign: bool = False,
    sensitivity: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    results = results.resolve()
    inference = inference.resolve()
    output = output.resolve()
    prepare_output(output, force)
    apply_style()

    rq1_path = results / "rq1_repository_counts.csv"
    rq2_path = results / "rq2_repository_differences.csv"
    rq1_rows = read_csv(rq1_path)
    require_columns(
        rq1_rows,
        rq1_path,
        (
            "repo_full_name",
            "skill_commits",
            "production_commits",
            "test_commits",
        ),
    )
    rq2_rows = read_csv(rq2_path)
    require_columns(
        rq2_rows,
        rq2_path,
        (
            "repo_full_name",
            "production_pre",
            "production_post",
            "production_difference",
            "test_pre",
            "test_post",
            "test_difference",
        ),
    )
    if len(rq1_rows) != len(rq2_rows):
        print(
            "Aviso: rq1_repository_counts.csv y rq2_repository_differences.csv "
            "tienen distinto número de filas.",
            file=sys.stderr,
        )
    n = len(rq1_rows)

    bootstrap_path = inference / "bootstrap_ci.csv"
    effects_path = inference / "effect_sizes.csv"
    rq1_tests_path = inference / "rq1_tests.csv"
    rq2_tests_path = inference / "rq2_tests.csv"
    sign_tests_path = inference / "sign_tests.csv"
    sensitivity_effects_path = inference / "sensitivity_effects.csv"
    sensitivity_ci_path = inference / "sensitivity_ci.csv"
    sensitivity_tests_path = inference / "sensitivity_tests.csv"
    sensitivity_status_path = inference / "sensitivity_status.csv"
    has_inference = bootstrap_path.is_file()

    if forest and not has_inference:
        raise SystemExit(
            "El gráfico de efectos requiere results-inference/bootstrap_ci.csv."
        )
    if sign and not has_inference:
        raise SystemExit(
            "El gráfico del test del signo requiere results-inference/bootstrap_ci.csv."
        )
    if sign and not sign_tests_path.is_file():
        raise SystemExit(
            "El gráfico del test del signo requiere results-inference/sign_tests.csv."
        )
    if sensitivity and not has_inference:
        raise SystemExit(
            "El gráfico de sensibilidad requiere results-inference/bootstrap_ci.csv."
        )
    if sensitivity:
        missing = [
            str(path)
            for path in (
                sensitivity_effects_path,
                sensitivity_ci_path,
                sensitivity_tests_path,
            )
            if not path.is_file()
        ]
        if missing:
            raise SystemExit(
                "El gráfico de sensibilidad requiere: " + ", ".join(missing)
            )

    primary_effects: dict[tuple[str, str], dict[str, str]] = {}
    primary_intervals: dict[tuple[str, str], tuple[float, float]] = {}
    primary_pvalues: dict[tuple[str, str], float] = {}
    statuses = status_rows(sensitivity_status_path)
    sensitivity_names: list[str] = []

    if has_inference:
        bootstrap_rows = read_csv(bootstrap_path)
        primary_intervals = hl_ci_map(bootstrap_rows, "primary")
        primary_effects = effect_map(optional_csv(effects_path), "primary")
        primary_pvalues.update(holm_p_map(optional_csv(rq1_tests_path), "primary"))
        primary_pvalues.update(holm_p_map(optional_csv(rq2_tests_path), "primary"))
        sensitivity_names = computed_sensitivities(
            statuses, optional_csv(sensitivity_ci_path)
        )
    if not has_inference:
        print(
            "Aviso: no se encontró results-inference/bootstrap_ci.csv. "
            "Se grafican distribuciones sin IC.",
            file=sys.stderr,
        )

    figures: list[str] = []
    figures.append(plot_rq1_boxplot(rq1_rows, output))
    figures.append(plot_rq2_boxplot(rq2_rows, output))

    used_inputs = [rq1_path, rq2_path]
    if has_inference:
        used_inputs.append(bootstrap_path)
    for path in (effects_path, rq1_tests_path, rq2_tests_path):
        if path.is_file():
            used_inputs.append(path)

    if forest:
        figures.append(
            plot_effects(
                primary_effects, primary_intervals, primary_pvalues, output, n
            )
        )

    if sign:
        figures.append(plot_sign_tests(read_csv(sign_tests_path), output, n))
        used_inputs.append(sign_tests_path)

    if sensitivity:
        required = (
            sensitivity_effects_path,
            sensitivity_ci_path,
            sensitivity_tests_path,
        )
        sensitivity_effect_rows = read_csv(sensitivity_effects_path)
        sensitivity_ci_rows = read_csv(sensitivity_ci_path)
        sensitivity_test_rows = read_csv(sensitivity_tests_path)

        panels: list[
            tuple[str, list[tuple[str, str, float, float, float, float | None]]]
        ] = [
            (
                f"Conteo absoluto\n(n={n})",
                rq1_effect_entries(
                    primary_effects, primary_intervals, primary_pvalues
                ),
            )
        ]
        for name in sensitivity_names:
            entries = rq1_effect_entries(
                effect_map(sensitivity_effect_rows, name),
                hl_ci_map(sensitivity_ci_rows, name),
                holm_p_map(sensitivity_test_rows, name),
            )
            if not entries:
                print(
                    f"Aviso: la sensibilidad {name!r} figura como computed pero no "
                    "tiene efectos RQ1 utilizables; se omite del gráfico.",
                    file=sys.stderr,
                )
                continue
            panels.append((sensitivity_title(name, statuses), entries))

        figures.append(plot_rq1_sensitivity(panels, output, n))
        used_inputs.extend(required)
        if sensitivity_status_path.is_file():
            used_inputs.append(sensitivity_status_path)

    write_readme(
        output,
        n,
        figures,
        has_inference,
        sensitivity_names if "fig_rq1_sensitivity" in figures else [],
        statuses,
    )
    manifest = write_manifest(
        output,
        results,
        inference,
        n,
        figures,
        used_inputs,
        forest,
        sign,
        sensitivity,
        sensitivity_names,
        statuses,
    )
    print(f"Figuras escritas en {output}")
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Plot Stage 3 RQ1/RQ2 descriptive and inferential results."
    )
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--inference", type=Path, default=Path("results-inference"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("figures-etapa3"),
        help="Output directory for figures (default: figures-etapa3)",
    )
    parser.add_argument(
        "--forest",
        action="store_true",
        help="Also draw the effect-size forest plot (fig_effects).",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Also draw the sign-test robustness plot (fig_sign_tests).",
    )
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help=(
            "Also draw the RQ1 sensitivity figure for every sensitivity marked "
            "as computed in sensitivity_status.csv."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite figures in a non-empty output directory.",
    )
    args = parser.parse_args(argv)

    print(WARNING)
    run(
        args.results,
        args.inference,
        args.output,
        forest=args.forest,
        sign=args.sign,
        sensitivity=args.sensitivity,
        force=args.force,
    )


if __name__ == "__main__":
    main()
