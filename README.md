# Co-evolución de skills y código

Paquete de réplica de la investigación sobre co-evolución de *skills* y código.
Contiene el código, las dependencias, la configuración y los datos preservados
que permiten reproducir los resultados de RQ1 y RQ2 sin repetir la extracción de
datos. La Etapa 2 publicó los resultados preliminares; la Etapa 3 añade la
muestra definitiva, el análisis inferencial congelado, las sensibilidades y la
tabla de representatividad. Las tablas y figuras finales del artículo se
elaboran por separado.

## Paquete de réplica

| Elemento | Valor |
|---|---|
| Repositorio | [thomaswkm/coevolucion-skills-codigo](https://github.com/thomaswkm/coevolucion-skills-codigo) |
| Versión (etiqueta) | `v1.0-etapa3` |
| DOI de versión (Zenodo) | `[DOI-PENDIENTE]` |

La versión de Etapa 3 corresponde a la etiqueta `v1.0-etapa3` de este
repositorio y a su release en GitHub. Su DOI de versión en Zenodo se añadirá
tras la publicación. Los resultados inferenciales de `results-inference/`
pertenecen a esta versión. La versión de Etapa 2 (`v0.2-etapa2`) se conserva con
el DOI `10.5281/zenodo.23178699`.

El paquete incluye el código del flujo (`src/coevolution_skills/`), las pruebas
(`tests/`), la utilidad de figurado (`scripts/plot_results.py`), las dependencias
(`pyproject.toml`, `uv.lock`), la configuración (`extraction.toml`), los datos
preservados (`data/`), los resultados descriptivos (`results/`), los
inferenciales (`results-inference/`), las licencias (`LICENSE`, `LICENSE-DATA`)
y este README. No se incluyen los clones bajo `repositories/` (pesados y sujetos
a las licencias de sus repositorios de origen) ni el material local
`gitskills-sample/`.

## Entorno

Para reproducir los resultados desde los datos preservados se necesita:

- Git 2.31 o posterior;
- [`uv`](https://docs.astral.sh/uv/).
- SciPy, que `uv sync` instala y cuya versión queda fijada en `uv.lock`.

```bash
uv lock
uv sync
```

Volver a ejecutar la extracción o el clonado es opcional y, además, requiere
conexión a Hugging Face y GitHub y espacio para los 120 repositorios y un clon
temporal. No hace falta descargar la base SQLite de 41 GB. El detalle está en
`docs/procedimiento.md`.

## Etapa 3: corrida definitiva

La preparación de la muestra definitiva y del análisis de Etapa 3 está descrita
paso a paso en `docs/runbook-etapa3.md`. Incluye el archivado de las salidas de
Etapa 2, la extracción de 120 repositorios con tope de 1.500 candidatos, la
revisión humana con rúbrica, el procesamiento, el análisis descriptivo y el
análisis inferencial congelado.

Cambios de esta etapa en el flujo:

- `process-activity` acepta \(n<120\) cuando la extracción registra
  `cap_reached` o `frame_exhausted`;
- `--window-days` permite sensibilidades de 30 y 90 días sin alterar la
  elegibilidad ni la validación sellada;
- `commit_activity.csv` registra autor, *committer* e `is_bot`;
- `file_changes.csv` registra líneas añadidas/eliminadas y binarios;
- `skill_contents.csv` conserva el SHA-256 del `SKILL.md` adoptado;
- `analyze-results` excluye adopciones `invalid`, marca `ambiguous` y estima
  \(\hat N\) en `sample_estimate.csv`.

## Etapa 3: análisis inferencial

El plan congelado está en `docs/analysis-plan.md` (v1.2). Se ejecuta después del
análisis descriptivo:

```bash
uv run infer-results --config extraction.toml --fetch-representation
```

`infer-results` lee `results/analysis_sample.csv` y los CSV de
`data/processed/`, y escribe un directorio nuevo `results-inference/`; no
modifica `results/` ni `data/`. `--fetch-representation` consulta la revisión
congelada de GitSkills; las estrellas y la antigüedad no estaban disponibles, así
que la tabla de representatividad informa solo el número de *skills* y la copia
verbatim, sin imputar valores.

Las salidas principales son:

- `rq1_tests.csv`: Friedman con corrección por empates, \(W\) de Kendall y
  Wilcoxon por pares con Holm;
- `rq2_tests.csv`: Wilcoxon de \(post-pre\) por categoría con Holm;
- `effect_sizes.csv`: rango-biserial apareado y diferencia de Hodges–Lehmann;
- `bootstrap_ci.csv`: IC percentil al 95 % (10.000 remuestreos, semilla
  `20261020`);
- `sign_tests.csv`: test del signo con intervalo exacto de Clopper–Pearson;
- `sensitivity_tests.csv`, `sensitivity_effects.csv`, `sensitivity_ci.csv` y
  `sensitivity_sign_tests.csv`: *churn* por archivo, *commits* por archivo,
  proporción de *commits*, exclusión de bots, exclusión de ambiguos y un
  representante por firma de contenido de *skill* (D9). El artículo reporta
  *churn* y clustering de contenido; las demás quedan como material del paquete;
- `sensitivity_churn.csv`, `skill_cluster_assignment.csv` y
  `bot_commits_review.csv`: datos auditables de esas sensibilidades;
- `sample_representativeness.csv`: muestra frente al marco en número de *skills*
  y copia verbatim (estrellas y antigüedad no disponibles);
- `consistency_checks.txt` e `inference_manifest.json`: reconstrucción de cada
  prueba y registro de versiones, semilla, entradas y salidas.

Las sensibilidades de ventana de 30 y 90 días no se ejecutaron: quedan
registradas como `not_available` y se declaran como amenaza a la validez. La
ventana principal sigue siendo de 60 días.

## Reproducción

La reproducción parte de los datos preservados y **no** repite la extracción.

### Ruta corta: análisis desde los datos preservados

Parte de `data/processed/activity_counts.csv` y su manifiesto. No requiere clonar
repositorios ni conectarse a la red:

```bash
uv run analyze-results --config extraction.toml
uv run infer-results --config extraction.toml --fetch-representation
```

El comando `analyze-results` crea un directorio `results/` nuevo y no sobrescribe
uno existente. `infer-results` crea `results-inference/` y tampoco sobrescribe.
Para otro destino:
`uv run analyze-results --config extraction.toml --output <directorio>` y
`uv run infer-results --config extraction.toml --output <directorio>`.

### Ruta completa: reprocesar la actividad (opcional)

Reproducir el procesamiento requiere los clones de los 120 repositorios en
`repositories/`, que no se publican. Con ellos disponibles:

```bash
uv run process-activity --config extraction.toml
uv run prepare-activity-validation --config extraction.toml
uv run analyze-results --config extraction.toml
```

## Resultados descriptivos (Etapa 3)

Con los datos entregados, el análisis descriptivo produce:

**RQ1 (60 días posteriores, 120 repositorios).**

- *skills*: mediana 0, RIC 4, rango 0–32; 62 repositorios sin actividad.
- producción: mediana 17, RIC 70,75, rango 0–975; 12 repositorios sin
  actividad.
- pruebas: mediana 9,5, RIC 40,75, rango 0–777; 21 repositorios sin actividad.
- producción aparece como la categoría más modificada en 104 repositorios, los
  *skills* en 16 y las pruebas en 38; hay 26 empates y un repositorio puede
  contar en más de una categoría.

**RQ2 (diferencia `post − pre`).**

- producción: mediana previa 27 → mediana posterior 17; diferencia mediana 0,5
  (RIC 35,5; −1219 a 546); 60 repositorios aumentan, 58 disminuyen y 2 no
  cambian.
- pruebas: mediana previa 10 → mediana posterior 9,5; diferencia mediana 1
  (RIC 23,25; −498 a 500); 61 repositorios aumentan, 52 disminuyen y 7 no
  cambian.

Son resultados **descriptivos** de los 120 casos analizados: no incluyen pruebas
inferenciales ni se generalizan a la población.
`results/consistency_checks.txt` reconstruye cada cifra desde
`activity_counts.csv`.

## Resultados inferenciales (Etapa 3)

Con la muestra definitiva de 120 repositorios, la Etapa 3 produce:

**RQ1 (Friedman y pares, 60 días posteriores).**

- Friedman: \(\chi^2=150{,}72\), \(gl=2\), \(p<0{,}001\), \(W\) de Kendall
  \(=0{,}628\); 120 bloques completos.
- *skill*–producción: Wilcoxon \(p<0{,}001\) (Holm), rango-biserial
  \(=0{,}986\), Hodges–Lehmann \(=32\) commits, IC 95 % \([20{,}5;49]\).
- *skill*–pruebas: \(p<0{,}001\), rango-biserial \(=0{,}912\),
  Hodges–Lehmann \(=17{,}5\), IC \([11;29]\).
- producción–pruebas: \(p<0{,}001\), rango-biserial \(=-0{,}788\),
  Hodges–Lehmann \(=-8\), IC \([-15{,}5;-3{,}5]\).
- El test del signo coincide en dirección y significación en los tres pares.

**RQ2 (Wilcoxon de \(post-pre\)).**

- producción: \(p=0{,}759\) (Holm), rango-biserial \(=-0{,}032\),
  Hodges–Lehmann \(=-1\), IC \([-8;4{,}5]\).
- pruebas: \(p=0{,}360\) (Holm), rango-biserial \(=0{,}145\),
  Hodges–Lehmann \(=2{,}5\), IC \([-1;7{,}5]\).
- El test del signo no cambia la conclusión: no se detecta variación entre
  períodos en ninguna de las dos categorías.

La exclusión de bots por patrón congelado y el representante por firma de
contenido conservan la dirección de RQ1 y la ausencia de efecto en RQ2. La
normalización por archivo **no** conserva la dirección: con *commits* por
archivo, los *skills* superan a producción y *skill*–pruebas deja de ser
significativo; con *churn* por archivo, producción–pruebas se invierte y ningún
par de RQ1 queda significativo tras Holm. Conforme al plan, se reportan ambos
resultados y RQ1 se califica como no robusto bajo normalización: la conclusión se
limita al volumen absoluto de *commits*. La sensibilidad de contenido conserva
119 de 120 repositorios.

## Figuras de partida (Etapa 3)

`scripts/plot_results.py` genera figuras a partir de `results/` y
`results-inference/`. No sustituye la elaboración propia que exige la pauta:
es una utilidad para revisar, adaptar y asumir el diseño final.

```bash
uv run python scripts/plot_results.py \
  --results results \
  --inference results-inference \
  --output figures-etapa3 \
  --forest --sign --sensitivity
```

Salidas:

- `fig_rq1_boxplot`: caja de *commits* posteriores por categoría;
- `fig_rq2_boxplot`: caja de *commits* previos y posteriores por categoría;
- `fig_effects`: efectos de Hodges–Lehmann con IC para RQ1 y RQ2 (opcional);
- `fig_sign_tests`: test del signo con IC exacto y concordancia con Wilcoxon
  (opcional);
- `fig_rq1_sensitivity`: dirección de RQ1 con el conteo absoluto y todas las
  sensibilidades marcadas como `computed` en `sensitivity_status.csv` (opcional);
- `README.md` y `manifest.json` con la descripción, los checksums de entrada, las
  sensibilidades incluidas y las versiones de las herramientas.

El comando se niega a escribir en un directorio no vacío; `--force` permite
sobrescribir. Las figuras de `figures-etapa3/` no se versionan: son un punto de
partida para la revisión y el diseño propios.

## Estado del paquete

Implementado y ejecutado: extracción y *screening* (protocolo v2); validación
humana del *screening* con sello de checksums; procesamiento de actividad con
ventanas, exclusión de *merges* y del commit de adopción, y clasificación;
validación de rutas modificadas con sello; análisis descriptivo de RQ1 y RQ2;
análisis inferencial congelado con SciPy, efectos, IC y sensibilidades; tabla de
representatividad muestra-marco; y 98 pruebas automatizadas.

Pendiente: revisar y asumir las figuras de `figures-etapa3/` y elaborar las
tablas del artículo (de autoría propia); redactar los resultados, las
limitaciones y el paquete de réplica; declarar las desviaciones de alcance (bots
manuales, ventanas 30/90, estrellas/antigüedad); y actualizar el resumen, el ZIP
y Zenodo con la etiqueta definitiva.

## Procedencia de los datos

- fuente: dataset `mvaccargiu/gitskills` en Hugging Face, revisión
  `ebab17454a7c236f8f26b183567f1a126f42e3f8` (*snapshot* de julio de 2026);
- fecha de corte de la historia: `2026-10-04T23:59:59Z`;
- selección: repositorios Python no *fork*, examinados en orden pseudoaleatorio
  con la semilla `20261004`;
- transformaciones: deduplicación por `repo_full_name`, unión de las ocurrencias
  de GitSkills con su representante `dedup_primary` mediante `file_sha`, y
  reconstrucción de la adopción y de la actividad directamente desde Git;
- registro: `data/extraction/run_manifest.json` (configuración, fecha UTC y
  versiones de herramientas), `data/extraction/extraction.log`,
  `data/extraction/screening_log.csv` (aceptados y excluidos con su motivo) y
  `data/extraction/selected_repositories.csv` (los 120 casos con URL, rama y
  commits preservados).

## Estructura del repositorio

```text
src/coevolution_skills/    código del flujo
tests/                     pruebas automatizadas
scripts/plot_results.py    utilidad de figurado descriptivo e inferencial
data/extraction/           marco, screening y metadatos preservados
data/validation/           validación humana del screening y su sello
data/processed/            eventos y conteos de actividad
data/activity-validation/  revisión de rutas modificadas
results/                   descriptivos de RQ1 y RQ2
results-inference/         tests, efectos, IC y sensibilidades
figures-etapa3/            figuras de partida (no versionadas)
docs/procedimiento.md      detalle operativo de cada paso
extraction.toml            configuración de la extracción
pyproject.toml, uv.lock    dependencias
repositories/              clones locales (no incluidos en el paquete)
```

## Licencia

- **Código** (`src/`, `tests/`, `scripts/`): MIT, véase `LICENSE`.
- **Datos y resultados** (`data/`, `results/`): CC BY 4.0, véase `LICENSE-DATA`.

La licencia declarada en los metadatos de Zenodo debe corresponder a la del
material publicado. Como el paquete reúne ambos, se puede declarar CC BY 4.0
para los datos y CC BY 4.0 o «Other (Open)» para el conjunto, indicando en la
descripción que el código está bajo MIT.
