"""Console entry point for amaranth-sim-mcp."""


def main() -> None:
    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
