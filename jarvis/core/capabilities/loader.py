"""JARVIS Capability Manifest Loader.

Loads, validates, and serializes capability manifests from JSON and YAML files
and directories with SHA-256 integrity verification and schema validation.
"""

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import CapabilityFirewallError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class ManifestLoader:
    """Loader utility for persisting and loading CapabilityManifest specifications."""

    @staticmethod
    def load_file(file_path: Path | str, verify_digest: bool = True) -> CapabilityManifest:
        """Load and validate a single manifest from a JSON or YAML file."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Manifest file not found: {path}")

        raw_text = path.read_text(encoding="utf-8")

        try:
            if path.suffix in (".yaml", ".yml"):
                data: dict[str, Any] = yaml.safe_load(raw_text)
            else:
                import json

                data = json.loads(raw_text)
        except Exception as exc:
            raise CapabilityFirewallError(
                f"Failed to parse manifest syntax in '{path.name}': {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise CapabilityFirewallError(f"Manifest in '{path.name}' must be a dictionary.")

        try:
            manifest = CapabilityManifest.model_validate(data)
        except ValidationError as exc:
            raise CapabilityFirewallError(
                f"Schema validation error for manifest '{path.name}': {exc}"
            ) from exc

        if verify_digest and manifest.digest:
            if not manifest.verify_digest():
                raise CapabilityFirewallError(
                    f"Integrity check failed for manifest '{manifest.capability_id}' in '{path.name}'. "
                    f"Calculated SHA-256 does not match sealed digest."
                )
        elif not manifest.digest:
            manifest.seal()

        return manifest

    @staticmethod
    def load_directory(
        directory_path: Path | str,
        registry: CapabilityRegistry | None = None,
        verify_digest: bool = True,
    ) -> list[CapabilityManifest]:
        """Recursively discover and load all manifests from a directory."""
        dir_path = Path(directory_path)
        if not dir_path.exists() or not dir_path.is_dir():
            raise FileNotFoundError(f"Manifest directory not found: {dir_path}")

        loaded: list[CapabilityManifest] = []
        extensions = ("*.json", "*.yaml", "*.yml")

        for ext in extensions:
            for file_path in dir_path.rglob(ext):
                try:
                    manifest = ManifestLoader.load_file(file_path, verify_digest=verify_digest)
                    loaded.append(manifest)
                    if registry is not None:
                        registry.register(manifest, verify_digest=verify_digest)
                except Exception as exc:
                    logger.error("manifest_load_error", file=str(file_path), error=str(exc))
                    raise

        return loaded

    @staticmethod
    def save_file(
        manifest: CapabilityManifest,
        file_path: Path | str,
        format_type: Literal["json", "yaml"] = "json",
    ) -> None:
        """Serialize and save a manifest to disk with sealed digest."""
        manifest.seal()
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = manifest.model_dump(mode="json")
        if format_type == "yaml":
            raw_text = yaml.safe_dump(data, sort_keys=True)
        else:
            import json

            raw_text = json.dumps(data, indent=2, sort_keys=True)

        path.write_text(raw_text, encoding="utf-8")
        logger.info(
            "manifest_saved",
            capability_id=manifest.capability_id,
            path=str(path),
            format=format_type,
        )
