#!/usr/bin/env python3
"""Plot the descriptive RQ1/RQ2 results from results/*.csv.

IMPORTANT — assignment rule
---------------------------
The Stage 2 guidelines require that the tables and figures of the article be
produced by the students themselves and explicitly forbid generative AI from
producing them. This script is provided as a reusable utility of the
replication package so that *you* can inspect, adapt and own the figures. If you
include any of its output in the article, you are responsible for having
authored the figure design and for complying with the rule. Do not paste a
figure you have not reviewed and adapted.

Usage:
    uv run python scripts/plot_results.py \
        --results results --output figures

It reads:
    results/rq1_repository_counts.csv
    results/rq2_repository_differences.csv
and writes PNG (300 dpi) and PDF versions of each figure into --output.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


RQ1_CATEGORIES = ("skill", "production", "test")
CATEGORY_LABELS = {
    "skill": "skills",
    "production": "producción",
    "test": "pruebas",
}
COLORS = {
    "skill": "#4C72B0",
    "production": "#DD8452",
    "test": "#55A868",
}
WARNING = (
    "Utilidad de graficado del paquete de réplica. La pauta de la Etapa 2 exige "
    "que las tablas y figuras del artículo sean elaboradas por los estudiantes y "
    "prohíbe que la IA generativa las produzca. Revisá y adaptá cada figura antes "
    "de usarla."
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def integers(rows: list[dict[str, str]], column: str) -> list[int]:
    return [int(row[column]) for row in rows]


def save(figure: plt.Figure, output: Path, name: str) -> None:
    for suffix in ("png", "pdf"):
        figure.savefig(
            output / f"{name}.{suffix}",
            dpi=300,
            bbox_inches="tight",
            metadata={"Software": None},
        )
    plt.close(figure)


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "grid.linewidth": 0.5,
        }
    )


def rq1_boxplot(rows: list[dict[str, str]], output: Path) -> None:
    data = [integers(rows, f"{category}_commits") for category in RQ1_CATEGORIES]
    figure, axis = plt.subplots(figsize=(3.4, 2.6))
    box = axis.boxplot(
        data,
        tick_labels=[CATEGORY_LABELS[category] for category in RQ1_CATEGORIES],
        patch_artist=True,
        widths=0.55,
    )
    for patch, category in zip(box["boxes"], RQ1_CATEGORIES):
        patch.set_facecolor(COLORS[category])
        patch.set_alpha(0.7)
    axis.set_title("RQ1: commits de cada categoría (60 días posteriores)")
    axis.set_ylabel("commits por repositorio (n=10)")
    save(figure, output, "rq1_post_boxplot")


def rq1_grouped_bar(rows: list[dict[str, str]], output: Path) -> None:
    repositories = [row["repo_full_name"] for row in rows]
    positions = np.arange(len(repositories))
    width = 0.26
    figure, axis = plt.subplots(figsize=(7.0, 3.0))
    for offset, category in enumerate(RQ1_CATEGORIES):
        values = integers(rows, f"{category}_commits")
        axis.bar(
            positions + (offset - 1) * width,
            values,
            width,
            label=CATEGORY_LABELS[category],
            color=COLORS[category],
        )
    axis.set_title("RQ1: commits por repositorio y categoría (período posterior)")
    axis.set_ylabel("commits")
    axis.set_xticks(positions)
    axis.set_xticklabels(repositories, rotation=45, ha="right")
    axis.legend(title="categoría")
    save(figure, output, "rq1_post_by_repository")


def rq2_periods_boxplot(rows: list[dict[str, str]], output: Path) -> None:
    series = [
        ("producción", "pre", "production_pre"),
        ("producción", "post", "production_post"),
        ("pruebas", "pre", "test_pre"),
        ("pruebas", "post", "test_post"),
    ]
    data = [integers(rows, column) for _, _, column in series]
    labels = [f"{name}\n{period}" for name, period, _ in series]
    figure, axis = plt.subplots(figsize=(3.6, 2.6))
    box = axis.boxplot(data, tick_labels=labels, patch_artist=True, widths=0.55)
    for patch, (_, period, _) in zip(box["boxes"], series):
        patch.set_facecolor("#4C72B0" if period == "pre" else "#DD8452")
        patch.set_alpha(0.7)
    axis.set_title("RQ2: commits antes y después por categoría")
    axis.set_ylabel("commits por repositorio (n=10)")
    save(figure, output, "rq2_periods_boxplot")


def rq2_difference_boxplot(rows: list[dict[str, str]], output: Path) -> None:
    data = [
        integers(rows, "production_difference"),
        integers(rows, "test_difference"),
    ]
    figure, axis = plt.subplots(figsize=(3.2, 2.6))
    box = axis.boxplot(
        data, tick_labels=["producción", "pruebas"], patch_artist=True, widths=0.5
    )
    for patch, category in zip(box["boxes"], ("production", "test")):
        patch.set_facecolor(COLORS[category])
        patch.set_alpha(0.7)
    axis.axhline(0, color="black", linewidth=0.8, linestyle="--")
    axis.set_title("RQ2: diferencia de commits (post − pre)")
    axis.set_ylabel("post − pre")
    save(figure, output, "rq2_difference_boxplot")


def rq2_difference_bar(rows: list[dict[str, str]], output: Path) -> None:
    repositories = [row["repo_full_name"] for row in rows]
    positions = np.arange(len(repositories))
    width = 0.38
    figure, axis = plt.subplots(figsize=(7.0, 3.0))
    axis.bar(
        positions - width / 2,
        integers(rows, "production_difference"),
        width,
        label="producción",
        color=COLORS["production"],
    )
    axis.bar(
        positions + width / 2,
        integers(rows, "test_difference"),
        width,
        label="pruebas",
        color=COLORS["test"],
    )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_title("RQ2: diferencia de commits por repositorio (post − pre)")
    axis.set_ylabel("post − pre")
    axis.set_xticks(positions)
    axis.set_xticklabels(repositories, rotation=45, ha="right")
    axis.legend(title="categoría")
    save(figure, output, "rq2_difference_by_repository")


def write_readme(output: Path) -> None:
    content = f"""# Figuras

