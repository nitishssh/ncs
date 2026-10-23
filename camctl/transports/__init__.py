"""Transports are imported lazily so a missing optional dependency only disables that one."""
from importlib import import_module

REGISTRY = {
    "mock": ("camctl.transports.mock", "MockTransport"),
    "wifi": ("camctl.transports.ccapi", "CCAPITransport"),
    "ble": ("camctl.transports.ble", "BLETransport"),
    "usb-edsdk": ("camctl.transports.edsdk", "EDSDKTransport"),
    "usb-gphoto": ("camctl.transports.gphoto", "GPhotoTransport"),
    "video": ("camctl.transports.uvc", "UVCTransport"),
}


def load(kind: str, **kwargs):
    module, cls = REGISTRY[kind]
    return getattr(import_module(module), cls)(**kwargs)
