"""
API Version Management
Versioning support for Hyper Alpha Arena API
"""
from typing import Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class APIVersion:
    """Represents an API version"""
    version: str
    released_at: datetime
    deprecated: bool = False
    deprecated_at: Optional[datetime] = None
    sunset_date: Optional[datetime] = None
    notes: str = ""


class APIVersionManager:
    """
    Manages API versions and deprecation schedules
    """
    
    # Define supported versions
    VERSIONS = {
        "v1": APIVersion(
            version="v1",
            released_at=datetime(2024, 1, 1),
            deprecated=False,
            notes="Initial release"
        ),
        "v2": APIVersion(
            version="v2",
            released_at=datetime(2024, 6, 1),
            deprecated=False,
            notes="Enhanced signal system"
        ),
        "v3": APIVersion(
            version="v3",
            released_at=datetime(2025, 1, 1),
            deprecated=False,
            notes="Current stable version with ATAS V2"
        ),
    }
    
    CURRENT_VERSION = "v3"
    MIN_SUPPORTED_VERSION = "v1"
    
    @classmethod
    def get_current_version(cls) -> str:
        """Get the current API version"""
        return cls.CURRENT_VERSION
    
    @classmethod
    def get_supported_versions(cls) -> list[str]:
        """Get list of supported versions"""
        return [
            v for v, info in cls.VERSIONS.items() 
            if not info.deprecated or cls._is_within_sunset(v)
        ]
    
    @classmethod
    def is_supported(cls, version: str) -> bool:
        """Check if a version is supported"""
        if version not in cls.VERSIONS:
            return False
        info = cls.VERSIONS[version]
        return not info.deprecated or cls._is_within_sunset(version)
    
    @classmethod
    def is_deprecated(cls, version: str) -> bool:
        """Check if a version is deprecated"""
        if version not in cls.VERSIONS:
            return False
        return cls.VERSIONS[version].deprecated
    
    @classmethod
    def _is_within_sunset(cls, version: str) -> bool:
        """Check if deprecated version is still within sunset period"""
        info = cls.VERSIONS.get(version)
        if not info or not info.deprecated_at or not info.sunset_date:
            return False
        return datetime.now() < info.sunset_date
    
    @classmethod
    def get_version_info(cls, version: str) -> Optional[APIVersion]:
        """Get version information"""
        return cls.VERSIONS.get(version)
    
    @classmethod
    def get_deprecation_warning(cls, version: str) -> Optional[str]:
        """Get deprecation warning message for a version"""
        info = cls.VERSIONS.get(version)
        if not info:
            return None
        
        if info.deprecated:
            if cls._is_within_sunset(version):
                return (
                    f"API version '{version}' is deprecated. "
                    f"Please migrate to a supported version. "
                    f"Version will be sunset on {info.sunset_date}."
                )
            return f"API version '{version}' is no longer supported."
        
        # Check if next version is deprecated
        next_version = cls._get_next_version(version)
        if next_version and cls.is_deprecated(next_version):
            return (
                f"API version '{next_version}' will be deprecated soon. "
                f"Consider using version '{cls.CURRENT_VERSION}'."
            )
        
        return None
    
    @classmethod
    def _get_next_version(cls, current: str) -> Optional[str]:
        """Get the next version after current"""
        versions = list(cls.VERSIONS.keys())
        try:
            idx = versions.index(current)
            if idx + 1 < len(versions):
                return versions[idx + 1]
        except ValueError:
            pass
        return None
    
    @classmethod
    def get_version_metadata(cls) -> dict:
        """Get version metadata for API documentation"""
        return {
            "current": cls.CURRENT_VERSION,
            "supported": cls.get_supported_versions(),
            "minimum": cls.MIN_SUPPORTED_VERSION,
            "versions": {
                v: {
                    "released_at": info.released_at.isoformat(),
                    "deprecated": info.deprecated,
                    "notes": info.notes,
                }
                for v, info in cls.VERSIONS.items()
            }
        }


# Utility functions for FastAPI
def get_api_version_from_header(x_api_version: Optional[str]) -> str:
    """
    Extract API version from request header
    
    Args:
        x_api_version: X-API-Version header value
    
    Returns:
        API version string (defaults to current if not specified)
    """
    if not x_api_version:
        return APIVersionManager.CURRENT_VERSION
    
    if not APIVersionManager.is_supported(x_api_version):
        return APIVersionManager.CURRENT_VERSION
    
    return x_api_version
