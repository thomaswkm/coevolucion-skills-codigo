# Plan de análisis congelado — Etapa 3

**Versión:** 1.2
**Fecha de congelamiento:** 2026-10-22
**Ámbito:** análisis inferencial de RQ1 y RQ2 sobre la muestra definitiva de Etapa 3.
**Documentos de referencia:** `paper/PLAN_ETAPA3.md` (decisiones D1–D9), `docs/procedimiento.md` (extracción, procesamiento y validación) y `paper/etapa-2-diseno-metodológico-y-resultados-preliminares.md`.

Este documento traslada las decisiones D1–D9 de `paper/PLAN_ETAPA3.md` y fija los detalles estadísticos antes de ejecutar el análisis sobre la muestra definitiva. Una vez congelado, cualquier cambio debe registrarse como una versión nueva, justificarse por escrito y repetir el análisis completo. No se permite análisis secuencial ni parada por valores `p`.

---

## 1. Población y marco (D1)

- **Marco:** los **22.126** repositorios Python no *fork* con al menos un `SKILL.md` válido en las ocho rutas del protocolo v2, según GitSkills en la revisión `ebab17454a7c236f8f26b183567f1a126f42e3f8` y con corte `2026-10-04T23:59:59Z`.
- **Población \(E\):** subconjunto del marco que cumple todos los criterios de elegibilidad:
  - repositorio accesible y con rama predeterminada recuperable;
  - historial completo de la rama predeterminada;
  - transición histórica de cero skills válidos a uno o más skills válidos;
  - al menos 60 días observables antes y después de la adopción respecto del corte;
  - árbol de adopción con al menos un archivo Python de producción y uno de pruebas.
- **Unidad de análisis:** el repositorio. Las observaciones de un mismo repositorio no son independientes entre sí.
- **Aclaración obligatoria en el artículo:** los 22.126 son el **marco**, no la población. \(E\) no se conoce con exactitud; su tamaño \(\hat{N}\) se estima a partir del rendimiento del cribado y se reporta con intervalo.
- **Corte temporal:** todos los candidatos y repositorios se evalúan contra `2026-10-04T23:59:59Z`.

## 2. Muestra y regla de detención (D2, D3)

- **Orden de selección:** congelado con semilla textual `20261004`:
  `SHA256("20261004:" + repo_full_name)`, con `repo_full_name` como desempate.
- **Objetivo:** \(n=120\) repositorios elegibles.
- **Tope de cribado:** 1.500 candidatos examinados.
- **Regla de parada:** se toman los primeros 120 elegibles en el orden congelado. Si se alcanza el tope de 1.500 antes de 120, el estudio continúa con \(n<120\) y se reporta el motivo. **No se reemplaza manualmente a ningún candidato.**
- **Sin análisis secuencial:** la muestra se cierra antes de calcular los tests. No se amplía la muestra en función de los resultados.
- **Muestra piloto:** los 10 repositorios de Etapa 2 son un prefijo de la muestra definitiva; no se descartan ni se tratan de forma distinta.
- **Tamaño efectivo:** si una adopción se marca `invalid`, el repositorio se excluye y se reporta el \(n\) efectivo por RQ. Las adopciones `ambiguous` se conservan con bandera y se analizan también sin ellas como sensibilidad.
- **Si \(n<120\) por el tope:** el procesamiento acepta un número de seleccionados menor que `target_count` solo cuando el manifiesto de extracción registra `completion_status` igual a `cap_reached` o `frame_exhausted`. El déficit se registra en el manifiesto de procesamiento y en el análisis; no se completa la muestra con reemplazos.
- **Fracción de muestreo:** se reporta \(n/\hat{N}\) y la corrección por población finita \(\sqrt{1-n/\hat{N}}\), aunque se espera despreciable.

## 3. Definiciones temporales y de actividad

- **Adopción (\(t_0\)):** primer commit de la historia de primer padre en el que se pasa de cero skills válidos a uno o más skills válidos. El commit de adopción se excluye de ambas ventanas.
- **Ventana previa:** \([t_0-60\,\text{días},\ t_0)\).
- **Ventana posterior:** \((t_0,\ t_0+60\,\text{días}]\).
- **Fechas:** se usa la fecha del *committer* en UTC.
- **Merges:** todos los commits con más de un padre se excluyen.
- **Skill válido:** `SKILL.md` con front matter YAML válido (`name` y `description`), en una de las ocho rutas ancladas a la raíz definidas en `docs/procedimiento.md`. No se aceptan rutas anidadas ni nombres alternativos.
- **Categorías:** `skill`, `production`, `test` y `other`. Solo las tres primeras entran en los tests; `other` queda fuera.
- **Métrica principal:** número de **commits únicos** por repositorio, período y categoría. Cada commit cuenta como máximo una vez por categoría. Los ceros son resultados válidos y se conservan explícitamente.
- **Denominadores de normalización:** la elegibilidad garantiza al menos un archivo de producción, uno de pruebas y un skill en el árbol de adopción. Por tanto, los cocientes de la sección 6.2 están definidos para las tres categorías.

