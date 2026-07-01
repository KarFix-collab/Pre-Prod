# KarFix safe-delete checklist (v19)

Safe to remove from deployment bundles:
- __pycache__/
- *.pyc
- tests/
- docs/
- scripts/ (if not used at runtime)
- old unused logo assets
- internal project notes and review docs

Keep in deployment bundles:
- app/
- migrations/
- config/
- templates/
- static assets used by the app
- requirements.txt
- pyproject.toml
- render.yaml
- Procfile
- run.py
- wsgi.py
- start.sh
