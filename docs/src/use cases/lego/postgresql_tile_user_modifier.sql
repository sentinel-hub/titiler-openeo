/*
PostgreSQL Stored Procedure for Tile User ID Modification
=========================================================

This script creates a PostgreSQL trigger system that automatically modifies user IDs 
when tiles are submitted in the titiler-openeo application.

IMPORTANT: This is PostgreSQL-specific syntax and will NOT work with other databases.

Based on the SQLAlchemy tile_assignments table structure:
- id (Integer, Primary Key)
- service_id (String)
- user_id (String) 
- x, y, z (Integer coordinates)
- stage (String)
- created_at (DateTime)
- data (JSON)

Installation Instructions:
1. Connect to your PostgreSQL database
2. Execute this entire script
3. Configure user mappings using the helper functions
4. The trigger will automatically activate on tile submissions

*/

-- ============================================================================
-- 1. CREATE USER MAPPING TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_id_mappings (
    id SERIAL PRIMARY KEY,
    original_user_id VARCHAR(255) NOT NULL,
    modified_user_id VARCHAR(255) NOT NULL,
    service_id VARCHAR(255), -- Optional: restrict mapping to specific services
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(original_user_id, service_id)
);

-- Performance index
CREATE INDEX IF NOT EXISTS idx_user_mappings_original_active 
ON user_id_mappings(original_user_id, active) WHERE active = TRUE;

-- ============================================================================
-- 2. CREATE AUDIT TABLE (Optional but recommended)
-- ============================================================================