## 4. Hipótesis y pruebas principales (D5)

**Nivel de significación:** \(\alpha=0{,}05\). Los contrastes direccionales (Wilcoxon y test del signo) son bilaterales; Friedman se contrasta en la cola superior por construcción.

### 4.1 RQ1 — actividad posterior por categoría

- **Hipótesis nula \(H_0\):** las distribuciones de commits posteriores de `skill`, `production` y `test` son iguales.
- **Hipótesis alternativa \(H_1\):** al menos una de las tres distribuciones difiere.
- **Prueba global:** Friedman para medidas repetidas, con corrección por empates.
- **Tamaño del efecto global:** \(W\) de Kendall, calculada a partir del estadístico de Friedman.
- **Comparaciones por pares (solo si Friedman es significativo):** Wilcoxon de rangos con signo para `skill`–`production`, `skill`–`test` y `production`–`test`.
- **Corrección por comparaciones múltiples:** Holm dentro de la familia de 3 pares de RQ1.
- **Tamaños del efecto por par:** rango-biserial apareado y diferencia de Hodges–Lehmann.
- **Orientación del signo:** para el par \(A\)–\(B\), el signo positivo indica que \(B\) tiene más commits que \(A\); se explicita en cada tabla.

### 4.2 RQ2 — variación entre períodos

- **Hipótesis nula \(H_0\):** la mediana de la diferencia \(post-pre\) es cero, para cada categoría.
- **Hipótesis alternativa \(H_1\):** la mediana de la diferencia \(post-pre\) es distinta de cero, para cada categoría.
- **Pruebas:** Wilcoxon de rangos con signo por separado para `production` y `test`.
- **Corrección por comparaciones múltiples:** Holm dentro de la familia de 2 pruebas de RQ2.
- **Tamaños del efecto:** rango-biserial apareado y diferencia de Hodges–Lehmann; signo positivo = aumento del período previo al posterior.
- **Interpretación:** aumento, disminución o ausencia de variación según el signo y el intervalo; **sin lectura causal**.

### 4.3 Familias de comparaciones

- Se aplica **Holm dentro de cada RQ** (3 pruebas en RQ1, 2 en RQ2), no Holm sobre las 5.
- **Justificación:** RQ1 y RQ2 responden preguntas distintas y se reportan por separado; agruparlas en una sola familia castigaría innecesariamente ambas. Se informan también los valores `p` sin ajustar para transparencia.
- Los tests de sensibilidad (sección 6) usan la misma familia que su test principal correspondiente.

### 4.4 Ceros, empates y valores atípicos

- **Wilcoxon:** las diferencias nulas se descartan y se reporta el número de diferencias no nulas (\(n_{\text{nonzero}}\)). Se usa `scipy.stats.wilcoxon` con `mode="auto"` y sin corrección de continuidad: exacto si \(n_{\text{nonzero}}\le 25\) y no hay empates; en otro caso, aproximación normal con corrección de empates.
- **Friedman:** corrección por empates; se reporta el número de bloques completos.
- **Test del signo:** se aplica sobre las diferencias no nulas; se reporta la proporción de diferencias positivas y su intervalo exacto (Clopper–Pearson).
- **Atípicos:** no se eliminan ni se winsorizan. Se reportan mínimo, máximo y cuartiles. La inferencia no paramétrica se elige precisamente para no depender de la normalidad.

## 5. Intervalos de confianza y bootstrap

- **Método:** bootstrap percentil al 95 % (percentiles 2,5 y 97,5).
- **Remuestreos:** 10.000.
- **Semilla:** `20261020`.
- **Unidad de remuestreo:** el repositorio, respetando la estructura pareada:
  - RQ1: se remuestrean repositorios y se recalculan medianas y diferencias por pares;
  - RQ2: se remuestrean las diferencias \(post-pre\) por repositorio.
