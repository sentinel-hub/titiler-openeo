# Changelog

## 0.16.4 (2026-06-21)

## What's Changed
* fix: accept TemporalIntervals in aggregate_temporal by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/292


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.16.3...v0.16.4

## 0.16.3 (2026-06-21)

## What's Changed
* fix: seed pixel-selection band count from the realized image by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/290


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.16.2...v0.16.3

## 0.16.2 (2026-06-21)

## What's Changed
* fix: accept TemporalInterval extent from the graph parser by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/288


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.16.1...v0.16.2

## 0.16.1 (2026-06-21)

## What's Changed
* perf: concurrently prefetch in-interval slices by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/286


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.16.0...v0.16.1

## 0.16.0 (2026-06-19)

## What's Changed
* ci: exclude nested CHANGELOG.md files from markdownlint by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/272
* chore(main): release titiler-openeo-chart 2.0.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/238
* fix: correct default image tag format in deployment.yaml by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/276
* fix: remove deprecated load_collection_and_reduce process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/277
* fix: add semver Docker tags on release (0.x.y, 0.x, 0, latest) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/278
* feat: add filter_temporal openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/282
* feat: add mask openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/283


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.15.0...v0.16.0

## 0.15.0 (2026-06-15)

## What's Changed
* fix: resolve ParameterReference objects in context for callbacks by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/257
* fix: nested resolution by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/259
* feat: add logical OR operation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/260
* fix: update context parameter type to Optional[Any] as per spec by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/261
* fix: align if_ operands with leading spectral dimension by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/262
* chore(deps): bump the all group across 1 directory with 9 updates by @dependabot[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/263
* feat: add validation for saving multi-slice RasterStack to single-frame formats by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/264
* feat: add /healthz and /readyz endpoints with backend health checks by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/269
* feat(helm)!: publish chart to ghcr OCI and make postgres DSN GitOps-compatible by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/267
* ci: ignore release-please PRs in title validation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/270
* refactor: remove /readyz response cache by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/271


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.14.1...v0.15.0

## 0.14.1 (2026-04-01)

## What's Changed
* fix: reproject bbox to output CRS before calculating dimensions by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/248
* fix: update openeo-pg-parser-networkx dep by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/249


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.14.0...v0.14.1

## 0.14.0 (2026-03-31)

## What's Changed
* chore(main): release titiler-openeo-chart 1.1.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/197
* ci: gitHub Actions to commit SHAs (coordination#239) by @lhoupert in https://github.com/sentinel-hub/titiler-openeo/pull/235
* style: remove trailing white space by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/237
* chore(deps): bump the all group across 1 directory with 6 updates by @dependabot[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/236
* feat: update openeo dependencies and add python3.13 by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/239
* feat: add aggregate_temporal openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/240
* feat: add `merge_cubes` openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/241
* ci: use python trusted publishing by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/242
* fix: refactor _value_to_openeo_name — dict should not default to 'datacube' by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/247
* feat: implement mask_polygon openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/246


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.13.0...v0.14.0

## 0.13.0 (2026-03-24)

## What's Changed
* docs: add openeo-titiler logos by @zacdezgeo in https://github.com/sentinel-hub/titiler-openeo/pull/223
* fix: set titiler requirement upper limit by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/226
* fix: update versioning in CI and deployment configurations by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/230
* fix: helm chart default version for docker container. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/229
* feat: update for titiler 2.0 and rio-tiler 9.0 by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/227
* fix: handle non-compliant STAC collection summaries by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/233


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/v0.12.0...v0.13.0

## 0.12.0 (2026-02-03)

## What's Changed
* fix: add version extraction from tags in CI workflow by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/209
* refactor: spectral dimension reduction to unify handling by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/211
* ci: relied on container-registry-cleanup instead of custom script. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/214
* feat: make LazyRasterStack truly lazy with deferred task execution by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/215
* fix: update resample_spatial method to use OpenEO string alias by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/217
* refactor: unify ImageRef class and complete RasterStack documentation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/216
* feat: add target_crs parameter to load_collection for native CRS preservation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/219
* fix: update validate_process_graph to use ProcessGraphValidation model by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/220
* fix: correct multi-tile mosaic termination by removing cutline_mask from individual tiles by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/222


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-v0.11.0...titiler-openeo-v0.12.0

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
