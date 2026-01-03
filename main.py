#!/usr/bin/env python3
"""
LightsOut Hue Analyzer - Entry point.

A smart lighting analysis system that learns your habits
and predicts your lighting needs.
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.service import HueAnalyzerService


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="LightsOut Hue Analyzer - Learn and predict lighting patterns"
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run analysis once and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show status and exit",
    )

    args = parser.parse_args()

    service = HueAnalyzerService(config_path=args.config)

    if args.analyze:
        # One-time analysis mode
        if not service.bridge.connect():
            print("Failed to connect to Hue Bridge")
            sys.exit(1)
        service.run_analysis_now()

    elif args.status:
        # Show status
        if not service.bridge.connect():
            print("Failed to connect to Hue Bridge")
            sys.exit(1)
        status = service.get_status()
        print("\n📊 LightsOut Hue Analyzer Status")
        print("=" * 40)
        print(f"Bridge connected: {status['bridge_connected']}")
        print(f"Total events: {status['database_stats']['total_events']}")
        print(f"Active patterns: {status['database_stats']['active_patterns']}")
        if status['database_stats']['oldest_event']:
            print(f"Data since: {status['database_stats']['oldest_event']}")

    else:
        # Normal service mode
        service.start()


if __name__ == "__main__":
    main()
