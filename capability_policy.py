from __future__ import annotations

from dataclasses import dataclass


KNOWN_CAPABILITIES = {
    "apps.control",
    "apps.launch",
    "files.read",
    "files.write",
    "media.control",
    "packages.install",
    "shell.run",
    "web.download",
    "web.open",
    "web.read",
}

PROFILE_CAPABILITIES = {
    "read_only": {"files.read", "web.read"},
    "files_and_apps": {
        "apps.control",
        "apps.launch",
        "files.read",
        "files.write",
        "media.control",
        "web.download",
        "web.open",
        "web.read",
    },
    "developer": set(KNOWN_CAPABILITIES),
}


@dataclass(frozen=True)
class CapabilityPolicy:
    profile: str
    overrides: dict[str, bool]

    def __post_init__(self) -> None:
        if self.profile not in PROFILE_CAPABILITIES:
            raise ValueError(f"Unknown permission profile: {self.profile}")
        unknown = set(self.overrides) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"Unknown capability overrides: {', '.join(sorted(unknown))}")

    def allows(self, capability: str) -> bool:
        if capability not in KNOWN_CAPABILITIES:
            return False
        if capability in self.overrides:
            return bool(self.overrides[capability])
        return capability in PROFILE_CAPABILITIES[self.profile]

    def require(self, capability: str) -> None:
        if not self.allows(capability):
            raise PermissionError(f"Capability '{capability}' is disabled")

    def enabled(self) -> tuple[str, ...]:
        return tuple(sorted(item for item in KNOWN_CAPABILITIES if self.allows(item)))
