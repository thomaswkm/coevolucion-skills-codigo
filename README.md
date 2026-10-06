# Co-evolución de skills y código

Paquete de réplica parcial de la Etapa 2 de la investigación sobre co-evolución
de *skills* y código. Contiene el código, las dependencias, la configuración y
los datos preservados que permiten reproducir los resultados preliminares de RQ1
y RQ2 sin repetir la extracción de datos. El análisis inferencial, las tablas y
figuras finales y la muestra definitiva quedan fuera de esta etapa.

## Paquete de réplica

| Elemento | Valor |
|---|---|
| Repositorio | [thomaswkm/coevolucion-skills-codigo](https://github.com/thomaswkm/coevolucion-skills-codigo) |
| Versión (etiqueta) | `v0.2-etapa2` |
| DOI de Zenodo | `10.5281/zenodo.23178699` |

La versión publicada en Zenodo `10.5281/zenodo.23178699` corresponde a la
etiqueta `v0.2-etapa2` de este repositorio.

El paquete incluye el código del flujo (`src/coevolution_skills/`), las pruebas
(`tests/`), la utilidad de figurado (`scripts/plot_results.py`), las dependencias
(`pyproject.toml`, `uv.lock`), la configuración (`extraction.toml`), los datos
preservados (`data/`), los resultados (`results/`), las licencias (`LICENSE`,
`LICENSE-DATA`) y este README. No se incluyen
los clones bajo `repositories/` (pesados y sujetos a las licencias de sus
repositorios de origen) ni el material local `gitskills-sample/`.

## Entorno

Para reproducir los resultados desde los datos preservados se necesita:

- Git 2.31 o posterior;
- [`uv`](https://docs.astral.sh/uv/).

```bash
uv lock
uv sync
```

Volver a ejecutar la extracción o el clonado es opcional y, además, requiere
conexión a Hugging Face y GitHub y espacio para los diez repositorios y un clon
temporal. No hace falta descargar la base SQLite de 41 GB. El detalle está en
`docs/procedimiento.md`.

## Reproducción

La reproducción parte de los datos preservados y **no** repite la extracción.

### Ruta corta: análisis desde los datos preservados

Parte de `data/processed/activity_counts.csv` y su manifiesto. No requiere clonar
repositorios ni conectarse a la red:

```bash
uv run analyze-results --config extraction.toml
```

El comando crea un directorio `results/` nuevo y no sobrescribe uno existente.
Para otro destino:
`uv run analyze-results --config extraction.toml --output <directorio>`.

### Ruta completa: reprocesar la actividad (opcional)

Reproducir el procesamiento requiere los clones de los diez repositorios en
`repositories/`, que no se publican. Con ellos disponibles:

```bash
uv run process-activity --config extraction.toml
uv run prepare-activity-validation --config extraction.toml
uv run analyze-results --config extraction.toml
```

## Resultados preliminares

Con los datos entregados, el análisis produce:

**RQ1 (60 días posteriores, 10 repositorios).**

- *skills*: mediana 5, RIC 12,5, rango 0–15; 3 repositorios sin actividad.
- producción: mediana 27, RIC 37,5, rango 0–211; 1 repositorio sin actividad.
- pruebas: mediana 12, RIC 44,25, rango 0–97; 1 repositorio sin actividad.
- La producción fue la categoría más modificada en 7 repositorios, los *skills*
  en 1, las pruebas en 1, y 1 quedó empatado sin actividad en las tres.

**RQ2 (diferencia `post − pre`).**

- producción: mediana previa 8 → mediana posterior 27; diferencia mediana 9,5
  (RIC 80,75; −108 a 211); 7 repositorios aumentan y 3 disminuyen.
- pruebas: mediana previa 5,5 → mediana posterior 12; diferencia mediana 4,5
  (RIC 33,75; −87 a 97); 7 repositorios aumentan y 3 disminuyen.

Son resultados **descriptivos** de los diez casos analizados: no incluyen pruebas
inferenciales ni se generalizan a la población.
`results/consistency_checks.txt` reconstruye cada cifra desde
`activity_counts.csv`.

## Estado del paquete

Implementado y ejecutado: extracción y *screening* (protocolo v2); validación
humana del *screening* con sello de checksums; procesamiento de actividad con
ventanas, exclusión de *merges* y del commit de adopción, y clasificación;
validación de rutas modificadas con sello; análisis descriptivo de RQ1 y RQ2; y
60 pruebas automatizadas.

Pendiente: elaborar las tablas y figuras del artículo (de autoría propia);
redactar en el manuscrito los resultados, las limitaciones y el paquete de
réplica, y actualizar el resumen; armar el ZIP y publicar en Zenodo con el DOI;
y decidir la muestra definitiva y, si corresponde, incorporar pruebas
inferenciales.

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
  `data/extraction/selected_repositories.csv` (los diez casos con URL, rama y
  commits preservados).

## Estructura del repositorio

```text
src/coevolution_skills/    código del flujo
tests/                     pruebas automatizadas
scripts/plot_results.py    utilidad de figurado (opcional)
data/extraction/           marco, screening y metadatos preservados
data/validation/           validación humana del screening y su sello
data/processed/            eventos y conteos de actividad
data/activity-validation/  revisión de rutas modificadas
results/                   descriptivos de RQ1 y RQ2
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
