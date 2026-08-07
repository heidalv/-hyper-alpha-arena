"""
Private Key Encryption Utilities

Provides secure encryption/decryption for Hyperliquid private keys using Fernet (symmetric encryption).
Private keys are encrypted before storing in database and decrypted only when needed for API calls.
"""
import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """
    Get encryption key from file or environment variable

    Returns:
        Encryption key as bytes

    Raises:
        ValueError: If HYPERLIQUID_ENCRYPTION_KEY not found
    """
    # Try to read from Docker persistent file first
    key_file = '/app/data/.encryption_key'
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            key = f.read().strip()
            if key:
                return key.encode()

    # Fallback to environment variable
    key = os.getenv('HYPERLIQUID_ENCRYPTION_KEY')
    if not key:
        raise ValueError(
            "HYPERLIQUID_ENCRYPTION_KEY not found. "
            "Docker should auto-generate this on first startup."
        )
    return key.encode()


def encrypt_private_key(private_key: str) -> str:
    """
    Encrypt private key for database storage

    Args:
        private_key: Hyperliquid private key (plain text, e.g., "0x...")

    Returns:
        Encrypted private key as string

    Raises:
        ValueError: If encryption fails
    """
    try:
        f = Fernet(get_encryption_key())
        encrypted = f.encrypt(private_key.encode())
        logger.debug("Private key encrypted successfully")
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Failed to encrypt private key: {e}")
        raise ValueError(f"Encryption failed: {e}")


def decrypt_private_key(encrypted_key: str) -> str:
    """
    Decrypt private key from database

    Args:
        encrypted_key: Encrypted private key from database

    Returns:
        Decrypted private key as string

    Raises:
        ValueError: If decryption fails
    """
    try:
        f = Fernet(get_encryption_key())
        decrypted = f.decrypt(encrypted_key.encode())
        logger.debug("Private key decrypted successfully")
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Failed to decrypt private key: {e}")
        raise ValueError(f"Decryption failed: {e}")


def generate_encryption_key() -> str:
    """
    Generate a new encryption key

    Use this function to generate a new encryption key for initial setup.
    Store the generated key in HYPERLIQUID_ENCRYPTION_KEY environment variable.

    Returns:
        New encryption key as string

    Example:
        >>> key = generate_encryption_key()
        >>> print(f"Export this: export HYPERLIQUID_ENCRYPTION_KEY={key}")
    """
    key = Fernet.generate_key().decode()
    logger.info("Generated new encryption key")
    return key


def validate_encryption_setup() -> bool:
    """
    Validate that encryption is properly configured

    Returns:
        True if encryption key is set and valid

    Raises:
        ValueError: If encryption setup invalid
    """
    try:
        key = get_encryption_key()
        # Test encryption/decryption
        test_data = "test_private_key_0x123"
        encrypted = encrypt_private_key(test_data)
        decrypted = decrypt_private_key(encrypted)

        if decrypted != test_data:
            raise ValueError("Encryption test failed: decrypted data doesn't match")

        logger.info("Encryption setup validated successfully")
        return True
    except Exception as e:
        logger.error(f"Encryption setup validation failed: {e}")
        raise


# ══════════════════════════════════════════════════════════════
# LLM API Key encryption (reuses same Fernet key as Hyperliquid)
# ══════════════════════════════════════════════════════════════

# Fernet tokens are base64-encoded and always start with "gAAAAA"
# (version byte 0x80 + timestamp + IV + ciphertext + HMAC)
_FERNET_PREFIX = "gAAAAA"


def is_encrypted(value: str) -> bool:
    """Check if a value looks like it's already Fernet-encrypted."""
    return bool(value) and value.startswith(_FERNET_PREFIX)


def encrypt_llm_key(api_key: str) -> str:
    """
    Encrypt an LLM API key for database storage.
    Idempotent: returns as-is if already encrypted.

    Args:
        api_key: Plain-text or already-encrypted LLM API key

    Returns:
        Encrypted value (Fernet token)
    """
    if not api_key:
        return api_key
    if is_encrypted(api_key):
        return api_key
    try:
        f = Fernet(get_encryption_key())
        encrypted = f.encrypt(api_key.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Failed to encrypt LLM key: {e}")
        raise ValueError(f"LLM key encryption failed: {e}")


def decrypt_llm_key(encrypted_key: str) -> str:
    """
    Decrypt an LLM API key from database storage.
    Returns as-is if the value is not encrypted (plaintext / legacy).

    Args:
        encrypted_key: Stored value (may be encrypted or legacy plaintext)

    Returns:
        Decrypted plain-text API key
    """
    if not encrypted_key:
        return encrypted_key
    if not is_encrypted(encrypted_key):
        # Legacy plaintext key — return as-is
        return encrypted_key
    try:
        f = Fernet(get_encryption_key())
        decrypted = f.decrypt(encrypted_key.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Failed to decrypt LLM key: {e}")
        raise ValueError(f"LLM key decryption failed: {e}")


if __name__ == "__main__":
    # Script mode: generate encryption key
    print("Generating new encryption key for Hyperliquid private key storage...")
    print("")
    key = generate_encryption_key()
    print(f"Generated Key: {key}")
    print("")
    print("Add this to your .env file or export as environment variable:")
    print(f"export HYPERLIQUID_ENCRYPTION_KEY={key}")
    print("")
    print("Keep this key secure! If lost, you cannot decrypt stored private keys.")
