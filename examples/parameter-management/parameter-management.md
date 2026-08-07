# Parameter Management Examples

This file provides practical examples of the parameter management features in openEO by TiTiler.

## Basic Query Parameters

### Simple Parameter Override

```bash
# Basic parameter substitution
curl -X POST "http://localhost:8081/result?collection=L8&temporal_extent=[\"2024-01-01\",\"2024-06-30\"]" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": {"from_parameter": "collection"},
            "temporal_extent": {"from_parameter": "temporal_extent"},
            "spatial_extent": {
              "west": 16.1,
              "east": 16.6,
              "north": 48.6,
              "south": 47.2
            }
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "collection",
          "schema": {"type": "string"},
          "default": "S2"
        },
        {
          "name": "temporal_extent",
          "schema": {"type": "array"},
          "default": ["2023-01-01", "2023-12-31"]
        }
      ]
    }
  }'
```

### Complex Object Parameters

```bash
# Using complex JSON objects as parameters
curl -X POST "http://localhost:8081/result?bounding_box={\"west\":10,\"east\":20,\"north\":50,\"south\":40}&bands=[\"B04\",\"B03\",\"B02\"]" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": "S2",
            "spatial_extent": {"from_parameter": "bounding_box"},
            "bands": {"from_parameter": "bands"},
            "temporal_extent": ["2024-01-01", "2024-06-30"]
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "bounding_box",
          "description": "Spatial bounding box",
          "schema": {
            "type": "object",
            "required": ["west", "east", "north", "south"],
            "properties": {
              "west": {"type": "number"},
              "east": {"type": "number"},
              "north": {"type": "number"},
              "south": {"type": "number"}
            }
          },
          "default": {
            "west": 16.1,
            "east": 16.6,
            "north": 48.6,
            "south": 47.2
          }
        },
        {
          "name": "bands",
          "schema": {"type": "array"},
          "default": ["B04", "B08"]
        }
      ]
    }
  }'
```

## XYZ Tile Service Parameters

### Create Parameterized XYZ Service

```bash
# Create a service with bounding_box parameter
curl -X POST "http://localhost:8081/services" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": "S2",
            "spatial_extent": {"from_parameter": "bounding_box"},
            "temporal_extent": {"from_parameter": "time_range"},
            "bands": {"from_parameter": "bands"}
          }
        },
        "save1": {
          "process_id": "save_result",
          "arguments": {
            "data": {"from_node": "load1"},
            "format": "PNG"
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "time_range",
          "description": "Temporal extent for data loading",
          "schema": {"type": "array"},
          "default": ["2023-01-01", "2023-12-31"]
        },
        {
          "name": "bands",
          "description": "Spectral bands to load",
          "schema": {"type": "array"},
          "default": ["B04", "B03", "B02"]
        }
      ]
    },
    "type": "XYZ",
    "title": "Parameterized Sentinel-2 Service"
  }'
```

### Use XYZ Service with Parameters

```bash
# Access tiles with custom parameters
curl "http://localhost:8081/services/xyz/{service_id}/tiles/10/512/341?time_range=[\"2024-06-01\",\"2024-06-30\"]&bands=[\"B08\",\"B04\",\"B03\"]" \
  -H "Authorization: Bearer test" \
  -o tile.png
```

### Deprecated: Individual Spatial Parameters

> **⚠️ Deprecated**: The `spatial_extent_*` parameters are deprecated and will be removed in a future release. Use the `bounding_box` parameter instead.

For backward compatibility, you can still use individual spatial extent parameters:

```json
{
  "spatial_extent": {
    "west": {"from_parameter": "spatial_extent_west"},
    "east": {"from_parameter": "spatial_extent_east"},
    "north": {"from_parameter": "spatial_extent_north"},
    "south": {"from_parameter": "spatial_extent_south"}
  }
}
```

These individual parameters (`spatial_extent_west`, `spatial_extent_east`, etc.) are automatically provided by the system for XYZ tile services, but using the `bounding_box` parameter is recommended for new implementations.

## User Context Examples

### Process with User Information

```bash
# Process that uses authenticated user information
curl -X POST "http://localhost:8081/result" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": "S2",
            "spatial_extent": {
              "west": 16.1,
              "east": 16.6,
              "north": 48.6,
              "south": 47.2
            },
            "temporal_extent": ["2024-01-01", "2024-06-30"]
          }
        },
        "user_filter": {
          "process_id": "custom_user_process",
          "arguments": {
            "data": {"from_node": "load1"},
            "user": {"from_parameter": "_openeo_user"}
          },
          "result": true
        }
      }
    }
  }'
```

## Parameter Validation Examples

### Schema Validation

```bash
# Parameters with strict validation
curl -X POST "http://localhost:8081/result?cloud_cover=15&processing_level=L2A" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": "S2",
            "spatial_extent": {
              "west": 16.1,
              "east": 16.6,
              "north": 48.6,
              "south": 47.2
            },
            "temporal_extent": ["2024-01-01", "2024-06-30"],
            "properties": {
              "cloud_cover": {"lte": {"from_parameter": "cloud_cover"}},
              "processing_level": {"eq": {"from_parameter": "processing_level"}}
            }
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "cloud_cover",
          "description": "Maximum cloud cover percentage",
          "schema": {
            "type": "number",
            "minimum": 0,
            "maximum": 100
          },
          "default": 20
        },
        {
          "name": "processing_level",
          "description": "Sentinel-2 processing level",
          "schema": {
            "type": "string",
            "enum": ["L1C", "L2A"]
          },
          "default": "L2A"
        }
      ]
    }
  }'
```

## Error Handling

### Parameter Validation Errors

```bash
# This will fail validation (cloud_cover > 100)
curl -X POST "http://localhost:8081/result?cloud_cover=150" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "id": "S2",
            "properties": {
              "cloud_cover": {"lte": {"from_parameter": "cloud_cover"}}
            }
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "cloud_cover",
          "schema": {
            "type": "number",
            "minimum": 0,
            "maximum": 100
          }
        }
      ]
    }
  }'

# Response: 422 Unprocessable Entity with validation details
```

### Invalid JSON Parameters

```bash
# This will fail due to malformed JSON
curl -X POST "http://localhost:8081/result?bounding_box={invalid-json}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "process": {
      "process_graph": {
        "load1": {
          "process_id": "load_collection",
          "arguments": {
            "spatial_extent": {"from_parameter": "bounding_box"}
          },
          "result": true
        }
      },
      "parameters": [
        {
          "name": "bounding_box",
          "schema": {"type": "object"}
        }
      ]
    }
  }'

# Response: 400 Bad Request with JSON parsing error details
```

## Migration from Previous Versions

### Update User Parameter References

```bash
# OLD: Using "user" parameter (no longer works)
{
  "arguments": {
    "user_data": {"from_parameter": "user"}
  }
}

# NEW: Using "_openeo_user" reserved parameter
{
  "arguments": {
    "user_data": {"from_parameter": "_openeo_user"}
  }
}
```

### Parameter Definition Migration

```bash
# OLD: Manual parameter handling (deprecated)
# Parameters were passed but not formally defined

# NEW: Formal parameter definitions with validation
{
  "process_graph": { ... },
  "parameters": [
    {
      "name": "my_param",
      "description": "Parameter description",
      "schema": {"type": "string"},
      "default": "default_value"
    }
  ]
}
```
