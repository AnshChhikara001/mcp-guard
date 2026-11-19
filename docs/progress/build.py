"""Put progress.json into template.html and write page.html. Run: python3 docs/progress/build.py"""

import json
import pathlib

here = pathlib.Path(__file__).parent
data = json.loads((here / "progress.json").read_text())
blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
template = (here / "template.html").read_text()
if "/*DATA*/null" not in template:
    raise SystemExit("template.html is missing the /*DATA*/null marker")
(here / "page.html").write_text(template.replace("/*DATA*/null", blob))
print(f"page.html built from progress.json (updated {data['updated']})")
