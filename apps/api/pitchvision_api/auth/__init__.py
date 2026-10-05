"""Authentication. Today: a LOCAL-ONLY dev stub. Real JWT (RS256 via identity JWKS) arrives in M1-BE-05."""
from pitchvision_api.auth.dev import DEV_ORG_HEADER, Principal, current_principal

__all__ = ["DEV_ORG_HEADER", "Principal", "current_principal"]
