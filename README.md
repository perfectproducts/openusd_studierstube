# MkDocs + GitHub Actions Starter

This repo is a minimal, ready-to-deploy MkDocs site using the **Material** theme and **GitHub Pages**.

## Quick start

1. Create a new repo on GitHub (private or public).
2. Download this starter as a ZIP, unzip it, and push the contents to your new repo.
3. Optional: update `repo_url` and `repo_name` in `mkdocs.yml`.
4. Go to **Settings ▸ Pages** and ensure the **Build and deployment** source is set to **GitHub Actions**.
5. Push to `main`. The included workflow will build and publish your site automatically.
6. After the first deploy, your site will be available at:  
   `https://<your-user>.github.io/<your-repo>/`

## Local preview

```bash
pip install -r requirements.txt
mkdocs serve
```

Then open http://127.0.0.1:8000

---

### Customize the theme

- Start editing `docs/index.md` and `docs/about.md`.
- Add pages and update the `nav` section in `mkdocs.yml`.
- See the Material for MkDocs docs for more features.