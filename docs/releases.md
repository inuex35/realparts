# Product releases

Web, Native and Blender use separate GitHub Releases in this repository.
Each product has its own version tag and can ship from a different commit.

| Product | Example tag | Download |
| --- | --- | --- |
| Web | `web-v0.2.4` | Python server and built browser UI |
| Native | `native-v0.2.4` | Python desktop app, including QML |
| Blender | `blender-v0.2.4` | Installable add-on ZIP |

Web and Native require Python 3.12 or 3.13 and an internet connection to
install dependencies. They include `START.txt`; Native is not a standalone
executable. Web runs a local Python server; GitHub Pages alone cannot run it.
Blender downloads the kernel dependencies from the add-on preferences.
The dependency wheels support Linux and Windows. macOS needs a separately
built planegcs wheel; see `wheels.yml`.

After committing and pushing the release changes, tag the desired commit:

```bash
git tag web-v0.2.4 <commit>
git push origin web-v0.2.4
```

Use `native-v...` or `blender-v...` to release those products independently.
The Product release workflow builds only the tagged product and creates a
draft with its ZIP attached. Review the draft and publish it on GitHub.
Release tags identify distributions; the shared kernel version in
`pyproject.toml` and Blender's manifest version are maintained separately.
Update the Blender manifest when shipping a new add-on version.

The workflow does not set a repository-wide Latest release. Link directly
to `/releases/tag/web-v0.2.4` (or the other product's tag).
The existing `v*` workflow for macOS wheels remains separate.

To build locally:

```bash
cd web_ui
npm ci
npm run build
cd ..
python tools/package_release.py web 0.2.4
python tools/package_release.py native 0.2.4
python tools/package_release.py blender 0.2.4
```
