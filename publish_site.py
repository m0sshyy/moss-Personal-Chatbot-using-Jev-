"""
Publishes the project page (site/) to Firebase Hosting: https://moss-alya.web.app

    .venv\\Scripts\\python publish_site.py

Uploads every file in site/ and keeps /dashboard pointing at the bot on Cloud Run
(the private dashboard). Uses your gcloud login, so no keys are needed.
"""

import gzip
import hashlib
import json
import os
import pathlib
import subprocess
import urllib.request

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
SITE = os.environ.get("FIREBASE_SITE", "moss-alya")
API = "https://firebasehosting.googleapis.com/v1beta1"
GCLOUD = os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")
RUN = {"serviceId": "mosshybot", "region": "asia-southeast1"}
CONFIG = {
    "cleanUrls": True,  # /privacy serves privacy.html
    "rewrites": [{"glob": "/dashboard", "run": RUN}, {"glob": "/dashboard/**", "run": RUN}],
    "headers": [{"glob": "**", "headers": {"X-Content-Type-Options": "nosniff",
                                           "Referrer-Policy": "strict-origin-when-cross-origin"}}],
}


def main() -> None:
    token = subprocess.run([GCLOUD, "auth", "print-access-token"], capture_output=True, text=True,
                           shell=True).stdout.strip()

    def call(method, url, body=None, data=None, content_type="application/json"):
        request = urllib.request.Request(url, method=method, data=data if data is not None else json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": content_type,
                                                  "x-goog-user-project": PROJECT})
        with urllib.request.urlopen(request) as response:
            return json.load(response) if content_type == "application/json" else response.status

    root = pathlib.Path(__file__).parent / "site"
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            zipped = gzip.compress(path.read_bytes(), mtime=0)
            files["/" + path.relative_to(root).as_posix()] = (hashlib.sha256(zipped).hexdigest(), zipped)

    version = call("POST", f"{API}/sites/{SITE}/versions", {"config": CONFIG})["name"]
    upload = call("POST", f"{API}/{version}:populateFiles", {"files": {p: h for p, (h, _) in files.items()}})
    needed = set(upload.get("uploadRequiredHashes", []))
    for path, (digest, zipped) in files.items():
        if digest in needed:
            call("POST", f"{upload['uploadUrl']}/{digest}", data=zipped, content_type="application/octet-stream")
            print("uploaded", path)
    call("PATCH", f"{API}/{version}?update_mask=status", {"status": "FINALIZED"})
    call("POST", f"{API}/sites/{SITE}/releases?versionName={version}", {"message": "Project page"})
    print(f"Live at https://{SITE}.web.app")


if __name__ == "__main__":
    main()