- **Cantidades con IC:** medianas por categoría y período, diferencias de Hodges–Lehmann y diferencias \(post-pre\).
- **Casos degenerados:** si la distribución bootstrap es degenerada (por ejemplo, todas las diferencias son cero), se reporta el hecho y no se fuerza un intervalo.
- **Reproducibilidad:** la semilla y el número de remuestreos se registran en el manifiesto y en `results/`.

## 6. Sensibilidades

Las sensibilidades se reportan **después** del resultado principal y no lo sustituyen. Si alguna cambia la dirección o la conclusión, se reportan ambas y se califica el hallazgo como no robusto.

### 6.1 D6 — Robustez ante asimetría

- **Test del signo** para cada contraste pareado:
  - RQ1: los 3 pares de categorías, solo si el Friedman global es significativo (igual que los Wilcoxon por pares);
  - RQ2: las 2 diferencias \(post-pre\).
- **Corrección:** Holm dentro de la misma familia que el test principal (3 y 2, respectivamente).
- **Bootstrap:** el bootstrap de la sección 5 ya es obligatorio; D6 añade el contraste de dirección del test del signo.
- **Criterio de robustez:** si el test del signo y Wilcoxon coinciden en dirección y significación, el resultado se considera robusto. Si discrepan, se reporta la discrepancia y no se afirma el efecto.
- **Permutación con cambio de signo:** opcional, solo si el coste de implementación es bajo; no es obligatoria.

### 6.2 D7 — Métrica normalizada (*churn*)

Se calculan métricas secundarias para RQ1 y RQ2, manteniendo los conteos brutos como resultado principal:

- **Commits por archivo de la categoría:**
  \[
  \text{commits\_por\_archivo}_{cat} = \frac{\text{commits}_{cat}}{\text{archivos}_{cat}\ \text{en el árbol de adopción}}
  \]
- **Churn por archivo de la categoría:**
  \[
  \text{churn\_por\_archivo}_{cat} = \frac{\text{líneas añadidas} + \text{líneas eliminadas}_{cat}}{\text{archivos}_{cat}\ \text{en el árbol de adopción}}
  \]
- **Churn por skill:** para `skill`, el denominador es el número de `SKILL.md` válidos del árbol de adopción; como cada skill es un archivo, coincide con el churn por archivo de esa categoría. Se reporta explícitamente para evitar ambigüedad.
- **Medida complementaria:** proporción de commits que tocan la categoría,
  \(\text{commits}_{cat}/\text{commits totales del repositorio en el período}\), con 0 si el total es 0.
- **Archivos binarios:** Git no proporciona líneas para binarios; se excluyen del numerador de churn, se cuentan aparte y se reporta cuántos hay. No se imputan líneas.
- **Renombrados:** se imputan a la ruta resultante (o a la del padre en eliminaciones), siguiendo la regla ya usada en `process.py`.
- **Requisito de datos:** `file_changes.csv` incorpora `lines_added`, `lines_deleted` e `is_binary` (protocolo de procesamiento v2). El análisis final debe usar un directorio procesado con esta versión, no los datos de Etapa 2.

### 6.3 D9 — Dependencia por skills copiados

- **Firma de contenido:** para cada repositorio se calcula el SHA-256 del contenido de cada `SKILL.md` válido **en el commit de adopción** (`git cat-file`). Es el equivalente operativo del `file_sha` de GitSkills, pero calculado en \(t_0\) y no en el snapshot. Se usa la tupla ordenada de hashes como firma del repositorio.
- **Agrupación:** se agrupan los repositorios con la misma firma. Un repositorio con varios skills copiados comparte grupo solo si comparte el conjunto completo.
- **Representante:** se conserva **un repositorio por firma**, el de `repo_full_name` lexicográficamente menor. La regla es determinista y ajena a los resultados.
- **Procedimiento:** se repiten los tests principales y los IC sobre la muestra reducida. Se reportan \(n\) de grupos, repositorios descartados y si cambian dirección o significación.
- **Limitación:** la agrupación es por igualdad exacta de contenido. Si una skill copiada se reformateó, el hash difiere y el repositorio queda como caso separado; la sensibilidad es, por tanto, conservadora (no elimina todas las near-duplicadas).
- **Modelo mixto:** no se usa como análisis principal. Con \(n=120\), pocos grupos y un diseño pareado, la sensibilidad por contenido es suficiente y más transparente; el modelo mixto se menciona como alternativa no implementada.
- **Requisito de datos:** `data/processed/skill_contents.csv` conserva los hashes de contenido por repositorio y skill para que la sensibilidad sea reproducible sin los clones.

