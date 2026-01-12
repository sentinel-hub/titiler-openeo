# Release Process

This document outlines the steps to create a new release of titiler-openeo using SCM-based automatic versioning.

## SCM-Based Versioning

This project uses `setuptools_scm` for automatic version determination based on git tags. The version is automatically calculated from:
- Git tags (for released versions)
- Distance from the latest tag (for development versions)
- Current commit hash (for dirty builds)

## Release Steps

1. **Ensure your working directory is clean**:
   ```bash
   git status
   ```

2. **Update CHANGES.md**:
   - Change the "(Unreleased)" text to the current date
   - Ensure all changes are properly documented under the new version
   - Keep the format consistent with previous entries

3. **Create and push a release tag**:
   ```bash
   # For a patch release (0.7.0 -> 0.7.1)
   git tag v0.7.1
   
   # For a minor release (0.7.0 -> 0.8.0)
   git tag v0.8.0
   
   # For a major release (0.7.0 -> 1.0.0)
   git tag v1.0.0
   
   # Push the tag
   git push origin v0.7.1  # replace with your tag
   ```

4. **Verify the version**:
   ```bash
   python -c "import titiler.openeo; print(titiler.openeo.__version__)"
   ```

5. **Update deployment files** (if needed):
   ```
   deployment/k8s/charts/Chart.yaml:
   - Update appVersion to new version
   - Increment chart version by 0.0.1
   ```

6. **Create a GitHub release**:
   - Go to https://github.com/sentinel-hub/titiler-openeo/releases
   - Click "Draft a new release"
   - Choose the tag you just created
   - Title the release with the tag name (e.g., "v0.7.1")
   - Copy the changelog entries for this version into the description
   - Publish the release

## Building Docker Images

Docker images automatically get the correct version through setuptools_scm:

```bash
# In CI/automated builds - version is passed via SETUPTOOLS_SCM_PRETEND_VERSION
docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION=0.7.1 -t titiler-openeo:0.7.1 .

# Local builds from git repo - uses git metadata automatically
docker build -t titiler-openeo:dev .
```

GitHub Actions automatically sets the correct version based on tags and branches.

## Version Number Guidelines

Follow [Semantic Versioning (SemVer)](https://semver.org/):
- **MAJOR** version (e.g., v1.0.0): Breaking changes
- **MINOR** version (e.g., v0.8.0): New features, backward compatible
- **PATCH** version (e.g., v0.7.1): Bug fixes, backward compatible

## Development Versions

During development, setuptools_scm will automatically generate version numbers like:
- `0.7.1.dev5+g1234567` (5 commits after v0.7.0 tag, on commit 1234567)
- `0.7.1.dev5+g1234567.dirty` (same as above but with uncommitted changes)

## Checking Current Version

You can check the current version in several ways:

```bash
# From Python
python -c "import titiler.openeo; print(titiler.openeo.__version__)"

# From setuptools_scm directly
python -m setuptools_scm

# From pip (if installed)
pip show titiler-openeo
```
   ```
   deployment/k8s/charts/Chart.yaml:
   - Update appVersion to new version (e.g., 0.2.1)
   - Increment version by 0.0.1 (e.g., 0.9.0 -> 0.9.1)

   pyproject.toml:
   - Update current_version under [tool.bumpversion]

   titiler/openeo/__init__.py:
   - Update __version__
   ```

3. Update CHANGES.md:
   - Change the "(Unreleased)" text to the current date
   - Ensure all changes are properly documented under the new version
   - Keep the format consistent with previous entries

4. Create a Pull Request:
   - Push your branch to GitHub
   - Create a PR titled "Release vX.Y.Z"
   - Include a summary of the changes in the PR description
   - Wait for approval and merge

5. After the PR is merged, create a git tag and push:
   ```bash
   git checkout main
   git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

6. Create a GitHub release:
   - Go to https://github.com/sentinel-hub/titiler-openeo/releases
   - Click "Draft a new release"
   - Choose the tag you just created
   - Title the release "vX.Y.Z"
   - Copy the changelog entries for this version into the description
   - Publish the release

## Version Numbering

We follow semantic versioning (MAJOR.MINOR.PATCH):
- MAJOR version for incompatible API changes
- MINOR version for new functionality in a backward compatible manner
- PATCH version for backward compatible bug fixes

## Helm Chart Versioning

The Helm chart version (in Chart.yaml) follows its own versioning scheme:
- Increment the chart version by 0.0.1 for each release
- Update appVersion to match the new titiler-openeo version
