# Repository context

- This is a research workspace for skill/code co-evolution in public Python repositories. There is currently no analysis implementation, dependency manifest, or configured build/test/lint workflow.
- `.gitignore` excludes all of `paper/` and `gitskills-sample/`; these local materials will not appear in ordinary Git diffs or a fresh clone. Inspect them directly when available.
- The study uses GitSkills to select repositories, then retrieves Git histories separately. RQ1 compares skill/production/test commit frequencies after adoption; RQ2 compares production/test activity across equal pre/post-adoption windows. These are observational comparisons, not causal claims.

## Dataset

- Read `gitskills-sample/README.md` for schema, queries, and limitations. Its descriptive statistics refer to the **full dataset**, not the bundled sample.
- From `gitskills-sample/`, run `unzip agent_skills_sample.zip`, then `sqlite3 agent_skills_sample.db "SELECT COUNT(*) FROM artifacts;"`. The archive contains a roughly 291 MB database; the documented sample count is 29,786 occurrences.
- Use `repos.language` and join `artifacts.repo_full_name = repos.full_name`. The stage-2 draft's mentions of `repositories`, `lenguaje`, and `SKILLS.md` are unfinished prose; the documented names are `repos`, `language`, and `SKILL.md`.
- `artifacts` counts file occurrences; `file_sha` groups identical content. Only `dedup_primary = 1` representatives carry text and composition enrichment. Representative metadata does not describe every repository containing a copy.
- GitSkills is a July 2026 snapshot, not complete repository history. Skill history is sampled and follows only the current path; `first_commit_at` may reflect a rename rather than adoption. Recover adoption and production/test history from Git.
- Filename search also includes lowercase and pre-specification matches; define the valid-skill population explicitly using filename, location, and frontmatter fields.

## Manuscripts

- The stage-1 baseline and stage-2 working draft are `paper/investigacion_aplicada_etapa{1,2}/conference_101719.tex`; each uses its own sibling `references.bib` and the `IEEEtran` class/bibliography style. Stage 2 still contains methodological placeholders and empty results sections.
- Before stage-2 edits, read `paper/etapa-2-diseno-metodológico-y-resultados-preliminares.md`. It requires Spanish, IEEE two-column layout, at most four content pages plus one references-only page. Review-driven corrections are blue; unchanged text and new stage-2 sections are black.
- That assignment requires students to produce tables and figures from study data and explicitly prohibits generative AI from producing them.
- The replication package must reproduce reported preliminary results from preserved data. Its README and Zenodo description must identify the same commit/tag; the paper must link GitHub and the corresponding published Zenodo version DOI. Full re-extraction is not required.