### 6.4 Otras sensibilidades ya decididas

- **Adopciones ambiguas (D4):** se repite el análisis principal excluyendo los casos marcados `ambiguous`; se reporta el \(n\) y si cambian las conclusiones.
- **Adopciones inválidas (D4):** se excluyen del análisis principal y se reporta el \(n\) efectivo; no se reemplazan.
- **Bots:** la exclusión por el patrón congelado
  `(?i)(\[bot\]|(?:^|[._+@-])bot(?:$|[._+@-])|dependabot|renovate|github-actions|pre-commit-ci|snyk-bot|mergify|codecov|greenkeeper|semantic-release-bot|allcontributors)`
  ya está calculada en `results-inference/`, pero **no se exige revisión manual** de las coincidencias ni se reporta en el artículo. La presencia de bots en los conteos principales se declara como amenaza a la validez.
- **Ventanas 30 y 90 días:** **fuera del alcance de esta entrega**. La ventana principal sigue siendo de 60 días y la elección de esa duración se declara como amenaza a la validez. El comando queda disponible para una ampliación futura.
- **Sin placebo (D8):** no se añade una ventana placebo. La posible tendencia previa a la adopción se declara como amenaza a la validez; no se cambia \(E\) ni el \(n\).

## 7. Representatividad de la muestra frente al marco

- **Comparaciones descriptivas:** número de skills elegibles y proporción de skills copiadas verbatim. Esta última se define como la fracción de `SKILL.md` válidos cuyo `file_sha` aparece en dos o más repositorios del conjunto comparado. Las estrellas y la antigüedad quedan fuera de alcance y no se reportan.
- **Estadísticos:** mediana y rango intercuartílico para la muestra y para el marco, presentados en paralelo.
- **Sin pruebas de significación:** la muestra es aleatoria de \(E\), no del marco; los valores \(p\) frente al marco no tendrían una interpretación válida.
- **Interpretación esperada:** \(E\) es más antigua que el marco por diseño, porque exige 60 días de historia previa y una ventana posterior completa. Se explicita en el texto, sin medición de antigüedad.
- **Fuente:** `candidate_frame.csv` y `selected_repositories.csv` para el número de skills; la copia verbatim se calcula con los hashes de contenido en \(t_0\). No se consultan metadatos externos de GitHub y no se imputa ningún valor.

## 8. Datos faltantes, exclusiones y trazabilidad

- **Entrada principal:** `data/processed/activity_counts.csv`. Todas las filas deben estar en estado `observed`; cualquier fila `missing` o `incomplete` detiene el análisis y se investiga. **No se imputan conteos.**
- **Entrada de sensibilidades:** `data/processed/file_changes.csv` (*churn*) y la tabla de hashes de skills (D9).
- **Exclusiones:** solo las previstas en este plan (adopción inválida, `other`, merges y \(t_0\)). Cualquier exclusión adicional se documenta y se reporta. `data/processed/` conserva todos los repositorios procesados; el filtro de adopciones inválidas se aplica en `analyze-results` y queda registrado en `results/analysis_sample.csv`.
- **Trazabilidad:** cada cifra del artículo debe poder reconstruirse desde un CSV de `results/`. Los manifiestos registran configuración, versiones, semilla y SHA-256 de las entradas.
- **Sellado:** la validación humana de la extracción (`data/validation/human_review.sha256`) no se modifica. Las extensiones de procesamiento no alteran ese sello; solo generan un directorio nuevo de datos procesados.

### 8.1 Criterios de la revisión humana de adopciones

La revisión de todas las adopciones de la muestra usa estos criterios, también incluidos en el `README.md` que genera `prepare-validation`:

- **`valid`:** primera incorporación de al menos un `SKILL.md` que cumple las ocho rutas y el front matter; antes de \(t_0\) no había skills válidos; no es solo un renombrado o movimiento.
- **`invalid`:** la evidencia contradice la clasificación automática (front matter o ruta inválidos, skill válido preexistente, renombrado sin adopción real, artefacto de merge o historia inconsistente que no represente una transición real desde el primer padre). Que \(t_0\) sea un merge no es inválido por sí mismo.
- **`ambiguous`:** evidencia insuficiente o caso frontera; se conserva con bandera y se analiza también sin estos casos.

