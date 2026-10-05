# Co-evolución de skills y código

Este repositorio contiene el procedimiento reproducible para seleccionar y
extraer repositorios de la investigación y para procesar su actividad. Las
etapas implementadas son:

1. construir el marco de candidatos desde la versión completa de GitSkills;
2. generar un orden determinista por repositorio;
3. comprobar criterios de inclusión mediante Git;
4. conservar los primeros diez repositorios elegibles;
5. preparar y validar la revisión humana del screening;
6. procesar la actividad de commits en ventanas fijas previas y posteriores;
7. preparar y revisar las rutas modificadas en las ventanas;
8. calcular los descriptivos de RQ1 y RQ2;
9. verificar las reglas y la reproducibilidad con pruebas automatizadas.

El análisis inferencial y la muestra definitiva quedan fuera de esta etapa.

## Requisitos

- Git 2.31 o posterior;
- [`uv`](https://docs.astral.sh/uv/);
- conexión a Hugging Face y GitHub;
- espacio suficiente para los diez repositorios seleccionados y un clon
  temporal adicional.

No es necesario descargar la base SQLite de 41 GB. DuckDB consulta los Parquet
de `artifacts` y `repos` en Hugging Face usando únicamente las columnas
necesarias. La primera ejecución puede transferir una cantidad considerable de
datos porque debe examinar las 3,797,117 ocurrencias de `artifacts`.

## Ejecución

Desde la raíz del repositorio:

```bash
uv lock
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

El comando se puede interrumpir y volver a ejecutar. Los resultados de
screening terminados se conservan en archivos JSON individuales y no se
repiten. El comando rechazará reutilizar el directorio de salida si cambió la
configuración congelada; en ese caso debe usarse otro `output_directory`.
`target_count` es la excepción deliberada: puede aumentarse para continuar el
mismo orden hasta formar la muestra definitiva.

## Protocolo congelado

La configuración predeterminada usa:

- dataset `mvaccargiu/gitskills` completo;
- revisión `ebab17454a7c236f8f26b183567f1a126f42e3f8`;
- repositorios no fork cuyo lenguaje principal es Python;
- fecha de corte `2026-10-04T23:59:59Z`;
- ventanas de 60 días antes y después de la adopción;
- semilla textual `20261004`;
- objetivo de diez repositorios elegibles.

Las reglas implementadas están identificadas internamente como protocolo de
screening versión `2`. Si se modifica una regla de población o elegibilidad,
debe incrementarse esa versión y utilizarse un nuevo directorio de salida para
evitar mezclar decisiones incompatibles.

El orden se obtiene con:

```text
SHA256("20261004:" + repo_full_name)
```

seguido de `repo_full_name` como desempate. Para ampliar posteriormente la
muestra, se debe reutilizar la misma revisión, fecha de corte, semilla, reglas y
`candidate_order.csv`, y aumentar `target_count`. Los diez casos iniciales son
así un prefijo de la muestra ampliada. Si el piloto obliga a cambiar una regla,
se debe crear una nueva extracción y repetir el screening desde la primera
posición.

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
restringe a ubicaciones ancladas a la raíz para mantener una definición
homogénea y reproducible. Esas formas se tratarán como una limitación de
cobertura. El archivo debe tener front matter YAML con
`name` y `description`. `name` debe coincidir con el directorio padre, tener de
1 a 64 caracteres y contener únicamente minúsculas, números y guiones, sin
guiones iniciales, finales o consecutivos. `description` debe tener de 1 a
1024 caracteres.

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

## Salidas

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

`candidate_frame.csv` contiene el marco deduplicado y
`candidate_order.csv` su orden completo. `screening_log.csv` incluye candidatos
aceptados y rechazados. Los JSON bajo `screening/` son puntos de control
atómicos para reanudar la ejecución.

Los repositorios seleccionados son clones parciales (`--filter=blob:none`) con
el historial completo alcanzable desde la rama predeterminada. El árbol de
trabajo queda en el último commit anterior a la fecha de corte. Git puede
descargar blobs faltantes bajo demanda.

## Organización de datos

Las etapas del flujo usan ubicaciones separadas:

```text
data/extraction/           marco, screening y metadatos preservados
data/validation/           validación humana del screening
data/processed/            eventos y conteos de actividad (fase 2)
data/activity-validation/  revisión de rutas modificadas (fase 4)
results/                   descriptivos para RQ1 y RQ2 (fase 5)
repositories/              clones locales, no incluidos en el paquete publicado
```

`data/extraction/` y `data/validation/` son entradas inmutables para las fases
posteriores. Los comandos posteriores que generen datos usarán directorios
nuevos y rechazarán sobrescribir una ejecución existente. Cada manifiesto debe
registrar la configuración, la versión del protocolo, la fecha UTC, las
versiones de herramientas y los SHA-256 de sus entradas. Los clones de
`repositories/` son insumos locales y no son necesarios para reproducir los
resultados desde los datos procesados preservados.

## Preparación de la validación manual

Después de completar la extracción, se prepara la evidencia de auditoría con:

```bash
uv run prepare-validation --config extraction.toml
```

El comando no decide si la clasificación es correcta. Ejecuta comprobaciones
mecánicas, prepara los diez eventos de adopción y selecciona mediante SHA-256
hasta diez rutas de cada categoría: `skill`, `production`, `test` y `other`.
La selección procura representar una vez a cada repositorio antes de completar
posiciones adicionales.

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
`README.md` generado. Los checks automáticos no sustituyen esa revisión. Si la
ejecución falla y deja un directorio parcial, debe conservarse para diagnóstico
y usarse otro valor de `--output`, o apartarse explícitamente antes de repetir;
el comando nunca sobrescribe una revisión existente.

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
previsualizaciones extensas y nunca modifica los CSV. Para obtener únicamente
los comandos sin ejecutarlos:

```bash
uv run review-validation --commands-only adoption 1
uv run review-validation --commands-only path 1
```

Las posiciones de adopción van de 1 a 10 y las de rutas de 1 a 40 en el piloto
actual. Después de inspeccionar la evidencia, las decisiones se completan
manualmente en los formularios CSV.

El asistente rechaza encabezados distintos de los 18 y 13 campos esperados,
filas adicionales, campos desplazados, posiciones no contiguas, evidencia que
no corresponda a `selected_repositories.csv` y decisiones humanas incompletas
o inconsistentes. Al cerrar la revisión se conserva y comprueba
`data/validation/human_review.sha256`.

## Procesamiento de actividad

Con la validación humana cerrada y los diez clones preservados, se ejecuta:

```bash
uv run process-activity --config extraction.toml
```

El comando valida primero las entradas de extracción y revisión, comprueba la
rama predeterminada y los commits preservados, y crea un directorio nuevo
`data/processed/`. Nunca sobrescribe una ejecución existente. Usa la fecha del
committer en UTC y ventanas de 60 días con estas fronteras: período previo
`[t0-60 días, t0)` y período posterior `(t0, t0+60 días]`. El commit de adopción
y todos los merges quedan excluidos de los conteos.

Las salidas son:

```text
data/processed/
  processing_manifest.json  configuración, versiones y checksums
  processing.log            registro operativo
  commit_activity.csv       una fila por commit y categoría observada
  file_changes.csv          cambios de rutas auditables
  activity_counts.csv       80 conteos, incluidos los ceros explícitos
```

Adiciones y modificaciones se clasifican por la ruta resultante; eliminaciones,
por la ruta del padre. Los renombrados conservan ambas rutas y la similitud de
Git, y pueden afectar dos categorías. Cada commit cuenta como máximo una vez
por categoría. La clasificación es la misma función usada en la validación del
screening.

## Validación de rutas modificadas

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

Si la revisión detecta un error evidente, no se edita `data/processed/` en
sitio: se corrige la regla, se incrementa `PROCESSING_PROTOCOL_VERSION` en
`src/coevolution_skills/process.py`, se reprocesa en un directorio nuevo y se
repite esta revisión completa, documentando el ajuste.

## Análisis descriptivo de RQ1 y RQ2

A partir de los conteos procesados se calculan los descriptivos:

```bash
uv run analyze-results --config extraction.toml
```

El comando crea un directorio nuevo `results/` que no debe existir previamente.
Lee `data/processed/activity_counts.csv` y exige que las 80 filas estén en
estado `observed`; cualquier fila `missing` o `incomplete` detiene el análisis.
Solo se usan las categorías `skill`, `production` y `test`; `other` queda fuera.

**RQ1** usa únicamente el período posterior. **RQ2** calcula por repositorio la
diferencia `post - pre` para producción y pruebas. Los cuantiles se obtienen por
interpolación lineal (`rango = q * (n - 1)`). No se ejecutan pruebas
inferenciales ni se presentan los resultados como representativos de la
población.

Las salidas son:

```text
results/
  analysis_manifest.json
  rq1_repository_counts.csv
  rq1_summary.csv
  rq2_repository_differences.csv
  rq2_summary.csv
  consistency_checks.txt
```

`consistency_checks.txt` reconstruye cada cifra desde `activity_counts.csv` y
deja constancia de que no se ejecutó inferencia. Las tablas y figuras del
artículo deben elaborarse personalmente a partir de estos CSV; el comando no las
genera.

### Figuras (utilidad opcional)

La pauta de la Etapa 2 exige que las tablas y figuras del artículo sean
elaboradas por los estudiantes y **prohíbe que la IA generativa las produzca**.
Por eso `scripts/plot_results.py` se ofrece solo como utilidad reutilizable del
paquete: genera figuras de partida que debes revisar, adaptar y asumir como
propias antes de usarlas.

```bash
uv run python scripts/plot_results.py --results results --output figures
```

Produce en `figures/` (PNG a 300 dpi y PDF): boxplots de RQ1 por categoría y por
repositorio, boxplot de períodos y de diferencias de RQ2, y barras de las
diferencias por repositorio. Usa `matplotlib` y `numpy`, declarados en el grupo
de desarrollo.

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
y la reproducibilidad de dos ejecuciones sobre las mismas entradas. Los tests
están en:

```text
tests/
  test_classification.py
  test_windows.py
  test_git_changes.py
  test_processing.py
  test_validation_csv.py
  test_activity_validation.py
  test_analysis.py
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
candidato del orden congelado. Ante un error inesperado, el comando se detiene
sin escribir un resultado para ese candidato, de modo que pueda revisarse y
reintentarse sin introducir una exclusión metodológica artificial.

## Alcance y reproducibilidad

GitSkills es un snapshot de repositorios visibles en GitHub y constituye un
límite inferior de la población, no un censo completo de GitHub. Además, un
repositorio puede desaparecer o reescribir su historia después del snapshot.
Por esto se preservan la revisión del dataset, URL, rama predeterminada, commit
de corte, commit de adopción, fechas UTC, versión de Git y motivo de cada
exclusión.

Para una entrega o publicación deben conservarse las salidas CSV/JSON y fijarse
la versión del código mediante un commit o etiqueta. Los clones completos no
necesitan publicarse y pueden estar sujetos a las licencias de sus repositorios
de origen.
