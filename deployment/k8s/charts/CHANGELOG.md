# Changelog

## 2.0.1 (2026-06-26)

## What's Changed
* fix: correct default image tag format in deployment.yaml by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/276
* fix: remove deprecated load_collection_and_reduce process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/277
* fix: add semver Docker tags on release (0.x.y, 0.x, 0, latest) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/278
* feat: add filter_temporal openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/282
* feat: add mask openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/283
* chore(main): release 0.16.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/273
* perf: concurrently prefetch in-interval slices by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/286
* chore(main): release 0.16.1 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/287
* fix: accept TemporalInterval extent from the graph parser by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/288
* chore(main): release 0.16.2 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/289
* fix: seed pixel-selection band count from the realized image by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/290
* chore(main): release 0.16.3 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/291
* fix: accept TemporalIntervals in aggregate_temporal by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/292
* chore(main): release 0.16.4 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/293
* fix: merge_cubes handles mixed tz-aware/naive datetime keys by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/294
* chore(main): release 0.16.5 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/295
* fix: save_result GTiff preserves data instead of uint8/RGB by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/297
* fix: load_collection no longer silently caps items at 100 by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/302
* fix: make /healthz async so liveness isn't starved under load by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/303
* docs: add concurrency & memory usage audits for titiler-openeo by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/306
* feat: implement array_apply function to apply processes to array elements by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/313
* feat: array_apply over temporal dimension + thread pool by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/315
* fix: evaluate callbacks exactly once + graph integration tests by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/316
* perf: free process-graph intermediates during evaluation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/311
* fix: forward enclosing scope to array_apply callbacks + realized arrays by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/317
* fix: preserve nodata masks when stacking arrays by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/318
* fix: aggregators reduce over the leading axis, not globally by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/319
* perf: compute float math in float32 (promote integer inputs) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/309
* fix: evaluate the bands callback once over a multi-temporal stack by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/320
* feat: apply STAC raster:scale/offset (per band) on read by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/322
* fix: validate ignore_nodata as boolean (clear error, not numpy crash) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/321
* fix: bound & document GDAL/VSI native caches by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/310
* fix: collapse temporal axis into bands for target_dimension by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/323
* fix: recompute reducer per interval (fixes identical/grayscale periods) by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/324
* feat: add rename_labels by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/325
* feat: add resample_cube_spatial; unify spatial resampling by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/326
* fix: normalize RasterStack timestamps to naive UTC at creation by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/327
* fix: set band_descriptions for type=bands/spectral by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/328
* fix: sentinel key + band labels for multi-sensor feature fusion by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/329


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-chart-v2.0.0...titiler-openeo-chart-v2.0.1

## 2.0.0 (2026-06-15)

## What's Changed
* ci: gitHub Actions to commit SHAs (coordination#239) by @lhoupert in https://github.com/sentinel-hub/titiler-openeo/pull/235
* style: remove trailing white space by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/237
* chore(deps): bump the all group across 1 directory with 6 updates by @dependabot[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/236
* feat: update openeo dependencies and add python3.13 by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/239
* feat: add aggregate_temporal openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/240
* feat: add `merge_cubes` openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/241
* ci: use python trusted publishing by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/242
* fix: refactor _value_to_openeo_name — dict should not default to 'datacube' by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/247
* feat: implement mask_polygon openEO process by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/246
* chore(main): release 0.14.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/234
* fix: reproject bbox to output CRS before calculating dimensions by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/248
* fix: update openeo-pg-parser-networkx dep by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/249
* chore(main): release 0.14.1 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/250
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
* chore(main): release 0.15.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/258
* ci: exclude nested CHANGELOG.md files from markdownlint by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/272


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-chart-v1.1.0...titiler-openeo-chart-v2.0.0

## 1.1.0 (2026-03-24)

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
* fix: update versioning in CI and deployment configurations by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/230
* fix: helm chart default version for docker container. by @pantierra in https://github.com/sentinel-hub/titiler-openeo/pull/229
* feat: update for titiler 2.0 and rio-tiler 9.0 by @vincentsarago in https://github.com/sentinel-hub/titiler-openeo/pull/227
* fix: handle non-compliant STAC collection summaries by @emmanuelmathot in https://github.com/sentinel-hub/titiler-openeo/pull/233
* chore(main): release 0.13.0 by @github-actions[bot] in https://github.com/sentinel-hub/titiler-openeo/pull/224


**Full Changelog**: https://github.com/sentinel-hub/titiler-openeo/compare/titiler-openeo-chart-v1.0.0...titiler-openeo-chart-v1.1.0