Si la primera adopción es `invalid`, no se busca una segunda adopción y no se reemplaza el repositorio. La decisión humana se aplica **en el análisis**: `data/processed/` conserva todos los repositorios procesados y `results/analysis_sample.csv` registra cuáles se incluyen. El manifiesto de análisis guarda el \(n\) revisado, el \(n\) efectivo, los excluidos y los ambiguos.

### 8.2 Estimación del tamaño de la población

- Sea \(M\) el tamaño del marco (filas de `candidate_order.csv`), \(k\) el número de candidatos examinados (`screening_log.csv`) y \(n\) el número de elegibles encontrados.
- Rendimiento: \(\hat p = n/k\).
- Población estimada: \(\hat N = M\hat p\).
- Intervalo: Wilson al 95 % para \(\hat p\), transformado a \(N\) multiplicando por \(M\).
- Se reportan la fracción de muestreo \(n/\hat N\) y la corrección por población finita \(\sqrt{1-n/\hat N}\).
- El método es aproximado y asume que la elegibilidad no depende de la posición en el orden aleatorio. Se documenta como limitación.

## 9. Software, semillas y reproducibilidad

- **Python:** ≥ 3.12 (versión exacta registrada en el manifiesto).
- **SciPy:** se añadirá como dependencia para `infer.py`; la versión exacta se fijará en `uv.lock` y se registrará en `results/analysis_manifest.json`.
- **Semillas:** selección `20261004`; bootstrap `20261020`.
- **Script:** `src/coevolution_skills/infer.py` (nuevo) o extensión de `analyze.py`, con pruebas en `tests/`.
- **Salidas:** `results/rq1_*.csv`, `results/rq2_*.csv`, `results/analysis_sample.csv`, `results/sample_estimate.csv`, tablas de sensibilidad y `consistency_checks.txt` ampliado para reconstruir `p`, efectos e IC. `data/processed/skill_contents.csv` conserva los hashes de D9.
- **Regla de oro:** todos los tests se reportan, incluidos los no significativos; no se seleccionan resultados ni se cambia el plan tras ver los datos.

## 10. Trazabilidad con las decisiones D1–D9

| ID | Decisión | Dónde se aplica |
|---|---|---|
| D1 | Población \(E\); los 22.126 son el marco | Secciones 1 y 7 |
| D2 | \(n=120\) | Sección 2 |
| D3 | Tope de 1.500 candidatos; reportar \(n<120\) | Sección 2 |
| D4 | Revisar todas las adopciones y 4 rutas; contingencias | Secciones 2 y 6.4 |
| D5 | Friedman + Wilcoxon con Holm; efectos e IC | Sección 4 y 5 |
| D6 | Test del signo + bootstrap como sensibilidad | Sección 6.1 |
| D7 | Conteos brutos + *churn* por archivo/skill | Sección 6.2 |
| D8 | Sin placebo; tendencia previa como amenaza | Sección 6.4 |
| D9 | Sensibilidad con un repositorio por contenido de skill | Sección 6.3 |

## 11. Decisiones fuera de este plan

No forman parte del análisis estadístico y se cierran en `paper/PLAN_ETAPA3.md`:

- **D10:** tratamiento de preprints y documentación no publicada.
- **D11:** idioma y formato del artículo.
- **D12:** autoría.
- **D13:** versionado de `paper/`.

## 12. Congelamiento y cambios

- Este plan se congela **antes** de calcular los tests definitivos.
- Cualquier cambio posterior se registra como versión nueva en este archivo, con fecha, motivo y decisión afectada, y se añade al registro de decisiones de `paper/PLAN_ETAPA3.md`.
- Si un cambio altera la población, la muestra, las hipótesis o los tests, se repite el análisis completo y se reporta la versión anterior junto con la nueva.

**Cambios de 1.0 a 1.1 (2026-10-22):** se añadieron los criterios de revisión humana, la política para \(n<120\), el filtrado de decisiones en el análisis, la opción `--window-days`, el patrón congelado de bots, la estimación de \(\hat N\) y las salidas `analysis_sample.csv`, `sample_estimate.csv` y `skill_contents.csv`.

**Cambios de 1.1 a 1.2 (2026-10-22):** se acota el alcance de lo reportado. Se eliminan la revisión manual de coincidencias de bots, las sensibilidades de ventana de 30/90 días y la comparación de estrellas y antigüedad. **No cambia la población, la muestra, las hipótesis ni los tests**: los resultados inferenciales se ejecutaron con la versión 1.1 y siguen vigentes. Se mantienen la sensibilidad de *churn* (sección 6.2) y la de contenido de *skill* (sección 6.3).
