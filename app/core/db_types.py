"""
Custom SQLAlchemy types for cross-database compatibility
"""
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
import uuid as uuid_pkg


class UUID(TypeDecorator):
    """
    Platform-independent UUID type.
    
    Uses PostgreSQL's UUID type when connecting to PostgreSQL,
    otherwise uses CHAR(36) for databases like SQLite.
    
    Stores UUIDs as strings in non-PostgreSQL databases.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Load the appropriate type based on the database dialect"""
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        """Convert UUID to string for storage"""
        if value is None:
            return value
        
        # Ensure value is a UUID object
        if not isinstance(value, uuid_pkg.UUID):
            try:
                value = uuid_pkg.UUID(value)
            except (ValueError, AttributeError):
                raise ValueError(f"Cannot convert {value} to UUID")
        
        # Return as string
        return str(value)

    def process_result_value(self, value, dialect):
        """Convert string back to UUID when retrieving"""
        if value is None:
            return value
        
        # If already UUID, return it
        if isinstance(value, uuid_pkg.UUID):
            return value
        
        # Convert string to UUID
        return uuid_pkg.UUID(value)


# Re-export uuid for convenience
def uuid4():
    """Generate a new UUID4"""
    return uuid_pkg.uuid4()