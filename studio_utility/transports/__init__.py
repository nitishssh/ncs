"""Transports are imported lazily so a missing optional dependency only disables that one."""
from importlib import import_module

REGISTRY = {
    "hub": ("studio_utility.transports.hub", "HubTransport"),
    "mock": ("studio_utility.transports.mock", "MockTransport"),
    "wifi": ("studio_utility.transports.ccapi", "CCAPITransport"),
    "ble": ("studio_utility.transports.ble", "BLETransport"),
    "usb-edsdk": ("studio_utility.transports.edsdk", "EDSDKTransport"),
    "usb-gphoto": ("studio_utility.transports.gphoto", "GPhotoTransport"),
    "video": ("studio_utility.transports.uvc", "UVCTransport"),
}


def load(kind: str, **kwargs):
    module, cls = REGISTRY[kind]
    return getattr(import_module(module), cls)(**kwargs)
