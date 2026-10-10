"""Create a reproducible source delivery without credentials or local environments."""
from pathlib import Path
import zipfile
from settings import ROOT

paths = [ROOT / name for name in ['README.md', 'requirements.txt', 'requirements-dev.txt', 'pytest.ini', '.env.example', '.gitignore']]
for folder in ['src', 'scripts', 'frontend', 'swagger', 'infrastructure', 'tests', 'docs']:
    paths.extend(path for path in (ROOT / folder).rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts and path.name not in {'config.js', '.DS_Store'})
# Evidence, if it exists, never contains API keys, recipient email, or AWS credentials.
for name in ['ingestion-report.json', 'live-verification.json', 'base-verification.json']:
    path = ROOT / 'data' / name
    if path.exists(): paths.append(path)
output = ROOT / 'DiningConcierge_Project.zip'
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, 'DiningConcierge/' + path.relative_to(ROOT).as_posix())
    archive.writestr('DiningConcierge/frontend/config.js', (ROOT / 'frontend/config.example.js').read_text())
    archive.writestr('DiningConcierge/data/.gitkeep', '')
print(output)
