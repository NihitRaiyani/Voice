"""Historical command compatibility; all current generation uses Claude or mocks."""

from claude_language_lab import run

if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "claude"], default="mock")
    parser.add_argument("--allow-paid-api", action="store_true")
    parser.add_argument("--output", default="var/roma/claude-lab/review.json")
    parser.add_argument("--score")
    asyncio.run(run(parser.parse_args()))
