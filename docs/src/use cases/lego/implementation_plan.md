# Lego Map Web Services Implementation Plan

## Overview

Implementation plan for creating web services that allow users to interact with tiles for building a Lego map. The system consists of four main services with different authorization levels and functionalities.

## 1. Required Components

### New Process: xyz_to_bbox
- Location: `titiler/openeo/processes/data/xyz_to_bbox.json`
- Purpose: Convert XYZ tile coordinates to spatial extent
- Logic: Use mathematical formulas to convert web mercator tile coordinates to lat/lon bounds

### Services Directory
- Location: `titiler/openeo/services/lego_services/`
- Contents: Four service definition files 
  - base.json - Public tile service
  - claim.json - Restricted tile claiming service
  - release.json - Restricted tile release service
  - commit.json - Restricted tile commit service

## 2. Service Specifications

### Base Service (Public)
```json
{
  "id": "lego-base-service",
  "type": "XYZ",
  "configuration": {
    "scope": "public",
    "tile_size": 256,
    "minzoom": 7,
    "maxzoom": 7
  }
}
```

### Claim Service (Restricted)
```json
{
  "id": "lego-claim-service", 
  "type": "XYZ",
  "configuration": {
    "scope": "restricted",
    "tile_store": true,
    "tile_size": 256,
    "minzoom": 7,
    "maxzoom": 7,
    "grid": {
      "width": 26,
      "height": 27,
      "x_range": [55, 80],
      "y_range": [27, 52]
    }
  }
}
```

### Release Service (Restricted)
```json
{
  "id": "lego-release-service",
  "type": "XYZ", 
  "configuration": {
    "scope": "restricted",
    "tile_store": true,
    "referenced_service": "lego-base-service"
  }
}
```

### Commit Service (Restricted) 
```json
{
  "id": "lego-commit-service",
  "type": "XYZ",
  "configuration": {
    "scope": "restricted", 
    "tile_store": true,
    "referenced_service": "lego-base-service"
  }
}
```

## 3. Process Graphs

### Base Service Process Graph
```json
{
  "load": {
    "process_id": "load_collection_and_reduce",
    "arguments": {
      "bands": ["B04", "B03", "B02"],
      "id": "sentinel-2-global-mosaics",
      "spatial_extent": {
        "east": {"from_parameter": "spatial_extent_east"},
        "north": {"from_parameter": "spatial_extent_north"},
        "south": {"from_parameter": "spatial_extent_south"},
        "west": {"from_parameter": "spatial_extent_west"},
        "crs": {"from_parameter": "spatial_extent_crs"}
      },
      "temporal_extent": [
        "2024-04-01T00:00:00Z",
        "2024-04-30T23:59:59Z"
      ]
    }
  },
  "scale": {
    "process_id": "linear_scale_range",
    "arguments": {
      "x": {"from_node": "load"},
      "inputMin": 0,
      "inputMax": 10000,
      "outputMax": 255
    }
  },
  "color": {
    "process_id": "color_formula",
    "arguments": {
      "data": {"from_node": "scale"},
      "formula": "Gamma R 2 Gamma G 2.32 Gamma B 2.2 Sigmoidal RGB 10 0.237 Saturation 1.15"
    }
  },
  "lego": {
    "process_id": "legofication",
    "arguments": {
      "data": {"from_node": "color"},
      "nbbricks": 4,
      "bricksize": 64,
      "water_threshold": 0.1
    }
  },
  "save": {
    "process_id": "save_result",
    "arguments": {
      "data": {"from_node": "lego"},
      "format": "PNG"
    },
    "result": true
  }
}
```

### Claim Service Process Graph
```json
{
  "claim": {
    "process_id": "tile_assignment",
    "arguments": {
      "zoom": 7,
      "x_range": [55, 80],
      "y_range": [27, 52],
      "stage": "claim"
    }
  },
  "bbox": {
    "process_id": "xyz_to_bbox",
    "arguments": {
      "x": {"from_node": "claim", "path": "$.x"},
      "y": {"from_node": "claim", "path": "$.y"},
      "z": {"from_node": "claim", "path": "$.z"}
    }
  },
  "params": {
    "process_id": "get_param_item",
    "arguments": {
      "parameter": {"from_node": "bbox"},
      "path": "$"
    }
  },
  "load": {
    "process_id": "load_collection_and_reduce",
    "arguments": {
      "bands": ["B04", "B03", "B02"],
      "id": "sentinel-2-global-mosaics",
      "spatial_extent": {"from_node": "params"}
    }
  },
  "instructions": {
    "process_id": "generate_lego_instructions",
    "arguments": {
      "data": {"from_node": "load"}
    },
    "result": true
  }
}
```

### Release Service Process Graph
```json
{
  "release": {
    "process_id": "tile_assignment",
    "arguments": {
      "stage": "release",
      "service_id": "lego-base-service"
    },
    "result": true
  }
}
```

### Commit Service Process Graph
```json
{
  "commit": {
    "process_id": "tile_assignment",
    "arguments": {
      "stage": "submit",
      "service_id": "lego-base-service"
    },
    "result": true
  }
}
```

## 4. Implementation Steps

1. Create xyz_to_bbox Process
- Create process definition file
- Implement conversion logic
- Add unit tests

2. Setup Base Service
- Copy and adapt existing lego-s2-mosaic service
- Configure as public service
- Test tile generation

3. Setup Tile Management Services
- Create claim service with tile store integration
- Create release service
- Create commit service
- Add authentication handlers
- Test authorization

4. Integration Testing
- Test full workflow:
  1. Browse base map service
  2. Claim a tile
  3. Get build instructions
  4. Release or commit tile

## 5. Database Configuration

PostgreSQL tile store configuration:
```sql
CREATE TABLE tile_assignments (
  id SERIAL PRIMARY KEY,
  service_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  x INTEGER NOT NULL,
  y INTEGER NOT NULL,
  z INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('claimed', 'released', 'submitted')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(service_id, x, y, z)
);
```

## 6. Testing Strategy

1. Unit Tests:
- xyz_to_bbox process validation
- Tile assignment process validation
- Authorization validation

2. Integration Tests:
- Full workflow testing
- Error handling
- Concurrent access handling

3. Load Tests:
- Multiple concurrent users
- Grid capacity testing
- Assignment conflicts
