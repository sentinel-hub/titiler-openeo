# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

* move `get_load_collection_nodes` and `resolves_process_graph_parameters` method within the `EndpointsFactory` class

## [0.5.0] (2025-11-05)

### Fixed

* improve relative imports to avoid circular dependencies

## [0.4.0] (2025-11-04)

### Added

* Add user tracking functionality across all store implementations (SQLAlchemy, DuckDB, Local) to monitor user authentication history and activity
* Update `titiler` dependency to `>=0.24.0,<0.25`
* Update `openeo-pg-parser-networkx` to `==2025.10.1`

## [0.3.0] (2025-06-04)

### Added

* Added force-release functionality for tile assignment and update documentation [#96](https://github.com/sentinel-hub/titiler-openeo/pull/96)
* Added tile update functionality [#97](https://github.com/sentinel-hub/titiler-openeo/pull/97)
* Add width and height parameters to load_collection and load_collection_and_reduce processes [#99](https://github.com/sentinel-hub/titiler-openeo/pull/99)
* Add tiles_summary process and implement get_all_tiles method in TileAssignmentStore [#101](https://github.com/sentinel-hub/titiler-openeo/pull/101)
* Add max and min functions with no-data handling [#102](https://github.com/sentinel-hub/titiler-openeo/pull/102)

### Fixed

* Enhances pixel limit check to avoid double counting mosaic items by grouping them by datetime. [#98](https://github.com/sentinel-hub/titiler-openeo/pull/98)

### Changed

* Refactor tile assignment and add spatial extent handling [#100](https://github.com/sentinel-hub/titiler-openeo/pull/100)
* Refactor STAC reader and enhance output dimension handling [#103](https://github.com/sentinel-hub/titiler-openeo/pull/103)
* Refactor STAC item handling and enhance metadata retrieval [#104](https://github.com/sentinel-hub/titiler-openeo/pull/104)

## [0.2.1] (2025-05-21)

### Added

* Added force-release functionality for tile assignment to release tiles regardless of state

### Changed

* Fix load_collection to properly merge items from same date to maintain strict temporal dimension [#93](https://github.com/sentinel-hub/titiler-openeo/pull/93)
* Improve error handling for output size limits with clearer error messages and proper pixel count calculation [#94](https://github.com/sentinel-hub/titiler-openeo/pull/94)

## [0.2.0] (2025-05-19)

### Added

* OpenEO process graph to CQL2-JSON conversion feature [#65](https://github.com/sentinel-hub/titiler-openeo/pull/65)
* Output size estimation and validation [#58](https://github.com/sentinel-hub/titiler-openeo/pull/58)
* NDWI process implementation [#67](https://github.com/sentinel-hub/titiler-openeo/pull/67)
* `load_url` process for direct COG loading [#70](https://github.com/sentinel-hub/titiler-openeo/pull/70)
* PostgreSQL subchart support [#73](https://github.com/sentinel-hub/titiler-openeo/pull/73)
* Support for default services configuration [#74](https://github.com/sentinel-hub/titiler-openeo/pull/74)
* DynamicCacheControlMiddleware for improved cache management [#78](https://github.com/sentinel-hub/titiler-openeo/pull/78)
* Tile assignment functionality with SQLAlchemy integration [#80](https://github.com/sentinel-hub/titiler-openeo/pull/80)
* Service authorization management for restricted access [#81](https://github.com/sentinel-hub/titiler-openeo/pull/81)
* get_param_item process for JSONPath extraction [#82](https://github.com/sentinel-hub/titiler-openeo/pull/82)

### Changed

* Implement lazy rasterstack [#62](https://github.com/sentinel-hub/titiler-openeo/pull/62)
* Refactor processes to standardize data types to 'datacube' [#68](https://github.com/sentinel-hub/titiler-openeo/pull/68)
* Enhance navigation structure and improve documentation readability [#72](https://github.com/sentinel-hub/titiler-openeo/pull/72)
* Enhance service input validation and handling logic [#77](https://github.com/sentinel-hub/titiler-openeo/pull/77)
* Enhance process nodes with user parameter handling [#79](https://github.com/sentinel-hub/titiler-openeo/pull/79)
* Enhance tile assignment process with user control [#83](https://github.com/sentinel-hub/titiler-openeo/pull/83)
* Enhance user parameter handling in processes [#84](https://github.com/sentinel-hub/titiler-openeo/pull/84)

### Fixed

* Add check for version sync [#49](https://github.com/sentinel-hub/titiler-openeo/pull/49)

## [0.1.0] (2025-04-07)

Initial release of openEO by TiTiler

[unreleased]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/sentinel-hub/titiler-openeo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sentinel-hub/titiler-openeo/releases/tag/0.1.0
