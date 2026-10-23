# Procedimiento detallado

Documentación operativa del flujo de extracción, procesamiento y análisis. El
`README.md` resume el paquete de réplica y cómo reproducir los resultados; este
archivo conserva el detalle de cada paso.

## Extracción

```bash
uv run extract-repositories --config extraction.toml
```

`uv lock` genera `uv.lock` con las versiones concretas resueltas. Este archivo
debe conservarse en el paquete de réplica y versionarse antes de la ejecución
usada para producir los resultados de la entrega.

Para observar qué configuración y rutas se usarían sin consultar GitSkills ni
clonar repositorios:

```bash
uv run extract-repositories --config extraction.toml --show-config
```

El comando se puede interrumpir y volver a ejecutar. Los resultados de screening
terminados se conservan en archivos JSON individuales y no se repiten. El comando
rechazará reutilizar el directorio de salida si cambió la configuración
congelada; en ese caso debe usarse otro `output_directory`. `target_count` y
`max_candidates` son las excepciones deliberadas: el primero puede aumentarse
para continuar el mismo orden hasta formar la muestra definitiva y el segundo es
un tope de candidatos examinados que no altera el orden ni la elegibilidad.

### Protocolo congelado

La configuración predeterminada usa:

- dataset `mvaccargiu/gitskills` completo;
- revisión `ebab17454a7c236f8f26b183567f1a126f42e3f8`;
- repositorios no fork cuyo lenguaje principal es Python;
- fecha de corte `2026-10-04T23:59:59Z`;
- ventanas de 60 días antes y después de la adopción;
- semilla textual `20261004`;
- objetivo de 120 repositorios elegibles, con un tope de 1.500 candidatos examinados.

Las reglas implementadas están identificadas internamente como protocolo de
screening versión `2`. Si se modifica una regla de población o elegibilidad, debe
incrementarse esa versión y utilizarse un nuevo directorio de salida para evitar
mezclar decisiones incompatibles.

El orden se obtiene con:

```text
SHA256("20261004:" + repo_full_name)
```

seguido de `repo_full_name` como desempate. Para ampliar posteriormente la
muestra, se debe reutilizar la misma revisión, fecha de corte, semilla, reglas y
`candidate_order.csv`, y aumentar `target_count`. Los diez casos iniciales son
así un prefijo de la muestra ampliada. Si el piloto obliga a cambiar una regla,
se debe crear una nueva extracción y repetir el screening desde la primera
posición. Si se alcanza `max_candidates` antes de `target_count`, la ejecución
termina con \(n<120\) y lo registra en `run_manifest.json`.

### Requisitos adicionales de la extracción

- conexión a Hugging Face y GitHub;
- espacio suficiente para los 120 repositorios seleccionados y un clon temporal
  adicional.

No es necesario descargar la base SQLite de 41 GB. DuckDB consulta los Parquet de
`artifacts` y `repos` en Hugging Face usando únicamente las columnas necesarias.
La primera ejecución puede transferir una cantidad considerable de datos porque
debe examinar las 3,797,117 ocurrencias de `artifacts`.

## Definición de skill válido

Se acepta exclusivamente un `SKILL.md`, respetando mayúsculas, en uno de estos
patrones anclados a la raíz:

```text
.agent/skills/<name>/SKILL.md
.agents/skills/<name>/SKILL.md
.claude/skills/<name>/SKILL.md
.codex/skills/<name>/SKILL.md
.cursor/skills/<name>/SKILL.md
.gemini/skills/<name>/SKILL.md
.github/skills/<name>/SKILL.md
.opencode/skills/<name>/SKILL.md
```

