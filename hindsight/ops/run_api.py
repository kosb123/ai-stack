# Console runner for the hindsight API (bypasses the silent .exe launcher).
import sys

sys.argv = ["hindsight-api"]
from hindsight_api.main import main

if __name__ == "__main__":
    main()
