# Revisión manual del screening

Se preparó evidencia para 120 repositorios y las siguientes rutas:
skill=1, production=1, test=1, other=1.

## 1. Eventos de adopción

Completa las columnas `human_*`, `reviewer` y `reviewed_at_utc` de
`adoption_review.csv`. Para cada fila comprueba el diff entre `first_parent` y
`adoption_commit`, la ausencia de skills válidos antes, su presencia después y
que no sea solamente un renombrado engañoso.

Criterios congelados:

- **`valid`:** el diff muestra la primera incorporación de al menos un
  `SKILL.md` que cumple las ocho rutas y el front matter del protocolo; antes de
  `adoption_commit` no había ningún skill válido; no se trata solo de un
  renombrado o movimiento de un skill ya existente. Marca
  `human_rename_only = no` y `human_frontmatter_and_paths_valid = yes`.
- **`invalid`:** la evidencia contradice la clasificación automática. Incluye
  front matter o ruta inválidos, un skill válido preexistente, un renombrado o
  movimiento sin adopción real, un artefacto de merge o una historia
  inconsistente que no represente una transición real desde el primer padre.
  Explica el motivo en `human_notes`. Que el commit de adopción sea un merge no
  es inválido por sí mismo: se evalúa la transición desde su primer padre.
- **`ambiguous`:** la evidencia es insuficiente o el caso es frontera (por
  ejemplo, un diff enorme, un renombrado con similitud parcial o varias
  adopciones plausibles). Se conserva en la muestra con bandera y se analiza
  también sin estos casos. Explica el motivo en `human_notes`.

Reglas de cierre:

- Si la primera adopción es `invalid`, **no se busca una segunda adopción** y
  **no se reemplaza** el repositorio; se excluye del análisis y se reporta el
  n efectivo.
- `human_adoption_decision = valid` exige `human_rename_only = no` y
  `human_frontmatter_and_paths_valid = yes`; cualquier otro caso requiere notas.
- No calcules precisión ni recall con esta revisión acotada.

## 2. Rutas

Completa `human_category`, `agreement`, `human_notes`, `reviewer` y
`reviewed_at_utc` en `path_review.csv`. Categorías permitidas: `skill`,
`production`, `test` y `other`; `agreement` debe ser `yes` o `no`.

Para inspeccionar un archivo sin cambiar el checkout:

```bash
git -C <repository_directory> show '<commit>:<path>'
```

Para inspeccionar el evento de adopción:

```bash
git -C <repository_directory> diff --find-renames <first_parent> <adoption_commit>
```

## 3. Cierre

No edites `automated_checks.csv` ni `validation_manifest.json`. Si encuentras un
error evidente, registra el caso y corrige el protocolo antes de repetir
extracción y validación. No calcules precisión o recall con esta revisión
acotada.
