# Changelog

## 0.11.0 (2026-01-23)

## What's Changed
* fix: implement dynamic cache control middleware and add comprehensive tests by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/207


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-v0.10.2...titiler-openeo-v0.11.0

## 0.10.2 (2026-01-23)

## What's Changed
* perf: enhance cache control settings for tile endpoints and update version by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/205


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-v0.10.1...titiler-openeo-v0.10.2

## 0.10.1 (2026-01-23)

## What's Changed
* fix: pydantic validation error. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/200
* fix: skip non-existent special OpenEO args in parameter resolution by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/203
* fix: cutline aggregation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/204


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-v0.10.0...titiler-openeo-v0.10.1

## 0.10.0 (2026-01-23)

## What's Changed
* feat: manage multi-packages Python/Helm chart in monorepo setup by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/190
* fix: helm chart image tag from release. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/191
* chore(deps): update openeo-pg-parser-networkx dependency and remove shapely usage by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/194
* fix: streamline and fix reductions by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/193
* build: clean-up container registry. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/192
* fix: update release-please configuration to exclude specific paths by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/196
* fix: fix and improve parameter handling in core.py by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/199


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-v0.9.1...titiler-openeo-v0.10.0

## 0.9.1 (2026-01-22)

## What's Changed
* fix: permissions to push docker image. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/188


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.9.0...v0.9.1

## 0.9.0 (2026-01-21)

## What's Changed
* revert: setuptools_scm changes and add release-please automation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/168
* Bump actions/checkout from 5 to 6 in the all group by @dependabot[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/153
* ci: improve release please pipeline by @lhoupert in https://github.com/sentinel-hub/titiler-openeo/pull/170
* fix: permission for user store with explicit uid. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/171
* fix: revert version in release-please manifest to 0.8.0 by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/172
* fix: include-component-in-tag to false by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/173
* fix: fix release please config by @Copilot in https://github.com/sentinel-hub/titiler-openeo/pull/175
* fix: configure release-please to use python type for __init__.py by @Copilot in https://github.com/sentinel-hub/titiler-openeo/pull/176
* fix: change release-please extra-files type from python to generic by @Copilot in https://github.com/sentinel-hub/titiler-openeo/pull/177
* refactor: move version to pyproject.toml per PEP-621 by @Copilot in https://github.com/sentinel-hub/titiler-openeo/pull/178
* fix: validation openeo-test-suite WP3 by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/28
* fix: audience claim list. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/180
* chore: bump amannn/action-semantic-pull-request from 5 to 6 in the all group by @dependabot[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/179
* fix: revert Docker build changes from PR #153 by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/183
* fix: parameter resolution in nested process graphs for STAC API filtering by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/181
* fix: conditional logic for publishing jobs in CI workflow by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/184
* fix: enhance parameter resolution for BoundingBox and TemporalInterval types by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/185
* fix: parameter resolution with ParameterReference objects (Issue #186) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/187
* feat: optimize reduce_dimension to use PixelSelectionMethod for supported reducers by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/182

## New Contributors
* @lhoupert made their first contribution in https://github.com/sentinel-hub/titiler-openeo/pull/170
* @Copilot made their first contribution in https://github.com/sentinel-hub/titiler-openeo/pull/175

**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.8.0...v0.9.0
