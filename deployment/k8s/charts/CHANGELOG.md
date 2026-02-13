# Changelog

## 1.1.0 (2026-02-13)

## What's Changed
* feat: manage multi-packages Python/Helm chart in monorepo setup by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/190
* fix: helm chart image tag from release. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/191
* chore(deps): update openeo-pg-parser-networkx dependency and remove shapely usage by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/194
* fix: streamline and fix reductions by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/193
* build: clean-up container registry. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/192
* fix: update release-please configuration to exclude specific paths by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/196
* fix: fix and improve parameter handling in core.py by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/199
* chore(main): release titiler-openeo 0.10.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/198
* fix: pydantic validation error. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/200
* fix: skip non-existent special OpenEO args in parameter resolution by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/203
* fix: cutline aggregation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/204
* chore(main): release titiler-openeo 0.10.1 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/202
* perf: enhance cache control settings for tile endpoints and update version by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/205
* chore(main): release titiler-openeo 0.10.2 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/206
* fix: implement dynamic cache control middleware and add comprehensive tests by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/207
* chore(main): release titiler-openeo 0.11.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/208
* fix: add version extraction from tags in CI workflow by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/209
* refactor: spectral dimension reduction to unify handling by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/211
* ci: relied on container-registry-cleanup instead of custom script. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/214
* feat: make LazyRasterStack truly lazy with deferred task execution by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/215
* fix: update resample_spatial method to use OpenEO string alias by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/217
* refactor: unify ImageRef class and complete RasterStack documentation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/216
* feat: add target_crs parameter to load_collection for native CRS preservation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/219
* fix: update validate_process_graph to use ProcessGraphValidation model by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/220
* fix: correct multi-tile mosaic termination by removing cutline_mask from individual tiles by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/222
* chore(main): release titiler-openeo 0.12.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/210
* docs: add openeo-titiler logos by @zacdezgeo in https://github.com/sentinel-hub/titiler-openeo/pull/223
* fix: set titiler requirement upper limit by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/226


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-chart-v1.0.0...titiler-openeo-chart-v1.1.0
