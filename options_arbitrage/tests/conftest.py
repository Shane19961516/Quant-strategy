# Disable background feeds during pytest (TestClient triggers startup).
import os

os.environ.setdefault("AUTO_QUOTES", "0")
os.environ.setdefault("AUTO_CFMMC", "0")
os.environ.setdefault("AUTO_SCHEDULER", "0")
