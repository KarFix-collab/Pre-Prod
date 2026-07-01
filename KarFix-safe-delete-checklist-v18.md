# KarFix safe-delete checklist (v18)

Keep:
- app/
- migrations/
- config/
- requirements.txt
- pyproject.toml
- render.yaml
- Procfile
- run.py
- wsgi.py
- start.sh
- app/static/ referenced assets
- app/templates/

Safe to delete:
- __pycache__/
- *.pyc
- tests/
- docs/
- scripts/ (if no longer used for deployment)
- old unused logo assets
- planning markdown files that are not runtime docs
