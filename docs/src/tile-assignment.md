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
- The service has `tile_store: true` in its configuration

### Supported Store Types
Currently supported tile store implementations:
- PostgreSQL: `postgresql://user:pass@host/db`
- SQLite: `sqlite:///path/to/db.sqlite`
- SQLAlchemy URL: `sqlalchemy://...`

## Process Parameters

The tile assignment process accepts the following parameters:

- `zoom` (integer, required): Fixed zoom level for tile assignment
- `x_range` (array[integer, integer], required): Range of possible X values [min, max]
- `y_range` (array[integer, integer], required): Range of possible Y values [min, max]
- `stage` (string, required): Stage of tile assignment
  - `claim`: Request a new tile assignment
  - `release`: Release a currently assigned tile
  - `submit`: Mark a tile as submitted (locks the tile)

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
        "stage": "claim"
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
   - Cannot release a submitted tile
   - Error if user has no tile assigned

3. **Submitting a Tile**:
   - User submits their tile with stage="submit"
   - Tile becomes locked and cannot be released
   - Error if user has no tile assigned

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
