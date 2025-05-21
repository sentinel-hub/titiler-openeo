# Release Process

This document outlines the steps to create a new release of titiler-openeo.

## Steps

1. Create a new branch for the release:
   ```bash
   git checkout -b release/vX.Y.Z
   ```

2. Update version numbers in the following files:
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
