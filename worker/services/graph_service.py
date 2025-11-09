import sys
from importlib import import_module

sys.modules[__name__] = import_module("api.services.graph_service")
