# Runbook de Etapa 3 — de extracción a análisis

Este documento deja preparada la ejecución operativa. **No ejecutes los pasos de
archivado sin comprobar antes que la Etapa 2 está publicada y que sus artefactos
están respaldados.** La extracción puede reanudarse en `data/extraction/`; el
procesamiento y el análisis siempre escriben en directorios nuevos.

## Flujo resumido

Desde la raíz del repositorio, en este orden:

```bash
# 0. Congelar código y dependencias
uv run pytest
git add src tests docs pyproject.toml uv.lock README.md extraction.toml
git commit -m "Etapa 3: pipeline listo para la corrida definitiva"

# 1. Archivar las salidas de Etapa 2 (solo una vez)
mv data/validation data/validation-etapa2
mv data/activity-validation data/activity-validation-etapa2
mv data/processed data/processed-etapa2
mv results results-etapa2
mv figures figures-etapa2 2>/dev/null || true

# 2. Extracción: 120 elegibles, tope de 1.500 candidatos
uv run extract-repositories --config extraction.toml

# 3. Validación humana
uv run prepare-validation --config extraction.toml --maximum-per-category 1
uv run review-validation list
#   ... completar adoption_review.csv y las 4 filas de path_review.csv ...
uv run review-validation validate
sha256sum data/validation/adoption_review.csv \
          data/validation/path_review.csv > data/validation/human_review.sha256
sha256sum --check data/validation/human_review.sha256

# 4. Procesamiento principal (ventana de 60 días)
uv run process-activity --config extraction.toml

# 5. Sensibilidades de ventana (opcionales, en directorios nuevos)
uv run process-activity --config extraction.toml --window-days 30 \
  --output data/processed-30
uv run process-activity --config extraction.toml --window-days 90 \
  --output data/processed-90

# 6. Análisis descriptivo, filtro de adopciones inválidas y N estimada
uv run analyze-results --config extraction.toml

# 7. Verificación
uv run pytest
sha256sum --check data/validation/human_review.sha256
! grep -q MISMATCH results/consistency_checks.txt   # éxito = no hay discrepancias
```

La revisión humana del paso 3 es el único paso que no puede automatizarse: hay
que revisar todas las adopciones y las 4 rutas del árbol con la rúbrica de
`docs/procedimiento.md`. Los pasos 5 son opcionales; si se ejecutan, sus
resultados se guardan en `data/processed-30/` y `data/processed-90/`, sin tocar
`data/processed/`.

Requisitos: `uv`, Git 2.31 o posterior, conexión a Hugging Face y GitHub, y
espacio para los 120 clones y un clon temporal. El cribado y los clones son la
parte lenta (varias horas según red y máquina); la revisión humana de 120
adopciones es la otra parte no automatizable.

## 0. Preflight

```bash
# El árbol debe estar limpio y las pruebas deben pasar antes de congelar la corrida.
uv run pytest
git status --short

# Fija la versión del código y de las dependencias antes de la corrida definitiva.
# No añadas data/ ni results/ todavía: se versionan al cerrar la corrida.
git add src tests docs pyproject.toml uv.lock README.md extraction.toml
git commit -m "Etapa 3: pipeline listo para la corrida definitiva"
git tag -a v0.3-etapa3-run -m "Corrida definitiva de Etapa 3"
```

Registra en el cuaderno de trabajo el commit y la etiqueta. `extraction.toml` debe
tener `target_count = 120` y `max_candidates = 1500`.

Archiva las salidas de Etapa 2 para que los comandos no se nieguen a escribir y
para que sus checksums no se mezclen con los de Etapa 3:

```bash
mv data/validation data/validation-etapa2
mv data/activity-validation data/activity-validation-etapa2
mv data/processed data/processed-etapa2
mv results results-etapa2
# Opcional: conserva las figuras de partida de Etapa 2.
mv figures figures-etapa2 2>/dev/null || true
```

`data/extraction/` **no se archiva**: la corrida de 120 reanuda el mismo orden y
conserva el prefijo de 10 del piloto. `repositories/` se conserva porque el
procesamiento verifica los clones.

## 1. Extracción y cribado

```bash
uv run extract-repositories --config extraction.toml
```

Comprueba en `data/extraction/run_manifest.json`:

- `completion_status` (`target_reached`, `cap_reached` o `frame_exhausted`);
- `eligible_count` y `candidates_examined`;
- `cap_reached`;
- que los 10 repositorios del piloto siguen en `selected_repositories.csv`.

