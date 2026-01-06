# Parameter Management

This document explains the comprehensive parameter management system in openEO by TiTiler, which provides dynamic parameter substitution for both synchronous result processing and XYZ tile services.

## Overview

The parameter management system allows you to create flexible, reusable process graphs that can accept dynamic parameters at runtime. This enables:

- **Dynamic Query Parameters**: Pass parameters via query strings in API requests
- **Default Parameter Values**: Define fallback values in process graph definitions
- **Automatic Parameter Injection**: Built-in injection of system parameters like user information
- **Parameter Precedence**: Clear hierarchy for parameter resolution

## Parameter Types

### 1. Process Graph Parameters

Parameters defined in the process graph definition with optional default values:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "S2",
        "spatial_extent": { "from_parameter": "bbox" },
        "temporal_extent": { "from_parameter": "time_range" }
      },
      "result": true
    }
  },
  "parameters": [
    {
      "name": "bbox",
      "description": "Spatial bounding box",
      "schema": { "type": "object" },
      "default": {
        "west": 16.1,
        "east": 16.6,
        "north": 48.6,
        "south": 47.2
      }
    },
    {
      "name": "time_range",
      "description": "Temporal extent",
      "schema": { "type": "array" },
      "default": ["2023-01-01", "2023-12-31"]
    }
  ]
}
```

### 2. Query Parameters

Parameters passed in the URL query string that override defaults:

```bash
# Override bbox parameter
GET /result?bbox={"west":10.0,"east":20.0,"north":50.0,"south":40.0}

# Override multiple parameters
GET /services/xyz/{service_id}/tiles/{z}/{x}/{y}?time_range=["2024-01-01","2024-06-30"]&bands=["red","green","blue"]
```

### 3. Reserved System Parameters

Automatically injected parameters that provide system context:

| Parameter                    | Description               | Available In      | Comment                                                                      |
| ---------------------------- | ------------------------- | ----------------- | ---------------------------------------------------------------------------- |
| `_openeo_user`               | Authenticated user object | Both endpoints    |                                                                              |
| `_openeo_tile_store`         | Tile storage backend      | XYZ services only |                                                                              |
| `spatial_extent_*`           | Tile boundary coordinates | XYZ services only | Deprecated. Will be removed in a future release. Use `bounding_box` instead. |
| `tile_x`, `tile_y`, `tile_z` | Tile coordinates          | XYZ services only |                                                                              |
| `bounding_box`               | Tile bounding box object  | XYZ services only |                                                                              |

## Parameter Resolution Priority

Parameters are resolved in the following order (highest to lowest priority):

1. **Query Parameters**: Values passed in the URL query string
2. **System Parameters**: Automatically injected reserved parameters
3. **Default Values**: Default values defined in the process graph parameters
4. **Process Defaults**: Built-in default values from process implementations

## Endpoint Support

### POST /result (Synchronous Processing)

The `/result` endpoint supports full parameter management:

```bash
POST /result?temporal_extent=["2024-01-01","2024-12-31"]&bands=["B04","B08"]
Content-Type: application/json

{
  "process": {
    "process_graph": {
      "load1": {
        "process_id": "load_collection",
        "arguments": {
          "id": "S2",
          "temporal_extent": {"from_parameter": "temporal_extent"},
          "bands": {"from_parameter": "bands"}
        },
        "result": true
      }
    },
    "parameters": [
      {
        "name": "temporal_extent",
        "schema": {"type": "array"},
        "default": ["2023-01-01", "2023-12-31"]
      },
      {
        "name": "bands",
        "schema": {"type": "array"},
        "default": ["B04", "B03", "B02"]
      }
    ]
  }
}
```

**Features:**

- Query parameter parsing and JSON deserialization
- User injection via `_openeo_user` parameter
- Default parameter value application
- Built-in parameter substitution via OpenEO process graph parser

### GET /services/xyz/{service_id}/tiles/{z}/{x}/{y} (XYZ Tile Service)

XYZ tile services support the same parameter management with additional spatial context:

```bash
GET /services/xyz/abc123/tiles/10/512/341?temporal_extent=["2024-06-01","2024-06-30"]
```

**Additional Features:**

- Automatic spatial parameter injection (tile bounds, coordinates)
- Tile-specific context parameters
- Same query parameter and default value support as `/result`

## Parameter Validation

### JSON Parameter Validation

Complex parameters passed as query strings are automatically parsed as JSON:

```bash
# Array parameter
?bands=["red","green","blue"]

# Object parameter
?bounding_box={"west":10,"east":20,"north":50,"south":40}

