# Tile Assignment

The tile assignment feature allows services to manage and track XYZ tile assignments to users. This is particularly useful for scenarios where you need to coordinate work across multiple users, ensuring each user works on unique tiles without overlap.

## Setup

The tile assignment feature requires two levels of configuration:

1. Global Configuration:
   ```env
   # Enable tile assignment with SQLAlchemy store
   TITILER_OPENEO_TILE_STORE_URL=postgresql://user:pass@host/db
   ```

2. Service Configuration:
   ```json
   {
     "type": "XYZ",
     "configuration": {
       "tile_store": true,
       ...other configuration options...
     }
   }
   ```

The feature will only be active when both:
- A valid tile store URL is configured at the system level
- The service has `tile_store: true` in its configuration (this will inject the store as "_openeo_tile_store" parameter)

To access the injected tile store in your process graph, use the `from_parameter` reference:

```json
{
  "process_graph": {
    "tile_assignment1": {
      "process_id": "tile_assignment",
      "arguments": {
        "store": {
          "from_parameter": "_openeo_tile_store"
        },
        "zoom": 12,
        "x_range": [1000, 1010],
        "y_range": [2000, 2010],
        "stage": "claim"
      }
    }
  }
}
```

### Supported Store Types
Currently supported tile store implementations:
- PostgreSQL: `postgresql://user:pass@host/db`
- SQLite: `sqlite:///path/to/db.sqlite`
- SQLAlchemy URL: `sqlalchemy://...`

## Process Parameters

The tile assignment process is defined with the following JSON schema:

```json
{
  "parameters": {
    "zoom": {
      "description": "Fixed zoom level for tile assignment",
      "type": "integer",
      "required": true
    },
    "x_range": {
      "description": "Range of possible X values [min, max]",
      "type": "array",
      "items": {"type": "integer"},
      "minItems": 2,
      "maxItems": 2,
      "required": true
    },
    "y_range": {
      "description": "Range of possible Y values [min, max]",
      "type": "array",
      "items": {"type": "integer"},
      "minItems": 2,
      "maxItems": 2,
      "required": true
    },
    "stage": {
      "description": "Stage of tile assignment",
      "type": "string",
      "enum": ["claim", "release", "submit", "force-release"],
      "required": true
    },
    "user_id": {
      "description": "User identifier for tile assignment",
      "type": "string",
      "required": true
    },
  }
}
```

Because `user_id` is defined with `"type": "string"`, when using `from_parameter: "_openeo_user"`, it will automatically extract just the user ID from the User object.

## Access Control

The tile assignment process ensures that each tile can only be managed by the user who claimed it. 
Each operation (release/submit/force-release) requires a valid tile assignment - a user must have 
a claimed tile to perform any operation on it.

## Usage Example

Here's an example of using the tile assignment process in a service:

```json
{
  "process_graph": {
    "tile_assignment1": {
      "process_id": "tile_assignment",
      "arguments": {
        "zoom": 12,
        "x_range": [1000, 1010],
        "y_range": [2000, 2010],
        "stage": "claim",
        "user_id": "user123"
      }
    }
  }
}
```

## Workflow

1. **Claiming a Tile**:
   - User requests a tile with stage="claim"
   - System randomly assigns an available tile within the specified ranges
   - If user already has a tile assigned, returns that tile instead
   - If no tiles are available, raises an error

2. **Releasing a Tile**:
   - User releases their tile with stage="release"
   - Tile becomes available for other users to claim
   - Cannot release a submitted tile without force-release
   - Only the owner can release their tile
   - Error if another user tries to release it

3. **Submitting a Tile**:
   - User submits their tile with stage="submit"
   - Tile becomes locked and cannot be released normally
   - Only the owner of the tile can submit it
   - Submitted tiles can only be released using force-release

4. **Force-releasing a Tile**:
   - User can force-release their tile with stage="force-release"
   - Works on any tile state (claimed or submitted)
   - Only the owner of the tile can force-release it
   - Useful for recovering tiles that are stuck in submitted state

## Error Handling

The process handles several error conditions:

- `NoTileAvailableError`: When trying to claim a tile but none are available
- `TileNotAssignedError`: When trying to release/submit a tile but user has none assigned
- `TileAlreadyLockedError`: When trying to release a submitted tile

## Implementation Details

The tile assignment system:
- Maintains persistent tile assignments using SQLAlchemy
- Ensures unique tile assignments (no two users can have the same tile)
- Randomly distributes tiles to prevent predictable assignment patterns
- Supports multiple services with independent tile assignments
- Tracks tile state (claimed/released/submitted)

## Best Practices

1. **Range Selection**:
   - Choose appropriate x_range and y_range based on your data coverage
   - Consider zoom level when determining range size
   - Avoid overlapping ranges between different services

2. **Error Handling**:
   - Always handle potential errors in your client code
   - Implement retry logic for NoTileAvailableError
   - Verify tile assignment before starting work

3. **State Management**:
   - Submit completed tiles to prevent accidental release
   - Release tiles when work is abandoned
   - Check existing assignments before claiming new tiles
