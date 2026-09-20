from __future__ import annotations

from typing import TYPE_CHECKING

from .storage import VaultStorage

if TYPE_CHECKING:
    from ..config import Settings


def create_vault(settings: Settings) -> VaultStorage:
    if settings.vault_backend == "sqlite":
        from .sqlite import SqliteVaultManager
        path = settings.vault_path
        db_path = path / "vault.db" if path.suffix != ".db" else path
        return SqliteVaultManager(db_path)
    from .manager import VaultManager
    return VaultManager(settings.vault_path, git_enabled=settings.vault_fs.git)
