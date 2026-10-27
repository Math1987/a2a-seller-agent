"""A2A seller library. Owner operations are available through SellerStore."""

__version__ = "0.1.0"

# Keep discovery and `skill show` independent of database initialization.
def __getattr__(name):
    if name in {"SellerStore", "SellerError"}:
        from .storage import SellerError, SellerStore
        return {"SellerStore": SellerStore, "SellerError": SellerError}[name]
    raise AttributeError(name)


__all__ = ["SellerStore", "SellerError", "__version__"]
