# Revisión manual del screening

Se preparó evidencia para 10 repositorios y las siguientes rutas:
skill=10, production=10, test=10, other=10.

## 1. Eventos de adopción

Completa las columnas `human_*`, `reviewer` y `reviewed_at_utc` de
`adoption_review.csv`. Para cada fila comprueba el diff entre `first_parent` y
`adoption_commit`, la ausencia de skills válidos antes, su presencia después y
que no sea solamente un renombrado engañoso. Valores sugeridos para
`human_adoption_decision`: `valid`, `invalid` o `ambiguous`.

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
