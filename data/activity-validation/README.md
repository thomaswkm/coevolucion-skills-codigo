# Revisión manual de rutas modificadas

Se muestrearon cambios efectivamente ocurridos en las ventanas previa y
posterior: skill=10, production=10, test=10, other=10.

## 1. Completar la revisión

Completa `human_category`, `agreement`, `human_notes`, `reviewer` y
`reviewed_at_utc` en `activity_review.csv`. Categorías permitidas: `skill`,
`production`, `test` y `other`; `agreement` debe ser `yes` o `no`. Para
eliminaciones y renombrados el formulario incluye la ruta anterior y la nueva.

Para inspeccionar un cambio:

```bash
uv run review-activity-validation show <review_position>
```

## 2. Cierre

Valida y sella los formularios:

```bash
uv run review-activity-validation validate --seal
```

## 3. Si aparece un error evidente

No edites `data/processed/` en sitio. Corrige la regla de clasificación,
incrementa la versión del protocolo de procesamiento en
`src/coevolution_skills/process.py`, reprocesa en un directorio nuevo y repite
esta revisión desde el inicio. Documenta el ajuste en el manifiesto y en el
`README.md` del proyecto.