# Nested object parameter
?filter_options={"cloud_cover":{"max":20},"processing_level":"L2A"}
```

### Schema Validation

Parameters are validated against their schema definitions:

```json
{
  "name": "cloud_cover_max",
  "description": "Maximum cloud cover percentage",
  "schema": {
    "type": "number",
    "minimum": 0,
    "maximum": 100
  },
  "default": 20
}
```

## Best Practices

### 1. Parameter Naming

- Use descriptive, lowercase parameter names
- Use underscores for multi-word parameters: `temporal_extent`, `cloud_cover_max`
- Avoid conflicts with reserved parameter names (`_openeo_*`)

### 2. Default Values

- Always provide sensible default values for optional parameters
- Ensure defaults work across your expected data collections and time ranges
- Document the reasoning behind default choices

### 3. Parameter Documentation

```json
{
  "name": "temporal_extent",
  "description": "Temporal extent as [start_date, end_date] in ISO 8601 format",
  "schema": {
    "type": "array",
    "minItems": 2,
    "maxItems": 2,
    "items": { "type": "string", "format": "date" }
  },
  "default": ["2023-01-01", "2023-12-31"],
  "examples": [
    ["2024-01-01", "2024-06-30"],
    ["2023-07-15", "2023-08-15"]
  ]
}
```

### 4. Complex Parameters

For complex nested parameters, use clear structure and validation:

```json
{
  "name": "processing_options",
  "description": "Processing configuration options",
  "schema": {
    "type": "object",
    "properties": {
      "cloud_mask": { "type": "boolean", "default": true },
      "atmospheric_correction": { "type": "boolean", "default": false },
      "resampling": {
        "type": "string",
        "enum": ["nearest", "bilinear", "cubic"],
        "default": "bilinear"
      }
    }
  },
  "default": {
    "cloud_mask": true,
    "atmospheric_correction": false,
    "resampling": "bilinear"
  }
}
```

## Examples

### Basic Parameter Usage

Simple parameter substitution with defaults:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": { "from_parameter": "collection" },
        "temporal_extent": { "from_parameter": "time_range" }
      },
      "result": true
    }
  },
  "parameters": [
    {
      "name": "collection",
      "schema": { "type": "string" },
      "default": "S2"
    },
    {
      "name": "time_range",
      "schema": { "type": "array" },
      "default": ["2023-01-01", "2023-12-31"]
    }
  ]
}
```

Usage: `POST /result?collection=L8&time_range=["2024-01-01","2024-06-30"]`

### Advanced Parameter Usage

Complex parameters with validation and user context:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "S2",
        "spatial_extent": { "from_parameter": "bbox" },
        "temporal_extent": { "from_parameter": "time_range" }
      }
    },
    "filter1": {
      "process_id": "filter_bands",
      "arguments": {
        "data": { "from_node": "load1" },
        "bands": { "from_parameter": "bands" }
      }
    },
    "user_process": {
      "process_id": "custom_user_process",
      "arguments": {
        "data": { "from_node": "filter1" },
        "user_id": { "from_parameter": "_openeo_user" }
      },
      "result": true
    }
  },
  "parameters": [
    {
      "name": "bbox",
      "description": "Spatial bounding box",
      "schema": {
        "type": "object",
        "required": ["west", "east", "north", "south"],
        "properties": {
          "west": { "type": "number" },
          "east": { "type": "number" },
          "north": { "type": "number" },
          "south": { "type": "number" }
        }
      }
    },
    {
      "name": "time_range",
      "schema": { "type": "array" },
      "default": ["2023-01-01", "2023-12-31"]
    },
    {
      "name": "bands",
      "schema": { "type": "array" },
      "default": ["B04", "B03", "B02"]
    }
  ]
}
```

## Troubleshooting

### Common Issues

1. **Parameter Not Found**: Ensure parameter names match exactly between `from_parameter` references and parameter definitions.

2. **Invalid JSON in Query**: When passing complex parameters, ensure proper URL encoding:

   ```bash
   # Correct
   ?bbox=%7B%22west%22%3A10%2C%22east%22%3A20%7D

   # Also correct (many tools handle this automatically)
   ?bbox={"west":10,"east":20}
   ```

3. **Type Validation Errors**: Ensure parameter values match their schema types:

   ```bash
   # Wrong - string instead of number
   ?cloud_cover="20"

   # Correct
   ?cloud_cover=20
   ```

4. **Reserved Parameter Conflicts**: Don't define parameters that conflict with reserved names (`_openeo_*`, `spatial_extent_*`, `tile_*`).

### Debugging

Enable debug logging to see parameter resolution:

```python
import logging
logging.getLogger('titiler.openeo.factory').setLevel(logging.DEBUG)
```

This will show:

- Query parameter parsing results
- Default parameter application
- Final parameter values passed to process graph
