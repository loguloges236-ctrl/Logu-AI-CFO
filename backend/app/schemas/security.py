from pydantic import BaseModel


class VoiceUnlockStatus(BaseModel):
    """Honest status. Biometric voice verification is NOT implemented; no provider is configured."""

    enabled: bool
    implemented: bool = False
    provider_configured: bool = False
    enrollment_supported: bool = False
    fallback_required: bool = True
    status_message: str = "Voice unlock is not yet implemented. Use your password."


class VoiceUnlockUpdate(BaseModel):
    enabled: bool