CREATE TABLE IF NOT EXISTS tile_user_modifications (
    id SERIAL PRIMARY KEY,
    tile_id INTEGER NOT NULL,
    service_id VARCHAR(255) NOT NULL,
    original_user_id VARCHAR(255) NOT NULL,
    modified_user_id VARCHAR(255) NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    z INTEGER NOT NULL,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance index for audit table
CREATE INDEX IF NOT EXISTS idx_tile_modifications_service_user 
ON tile_user_modifications(service_id, original_user_id);

-- ============================================================================
-- 3. CREATE TRIGGER FUNCTION
-- ============================================================================

CREATE OR REPLACE FUNCTION modify_tile_user_id()
RETURNS TRIGGER AS $$
DECLARE
    new_user_id VARCHAR(255);
BEGIN
    -- Only process if the tile stage is being changed to 'submitted'
    IF NEW.stage = 'submitted' AND (OLD.stage IS NULL OR OLD.stage != 'submitted') THEN
        
        -- Look for a user ID mapping
        SELECT modified_user_id INTO new_user_id
        FROM user_id_mappings
        WHERE original_user_id = NEW.user_id
          AND active = TRUE
          AND (service_id IS NULL OR service_id = NEW.service_id)
        ORDER BY service_id NULLS LAST -- Prefer service-specific mappings
        LIMIT 1;
        
        -- If a mapping is found, update the user_id
        IF new_user_id IS NOT NULL THEN
            -- Log the change for audit purposes
            INSERT INTO tile_user_modifications (
                tile_id,
                service_id,
                original_user_id,
                modified_user_id,
                x, y, z,
                modified_at
            ) VALUES (
                NEW.id,
                NEW.service_id,
                NEW.user_id, -- Original user_id before modification
                new_user_id, -- New user_id
                NEW.x, NEW.y, NEW.z,
                CURRENT_TIMESTAMP
            );
            
            -- Modify the user_id
            NEW.user_id := new_user_id;
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 4. CREATE TRIGGER
-- ============================================================================

DROP TRIGGER IF EXISTS trigger_modify_tile_user_id ON tile_assignments;
CREATE TRIGGER trigger_modify_tile_user_id
    BEFORE UPDATE ON tile_assignments
    FOR EACH ROW
    EXECUTE FUNCTION modify_tile_user_id();

-- ============================================================================
-- 5. HELPER FUNCTIONS FOR MANAGING USER MAPPINGS
-- ============================================================================

-- Add or update a user ID mapping
CREATE OR REPLACE FUNCTION add_user_mapping(
    p_original_user_id VARCHAR(255),
    p_modified_user_id VARCHAR(255),
    p_service_id VARCHAR(255) DEFAULT NULL
)
RETURNS BOOLEAN AS $$
BEGIN
    INSERT INTO user_id_mappings (original_user_id, modified_user_id, service_id)
    VALUES (p_original_user_id, p_modified_user_id, p_service_id)
    ON CONFLICT (original_user_id, service_id) 
    DO UPDATE SET 
        modified_user_id = EXCLUDED.modified_user_id,
        active = TRUE,
        updated_at = CURRENT_TIMESTAMP;
    
    RETURN TRUE;
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Deactivate a user ID mapping
CREATE OR REPLACE FUNCTION remove_user_mapping(
    p_original_user_id VARCHAR(255),
    p_service_id VARCHAR(255) DEFAULT NULL
)
RETURNS BOOLEAN AS $$
BEGIN
    UPDATE user_id_mappings 
    SET active = FALSE, updated_at = CURRENT_TIMESTAMP
    WHERE original_user_id = p_original_user_id
      AND (p_service_id IS NULL OR service_id = p_service_id);
    
    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

-- Get all active user mappings
CREATE OR REPLACE FUNCTION get_user_mappings(p_service_id VARCHAR(255) DEFAULT NULL)
RETURNS TABLE(
    original_user_id VARCHAR(255),
    modified_user_id VARCHAR(255),
    service_id VARCHAR(255),
    created_at TIMESTAMP,
    updated_at TIMESTAMP
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        um.original_user_id,
        um.modified_user_id,
        um.service_id,
        um.created_at,
        um.updated_at
    FROM user_id_mappings um
    WHERE um.active = TRUE
      AND (p_service_id IS NULL OR um.service_id = p_service_id OR um.service_id IS NULL)
    ORDER BY um.service_id NULLS LAST, um.original_user_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 6. USAGE EXAMPLES
-- ============================================================================

/*

-- Add a global user mapping (applies to all services):
SELECT add_user_mapping('user123', 'anonymous_user_001');

-- Add a service-specific user mapping:
SELECT add_user_mapping('user456', 'anonymous_user_002', 'lego_service');

-- Add multiple mappings:
SELECT add_user_mapping('alice', 'user_a');
SELECT add_user_mapping('bob', 'user_b');
SELECT add_user_mapping('charlie', 'user_c', 'special_service');

-- Remove a user mapping:
SELECT remove_user_mapping('user123');

-- Remove a service-specific mapping:
SELECT remove_user_mapping('charlie', 'special_service');

-- View all active mappings:
SELECT * FROM get_user_mappings();

-- View mappings for a specific service:
SELECT * FROM get_user_mappings('lego_service');

-- View modification history:
SELECT 
    service_id,
    original_user_id,
    modified_user_id,
    x, y, z,
    modified_at
FROM tile_user_modifications 
ORDER BY modified_at DESC 
LIMIT 20;

-- Check if trigger is active:
SELECT 
    trigger_name,
    event_manipulation,
    event_object_table,
    action_statement
FROM information_schema.triggers 
WHERE trigger_name = 'trigger_modify_tile_user_id';

*/

-- ============================================================================
-- 7. PERMISSIONS (Uncomment and adjust as needed)
-- ============================================================================

-- Grant permissions for the openeo user
GRANT SELECT, INSERT, UPDATE ON user_id_mappings TO openeo;
GRANT SELECT, INSERT ON tile_user_modifications TO openeo;
GRANT EXECUTE ON FUNCTION add_user_mapping(VARCHAR, VARCHAR, VARCHAR) TO openeo;
GRANT EXECUTE ON FUNCTION remove_user_mapping(VARCHAR, VARCHAR) TO openeo;
GRANT EXECUTE ON FUNCTION get_user_mappings(VARCHAR) TO openeo;

-- Grant sequence permissions for auto-incrementing IDs
GRANT USAGE, SELECT ON SEQUENCE user_id_mappings_id_seq TO openeo;
GRANT USAGE, SELECT ON SEQUENCE tile_user_modifications_id_seq TO openeo;

-- ============================================================================
-- INSTALLATION COMPLETE
-- ============================================================================

-- Verify installation
DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL Tile User Modifier installation complete!';
    RAISE NOTICE 'Tables created: user_id_mappings, tile_user_modifications';
    RAISE NOTICE 'Trigger created: trigger_modify_tile_user_id on tile_assignments';
    RAISE NOTICE 'Helper functions: add_user_mapping, remove_user_mapping, get_user_mappings';
    RAISE NOTICE 'Use the helper functions to configure user ID mappings.';
END $$;
