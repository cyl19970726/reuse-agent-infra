# Repository guidance

- Skills live under `skills/<category>/<skill-name>/`; each skill is a complete package rooted at `SKILL.md`.
- Preserve skill names and relative reference links. Keep `session-forensics` and `session-to-workflow` in the same category.
- Install into the consuming project's `.agents/skills/` by default. Global installation or synchronization requires explicit authorization for each skill and scope.
- Never commit private session logs, conversation registries, credentials, local calibration data, or generated caches.
- Validate Python syntax and relevant existing self-tests after script changes. Test conversation registries with temporary `--registry` paths.
- The browser-chat skills require an external `ego-browser` installation. Do not bundle or install additional skills incidentally.