Esta unión corresponde a ubicaciones de proyecto documentadas por Antigravity,
Codex, Claude Code, Cursor, Gemini CLI, GitHub Copilot y OpenCode. Se consultan
las siguientes fuentes oficiales: [Agent Skills](https://agentskills.io/specification),
[Antigravity](https://antigravity.google/docs/skills),
[Codex](https://developers.openai.com/codex/customization/overview),
[Claude Code](https://code.claude.com/docs/en/skills),
[Cursor](https://cursor.com/docs/skills),
[Gemini CLI](https://geminicli.com/docs/cli/skills/),
[GitHub Copilot](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-skills)
y [OpenCode](https://opencode.ai/v2/docs/skills/).

No se aceptan niveles intermedios. Aunque algunos clientes permiten skills
anidados en monorepos, plugins o rutas configurables, el análisis principal se
restringe a ubicaciones ancladas a la raíz para mantener una definición homogénea
y reproducible. Esas formas se tratarán como una limitación de cobertura. El
archivo debe tener front matter YAML con `name` y `description`. `name` debe
coincidir con el directorio padre, tener de 1 a 64 caracteres y contener
únicamente minúsculas, números y guiones, sin guiones iniciales, finales o
consecutivos. `description` debe tener de 1 a 1024 caracteres.

En GitSkills, el enriquecimiento del front matter existe solamente en la fila
`dedup_primary` de cada contenido. El comando une las ocurrencias con su
representante mediante `file_sha` antes de aplicar estas reglas.

## Screening de elegibilidad

Los candidatos se examinan siempre en el orden generado. Un repositorio se
incluye cuando:

- es accesible y tiene rama predeterminada;
- su historial de la rama predeterminada puede recuperarse sin `--depth`;
- se identifica una transición desde cero skills válidos a uno o más skills
  válidos en la historia de primer padre de la rama;
- existen 60 días observables antes y después de la adopción, considerando la
  fecha de corte congelada;
- el árbol de adopción contiene al menos un archivo Python de producción y uno
  de pruebas.

No se exige actividad en ninguna categoría. Un futuro conteo igual a cero será
un resultado válido.

Los archivos dentro de carpetas de skills no cuentan como producción o pruebas.
Se consideran pruebas los `.py` bajo un componente `test` o `tests`, y los
archivos `test_*.py`, `*_test.py` y `conftest.py`. Los restantes `.py` son
producción, excepto los ubicados bajo `venv`, `.venv`, `build`, `dist`,
`__pycache__`, `docs`, `doc`, `examples` o `example`.

## Salidas de la extracción

Por defecto se generan:

```text
data/extraction/
  run_manifest.json
  extraction.log
  candidate_frame.csv
  candidate_order.csv
  screening_log.csv
  selected_repositories.csv
  screening/
repositories/
  <owner>__<repository>/
```

`candidate_frame.csv` contiene el marco deduplicado y `candidate_order.csv` su
orden completo. `screening_log.csv` incluye candidatos aceptados y rechazados.
Los JSON bajo `screening/` son puntos de control atómicos para reanudar la
ejecución.

Los repositorios seleccionados son clones parciales (`--filter=blob:none`) con
el historial completo alcanzable desde la rama predeterminada. El árbol de
trabajo queda en el último commit anterior a la fecha de corte. Git puede
descargar blobs faltantes bajo demanda.

## Organización de datos

Las etapas del flujo usan ubicaciones separadas:

```text
data/extraction/           marco, screening y metadatos preservados
data/validation/           validación humana del screening
data/processed/            eventos y conteos de actividad
data/activity-validation/  revisión de rutas modificadas
results/                   descriptivos para RQ1 y RQ2
results-inference/         tests, efectos, IC y sensibilidades
repositories/              clones locales, no incluidos en el paquete publicado
```

`data/extraction/` y `data/validation/` son entradas inmutables para las fases
posteriores. Los comandos posteriores que generen datos usarán directorios nuevos
y rechazarán sobrescribir una ejecución existente. Cada manifiesto registra la
configuración, la versión del protocolo, la fecha UTC, las versiones de
herramientas y los SHA-256 de sus entradas. Los clones de `repositories/` son
insumos locales y no son necesarios para reproducir los resultados desde los
datos procesados preservados.

## Validación manual del screening

Después de completar la extracción, se prepara la evidencia de auditoría con:

```bash
uv run prepare-validation --config extraction.toml
```

El comando no decide si la clasificación es correcta. Ejecuta comprobaciones
mecánicas, prepara un evento de adopción por repositorio seleccionado y
selecciona mediante SHA-256 hasta `--maximum-per-category` rutas de cada
categoría: `skill`, `production`, `test` y `other`. La selección procura
representar una vez a cada repositorio antes de completar posiciones
adicionales. En Etapa 3 se usa `--maximum-per-category 1`.

Se crea un directorio nuevo que no debe existir previamente:

```text
data/validation/
  README.md
  validation.log
  validation_manifest.json
  automated_checks.csv
  adoption_review.csv
  path_review.csv
```

El estudiante debe completar únicamente las columnas de revisión humana en
`adoption_review.csv` y `path_review.csv`, siguiendo las instrucciones del
`README.md` generado. Criterios congelados de adopción:

- **`valid`:** primera incorporación de al menos un `SKILL.md` que cumple ruta y
  front matter; antes de \(t_0\) no había skills válidos; no es solo un
  renombrado o movimiento. Exige `human_rename_only = no` y
  `human_frontmatter_and_paths_valid = yes`.
- **`invalid`:** la evidencia contradice la clasificación automática (front matter
  o ruta inválidos, skill válido preexistente, renombrado sin adopción real,
  artefacto de merge o historia inconsistente que no represente una transición
  real desde el primer padre). Que \(t_0\) sea un merge no es inválido por sí
  mismo; se evalúa la transición desde su primer padre.
- **`ambiguous`:** evidencia insuficiente o caso frontera; se conserva con
  bandera y se analiza también sin estos casos.

Si la primera adopción es `invalid`, no se busca una segunda y no se reemplaza el
repositorio. Los checks automáticos no sustituyen esa revisión. Para una
ejecución exploratoria puede continuarse con esos campos en blanco mediante el
modificador descrito en «Modo exploratorio», pero la entrega se generó con la
revisión completa. Si la ejecución falla y deja un directorio parcial, debe
conservarse para diagnóstico y usarse otro valor de `--output`, o apartarse
explícitamente antes de repetir; el comando nunca sobrescribe una revisión
existente.

Para evitar construir comandos copiando hashes y rutas desde los CSV, puede
usarse el asistente de revisión de solo lectura:

```bash
# Mostrar posiciones y cuáles siguen pendientes
uv run review-validation list

# Comprobar esquema, correspondencia con la extracción y campos humanos
uv run review-validation validate

# Mostrar evidencia de un evento de adopción
uv run review-validation adoption 1

# Mostrar evidencia de una ruta muestreada
uv run review-validation path 1
```

El asistente imprime cada comando Git antes de ejecutarlo, limita las
previsualizaciones extensas y nunca modifica los CSV. Para obtener únicamente los
comandos sin ejecutarlos:

```bash
uv run review-validation --commands-only adoption 1
uv run review-validation --commands-only path 1
```

Las posiciones de adopción van de 1 a \(n\) (120 en la muestra definitiva, o el
\(n\) logrado si se alcanza el tope) y las de rutas de 1 a 4 con
`--maximum-per-category 1` (una por categoría). Después de inspeccionar la
evidencia, las decisiones se completan manualmente en los formularios CSV.

El asistente rechaza encabezados distintos de los 18 y 13 campos esperados, filas
adicionales, campos desplazados, posiciones no contiguas, evidencia que no
corresponda a `selected_repositories.csv` y decisiones humanas incompletas o
inconsistentes. Al cerrar la revisión se conserva y comprueba
`data/validation/human_review.sha256`.

### Modo exploratorio: continuar con la revisión pendiente

Las decisiones humanas del *screening* son **obligatorias por defecto** para
`process-activity`, tal como se describió arriba. Para ejecuciones exploratorias
o para una muestra ampliada, donde revisar cada formulario a mano no es viable,
se puede continuar con la revisión pendiente:

```bash
uv run process-activity --config extraction.toml --allow-pending-human-review
```

Este modificador **solo** tolera campos humanos en blanco. La evidencia
preservada y las comprobaciones automáticas siguen siendo obligatorias: si un CSV
está malformado, una columna preservada no coincide con la extracción o alguna de
las comprobaciones automáticas falla, el comando se detiene. Cuando la revisión
queda pendiente, el manifiesto lo registra explícitamente:

```json
"human_screening_review": {
  "required": false,
  "status": "pending",
  "pending_adoptions": [ ... ],
  "pending_paths": [ ... ],
  "checksum_file_present": false
}
```

`analyze-results` no se bloquea por este estado: continúa y copia el bloque a
`analysis_manifest.json`. Los resultados preliminares entregados se generaron con
la revisión **completa** (`status: "complete"`), por lo que este modificador no
altera ninguna cifra publicada; solo acelera corridas futuras.

## Procesamiento de actividad

Con la validación humana cerrada y los 120 clones preservados, se ejecuta:

```bash
uv run process-activity --config extraction.toml
```

El comando valida primero las entradas de extracción y revisión, comprueba la
rama predeterminada y los commits preservados, y crea un directorio nuevo
`data/processed/`. Nunca sobrescribe una ejecución existente. Por defecto exige
la revisión humana del screening cerrada; ver «Modo exploratorio» para el
modificador que permite continuar con formularios en blanco. Usa la fecha del
committer en UTC y ventanas de 60 días con estas fronteras: período previo
`[t0-60 días, t0)` y período posterior `(t0, t0+60 días]`. El commit de adopción
y todos los merges quedan excluidos de los conteos.

Las salidas son:

```text
data/processed/
  processing_manifest.json  configuración, versiones y checksums
  processing.log            registro operativo
  commit_activity.csv       una fila por commit y categoría observada
  file_changes.csv          cambios de rutas auditables, con líneas añadidas/eliminadas
  activity_counts.csv       conteos por repositorio, período y categoría
  skill_contents.csv        SHA-256 del SKILL.md adoptado en t0 (D9)
```

El esquema actual es el protocolo de procesamiento **v2**. `commit_activity.csv`
registra `author_name`, `author_email`, `committer_name`, `committer_email` e
`is_bot`; `file_changes.csv` registra `lines_added`, `lines_deleted` e
`is_binary`. Los binarios cuentan como cero líneas y quedan
marcados. Los renombrados conservan ambas rutas; para el *churn* se imputan a la
categoría de la ruta resultante (o a la del padre en eliminaciones).

Si la extracción termina con \(n<120\) por el tope de candidatos o por agotar el
marco, `process-activity` acepta el déficit solo cuando `run_manifest.json`
registra `completion_status` igual a `cap_reached` o `frame_exhausted`. Para las
sensibilidades de ventana puede usarse:

```bash
uv run process-activity --config extraction.toml --window-days 30 --output data/processed-30
uv run process-activity --config extraction.toml --window-days 90 --output data/processed-90
```

La opción `--window-days` solo cambia las ventanas de actividad; no altera la
elegibilidad ni la validación sellada. Con 90 días, los repositorios sin 90 días
completos antes y después se excluyen de esa sensibilidad y se reporta el \(n\).

Adiciones y modificaciones se clasifican por la ruta resultante; eliminaciones,
por la ruta del padre. Los renombrados conservan ambas rutas y la similitud de
Git, y pueden afectar dos categorías. Cada commit cuenta como máximo una vez por
categoría. La clasificación es la misma función usada en la validación del
screening.

## Validación de rutas modificadas

> En Etapa 3 no se repite esta validación: se conserva la de Etapa 2 archivada y
> la revisión acotada de Etapa 3 se limita a las 4 rutas del árbol de adopción
> descritas arriba.

Sobre las salidas de la fase anterior se prepara la revisión humana de las rutas
efectivamente modificadas en las ventanas:

```bash
uv run prepare-activity-validation --config extraction.toml
```

El comando crea un directorio nuevo `data/activity-validation/` que no debe
existir previamente y selecciona hasta diez cambios por categoría desde
`data/processed/file_changes.csv`. El estrato es la categoría efectiva
(`new_category` si existe, si no `old_category`). El orden es por SHA-256 con la
semilla `20261004:activity-validation:1`, garantizando una eliminación y un
renombrado por categoría cuando existan y procurando cubrir cada repositorio
antes de repetirlo. Para eliminaciones y renombrados el formulario incluye la
ruta anterior y la nueva, y resuelve el commit padre para poder ver el contenido
previo.

Las salidas son:

```text
data/activity-validation/
  README.md
  activity_validation.log
  activity_validation_manifest.json
  activity_review.csv
```

El asistente de revisión es de solo lectura salvo al sellar:

```bash
uv run review-activity-validation list              # posiciones y estado
uv run review-activity-validation show 12           # evidencia de un cambio
uv run review-activity-validation validate          # exige los campos humanos
uv run review-activity-validation validate --seal   # valida y sella checksums
```

`show` imprime el contenido anterior y nuevo y los comandos Git antes de
ejecutarlos; `--commands-only` los muestra sin ejecutarlos
(`uv run review-activity-validation --commands-only show 12`). `validate` exige
categorías humanas válidas, `agreement` coherente con
`human_category == automatic_category`, revisor y fecha UTC. `--seal` escribe
`human_activity_review.sha256` una sola vez y se niega a resellarlo.

Si la revisión detecta un error evidente, no se edita `data/processed/` en sitio:
se corrige la regla, se incrementa `PROCESSING_PROTOCOL_VERSION` en
`src/coevolution_skills/process.py`, se reprocesa en un directorio nuevo y se
repite esta revisión completa, documentando el ajuste.

## Análisis descriptivo de RQ1 y RQ2

A partir de los conteos procesados se calculan los descriptivos:

```bash
uv run analyze-results --config extraction.toml
```

El comando crea un directorio nuevo `results/` que no debe existir previamente.
Lee `data/processed/activity_counts.csv` y exige que todas las filas estén en
estado `observed`; cualquier fila `missing` o `incomplete` detiene el análisis. Solo se
usan las categorías `skill`, `production` y `test`; `other` queda fuera.

**RQ1** usa únicamente el período posterior. **RQ2** calcula por repositorio la
diferencia `post - pre` para producción y pruebas. Los cuantiles se obtienen por
interpolación lineal (`rango = q * (n - 1)`). Este comando descriptivo no ejecuta
pruebas inferenciales; el módulo `infer-results` las calcula por separado a partir
de `results/analysis_sample.csv`.

`analyze-results` lee `data/validation/adoption_review.csv` y aplica las
decisiones humanas: excluye las adopciones `invalid`, conserva las `ambiguous`
con bandera y registra el \(n\) efectivo. `data/processed/` no se modifica. Si el
manifiesto de procesamiento declara una revisión completa y faltan las decisiones,
el comando se detiene. También estima el rendimiento del cribado y \(\hat N\) a
partir de `screening_log.csv` y `candidate_order.csv`, con un intervalo de Wilson
aproximado.

Las salidas son:

```text
results/
  analysis_manifest.json
  analysis_sample.csv       decisión por repositorio y si entra en el análisis
  rq1_repository_counts.csv
  rq1_summary.csv
  rq2_repository_differences.csv
  rq2_summary.csv
  sample_estimate.csv       k, rendimiento, N estimada e IC
  consistency_checks.txt
```

`consistency_checks.txt` reconstruye cada cifra desde `activity_counts.csv` y
deja constancia de que no se ejecutó inferencia. Las tablas y figuras del artículo
deben elaborarse personalmente a partir de estos CSV; el comando no las genera.

## Análisis inferencial de RQ1 y RQ2

El plan congelado (`docs/analysis-plan.md`, v1.2) se ejecuta después del análisis
descriptivo y escribe un directorio nuevo:

```bash
uv run infer-results --config extraction.toml --fetch-representation
```

`infer-results` requiere SciPy, fijado en `pyproject.toml` y `uv.lock`. Lee
`results/analysis_sample.csv` (para respetar el \(n\) efectivo y las adopciones
`invalid`) y los CSV de `data/processed/`; no modifica `results/` ni `data/`.
Escribe en `results-inference/`:

```text
rq1_tests.csv                 Friedman, W de Kendall y Wilcoxon por pares
rq2_tests.csv                 Wilcoxon de post-pre por categoría
effect_sizes.csv              rango-biserial y Hodges-Lehmann
bootstrap_ci.csv              IC percentiles al 95 %
sign_tests.csv                test del signo y Clopper-Pearson
sensitivity_tests.csv         pruebas de las sensibilidades
sensitivity_effects.csv       efectos de las sensibilidades
sensitivity_ci.csv            IC de las sensibilidades
sensitivity_sign_tests.csv    test del signo de las sensibilidades
sensitivity_status.csv        estado y n de cada sensibilidad
sensitivity_churn.csv         commits/archivo, churn/archivo y proporción
skill_cluster_assignment.csv  grupos por firma de contenido (D9)
bot_commits_review.csv        coincidencias del patrón congelado de bots
sample_representativeness.csv muestra frente al marco
consistency_checks.txt        reconstrucción de pruebas, efectos e IC
inference_manifest.json       configuración, semillas, versiones y checksums
```

Las pruebas son las del plan: Friedman con corrección por empates para RQ1;
Wilcoxon por pares solo si el global es significativo; Wilcoxon de \(post-pre\)
para RQ2; Holm dentro de cada RQ; rango-biserial apareado y diferencia de
Hodges-Lehmann. El bootstrap usa el repositorio como unidad, 10.000 remuestreos y
la semilla `20261020`. El test del signo es la sensibilidad D6: se marca
`robust=true` solo cuando coincide en dirección y significación con Wilcoxon; si
discrepan, se reportan ambos y no se afirma el efecto.

Las sensibilidades se calculan desde los CSV procesados:

- **D7:** *commits* por archivo, *churn* por archivo y proporción de *commits*.
  Los binarios se excluyen del numerador de *churn* y se cuentan aparte; los
  renombrados se imputan a la ruta resultante y las eliminaciones a la del padre.
- **Bots (no reportada):** se excluyen los commits cuyo autor o *committer*
  coincide con el patrón congelado. `bot_commits_review.csv` deja las
  coincidencias para revisión manual; en esta entrega no se revisan ni se
  reportan, y la presencia de bots se declara como amenaza.
- **Ambigüedad:** si hay adopciones `ambiguous`, se repite el análisis sin ellas.
- **D9:** se agrupa por la tupla ordenada de SHA-256 de los `SKILL.md` en \(t_0\)
  y se conserva el `repo_full_name` lexicográficamente menor de cada grupo.
- **Ventanas 30/90 (no ejecutadas):** se activan solo si se pasan
  `--processed-30` y `--processed-90` a `infer-results`. Con 90 días se excluyen
  los repositorios que el manifiesto marca con `pre_window_complete` o
  `post_window_complete` en `false`. La ventana principal sigue siendo 60 días.

La representatividad usa `candidate_frame.csv`, `selected_repositories.csv` y,
si se pasa `--fetch-representation`, las tablas `repos` y `artifacts` de la
revisión congelada de GitSkills. Se informan mediana y RIC del número de *skills*
y de la fracción de *skills* copiadas verbatim; las estrellas y la antigüedad no
estaban disponibles y se reportan como faltantes. No se hacen pruebas de
significación frente al marco: la muestra proviene de \(E\), no del marco.

### Figuras (utilidad opcional)

La pauta de la Etapa 2 exige que las tablas y figuras del artículo sean elaboradas
por los estudiantes y **prohíbe que la IA generativa las produzca**. Por eso
`scripts/plot_results.py` se ofrece solo como utilidad reutilizable del paquete:
genera figuras de partida que debes revisar, adaptar y asumir como propias antes
de usarlas.

```bash
uv run python scripts/plot_results.py \
  --results results \
  --inference results-inference \
  --output figures-etapa3 \
  --forest --sign --sensitivity
```

Produce en `figures-etapa3/` (PNG a 300 dpi y PDF):

- `fig_rq1_boxplot`: caja de *commits* posteriores por categoría;
- `fig_rq2_boxplot`: caja de *commits* previos y posteriores por categoría;
- `fig_effects` (opcional): efectos de Hodges–Lehmann con IC para RQ1 y RQ2;
- `fig_sign_tests` (opcional): test del signo con IC exacto de Clopper–Pearson y
  concordancia con Wilcoxon;
- `fig_rq1_sensitivity` (opcional): dirección de RQ1 con el conteo absoluto y
  cada sensibilidad marcada como `computed` en `sensitivity_status.csv`; los
  paneles se descubren desde ese archivo, sin editar el script. Las
  sensibilidades no calculadas se listan en el `README.md` generado.

También escribe `README.md` y `manifest.json` con checksums y versiones. Se
niega a escribir en un directorio no vacío salvo que se pase `--force`. Usa
`matplotlib` y `numpy`, declarados en el grupo de desarrollo. Las barras por
repositorio de la Etapa 2 se retiraron porque 120 etiquetas no son legibles en
IEEE a dos columnas.

## Pruebas automatizadas

`pytest` es una dependencia de desarrollo declarada en `[dependency-groups]` de
`pyproject.toml`. Después de `uv lock` y `uv sync`, se ejecuta:

```bash
uv run pytest
```

Las pruebas usan repositorios Git sintéticos y temporales, sin depender de los
clones de `repositories/` ni de la red. Cubren las fronteras exactas de las
ventanas, la exclusión del commit de adopción y de los merges, la agregación por
categoría con ceros explícitos, los cambios A/M/D/R, la clasificación de skills,
producción, pruebas y rutas excluidas, el esquema estricto de los CSV de revisión
y la reproducibilidad de dos ejecuciones sobre las mismas entradas. También
comprueban que la revisión humana pendiente bloquea por defecto, que el
modificador `--allow-pending-human-review` no relaja la evidencia mecánica y que
el estado se propaga al manifiesto del análisis. Los tests están en:

```text
tests/
  test_classification.py
  test_windows.py
  test_git_changes.py
  test_processing.py
  test_validation_csv.py
  test_activity_validation.py
  test_analysis.py
  test_inference.py
```

Estas pruebas son control de calidad interno del paquete de réplica: la pauta de
la Etapa 2 no las exige y no forman parte de las tablas ni figuras del artículo.

## Códigos de screening

| Código | Significado |
|---|---|
| `eligible` | Cumple todos los criterios de inclusión. |
| `repository_unavailable` | GitHub no expone `HEAD` o la rama predeterminada. |
| `clone_failed` | El clon falló después de los reintentos configurados. |
| `default_branch_missing` | No se pudo interpretar la referencia simbólica de `HEAD`. |
| `history_incomplete` | No existe un commit alcanzable anterior a la fecha de corte o falta el padre requerido. |
| `no_valid_skill` | No se encontró una transición histórica hacia un skill válido. |
| `adoption_not_found` | Hubo cambios en rutas de skills, pero no se reconstruyó una adopción válida. |
| `insufficient_pre_window` | El repositorio no tiene historia al menos 60 días antes de la adopción. |
| `insufficient_post_window` | La adopción ocurrió a menos de 60 días de la fecha de corte. |
| `no_production_python` | El árbol de adopción no contiene Python de producción. |
| `no_test_python` | El árbol de adopción no contiene Python de pruebas. |
| `python_only_inside_skill` | Los únicos `.py` están dentro de carpetas de skills. |
| `history_rewritten_or_corrupt` | Git no pudo leer de forma consistente commits, árboles o blobs requeridos. |
| `unexpected_error` | Reservado para fallos no clasificados. El comando se detiene y no descarta silenciosamente al candidato. |

Los errores transitorios de red se reintentan. No se seleccionan reemplazos
manualmente: después de una exclusión el comando continúa con el siguiente
candidato del orden congelado. Ante un error inesperado, el comando se detiene sin
escribir un resultado para ese candidato, de modo que pueda revisarse y
reintentarse sin introducir una exclusión metodológica artificial.

## Alcance y limitaciones

GitSkills es un snapshot de repositorios visibles en GitHub y constituye un
límite inferior de la población, no un censo completo de GitHub. Además, un
repositorio puede desaparecer o reescribir su historia después del snapshot. Por
esto se preservan la revisión del dataset, URL, rama predeterminada, commit de
corte, commit de adopción, fechas UTC, versión de Git y motivo de cada exclusión.

Para una entrega o publicación deben conservarse las salidas CSV/JSON y fijarse
la versión del código mediante un commit o etiqueta. Los clones completos no
necesitan publicarse y pueden estar sujetos a las licencias de sus repositorios
de origen.