Si `completion_status` es `cap_reached` o `frame_exhausted`, el análisis se hará
con \(n<120\) y se reportará el motivo. No se añaden reemplazos manuales.

## 2. Validación humana

```bash
uv run prepare-validation --config extraction.toml --maximum-per-category 1
uv run review-validation list
```

Completa **todas** las adopciones de `data/validation/adoption_review.csv` con la
rúbrica de `docs/procedimiento.md`:

- `valid`: primera incorporación real de un skill válido, sin renombrado;
- `invalid`: la evidencia contradice la clasificación;
- `ambiguous`: evidencia insuficiente o caso frontera.

Completa las **4 rutas** de `path_review.csv` (una por categoría). Revisor:
Thomas Kinderman. Fecha: UTC del momento de la revisión. No calcules precisión ni
recall.

```bash
uv run review-validation validate
sha256sum data/validation/adoption_review.csv \
          data/validation/path_review.csv > data/validation/human_review.sha256
sha256sum --check data/validation/human_review.sha256
```

El sello es obligatorio para `process-activity`. Si necesitas una corrida
exploratoria, usa `--allow-pending-human-review`; la corrida definitiva debe
llevar la revisión completa y sellada.

## 3. Procesamiento principal

```bash
uv run process-activity --config extraction.toml
```

Verifica las salidas:

```text
data/processed/
  processing_manifest.json
  processing.log
  commit_activity.csv       autor/committer, is_bot, categoría
  file_changes.csv          líneas añadidas/eliminadas, is_binary
  activity_counts.csv       conteos con ceros explícitos
  skill_contents.csv        SHA-256 del SKILL.md adoptado (D9)
```

El manifiesto debe registrar `activity_window_days = 60`,
`selected_repository_count`, `target_count`, `extraction_completion_status`,
`cap_reached`, `bot_commit_category_rows` y los checksums de las salidas.

## 4. Sensibilidades de ventana

Se ejecutan sobre la misma muestra y la misma validación sellada, en directorios
nuevos:

```bash
uv run process-activity --config extraction.toml --window-days 30 \
  --output data/processed-30
uv run process-activity --config extraction.toml --window-days 90 \
  --output data/processed-90
```

`--window-days` no cambia la elegibilidad ni la validación; solo las ventanas de
actividad. En `processing_manifest.json`, cada repositorio incluye
`pre_window_complete` y `post_window_complete`. Para la sensibilidad de 90 días,
excluye los repositorios con alguno de esos indicadores en `false` y reporta el
\(n\) resultante. La ventana principal sigue siendo 60 días.

## 5. Análisis descriptivo y muestra

```bash
uv run analyze-results --config extraction.toml
```

El comando:

- lee `data/validation/adoption_review.csv`;
- excluye adopciones `invalid`, conserva `ambiguous` con bandera;
- escribe `results/analysis_sample.csv` con la decisión por repositorio;
- calcula RQ1/RQ2 sobre el \(n\) efectivo;
- estima el rendimiento y \(\hat N\) en `results/sample_estimate.csv`;
- registra todo en `results/analysis_manifest.json`.

Comprueba:

```bash
! grep -q MISMATCH results/consistency_checks.txt   # éxito = no hay discrepancias
uv run python - <<'PY'
import json
m = json.load(open("results/analysis_manifest.json"))
print(m["reviewed_repository_count"], m["repository_count"])
print(m["excluded_invalid_count"], m["ambiguous_count"])
print(m["sample_estimate"])
PY
```

## 6. Verificación final

```bash
uv run pytest
sha256sum --check data/validation/human_review.sha256
```

Revisa que:

- `results/` contiene `analysis_sample.csv` y `sample_estimate.csv`;
- las cifras de `consistency_checks.txt` no tienen `MISMATCH`;
- el \(n\) efectivo del análisis coincide con el reportado en el artículo;
- los repositorios excluidos por `invalid` y los `ambiguous` están listados en el
  manifiesto;
- las sensibilidades de 30/90 y de bots se calculan desde los CSV de
  `data/processed*/`, no desde los clones.

## 7. Pendiente fuera de este runbook

La fase de inferencia (`src/coevolution_skills/infer.py`), la tabla de
representatividad muestra-marco (`sample_representativeness.csv`), las tablas y
figuras del artículo, la auditoría de referencias y la publicación en Zenodo se
abordan después de cerrar la corrida y el análisis descriptivo.
