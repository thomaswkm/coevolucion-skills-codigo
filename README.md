# Co-evolución de skills y código

Este repositorio contiene el procedimiento reproducible para seleccionar y
extraer repositorios de la investigación. En esta etapa el comando solamente:

1. construye el marco de candidatos desde la versión completa de GitSkills;
2. genera un orden determinista por repositorio;
3. comprueba criterios de inclusión mediante Git;
4. conserva los primeros diez repositorios elegibles.

No calcula frecuencias de commits ni resultados para RQ1 o RQ2.

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