> {WARNING}

Generadas por `scripts/plot_results.py` a partir de `results/`. Cada figura se
entrega en PNG (300 dpi) y PDF.

| Archivo | Contenido |
|---|---|
| `rq1_post_boxplot` | Distribución de commits de skills, producción y pruebas en los 60 días posteriores (n=10). |
| `rq1_post_by_repository` | Commits por repositorio y categoría en el período posterior. |
| `rq2_periods_boxplot` | Commits de producción y pruebas antes y después de la adopción. |
| `rq2_difference_boxplot` | Distribución de la diferencia `post − pre` por categoría. |
| `rq2_difference_by_repository` | Diferencia `post − pre` por repositorio y categoría. |

Recordá numerar cada figura, agregar un pie descriptivo y mencionarla en el
texto, y elaborar el diseño final vos mismo según la pauta.
"""
    (output / "README.md").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot RQ1/RQ2 descriptive results.")
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("figures"),
        help="Output directory for figures (created if missing)",
    )
    args = parser.parse_args()
    results = args.results.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    print(WARNING)
    apply_style()
    rq1_rows = read_csv(results / "rq1_repository_counts.csv")
    rq2_rows = read_csv(results / "rq2_repository_differences.csv")

    rq1_boxplot(rq1_rows, output)
    rq1_grouped_bar(rq1_rows, output)
    rq2_periods_boxplot(rq2_rows, output)
    rq2_difference_boxplot(rq2_rows, output)
    rq2_difference_bar(rq2_rows, output)
    write_readme(output)
    print(f"Figures written to {output}")


if __name__ == "__main__":
    main()
